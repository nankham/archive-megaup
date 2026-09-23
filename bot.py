import os
import re
import time
import uuid
import shutil
import pathlib
import logging
import asyncio
import zipfile
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

# 4GB Split threshold (4 * 1024 * 1024 * 1024 bytes)
MAX_SPLIT_BYTES = 4 * 1024 * 1024 * 1024

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


def split_large_file(file_path: pathlib.Path, chunk_size: int = MAX_SPLIT_BYTES) -> list[pathlib.Path]:
    """Split a single archive file into multiple numbered parts if it exceeds chunk_size."""
    file_size = file_path.stat().st_size
    if file_size <= chunk_size:
        return [file_path]

    part_paths = []
    part_num = 1
    buffer_size = 32 * 1024 * 1024  # 32MB read buffer

    with open(file_path, "rb") as src:
        while True:
            part_name = f"{file_path.name}.{part_num:03d}"
            part_path = file_path.parent / part_name
            written_bytes = 0

            with open(part_path, "wb") as dest:
                while written_bytes < chunk_size:
                    to_read = min(buffer_size, chunk_size - written_bytes)
                    chunk = src.read(to_read)
                    if not chunk:
                        break
                    dest.write(chunk)
                    written_bytes += len(chunk)

            if written_bytes > 0:
                part_paths.append(part_path)
                part_num += 1
            else:
                part_path.unlink(missing_ok=True)
                break

    # Remove the original unsplit archive to reclaim disk space
    file_path.unlink(missing_ok=True)
    return part_paths


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
            f"⚡ **Batch Progress ({self.index}/{self.total})**\n"
            f"📁 `{self.filename}`\n\n"
            f"{self.phase}...\n"
            f"`[{bar}] {percent:.1f}%`\n"
            f"⚡ **Speed:** {speed / (1024*1024):.2f} MB/s\n"
            f"⏳ **ETA:** {int(eta)}s | 📊 **Done:** {current / (1024*1024):.2f}/{total / (1024*1024):.2f} MB"
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
        "🚀 **Archive.org to Megaup Pipeline Bot**\n\n"
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
    format_sizes = {}
    for f in files:
        fmt = f.get("format")
        if fmt and fmt not in ("Metadata", "Item Tile", "Archive BitTorrent"):
            formats[fmt] = formats.get(fmt, 0) + 1
            try:
                raw_sz = int(f.get("size", 0))
            except (ValueError, TypeError):
                raw_sz = 0
            format_sizes[fmt] = format_sizes.get(fmt, 0) + raw_sz

    if not formats:
        await status_msg.edit("❌ No suitable audio/media formats found.")
        return

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "identifier": identifier,
        "files": files,
        "meta": meta_data,
        "format_sizes": format_sizes,
    }

    buttons = []
    for fmt, count in formats.items():
        size_mb = format_sizes.get(fmt, 0) / (1024 * 1024)
        size_label = f"{size_mb / 1024:.2f} GB" if size_mb >= 1024 else f"{size_mb:.1f} MB"
        buttons.append([InlineKeyboardButton(f"{fmt} ({count} files ~ {size_label})", callback_data=f"pickformat|{job_id}|{fmt}")])

    title = meta_data.get("metadata", {}).get("title", identifier)

    await status_msg.edit(
        f"🎵 **Album:** `{title}`\n"
        f"Select the format to download and upload:",
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

    total_bytes = job.get("format_sizes", {}).get(format_, 0)
    total_gb = total_bytes / (1024 * 1024 * 1024)

    # Indicate auto-split status if total format size exceeds 4GB
    split_info = " (Auto-split into 4GB parts)" if total_bytes > MAX_SPLIT_BYTES else ""

    mode_buttons = [
        [
            InlineKeyboardButton("📁 Upload Individual Files", callback_data=f"process|{jobid}|{format_}|individual"),
            InlineKeyboardButton(f"🗜️ Archive as .ZIP{split_info}", callback_data=f"process|{jobid}|{format_}|zip")
        ]
    ]

    await cq.message.edit(
        f"Selected Format: `{format_}` (~{total_gb:.2f} GB)\n\n"
        f"Choose upload strategy for Megaup:",
        reply_markup=InlineKeyboardMarkup(mode_buttons)
    )


@app.on_callback_query(filters.regex(r"^process\|"))
@authorized
async def process_download(client, cq: CallbackQuery):
    _, jobid, format_, mode = cq.data.split("|", 3)
    await cq.answer()

    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("❌ Job session expired. Please re-send the link.", reply_markup=None)
        return

    ident = job["identifier"]
    metadata_info = job.get("meta", {}).get("metadata", {})
    album_title = metadata_info.get("title") or ident
    m = cq.message

    await m.edit(f"📁 Preparing Megaup storage folder:\n`{album_title}`...")

    target_folder_id = await asyncio.to_thread(create_or_get_folder, album_title)
    target_dir = TEMP_DIR / ident
    target_dir.mkdir(parents=True, exist_ok=True)

    target_files = [f for f in job["files"] if f.get("format") == format_]

    image_files = [
        f for f in job["files"]
        if any(f.get("name", "").lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"])
        or "Item Image" in f.get("format", "")
        or "Thumbnail" in f.get("format", "")
    ]
    if image_files and image_files[0] not in target_files:
        target_files.insert(0, image_files[0])

    total_files = len(target_files)
    loop = asyncio.get_running_loop()

    try:
        if mode == "zip":
            # Download all target files to local storage
            downloaded_paths = []
            for idx, file_info in enumerate(target_files, start=1):
                filename = file_info["name"]
                local_path = safe_child_path(target_dir, filename)
                safe_filename = quote(filename, safe="/")
                download_url = f"https://archive.org/download/{ident}/{safe_filename}"

                dl_tracker = ProgressTracker(loop, m, "⬇️ Downloading (ZIP Prep)", filename, idx, total_files)
                await asyncio.to_thread(
                    stream_download_with_progress,
                    download_url,
                    local_path,
                    dl_tracker.update
                )
                downloaded_paths.append(local_path)

            clean_zip_name = re.sub(r'[\/:*?"<>|]', '_', album_title).strip()[:70]
            zip_filename = f"{clean_zip_name}.zip"
            zip_path = safe_child_path(TEMP_DIR, zip_filename)

            await m.edit(f"📦 Compressing into `{zip_filename}`...")

            def create_zip():
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for p in downloaded_paths:
                        zipf.write(p, arcname=p.name)

            await asyncio.to_thread(create_zip)

            # Check if compression result exceeds 4GB and auto-split if necessary
            actual_zip_size = zip_path.stat().st_size
            if actual_zip_size > MAX_SPLIT_BYTES:
                await m.edit(f"✂️ File is {actual_zip_size / (1024**3):.2f} GB (> 4GB). Splitting into 4GB parts...")
                upload_parts = await asyncio.to_thread(split_large_file, zip_path, MAX_SPLIT_BYTES)
            else:
                upload_parts = [zip_path]

            uploaded_links = []
            total_parts = len(upload_parts)

            for idx, part_file in enumerate(upload_parts, start=1):
                part_name = part_file.name
                up_tracker = ProgressTracker(loop, m, f"⬆️ Uploading [{idx}/{total_parts}]", part_name, idx, total_parts)
                res = await asyncio.to_thread(
                    megaup_upload,
                    part_file,
                    target_folder_id,
                    up_tracker.update
                )
                part_file.unlink(missing_ok=True)
                dl_url = res.get("url") or res.get("short_url") or "Uploaded"
                uploaded_links.append(f"📦 `{part_name}`\n🔗 {dl_url}")

            result_header = (
                f"🎉 **Archive Upload Complete!**\n"
                f"📁 **Folder:** `{album_title}`\n"
                f"📂 **Folder ID:** `{target_folder_id}`\n"
                f"📊 **Total Parts:** {total_parts}\n\n"
            )
            result_text = result_header + "\n\n".join(uploaded_links)
            await m.edit(result_text)

        else:
            # Individual File Upload Logic
            downloaded_count = 0
            uploaded_links = []

            for idx, file_info in enumerate(target_files, start=1):
                filename = file_info["name"]
                local_path = safe_child_path(target_dir, filename)
                safe_filename = quote(filename, safe="/")
                download_url = f"https://archive.org/download/{ident}/{safe_filename}"

                success = False
                for attempt in range(3):
                    try:
                        dl_tracker = ProgressTracker(loop, m, "⬇️ Downloading", filename, idx, total_files)
                        await asyncio.to_thread(
                            stream_download_with_progress,
                            download_url,
                            local_path,
                            dl_tracker.update
                        )

                        up_tracker = ProgressTracker(loop, m, f"⬆️ Uploading to [{album_title}]", filename, idx, total_files)
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
                f"🎉 **Album Upload Complete!**\n"
                f"📁 **Album Folder:** `{album_title}`\n"
                f"📂 **Folder ID:** `{target_folder_id}`\n"
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
