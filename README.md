# YT Sync

Automatic YouTube video downloader with NAS upload support and CLI dashboard.

## Features

- Auto-downloads videos from YouTube subscriptions (via YouTube Data API)
- Parallel downloads (3 concurrent)
- Parallel NAS uploads via SMB (5 concurrent)
- Separates Shorts (≤60s) into dedicated folder
- Manual subtitles download (Spanish/English) embedded in MP4
- Real-time CLI dashboard (htop-style)
- Skips live streams automatically
- YouTube API quota management with automatic backoff

## Requirements

- Linux/macOS
- Python 3.9+
- ffmpeg

## Quick Install

```bash
# Install system dependencies
sudo apt install python3 python3-pip python3-venv ffmpeg

# Clone and install
git clone <repo-url> yt-sync
cd yt-sync
chmod +x install.sh
./install.sh
```

The installer will:
1. Create Python virtual environment
2. Install dependencies
3. Create `.env` from template
4. Install `yt-sync` command globally
5. Optionally set up systemd service for auto-start

## Manual Installation

### 1. Install system dependencies

```bash
sudo apt install python3 python3-pip python3-venv ffmpeg
```

### 2. Clone and configure

```bash
git clone <repo-url> yt-sync
cd yt-sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # Edit with your configuration
```

### 3. YouTube API Setup (optional, for subscription downloads)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop app)
4. Download as `google-client.json` in project root
5. Run OAuth setup:
   ```bash
   source venv/bin/activate
   python3 oauth_setup.py
   ```
6. Authorize in browser → `youtube_token.json` will be created

### 4. Install CLI globally

```bash
chmod +x yt-sync yt-sync-service
sudo ln -sf $(pwd)/yt-sync /usr/local/bin/yt-sync
```

### 5. Start the service

```bash
./yt-sync-service
```

Or install as systemd service (see below).

## Usage

### CLI Dashboard

```bash
# Watch mode (default - real-time updates like htop)
yt-sync

# Single snapshot (no watch)
yt-sync --help
```

### Service Management (systemd)

```bash
# Start/stop
sudo systemctl start yt-sync
sudo systemctl stop yt-sync
sudo systemctl restart yt-sync
sudo systemctl status yt-sync

# Enable auto-start on boot
sudo systemctl enable yt-sync

# View logs
journalctl -u yt-sync -f
```

### Manual service (without systemd)

```bash
# Start in foreground
./yt-sync-service

# Start in background
nohup ./yt-sync-service > /dev/null 2>&1 &
```

## Configuration

Edit `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `VIDEO_QUALITY` | Video quality (best, 1080p, 720p, 480p) | best |
| `MAX_CONCURRENT_DOWNLOADS` | Parallel downloads | 3 |
| `NAS_ENABLED` | Enable NAS upload | false |
| `NAS_HOST` | NAS IP address | - |
| `NAS_SHARE` | SMB share name | - |
| `NAS_USER` | SMB username | - |
| `NAS_PASSWORD` | SMB password | - |
| `NAS_PATH` | Path for videos | /youtube |
| `NAS_SHORTS_PATH` | Path for shorts | /shorts |
| `NAS_DELETE_AFTER_UPLOAD` | Delete local after upload | false |
| `SHORTS_MAX_DURATION` | Max duration for shorts (seconds) | 60 |

## How It Works

1. **Auto-download (every hour)**: Scans your subscriptions and downloads new videos from the last 5 days
2. **Download**: yt-dlp downloads in best quality with embedded metadata and subtitles
3. **NAS Upload**: Videos are automatically uploaded via SMB (5 concurrent)
4. **Shorts Separation**: Videos ≤60s go to `/shorts`, the rest to `/youtube`
5. **Cleanup**: Local files are deleted after successful NAS upload

## API Endpoints

The service exposes a REST API on `http://127.0.0.1:8000`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/videos` | GET | List all videos |
| `/api/download/{youtube_id}` | POST | Queue a download |
| `/api/videos/{id}/status` | GET | Get video status |
| `/api/videos/{id}` | DELETE | Delete video |
| `/api/stats` | GET | Get statistics |
| `/api/uploads` | GET | List uploads |
| `/api/uploads/progress` | GET | Upload progress |
| `/api/downloads/progress` | GET | Download progress |
| `/api/auto-download/run` | POST | Trigger auto-download |
| `/api/auto-download/status` | GET | Auto-download status |

## File Structure

```
yt-sync/
├── app/                    # Application code
│   ├── main.py            # FastAPI app
│   ├── downloader.py      # yt-dlp wrapper
│   ├── nas_upload.py      # SMB upload
│   ├── youtube_api.py     # YouTube API client
│   ├── auto_download.py   # Subscription downloads
│   ├── ytcli.py           # CLI dashboard
│   ├── config.py          # Settings
│   ├── database.py        # SQLite setup
│   └── models.py          # SQLAlchemy models
├── venv/                   # Python virtual environment
├── data/                   # SQLite database
├── downloads/              # Downloaded videos
├── .env                    # Configuration (gitignored)
├── .env.example           # Configuration template
├── requirements.txt
├── install.sh             # Installation script
├── oauth_setup.py         # YouTube OAuth setup
├── yt-sync                # CLI command
├── yt-sync-service        # Service entry point
├── yt-sync.service        # Systemd unit file
└── README.md
```

## Troubleshooting

**CLI says "Connecting to API..."**
- Make sure the service is running: `systemctl status yt-sync`
- Or start manually: `./yt-sync-service`

**Uploads stuck at "X videos waiting"**
- Check NAS connectivity: `ping <NAS_HOST>`
- Check SMB credentials in `.env`
- View logs: `journalctl -u yt-sync -f`

**YouTube API quota exceeded**
- Quota resets at midnight Pacific Time
- CLI shows reset time when quota is exceeded
- App will automatically resume when quota resets

**Permission denied errors**
- Make sure scripts are executable: `chmod +x yt-sync yt-sync-service`
- Check file ownership in data/ and downloads/

## License

MIT
