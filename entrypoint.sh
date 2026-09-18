#!/bin/sh
set -e

# Ensure downloads directory exists with secure permissions
mkdir -p "${TEMP_DOWNLOAD_DIR:-/downloads}"
chmod 750 "${TEMP_DOWNLOAD_DIR:-/downloads}"

# Warn if Telegram security whitelist is not configured
if [ -z "$ALLOWED_USER_IDS" ]; then
  echo "[WARN] ALLOWED_USER_IDS is not set. Bot is open to public access!"
fi

# Warn if Megaup credentials are not explicitly set
if [ -z "$MEGAUP_API_KEY" ]; then
  echo "[INFO] MEGAUP_API_KEY not passed in env. Falling back to default script key."
fi

# Execute main bot process
exec python bot.py
