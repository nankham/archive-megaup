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
    """Discover active storage node or fallback to direct URL."""
    global _RESOLVED_UPLOAD_URL
    if _RESOLVED_UPLOAD_URL:
        return _RESOLVED_UPLOAD_URL

    try:
        res = session.get(f"{MEGAUP_BASE}/", timeout=30)
        res.raise_for_status()
        match = re.search(r'https?://[a-zA-Z0-9_\-\.]+\.mupload\.store/ajax/file_upload_handler[^\s\'"]*', res.text)
        if match:
            _RESOLVED_UPLOAD_URL = match.group(0).replace("&amp;", "&")
            logger.info("Auto-discovered Storage Node: %s", _RESOLVED_UPLOAD_URL)
            return _RESOLVED_UPLOAD_URL
    except Exception as exc:
        logger.warning("Dynamic node resolution failed: %s", exc)

    if MEGAUP_FALLBACK_NODE_URL:
        logger.info("Using configured MEGAUP_DIRECT_NODE_URL fallback.")
        _RESOLVED_UPLOAD_URL = MEGAUP_FALLBACK_NODE_URL
        return _RESOLVED_UPLOAD_URL

    raise RuntimeError("Could not resolve Megaup Storage Node. Please provide MEGAUP_DIRECT_NODE_URL in .env")


def create_or_get_folder(folder_name: str, parent_id: str = None) -> str:
    """
    Directly invoke Megaup's /account/ajax/add_edit_folder_process endpoint.
    """
    parent_id = str(parent_id or MEGAUP_PARENT_FOLDER_ID)
    session = get_authenticated_session()
    url = f"{MEGAUP_BASE}/account/ajax/add_edit_folder_process"

    # Remove invalid filesystem characters
    clean_name = re.sub(r'[\/:*?"<>|]', ' - ', folder_name)
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()[:80]

    payload = {
        "folderName": clean_name,
        "parentId": parent_id,
        "parent_folder_id": parent_id,
        "isPublic": "1",
        "password": "",
        "watermarkPreviews": "0",
        "showDownloadLinks": "1",
        "submitme": "1",
    }

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"{MEGAUP_BASE}/",
        "Origin": MEGAUP_BASE,
    }

    try:
        logger.info("Sending Folder Create Request to Megaup for '%s' (Parent: %s)...", clean_name, parent_id)
        res = session.post(url, data=payload, headers=headers, timeout=30)
        res.raise_for_status()

        try:
            data = res.json()
        except Exception:
            logger.error("Megaup folder create returned non-JSON: %s", res.text[:300])
            return parent_id

        logger.info("Megaup Folder Create Result: %s", data)

        if data.get("success") and data.get("folder_id"):
            new_folder_id = str(data["folder_id"])
            logger.info("✅ SUCCESS: Created Folder '%s' -> Folder ID: %s", clean_name, new_folder_id)
            return new_folder_id
        else:
            logger.error("❌ Megaup failed to create folder: %s", data.get("msg", "Unknown error"))
    except Exception as exc:
        logger.exception("Critical exception in create_or_get_folder: %s", exc)

    logger.warning("Using parent ID %s as fallback.", parent_id)
    return parent_id


def move_file_to_folder(file_id: str, target_folder_id: str) -> bool:
    """Move file into designated folder ID on Megaup."""
    session = get_authenticated_session()
    endpoints = [
        f"{MEGAUP_BASE}/account/ajax/drag_files_into_folder",
        f"{MEGAUP_BASE}/ajax/drag_files_into_folder",
    ]

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"{MEGAUP_BASE}/",
        "Origin": MEGAUP_BASE,
    }

    payloads = [
        {"fileIds[]": str(file_id), "folderId": str(target_folder_id)},
        {"fileIds": str(file_id), "folderId": str(target_folder_id)},
    ]

    for ep in endpoints:
        for p in payloads:
            try:
                res = session.post(ep, data=p, headers=headers, timeout=20)
                if res.status_code == 200:
                    logger.info("Moved file ID %s into Folder ID %s", file_id, target_folder_id)
                    return True
            except Exception:
                pass

    return False


def megaup_upload(file_path: pathlib.Path | str, target_folder_id: str = None, progress_callback=None) -> dict:
    """Upload a file directly into target Album folder in 15 MB chunks."""
    folder_id = str(target_folder_id or MEGAUP_PARENT_FOLDER_ID)
    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    file_name = path.name
    c_tracker = str(uuid.uuid4())

    session = get_authenticated_session()
    upload_url = resolve_upload_url(session)

    logger.info("Uploading %s (%.2f MB) into Target Folder ID %s...", 
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
                "folder_id": folder_id,
                "folderId": folder_id,
                "upload_folder": folder_id,
                "upload_folder_id": folder_id,
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
                pass

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    logger.info("Upload completed for %s: %s", file_name, res_data)

    if isinstance(res_data, list) and len(res_data) > 0:
        res_data = res_data[0]

    if isinstance(res_data, dict):
        if res_data.get("error"):
            raise RuntimeError(f"Megaup Error: {res_data.get('error')}")

        file_id = res_data.get("file_id")
        if file_id and folder_id != str(MEGAUP_PARENT_FOLDER_ID):
            move_file_to_folder(file_id, folder_id)

    return res_data or {}
