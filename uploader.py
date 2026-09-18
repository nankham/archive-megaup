import os
import logging
import pathlib
import uuid
import requests

logger = logging.getLogger(__name__)

MEGAUP_BASE = "https://megaup.net"
MEGAUP_COOKIE_FILEHOSTING = os.environ.get("MEGAUP_COOKIE_FILEHOSTING")
MEGAUP_COOKIE_CFCLEARANCE = os.environ.get("MEGAUP_COOKIE_CFCLEARANCE")
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID")

CHUNK_SIZE = 15 * 1024 * 1024


def megaup_upload(file_path: pathlib.Path | str, progress_callback=None) -> dict:
    """
    Upload a file using web session cookies (filehosting + cf_clearance)
    to bypass Free User API restriction and Cloudflare.
    """
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
    upload_url = f"{MEGAUP_BASE}/core/page/ajax/file_upload_handler.ajax.php"

    session = requests.Session()

    # Browser Cookie string တည်ဆောက်ခြင်း
    cookie_parts = [f"filehosting={MEGAUP_COOKIE_FILEHOSTING}"]
    if MEGAUP_COOKIE_CFCLEARANCE:
        cookie_parts.append(f"cf_clearance={MEGAUP_COOKIE_CFCLEARANCE}")
    cookie_header = "; ".join(cookie_parts)

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{MEGAUP_BASE}/",
        "Origin": MEGAUP_BASE,
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie_header,
    })

    logger.info("Uploading %s (%.2f MB) via Authenticated Web Session to folder %s...", 
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
                "Content-Range": f"bytes {range_start}-{range_end}/{total_size}"
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
                logger.warning("Non-JSON response received: %s", response.text[:200])

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    logger.info("Megaup final response for %s: %s", file_name, res_json)

    # Yetishare response normalization
    if isinstance(res_json, list) and len(res_json) > 0:
        res_json = res_json[0]

    if isinstance(res_json, dict) and res_json.get("error"):
        raise RuntimeError(f"Megaup Error: {res_json.get('error')}")

    return res_json
