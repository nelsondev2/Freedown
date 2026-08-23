# MoodleBot

A DeltaChat bot that downloads files from public URLs and re-hosts them on a Moodle instance, returning permanent, token-authenticated download links.

---

## Project Structure

```
moodlebot/
├── main.py                  # Entry point
├── requirements.txt
├── .env.example             # Copy to .env and fill in values
│
├── config/
│   └── settings.py          # All config loaded from environment
│
├── services/
│   ├── downloader.py        # Streams remote files to disk
│   ├── uploader.py          # Uploads files to Moodle draft area
│   └── converter.py         # Converts draftfile → pluginfile URLs
│
├── handlers/
│   └── message.py           # DeltaChat event handlers (thin layer)
│
└── utils/
    ├── files.py             # File sizing, naming, multi-volume splitting
    ├── logger.py            # Centralised logging setup
    └── exceptions.py        # Domain-specific exceptions
```

---

## Setup

### 1. Clone and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

| Variable | Description |
|---|---|
| `MOODLE_HOST` | Full URL of your Moodle instance (no trailing slash) |
| `MOODLE_TOKEN` | Webservice token with upload permissions |
| `MOODLE_USER` | Username of a Moodle account (for calendar URL conversion) |
| `MOODLE_PASS` | Password for `MOODLE_USER` |
| `MOODLE_VERIFY_SSL` | Verify Moodle's HTTPS certificate (default: `true`). Use `false` only temporarily for an expired certificate |
| `MAX_FILE_SIZE_MB` | Maximum file size accepted (default: 999) |
| `PART_SIZE_MB` | Volume size for multi-part splits (default: 99) |
| `UPLOAD_DELAY_SECONDS` | Delay between part uploads (default: 2) |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |

### 3. Run

```bash
python main.py init       # First-time setup: configure email account
python main.py run        # Start the bot
```

---

## How It Works

1. User sends a public download URL to the bot via DeltaChat.
2. The bot streams the file to a temporary directory.
3. **Small files (≤ 99 MB):** uploaded directly to Moodle. The bot replies with the permanent URL.
4. **Large files (> 99 MB):** compressed into a ZIP and split into numbered volumes (`file.zip.0001`, `.0002`, …). Each part is uploaded separately. The bot sends a `.txt` file with all download links.

### Reassembling split archives

1. Download **all** parts into the same folder.
2. Open the **first** part (`file.zip.0001`) with 7-Zip or WinRAR.
3. Extract normally — the tool will read the remaining volumes automatically.

---

## Architecture Notes

- **No hardcoded credentials** — all secrets live in `.env`.
- **Single-responsibility services** — download, upload, and URL-conversion are independent classes with clear interfaces.
- **Typed, documented** — every public function has a docstring and type hints.
- **Structured logging** — consistent timestamped output, log level configurable at runtime.
- **Domain exceptions** — `DownloadError`, `UploadError`, `UrlConversionError`, etc. propagate cleanly without swallowed `except: pass` blocks.
- **Thin handlers** — event handlers contain no business logic; they only delegate to services.
