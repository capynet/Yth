import os
import json
from pathlib import Path
from typing import Optional

# App metadata
APP_NAME = "tubesync"
APP_LABEL = "Tube Sync"
APP_VERSION = "0.1.0"

# XDG Base Directories
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

# App directories
CONFIG_DIR = XDG_CONFIG_HOME / APP_NAME
DATA_DIR = XDG_DATA_HOME / APP_NAME
CACHE_DIR = XDG_CACHE_HOME / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

# Base directory for code (for finding assets, etc.)
BASE_DIR = Path(__file__).resolve().parent.parent

# Default configuration
DEFAULT_CONFIG = {
    "download_dir": str(DATA_DIR / "downloads"),
    "video_quality": "best",
    "max_concurrent_downloads": 3,
    # SMB Configuration
    "smb_enabled": False,
    "smb_host": "",
    "smb_share": "video",
    "smb_user": "",
    "smb_password": "",
    "smb_path": "/youtube",
    "smb_shorts_path": "/shorts",
    # FTP Configuration
    "ftp_enabled": False,
    "ftp_host": "",
    "ftp_port": 21,
    "ftp_user": "",
    "ftp_password": "",
    "ftp_path": "/youtube",
    "ftp_shorts_path": "/shorts",
    "ftp_use_tls": False,
    # General
    "delete_after_upload": True,
    "shorts_max_duration": 60,
    "youtube_client_file": str(CONFIG_DIR / "google-client.json"),
    "youtube_token_file": str(CONFIG_DIR / "youtube_token.json"),
}


def ensure_directories():
    """Create app directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "downloads").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "data").mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration from JSON file."""
    ensure_directories()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                # Migrate old nas_* keys to smb_*
                migrations = [
                    ("nas_enabled", "smb_enabled"),
                    ("nas_host", "smb_host"),
                    ("nas_share", "smb_share"),
                    ("nas_user", "smb_user"),
                    ("nas_password", "smb_password"),
                    ("nas_path", "smb_path"),
                    ("nas_shorts_path", "smb_shorts_path"),
                ]
                for old_key, new_key in migrations:
                    if old_key in config and new_key not in config:
                        config[new_key] = config[old_key]
                # Migrate old per-service delete settings to unified setting
                if "delete_after_upload" not in config:
                    # If either old setting was True, enable the unified setting
                    old_smb_delete = config.get("smb_delete_after_upload", config.get("nas_delete_after_upload", False))
                    old_ftp_delete = config.get("ftp_delete_after_upload", False)
                    config["delete_after_upload"] = old_smb_delete or old_ftp_delete
                # Merge with defaults for any missing keys
                return {**DEFAULT_CONFIG, **config}
        except (json.JSONDecodeError, IOError):
            pass

    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save configuration to JSON file."""
    ensure_directories()

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


class Settings:
    """Settings class that loads from XDG config."""

    def __init__(self):
        self._config = load_config()

    def reload(self):
        """Reload configuration from file."""
        self._config = load_config()

    @property
    def download_dir(self) -> str:
        return self._config.get("download_dir", DEFAULT_CONFIG["download_dir"])

    @property
    def database_url(self) -> str:
        db_path = DATA_DIR / "data" / "videos.db"
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def video_quality(self) -> str:
        return self._config.get("video_quality", "best")

    @property
    def max_concurrent_downloads(self) -> int:
        return self._config.get("max_concurrent_downloads", 3)

    @property
    def smb_enabled(self) -> bool:
        return self._config.get("smb_enabled", False)

    @property
    def smb_host(self) -> str:
        return self._config.get("smb_host", "")

    @property
    def smb_share(self) -> str:
        return self._config.get("smb_share", "video")

    @property
    def smb_user(self) -> str:
        return self._config.get("smb_user", "")

    @property
    def smb_password(self) -> str:
        return self._config.get("smb_password", "")

    @property
    def smb_path(self) -> str:
        return self._config.get("smb_path", "/youtube")

    @property
    def smb_shorts_path(self) -> str:
        return self._config.get("smb_shorts_path", "/shorts")

    @property
    def delete_after_upload(self) -> bool:
        return self._config.get("delete_after_upload", True)

    @property
    def shorts_max_duration(self) -> int:
        return self._config.get("shorts_max_duration", 60)

    # FTP properties
    @property
    def ftp_enabled(self) -> bool:
        return self._config.get("ftp_enabled", False)

    @property
    def ftp_host(self) -> str:
        return self._config.get("ftp_host", "")

    @property
    def ftp_port(self) -> int:
        return self._config.get("ftp_port", 21)

    @property
    def ftp_user(self) -> str:
        return self._config.get("ftp_user", "")

    @property
    def ftp_password(self) -> str:
        return self._config.get("ftp_password", "")

    @property
    def ftp_path(self) -> str:
        return self._config.get("ftp_path", "/youtube")

    @property
    def ftp_shorts_path(self) -> str:
        return self._config.get("ftp_shorts_path", "/shorts")

    @property
    def ftp_use_tls(self) -> bool:
        return self._config.get("ftp_use_tls", False)

    @property
    def youtube_client_file(self) -> str:
        return self._config.get("youtube_client_file", str(CONFIG_DIR / "google-client.json"))

    @property
    def youtube_token_file(self) -> str:
        return self._config.get("youtube_token_file", str(CONFIG_DIR / "youtube_token.json"))


# Global settings instance
settings = Settings()
