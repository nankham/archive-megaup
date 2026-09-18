#!/bin/sh
set -e

mkdir -p "${TEMP_DOWNLOAD_DIR:-/downloads}"
chmod 750 "${TEMP_DOWNLOAD_DIR:-/downloads}"

# Validate required Megaup Web Session & Folder ID
if [ -z "$MEGAUP_COOKIE_FILEHOSTING" ] || [ -z "$MEGAUP_FOLDER_ID" ]; then
  echo "[ERROR] MEGAUP_COOKIE_FILEHOSTING and MEGAUP_FOLDER_ID must be set in environment!"
  exit 1
fi

if [ -z "$ALLOWED_USER_IDS" ]; then
  echo "[WARN] ALLOWED_USER_IDS is not set. Bot is open to public access!"
fi

exec python bot.py
