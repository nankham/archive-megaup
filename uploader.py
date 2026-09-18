import os
import logging
import pathlib
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

logger = logging.getLogger(__name__)

MEGAUP_UPLOAD_URL = os.environ.get(
    "MEGAUP_UPLOAD_URL", 
    "https://megaup.net/api/v2/file/upload"
)
MEGAUP_API_KEY = os.environ.get("MEGAUP_API_KEY")
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID")

def megaup_upload(file_path: pathlib.Path | str, progress_callback=None) -> dict:
    if not MEGAUP_API_KEY:
        raise ValueError("Missing required environment variable: MEGAUP_API_KEY")
    if not MEGAUP_FOLDER_ID:
        raise ValueError("Missing required environment variable: MEGAUP_FOLDER_ID")

    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    logger.info("Uploading %s (%.2f MB) to Megaup folder %s...", 
                path.name, total_size / (1024 * 1024), MEGAUP_FOLDER_ID)

    with open(path, "rb") as fh:
        encoder = MultipartEncoder(
            fields={
                "key1": MEGAUP_API_KEY,
                "folder_id": str(MEGAUP_FOLDER_ID),
                "file": (path.name, fh, "application/octet-stream"),
            }
        )

        def _monitor_callback(monitor):
            if progress_callback:
                progress_callback(monitor.bytes_read, total_size)

        monitor = MultipartEncoderMonitor(encoder, _monitor_callback)

        response = requests.post(
            MEGAUP_UPLOAD_URL,
            data=monitor,
            headers={"Content-Type": monitor.content_type},
            timeout=1800,
        )

    response.raise_for_status()
    res_json = response.json()

    if res_json.get("error") or res_json.get("status") == "error":
        err_msg = res_json.get("message") or res_json.get("response") or "Upload failed"
        raise RuntimeError(f"Megaup API Error: {err_msg}")

    return res_json
