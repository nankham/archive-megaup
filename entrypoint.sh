#!/bin/sh
set -e

# Ensure downloads directory exists
mkdir -p "${TEMP_DOWNLOAD_DIR:-/downloads}"
chmod 750 "${TEMP_DOWNLOAD_DIR:-/downloads}"

# Validate required Megaup credentials
if [ -z "$MEGAUP_API_KEY" ] || [ -z "$MEGAUP_FOLDER_ID" ]; then
  echo "[ERROR] MEGAUP_API_KEY and MEGAUP_FOLDER_ID are required environment variables!"
  echo "[ERROR] Please configure them before starting the container."
  exit 1
fi

# Warn if Telegram whitelist is not configured
if [ -z "$ALLOWED_USER_IDS" ]; then
  echo "[WARN] ALLOWED_USER_IDS is not set. Bot is open to public access!"
fi

exec python bot.py
