import os
import logging
import pathlib
import uuid
import requests

logger = logging.getLogger(__name__)

MEGAUP_API_BASE = os.environ.get("MEGAUP_API_BASE", "https://megaup.net/api/v2")
MEGAUP_KEY1 = os.environ.get("MEGAUP_API_KEY")
MEGAUP_KEY2 = os.environ.get("MEGAUP_API_KEY2")
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID")

CHUNK_SIZE = 15 * 1024 * 1024

_AUTH_CACHE = {"token": None, "account_id": None}


def get_auth_token() -> tuple[str, str]:
    """Authorize using Key1 and Key2 from environment to retrieve access_token and account_id."""
    if _AUTH_CACHE["token"] and _AUTH_CACHE["account_id"]:
        return _AUTH_CACHE["token"], _AUTH_CACHE["account_id"]

    if not MEGAUP_KEY1:
        raise ValueError("Missing required environment variable: MEGAUP_API_KEY")
    if not MEGAUP_KEY2:
        raise ValueError("Missing required environment variable: MEGAUP_API_KEY2")

    url = f"{MEGAUP_API_BASE}/authorize"
    data = {
        "key1": MEGAUP_KEY1,
        "key2": MEGAUP_KEY2,
    }
    logger.info("Authorizing with Megaup API v2...")
    res = requests.post(url, data=data, timeout=30)
    res.raise_for_status()
    res_data = res.json()

    if res_data.get("_status") == "error" or res_data.get("status") == "error":
        err_msg = res_data.get("response") or res_data.get("message")
        raise RuntimeError(f"Megaup Authorization Failed: {err_msg}")

    payload = res_data.get("data", {})
    token = payload.get("access_token")
    account_id = payload.get("account_id")

    if not token or not account_id:
        raise RuntimeError(f"Unexpected auth response from Megaup: {res_data}")

    _AUTH_CACHE["token"] = token
    _AUTH_CACHE["account_id"] = str(account_id)
    return token, str(account_id)


def megaup_upload(file_path: pathlib.Path | str, progress_callback=None) -> dict:
    """Upload a file using authorized session in chunks to avoid 413 limits."""
    if not MEGAUP_FOLDER_ID:
        raise ValueError("Missing required environment variable: MEGAUP_FOLDER_ID")

    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    access_token, account_id = get_auth_token()
    total_size = path.stat().st_size
    file_name = path.name
    c_tracker = str(uuid.uuid4())
    upload_url = f"{MEGAUP_API_BASE}/file/upload"

    logger.info("Uploading %s (%.2f MB) to Megaup folder %s...", 
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
                "access_token": access_token,
                "account_id": account_id,
                "folder_id": str(MEGAUP_FOLDER_ID),
                "c_tracker": c_tracker,
                "max_chunk_size": str(CHUNK_SIZE),
            }

            headers = {
                "Content-Range": f"bytes {range_start}-{range_end}/{total_size}"
            }

            files = {
                "upload_file": (file_name, chunk_data, "application/octet-stream")
            }

            response = requests.post(
                upload_url,
                data=form_data,
                files=files,
                headers=headers,
                timeout=180,
            )
            response.raise_for_status()
            res_json = response.json()

            if isinstance(res_json, dict) and res_json.get("upload_server"):
                upload_url = res_json["upload_server"]

            bytes_sent += chunk_len
            if progress_callback:
                progress_callback(bytes_sent, total_size)

    logger.info("Megaup upload response for %s: %s", file_name, res_json)

    if res_json.get("error") or res_json.get("status") == "error" or res_json.get("_status") == "error":
        err_msg = res_json.get("message") or res_json.get("response") or "Upload failed"
        raise RuntimeError(f"Megaup API Error: {err_msg}")

    return res_json
