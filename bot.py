import os
import re
import time
import uuid
import shutil
import pathlib
import logging
import asyncio
from urllib.parse import quote
from functools import wraps

import requests
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from uploader import megaup_upload, create_or_get_folder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")

ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

TEMP_DIR = pathlib.Path(os.environ.get("TEMP_DOWNLOAD_DIR", "/downloads"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

app = Client(
    "megaup_archive_bot",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH,
    in_memory=True
)

JOBS = {}


def authorized(func):
    @wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        user_id = update.from_user.id if update.from_user else None
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            if isinstance(update, Message):
                await update.reply_text("⛔ Unauthorized user access denied.")
            elif isinstance(update, CallbackQuery):
                await update.answer("⛔ Unauthorized user access denied.", show_alert=True)
            return
        return await func(client, update, *args, **kwargs)
    return wrapper


def safe_child_path(base_dir: pathlib.Path, file_name: str) -> pathlib.Path:
    base_dir = base_dir.resolve()
    target_path = (base_dir / file_name).resolve()
    if not str(target_path).startswith(str(base_dir)):
        raise ValueError(f"Directory traversal attack detected: {file_name}")
    return target_path


class ProgressTracker:
    def __init__(self, loop, message: Message, phase: str, filename: str, index: int, total: int):
        self.loop = loop
        self.message = message
        self.phase = phase
        self.filename = filename
        self.index = index
        self.total = total
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_text = ""

    def update(self, current: int, total: int):
        now = time.time()
        if now - self.last_update_time < 3 and current < total:
            return
        self.last_update_time = now

        percent = (current / total) * 100 if total > 0 else 0
        elapsed = now - self.start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0

        bar_len = 12
        filled = int(bar_len * percent / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        text = (
            f"📦 **Batch Progress ({self.index}/{self.total})**\n"
            f"📄 `{self.filename}`\n\n"
            f"{self.phase}...\n"
            f"`[{bar}] {percent:.1f}%`\n"
            f"⚡ **Speed:** {speed / (1024*1024):.2f} MB/s\n"
            f"⏳ **ETA:** {int(eta)}s | 💾 **Done:** {current / (1024*1024):.2f}/{total / (1024*1024):.2f} MB"
        )

        if text != self.last_text:
            self.last_text = text
            asyncio.run_coroutine_threadsafe(self._edit_message(text), self.loop)

    async def _edit_message(self, text: str):
        try:
            await self.message.edit_text(text)
        except Exception:
            pass


def stream_download_with_progress(url: str, dest_path: pathlib.Path, progress_callback=None):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)


@app.on_message(filters.command("start"))
@authorized
async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 **Welcome to Archive.org to Megaup Pipeline Bot**\n\n"
        "Send me any Archive.org link via `/download <link>`\n"
        "Example:\n"
        "`/download https://archive.org/details/john-coltrane-quartet-crescent-high-res`"
    )


@app.on_message(filters.command("download"))
@authorized
async def download_cmd(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: `/download https://archive.org/details/<identifier>`")
        return

    url = args[1].strip()
    match = re.search(r"archive\.org/details/([^/?#]+)", url)
    if not match:
        await message.reply_text("❌ Invalid Archive.org details URL format.")
        return

    identifier = match.group(1)
    status_msg = await message.reply_text(f"🔍 Fetching metadata for `{identifier}`...")

    try:
        meta_url = f"https://archive.org/metadata/{identifier}"
        res = requests.get(meta_url, timeout=30)
        res.raise_for_status()
        meta_data = res.json()
    except Exception as exc:
        await status_msg.edit(f"❌ Failed to retrieve Archive.org metadata: {exc}")
        return

    files = meta_data.get("files", [])
    if not files:
        await status_msg.edit("❌ No downloadable files found in this item.")
        return

    formats = {}
    for f in files:
        fmt = f.get("format")
        if fmt and fmt not in ("Metadata", "Item Tile", "Archive BitTorrent"):
            formats[fmt] = formats.get(fmt, 0) + 1

    if not formats:
        await status_msg.edit("❌ No suitable audio/media formats found.")
        return

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "identifier": identifier,
        "files": files,
        "meta": meta_data,
    }

    buttons = [
        [InlineKeyboardButton(f"{fmt} ({count} files)", callback_data=f"pickformat|{job_id}|{fmt}")]
        for fmt, count in formats.items()
    ]
    title = meta_data.get("metadata", {}).get("title", identifier)

    await status_msg.edit(
        f"🎵 **Album:** `{title}`\n"
        f"Select the format to download and upload to Megaup:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex(r"^pickformat\|"))
@authorized
async def pickformat(client, cq: CallbackQuery):
    _, jobid, format_ = cq.data.split("|", 2)
    await cq.answer()

    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("❌ Job session expired. Please re-send the link.", reply_markup=None)
        return

    ident = job["identifier"]
    metadata_info = job.get("meta", {}).get("metadata", {})
    
    # 1. Album Title သန့်စင်ပြီး Megaup တွင် သီးခြား Folder ဆောက်ခြင်း
    album_title = metadata_info.get("title") or ident
    safe_folder_name = "".join(c for c in album_title if c not in r'\/:*?"<>|').strip()[:80]
    
    m = cq.message
    await m.edit(f"📁 Creating dedicated folder on Megaup:\n`{safe_folder_name}`...")
    target_folder_id = await asyncio.to_thread(create_or_get_folder, safe_folder_name)

    target_dir = TEMP_DIR / ident
    target_dir.mkdir(parents=True, exist_ok=True)

    # 2. ရွေးချယ်ထားသော Format နှင့် Cover/Album Art ဖိုင်များကို ထုတ်ယူခြင်း
    target_files = [f for f in job["files"] if f.get("format") == format_]
    
    image_files = [
        f for f in job["files"] 
        if any(f.get("name", "").lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"])
        or "Item Image" in f.get("format", "")
        or "Thumbnail" in f.get("format", "")
    ]
    if image_files:
        cover_file = image_files[0]
        if cover_file not in target_files:
            target_files.insert(0, cover_file)

    total_files = len(target_files)
    downloaded_count = 0
    uploaded_links = []
    loop = asyncio.get_running_loop()

    try:
        for idx, file_info in enumerate(target_files, start=1):
            filename = file_info["name"]
            local_path = safe_child_path(target_dir, filename)
            safe_filename = quote(filename, safe="/")
            download_url = f"https://archive.org/download/{ident}/{safe_filename}"

            success = False
            for attempt in range(3):
                try:
                    # Download Step
                    dl_tracker = ProgressTracker(loop, m, "⬇️ Downloading", filename, idx, total_files)
                    await asyncio.to_thread(
                        stream_download_with_progress,
                        download_url,
                        local_path,
                        dl_tracker.update
                    )

                    # Upload Step to Album Folder
                    up_tracker = ProgressTracker(loop, m, f"⬆️ Uploading to [{safe_folder_name}]", filename, idx, total_files)
                    res = await asyncio.to_thread(
                        megaup_upload,
                        local_path,
                        target_folder_id,
                        up_tracker.update
                    )

                    dl_url = res.get("url") or res.get("short_url") or "Uploaded"
                    uploaded_links.append(f"✅ `{filename}`\n🔗 {dl_url}")
                    downloaded_count += 1
                    success = True
                    break
                except Exception as exc:
                    logger.error("Attempt %d failed for %s: %s", attempt + 1, filename, exc)
                    if attempt < 2:
                        await asyncio.sleep(5)
                finally:
                    local_path.unlink(missing_ok=True)

            if not success:
                uploaded_links.append(f"❌ `{filename}`: Upload failed")

        result_header = (
            f"🎉 **Album Uploaded Successfully!**\n"
            f"📁 **Album:** `{safe_folder_name}`\n"
            f"🆔 **Folder ID:** `{target_folder_id}`\n"
            f"📊 **Files:** {downloaded_count}/{total_files}\n\n"
        )
        result_text = result_header + "\n\n".join(uploaded_links)

        if len(result_text) > 4000:
            chunks = [result_text[i:i + 4000] for i in range(0, len(result_text), 4000)]
            await m.edit(chunks[0])
            for ch in chunks[1:]:
                await m.reply_text(ch)
        else:
            await m.edit(result_text)

    except Exception as exc:
        logger.exception(exc)
        await m.edit(f"❌ Pipeline error: {exc}")
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)
        JOBS.pop(jobid, None)


if __name__ == "__main__":
    logger.info("Megaup Archive Telegram Bot starting...")
    app.run()
