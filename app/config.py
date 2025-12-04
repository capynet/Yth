import os
from pathlib import Path
from pydantic_settings import BaseSettings

# Base directory is where the app is installed (parent of app/)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Paths (relative to BASE_DIR if not absolute)
    download_dir: str = str(BASE_DIR / "downloads")
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'videos.db'}"
    video_quality: str = "best"
    max_concurrent_downloads: int = 3

    # NAS Configuration
    nas_enabled: bool = False
    nas_host: str = ""
    nas_share: str = "video"
    nas_user: str = ""
    nas_password: str = ""
    nas_path: str = "/"
    nas_shorts_path: str = "/shorts"
    nas_delete_after_upload: bool = True
    shorts_max_duration: int = 60

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"


settings = Settings()
