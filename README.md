# Tube Sync

[![Build Test](https://github.com/capynet/tubesync/actions/workflows/build-test.yml/badge.svg)](https://github.com/capynet/tubesync/actions/workflows/build-test.yml)
[![Release](https://github.com/capynet/tubesync/actions/workflows/release.yml/badge.svg)](https://github.com/capynet/tubesync/releases)

Automatic YouTube video downloader with NAS upload support, CLI dashboard and desktop GUI.

## Features

- Auto-downloads videos from YouTube subscriptions (via YouTube Data API)
- Parallel downloads (3 concurrent)
- Parallel NAS uploads via SMB or FTP (5 concurrent)
- Separates Shorts (<=60s) into dedicated folder
- Manual subtitles download (Spanish/English) embedded in MP4
- Real-time CLI dashboard (htop-style)
- Desktop GUI application (GTK4/Libadwaita)
- NAS configuration via GUI
- Skips live streams automatically
- YouTube API quota management with automatic backoff

## Quick Install

### APT Repository (Recommended for Debian/Ubuntu)

Add the Capynet APT repository and install with `apt`:

```bash
# Add GPG key
curl -fsSL https://capynet.github.io/tubesync/capynet-apt.gpg | sudo gpg --dearmor -o /usr/share/keyrings/capynet.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/capynet.gpg] https://capynet.github.io/tubesync stable main" | sudo tee /etc/apt/sources.list.d/capynet.list

# Install
sudo apt update
sudo apt install tubesync
```

Updates are automatically available via `apt upgrade`.

### Download .deb from GitHub Releases

Download the latest `.deb` from [Releases](https://github.com/capynet/tubesync/releases) and install:

```bash
wget https://github.com/capynet/tubesync/releases/latest/download/tubesync_0.1.0_amd64.deb
sudo dpkg -i tubesync_*.deb
sudo apt-get install -f  # Install dependencies if needed
```

### One-Line Install (from source)

The installer automatically detects your OS and installs all dependencies:

```bash
git clone https://github.com/capynet/tubesync.git && cd tubesync
./install.sh
```

Supported systems:
- **Debian/Ubuntu**: apt packages
- **Fedora**: dnf packages
- **Arch Linux**: pacman packages
- **macOS**: Homebrew packages

## Manual Installation

### 1. Install system dependencies

**Debian/Ubuntu:**
```bash
sudo apt install python3 python3-pip python3-venv python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 ffmpeg
```

**Fedora:**
```bash
sudo dnf install python3 python3-pip ffmpeg gtk4 libadwaita python3-gobject
```

**Arch Linux:**
```bash
sudo pacman -S python python-pip ffmpeg gtk4 libadwaita python-gobject
```

**macOS:**
```bash
brew install python3 ffmpeg gtk4 libadwaita pygobject3
```

### 2. Clone and configure

```bash
git clone https://github.com/capynet/tubesync.git
cd tubesync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. YouTube API Setup (optional, for subscription downloads)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop app)
4. Download as `google-client.json` in `~/.config/tubesync/`
5. Run OAuth setup:
   ```bash
   source venv/bin/activate
   python3 oauth_setup.py
   ```
6. Authorize in browser -> `youtube_token.json` will be created

### 4. Install CLI globally

```bash
chmod +x tubesync tubesync-service tubesync-gui
sudo ln -sf $(pwd)/tubesync /usr/local/bin/tubesync
sudo ln -sf $(pwd)/tubesync-gui /usr/local/bin/tubesync-gui
```

## Usage

### GUI Application

```bash
# Launch desktop GUI
tubesync-gui
```

Or find "Tube Sync" in your applications menu.

### CLI Dashboard

```bash
# Watch mode (default - real-time updates like htop)
tubesync

# Single snapshot (no watch)
tubesync --help
```

### Service Management (systemd)

```bash
# Start/stop
sudo systemctl start tubesync
sudo systemctl stop tubesync
sudo systemctl restart tubesync
sudo systemctl status tubesync

# Enable auto-start on boot
sudo systemctl enable tubesync

# View logs
journalctl -u tubesync -f
```

### Manual service (without systemd)

```bash
# Start in foreground
./tubesync-service

# Start in background
nohup ./tubesync-service > /dev/null 2>&1 &
```

## Configuration

Configuration is stored in `~/.config/tubesync/config.json`. You can configure settings through the GUI or edit the file directly.

| Setting | Description | Default |
|---------|-------------|---------|
| `video_quality` | Video quality (best, 1080p, 720p, 480p) | best |
| `max_concurrent_downloads` | Parallel downloads | 3 |
| `smb_enabled` | Enable SMB upload | false |
| `smb_host` | SMB server IP address | - |
| `smb_share` | SMB share name | - |
| `smb_user` | SMB username | - |
| `smb_password` | SMB password | - |
| `smb_path` | Path for videos | /youtube |
| `smb_shorts_path` | Path for shorts | /shorts |
| `ftp_enabled` | Enable FTP upload | false |
| `ftp_host` | FTP server address | - |
| `ftp_port` | FTP port | 21 |
| `ftp_user` | FTP username | - |
| `ftp_password` | FTP password | - |
| `ftp_path` | FTP path for videos | /youtube |
| `ftp_shorts_path` | FTP path for shorts | /shorts |
| `ftp_use_tls` | Use FTPS (TLS) | false |
| `delete_after_upload` | Delete local after upload | true |
| `shorts_max_duration` | Max duration for shorts (seconds) | 60 |

## Building Packages

### Build .deb (Linux)

```bash
./build-deb.sh [version]

# Example:
./build-deb.sh 1.0.0
# Output: dist/tubesync_1.0.0_amd64.deb
```

## How It Works

1. **Auto-download (every hour)**: Scans your subscriptions and downloads new videos from the last 5 days
2. **Download**: yt-dlp downloads in best quality with embedded metadata and subtitles
3. **NAS Upload**: Videos are automatically uploaded via SMB or FTP (5 concurrent)
4. **Shorts Separation**: Videos <=60s go to `/shorts`, the rest to `/youtube`
5. **Cleanup**: Local files are deleted after successful upload (configurable)

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
tubesync/
├── app/                    # Application code
│   ├── main.py            # FastAPI app
│   ├── downloader.py      # yt-dlp wrapper
│   ├── nas_upload.py      # SMB/FTP upload
│   ├── youtube_api.py     # YouTube API client
│   ├── auto_download.py   # Subscription downloads
│   ├── ytcli.py           # CLI dashboard
│   ├── gui_gtk.py         # Desktop GUI (GTK4)
│   ├── config.py          # Settings
│   ├── database.py        # SQLite setup
│   └── models.py          # SQLAlchemy models
├── venv/                   # Python virtual environment
├── .github/workflows/      # CI/CD workflows
├── scripts/               # Utility scripts
├── requirements.txt
├── install.sh             # Installation script
├── build-deb.sh           # Build .deb package
├── oauth_setup.py         # YouTube OAuth setup
├── tubesync               # CLI command
├── tubesync-gui           # Desktop GUI application
├── tubesync-service       # Service entry point
├── tubesync.service       # Systemd unit file
└── README.md
```

## Data Locations

Following XDG Base Directory Specification:

| Location | Purpose |
|----------|---------|
| `~/.config/tubesync/` | Configuration files |
| `~/.local/share/tubesync/` | Database and downloads |
| `~/.cache/tubesync/` | Temporary cache |

## Troubleshooting

**CLI says "Connecting to API..."**
- Make sure the service is running: `systemctl status tubesync`
- Or start manually: `./tubesync-service`

**Uploads stuck at "X videos waiting"**
- Check NAS connectivity: `ping <NAS_HOST>`
- Check SMB/FTP credentials in settings
- View logs: `journalctl -u tubesync -f`

**YouTube API quota exceeded**
- Quota resets at midnight Pacific Time
- CLI shows reset time when quota is exceeded
- App will automatically resume when quota resets

**Permission denied errors**
- Make sure scripts are executable: `chmod +x tubesync tubesync-service`
- Check file ownership in data directories

**GUI won't start**
- Install GTK4 dependencies: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`

## License

MIT
