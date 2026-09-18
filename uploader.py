import os
import logging
import pathlib
import requests

logger = logging.getLogger(__name__)

MEGAUP_UPLOAD_URL = os.environ.get(
    "MEGAUP_UPLOAD_URL", 
    "https://megaup.net/api/v2/file/upload"
)
MEGAUP_API_KEY = os.environ.get(
    "MEGAUP_API_KEY", 
    "4MGSbiIusAdcGloqCZsatuqMVeovZjTklKGvlEtLZRb6i7BcDmW0wrh6LRnCPxRz"
)
MEGAUP_FOLDER_ID = os.environ.get("MEGAUP_FOLDER_ID", "63172")[cite: 1]

def megaup_upload(file_path: pathlib.Path | str) -> dict:
    """
    Megaup.net API v2 သို့ Local file အား multipart/form-data ဖြင့် upload တင်ပြီး
    API မှ ပြန်လာသော JSON response ကို return ပေးသည်။
    """
    path = pathlib.Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    form_data = {
        "key1": MEGAUP_API_KEY,
        "folder_id": str(MEGAUP_FOLDER_ID),
    }

    file_size_mb = path.stat().st_size / (1024 * 1024)
    logger.info("Uploading %s (%.2f MB) to Megaup folder %s...", 
                path.name, file_size_mb, MEGAUP_FOLDER_ID)

    # ဖိုင်အရွယ်အစားကြီးနိုင်သဖြင့် timeout အား ၁၅ မိနစ် (900s) ထားရှိသည်
    with open(path, "rb") as fh:
        files = {"file": (path.name, fh)}
        response = requests.post(
            MEGAUP_UPLOAD_URL, 
            data=form_data, 
            files=files, 
            timeout=900
        )

    response.raise_for_status()
    res_json = response.json()

    # Yetishare v2 engine error status စစ်ဆေးခြင်း
    if res_json.get("error") or res_json.get("status") == "error":
        err_msg = res_json.get("message") or res_json.get("response") or "Upload failed"
        raise RuntimeError(f"Megaup API Error: {err_msg}")

    return res_json
