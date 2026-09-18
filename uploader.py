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
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID")

# Browser ထဲမှ ရရှိလာသော တကယ့် Direct Storage Node Upload Handler
DIRECT_NODE_URL = (
    "https://f103.mupload.store/ajax/file_upload_handler"
    "?r=megaup.net&p=https"
    "&csaKey1=0725c5d284f6e8a21ee12a71c6188ae34e4f682c24120f93ceab4ddfd131b518"
    "&csaKey2=36a0458e4cf784eed36494d15532e1b148bf524c39f933ee200956a101490abd"
    "&cupload100"
)

CHUNK_SIZE = 15 * 1024 * 1024


def get_authenticated_session() -> requests.Session:
    """Create requests session with browser cookies and headers."""
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
    Fetch the real dynamic storage node URL from Megaup uploader modal.
    If dynamic discovery fails, use the validated direct node URL.
    """
    try:
        res = session.get(f"{MEGAUP_BASE}/account/ajax/uploader", timeout=30)
        res.raise_for_status()
        match = re.search(r'https?://[^\s\'"]+?\.mupload\.store/ajax/file_upload_handler[^\s\'"]*', res.text)
        if match:
            url = match.group(0).replace("&amp;", "&")
            logger.info("Auto-discovered Active Megaup Storage Node: %s", url)
            return url
    except Exception as exc:
        logger.warning("Could not auto-fetch dynamic node (%s). Using verified DIRECT_NODE_URL.", exc)

    return DIRECT_NODE_URL


def megaup_upload(file_path: pathlib.Path | str, progress_callback=None) -> dict:
    """Upload file chunks directly to the Mupload Storage Node."""
    if not MEGAUP_COOKIE_FILEHOSTING:
        raise ValueError("Missing required environment variable: MEGAUP_COOKIE_FILEHOSTING")
    if not MEGAUP_FOLDER_ID:
        raise ValueError("Missing required environment variable: MEGAUP_FOLDER_ID")

    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    file_name = path.name
    c_tracker = str(uuid.uuid4())

    session = get_authenticated_session()
    upload_url = resolve_upload_url(session)

    logger.info("Uploading %s (%.2f MB) to Megaup folder %s via Storage Node...", 
                file_name, total_size / (1024 * 1024), MEGAUP_FOLDER_ID)

    bytes_sent = 0
    res_json = {}

    with open(path, "rb") as fh:
        while bytes_sent < total_size:
            chunk_data = fh.read(CHUNK_SIZE)
            chunk_len = len(chunk_data)
            if not chunk_data:
                break

            range_start = bytes_sent
            range_end = bytes_sent + chunk_len - 1

            form_data = {
                "folder_id": str(MEGAUP_FOLDER_ID),
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
                res_json = response.json()
            except Exception:
                logger.warning("Storage Node Non-JSON response: %s", response.text[:200])

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    logger.info("Storage Node final response for %s: %s", file_name, res_json)

    if isinstance(res_json, list) and len(res_json) > 0:
        res_json = res_json[0]

    if isinstance(res_json, dict) and res_json.get("error"):
        raise RuntimeError(f"Megaup Error: {res_json.get('error')}")

    return res_json
