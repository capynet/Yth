# YT Downloader

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

## Quick Install (Linux)

```bash
git clone <repo-url> yt-downloader
cd yt-downloader
chmod +x install.sh
./install.sh
```

The installer will:
1. Check dependencies (Docker, Docker Compose)
2. Create `.env` from template
3. Install `yt-sync` command globally (`/usr/local/bin/yt-sync`)
4. Optionally set up systemd service for auto-start

## Manual Installation

### 1. Clone and configure

```bash
git clone <repo-url> yt-downloader
cd yt-downloader
cp .env.example .env
nano .env  # Edit with your configuration
```

### 2. YouTube API Setup (optional, for subscription downloads)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop app)
4. Download as `google-client.json` in project root
5. Run OAuth setup:
   ```bash
   pip3 install google-api-python-client google-auth-oauthlib google-auth-httplib2
   python3 oauth_setup.py
   ```
6. Authorize in browser → `youtube_token.json` will be created

### 3. Start the service

```bash
docker compose up -d
```

### 4. Install CLI globally

```bash
chmod +x yt-sync
sudo ln -sf $(pwd)/yt-sync /usr/local/bin/yt-sync
```

## Usage

### CLI Dashboard

```bash
# Watch mode (default - real-time updates like htop)
yt-sync

# Single snapshot (no watch)
yt-sync --no-watch

# Custom refresh interval (seconds)
yt-sync -i 5
```

### Service Management

**With systemd (recommended for Linux servers):**
```bash
sudo systemctl start yt-downloader
sudo systemctl stop yt-downloader
sudo systemctl restart yt-downloader
sudo systemctl status yt-downloader

# Enable auto-start on boot
sudo systemctl enable yt-downloader

# View logs
journalctl -u yt-downloader -f
```

**With Docker Compose:**
```bash
docker compose up -d      # Start
docker compose down       # Stop
docker compose restart    # Restart
docker compose logs -f    # View logs
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
| `/api/youtube-api/status` | GET | YouTube API status |

## File Structure

```
yt-downloader/
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
├── data/                   # SQLite database (gitignored)
├── downloads/              # Downloaded videos (gitignored)
├── docker-compose.yml
├── Dockerfile
├── .env                    # Configuration (gitignored)
├── .env.example           # Configuration template
├── install.sh             # Installation script
├── oauth_setup.py         # YouTube OAuth setup
├── yt-sync                # CLI wrapper script (watch mode default)
└── README.md
```

## Troubleshooting

**CLI says "Connecting to API..."**
- Make sure the Docker container is running: `docker compose ps`

**Uploads stuck at "X videos waiting"**
- Check NAS connectivity: `ping <NAS_HOST>`
- Check SMB credentials in `.env`
- View logs: `docker compose logs | grep -i upload`

**YouTube API quota exceeded**
- Quota resets at midnight Pacific Time
- CLI shows reset time when quota is exceeded
- App will automatically resume when quota resets

## License

MIT
