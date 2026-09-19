import os
import re
import logging
import pathlib
import uuid
import requests

logger = logging.getLogger(__name__)

MEGAUP_BASE = "https://megaup.net"
MEGAUP_COOKIE_FILEHOSTING = os.environ.get("MEGAUP_COOKIE_FILEHOSTING")
MEGAUP_COOKIE_CFCLEARANCE = os.environ.get("MEGAUP_COOKIE_CFCLEARANCE")
MEGAUP_PARENT_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID", "63172")
MEGAUP_FALLBACK_NODE_URL = os.environ.get("MEGAUP_DIRECT_NODE_URL")

CHUNK_SIZE = 15 * 1024 * 1024
_RESOLVED_UPLOAD_URL = None


def get_authenticated_session() -> requests.Session:
    """Create requests session with browser cookies and headers."""
    if not MEGAUP_COOKIE_FILEHOSTING:
        raise ValueError("Missing required environment variable: MEGAUP_COOKIE_FILEHOSTING")

    session = requests.Session()
    cookie_parts = [f"filehosting={MEGAUP_COOKIE_FILEHOSTING}"]
    if MEGAUP_COOKIE_CFCLEARANCE:
        cookie_parts.append(f"cf_clearance={MEGAUP_COOKIE_CFCLEARANCE}")

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{MEGAUP_BASE}/",
        "Origin": MEGAUP_BASE,
        "Cookie": "; ".join(cookie_parts),
    })
    return session


def resolve_upload_url(session: requests.Session) -> str:
    """
    Dynamically discover the active upload storage node and signature keys
    from Megaup dashboard HTML/JS for the authenticated user session.
    """
    global _RESOLVED_UPLOAD_URL
    if _RESOLVED_UPLOAD_URL:
        return _RESOLVED_UPLOAD_URL

    try:
        res = session.get(f"{MEGAUP_BASE}/", timeout=30)
        res.raise_for_status()

        # Yetishare v5 storage node pattern
        match = re.search(r'https?://[a-zA-Z0-9_\-\.]+\.mupload\.store/ajax/file_upload_handler[^\s\'"]*', res.text)
        if match:
            _RESOLVED_UPLOAD_URL = match.group(0).replace("&amp;", "&")
            logger.info("Dynamically resolved Megaup upload node: %s", _RESOLVED_UPLOAD_URL)
            return _RESOLVED_UPLOAD_URL
    except Exception as exc:
        logger.warning("Dynamic node resolution failed: %s", exc)

    # Fallback to manual environment variable if provided
    if MEGAUP_FALLBACK_NODE_URL:
        logger.info("Using configured MEGAUP_DIRECT_NODE_URL fallback.")
        _RESOLVED_UPLOAD_URL = MEGAUP_FALLBACK_NODE_URL
        return _RESOLVED_UPLOAD_URL

    raise RuntimeError("Could not resolve Megaup Storage Node. Please provide MEGAUP_DIRECT_NODE_URL in .env")


def create_or_get_folder(folder_name: str, parent_id: str = None) -> str:
    """Create a new folder inside parent folder on Megaup and return its folder_id."""
    parent_id = parent_id or MEGAUP_PARENT_FOLDER_ID
    session = get_authenticated_session()
    url = f"{MEGAUP_BASE}/account/ajax/add_folder"

    payload = {
        "folder_name": folder_name,
        "parent_folder_id": str(parent_id),
    }

    try:
        res = session.post(
            url,
            data=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        data = res.json()
        if data.get("folder_id"):
            logger.info("Created Megaup folder '%s' with ID: %s", folder_name, data["folder_id"])
            return str(data["folder_id"])
    except Exception as exc:
        logger.warning("Could not create folder '%s': %s. Falling back to parent ID: %s", folder_name, exc, parent_id)

    return str(parent_id)


def megaup_upload(file_path: pathlib.Path | str, target_folder_id: str = None, progress_callback=None) -> dict:
    """Upload a file using session cookies directly to the Mupload Storage Node."""
    folder_id = target_folder_id or MEGAUP_PARENT_FOLDER_ID
    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    file_name = path.name
    c_tracker = str(uuid.uuid4())

    session = get_authenticated_session()
    upload_url = resolve_upload_url(session)

    logger.info("Uploading %s (%.2f MB) to Megaup folder %s...", 
                file_name, total_size / (1024 * 1024), folder_id)

    bytes_sent = 0
    res_data = None

    with open(path, "rb") as fh:
        while bytes_sent < total_size:
            chunk_data = fh.read(CHUNK_SIZE)
            chunk_len = len(chunk_data)
            if not chunk_data:
                break

            range_start = bytes_sent
            range_end = bytes_sent + chunk_len - 1

            form_data = {
                "folder_id": str(folder_id),
                "folderId": str(folder_id),
                "upload_folder": str(folder_id),
                "c_tracker": c_tracker,
                "max_chunk_size": str(CHUNK_SIZE),
            }

            headers = {
                "Content-Range": f"bytes {range_start}-{range_end}/{total_size}",
                "X-Requested-With": "XMLHttpRequest",
            }

            files = {
                "files[]": (file_name, chunk_data, "application/octet-stream")
            }

            response = session.post(
                upload_url,
                data=form_data,
                files=files,
                headers=headers,
                timeout=180,
            )
            response.raise_for_status()

            try:
                res_data = response.json()
            except Exception:
                logger.warning("Storage Node Non-JSON chunk response: %s", response.text[:200])

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    logger.info("Megaup final response for %s: %s", file_name, res_data)

    if isinstance(res_data, list) and len(res_data) > 0:
        res_data = res_data[0]

    if isinstance(res_data, dict) and res_data.get("error"):
        raise RuntimeError(f"Megaup Error: {res_data.get('error')}")

    return res_data or {}
