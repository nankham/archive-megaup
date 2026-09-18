import os
import logging
import pathlib
import uuid
import requests

logger = logging.getLogger(__name__)

MEGAUP_UPLOAD_URL = os.environ.get(
    "MEGAUP_UPLOAD_URL", 
    "https://megaup.net/api/v2/file/upload"
)
MEGAUP_API_KEY = os.environ.get("MEGAUP_API_KEY")
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID")

# 15 MB chunks to stay well below Cloudflare/Nginx 413 limits
CHUNK_SIZE = 15 * 1024 * 1024


def megaup_upload(file_path: pathlib.Path | str, progress_callback=None) -> dict:
    """
    Upload file in chunks to Megaup.net API v2 to bypass 413 Request Entity Too Large.
    """
    if not MEGAUP_API_KEY:
        raise ValueError("Missing required environment variable: MEGAUP_API_KEY")
    if not MEGAUP_FOLDER_ID:
        raise ValueError("Missing required environment variable: MEGAUP_FOLDER_ID")

    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    file_name = path.name
    c_tracker = str(uuid.uuid4())
    upload_url = MEGAUP_UPLOAD_URL

    logger.info("Uploading %s (%.2f MB) in chunks to Megaup...", 
                file_name, total_size / (1024 * 1024))

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
                "key1": MEGAUP_API_KEY,
                "folder_id": str(MEGAUP_FOLDER_ID),
                "c_tracker": c_tracker,
                "max_chunk_size": str(CHUNK_SIZE),
            }

            headers = {
                "Content-Range": f"bytes {range_start}-{range_end}/{total_size}"
            }

            files = {
                "file": (file_name, chunk_data, "application/octet-stream")
            }

            response = requests.post(
                upload_url,
                data=form_data,
                files=files,
                headers=headers,
                timeout=120,
            )
            response.raise_for_status()
            res_json = response.json()

            # Follow upload server if redirected by pooling
            if isinstance(res_json, dict) and res_json.get("upload_server"):
                upload_url = res_json["upload_server"]

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    if res_json.get("error") or res_json.get("status") == "error":
        err_msg = res_json.get("message") or res_json.get("response") or "Upload failed"
        raise RuntimeError(f"Megaup API Error: {err_msg}")

    return res_json
