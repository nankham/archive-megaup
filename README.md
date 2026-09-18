# Archive.org to Megaup Telegram Bot 🚀

An automated Telegram bot pipeline that downloads media/music files from Archive.org items and directly uploads them to **Megaup.net** cloud storage using the Yetishare v2 API.

## 🌟 Key Features
* **Telegram Bot Interface:** Control downloads directly via Telegram commands using the official Telegram Bot API (Bot Token).
* **Metadata Extraction:** Inspects Archive.org items, parses file formats, and lets you select specific media formats via inline keyboard buttons.
* **Direct Megaup API v2 Integration:** Uploads downloaded media files straight to designated Megaup folders via REST API endpoints.
* **Auto-Cleanup & Safe Storage:** Deletes downloaded temporary files chunk-by-chunk and file-by-file immediately upon upload completion to prevent disk exhaustion.
* **Security Whitelist:** Restricts access using an allowed user ID whitelist (`ALLOWED_USER_IDS`) to prevent unauthorized usage.
* **Containerized & Production Ready:** Optimized Docker container running as a non-privileged user.

## 🛠 Tech Stack
* **Language:** Python 3.11
* **Framework:** Pyrogram (Telegram Bot API)
* **Storage Provider:** Megaup.net (Yetishare v2 Engine)
* **Containerization:** Docker

---

## ⚙️ Environment Variables

Configure the following variables in your `.env` file or hosting environment:

| Variable | Description | Required | Default |
| :--- | :--- | :--- | :--- |
| `BOT_TOKEN` | Telegram Bot Token (from [@BotFather](https://t.me/BotFather)) | **Yes** | — |
| `API_ID` | Telegram API ID (from [my.telegram.org](https://my.telegram.org)) | **Yes** | — |
| `API_HASH` | Telegram API Hash (from [my.telegram.org](https://my.telegram.org)) | **Yes** | — |
| `ALLOWED_USER_IDS` | Comma-separated Telegram User IDs allowed to use the bot | **Yes** | — |
| `MEGAUP_API_KEY` | Megaup API Key 1 (Upload Key from your account) | **Yes** | — |
| `MEGAUP_FOLDER_ID` | Target Megaup Folder ID | **Yes** | — |
| `MEGAUP_UPLOAD_URL` | Megaup Upload Endpoint | No | `https://megaup.net/api/v2/file/upload` |
| `TEMP_DOWNLOAD_DIR` | Temporary download storage path | No | `/downloads` |
| `MAX_FILE_BYTES` | Maximum allowed file size in bytes | No | `5368709120` (5 GB) |

---

## 📖 Usage Guide

1. Open a chat with your deployed bot on Telegram.
2. Send `/start` to verify connectivity.
3. Send the download command along with an Archive.org details URL:
   ```text
   /download [https://archive.org/details/](https://archive.org/details/)<identifier>

## 🚀 Build and Run With Docker Command

* docker build -t archive-megaup-bot .
* docker run -d --name archive-megaup --env-file .env archive-megaup-bot
