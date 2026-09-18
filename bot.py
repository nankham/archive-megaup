import os
import logging
import asyncio
import shutil
import pathlib
import requests
from urllib.parse import quote
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from archive_scraper import parse_archive_url, fetch_metadata, list_files_from_metadata
from uploader import megaup_upload

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = int(os.environ["API_ID"])[cite: 1]
API_HASH = os.environ["API_HASH"][cite: 1]
BOT_TOKEN = os.environ["BOT_TOKEN"][cite: 1]
TEMP_DIR = pathlib.Path(os.environ.get("TEMP_DOWNLOAD_DIR", "/downloads")).resolve()[cite: 1]
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(5 * 1024 ** 3)))  # Default limit: 5 GB

# Authorization whitelist
_raw_ids = os.environ.get("ALLOWED_USER_IDS", "")[cite: 1]
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip().isdigit()
}[cite: 1]

TEMP_DIR.mkdir(parents=True, exist_ok=True)

app = Client(
    "archive_megaup_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# Active jobs tracker
JOBS: dict[str, dict] = {}


def authorized(func):
    """Decorator to enforce whitelist authorization check."""
    async def wrapper(client, update):
        user = update.from_user if hasattr(update, "from_user") and update.from_user else update.message.from_user
        user_id = user.id if user else None
        
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            logger.warning("Unauthorized access attempt from user_id=%s", user_id)
            if hasattr(update, "answer"):
                await update.answer("❌ You are not authorized to use this bot.", show_alert=True)
            else:
                await update.reply_text("❌ You are not authorized to use this bot.")
            return
        return await func(client, update)
    wrapper.__name__ = func.__name__
    return wrapper


def safe_child_path(base: pathlib.Path, untrusted_name: str) -> pathlib.Path:
    """Validate path traversal attempt and ensure target remains inside base directory."""
    candidate = (base / untrusted_name).resolve()
    if not str(candidate).startswith(str(base)):
        raise ValueError(f"Path traversal detected: {untrusted_name!r}")
    return candidate


def stream_download(url: str, dest: pathlib.Path, max_bytes: int = MAX_FILE_BYTES) -> None:
    """Stream download file in chunks from Archive.org with strict size enforcement."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise RuntimeError(
                f"File exceeds limit: ({int(content_length) / 1024**3:.2f} GB > {max_bytes / 1024**3:.2f} GB)"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    written += len(chunk)
                    if written > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(f"File exceeded maximum allowable size limit of {max_bytes / 1024**3:.2f} GB")
                    fh.write(chunk)


@app.on_message(filters.command("start"))
@authorized
async def start_cmd(client, message):
    await message.reply_text(
        "👋 **Archive.org to Megaup Uploader Bot**\n\n"
        "Send `/download <archive.org link>` to start.\n"
        "Example:\n`/download https://archive.org/details/<identifier>`"
    )


@app.on_message(filters.command("download"))
@authorized
async def download_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: `/download https://archive.org/details/<identifier>`")
        return

    url = message.command[1]
    ident = parse_archive_url(url)
    if not ident:
        await message.reply_text("❌ Invalid archive.org URL or identifier not found.")
        return

    msg = await message.reply_text(f"🔍 Fetching metadata for `{ident}`...")
    try:
        meta = fetch_metadata(ident)
        files = list_files_from_metadata(meta)
        if not files:
            await msg.edit("❌ No downloadable files found in this archive item.")
            return

        jobid = f"{message.chat.id}:{message.id}"
        JOBS[jobid] = {"identifier": ident, "files": files, "meta": meta}

        # Aggregate files count by format
        format_counts: dict[str, int] = defaultdict(int)
        for f in files:
            fmt = f.get("format", "Unknown")
            format_counts[fmt] += 1

        buttons = [
            [InlineKeyboardButton(f"{fmt} ({count} files)", callback_data=f"pickformat|{jobid}|{fmt}")]
            for fmt, count in sorted(format_counts.items())
        ]
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{jobid}")])

        await msg.edit(
            f"📦 **Archive Item:** `{ident}`\n"
            f"📁 **Total Files Available:** {len(files)}\n\n"
            "Select the format to download and upload to Megaup:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as exc:
        logger.exception(exc)
        await msg.edit(f"❌ Error fetching metadata: {exc}")


@app.on_callback_query(filters.regex(r"^pickformat\|"))
@authorized
async def pickformat(client, cq):
    _, jobid, format_ = cq.data.split("|", 2)
    await cq.answer()

    job = JOBS.get(jobid)
    if not job:
        await cq.message.edit("❌ Job session expired. Please send the link again.", reply_markup=None)
        return

    ident = job["identifier"]
    target_dir = TEMP_DIR / ident
    target_dir.mkdir(parents=True, exist_ok=True)
    m = cq.message

    await m.edit(
        f"🚀 **Pipeline started**\n"
        f"📁 Item: `{ident}`\n"
        f"🎵 Format: `{format_}`\n\n"
        "Downloading from Archive.org and uploading to Megaup...",
        reply_markup=None,
    )

    uploaded_links = []
    target_files = [f for f in job["files"] if f.get("format") == format_]
    total_files = len(target_files)
    downloaded_count = 0

    try:
        for idx, file_info in enumerate(target_files, start=1):
            filename = file_info["name"]

            try:
                local_path = safe_child_path(target_dir, filename)
            except ValueError as exc:
                logger.error("Path traversal blocked: %s", exc)
                continue

            safe_filename = quote(filename, safe="/")
            download_url = f"https://archive.org/download/{ident}/{safe_filename}"

            success = False
            for attempt in range(3):
                try:
                    # 1. Download stream to local VM storage
                    await m.edit(f"⬇️ Downloading ({idx}/{total_files}):\n`{filename}`")
                    await asyncio.to_thread(stream_download, download_url, local_path)

                    # 2. Upload file via Megaup API v2
                    await m.edit(f"⬆️ Uploading to Megaup ({idx}/{total_files}):\n`{filename}`")
                    res = await asyncio.to_thread(megaup_upload, local_path)

                    # Extract file URL from API response
                    file_data = res.get("data", res)
                    dl_url = (
                        file_data.get("url")
                        or file_data.get("download_url")
                        or file_data.get("short_url")
                        or "Upload successful (No URL returned)"
                    )
                    uploaded_links.append(f"✅ `{filename}`\n🔗 {dl_url}")

                    downloaded_count += 1
                    success = True
                    break
                except Exception as exc:
                    logger.error("Attempt %d failed for %s: %s", attempt + 1, filename, exc)
                    if attempt < 2:
                        await asyncio.sleep(5)
                finally:
                    # 3. Cleanup local file immediately to conserve disk space
                    local_path.unlink(missing_ok=True)

            if not success:
                uploaded_links.append(f"❌ `{filename}`: Upload failed")

        # 4. Deliver final output links to user
        result_header = f"🎉 **Upload Completed ({downloaded_count}/{total_files})**\n\n"
        result_text = result_header + "\n\n".join(uploaded_links)

        # Telegram message length limit: 4096 characters
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
        # Purge temporary directory and session job
        shutil.rmtree(target_dir, ignore_errors=True)
        JOBS.pop(jobid, None)


@app.on_callback_query(filters.regex(r"^cancel\|"))
@authorized
async def cancel(client, cq):
    _, jobid = cq.data.split("|", 1)
    await cq.answer("Operation cancelled.")
    JOBS.pop(jobid, None)
    await cq.message.edit("❌ Operation cancelled by user.", reply_markup=None)


if __name__ == "__main__":
    if not ALLOWED_USER_IDS:
        logger.warning("ALLOWED_USER_IDS is empty. Bot is publicly accessible.")
    app.run()
