from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    download_dir: str = "/app/downloads"
    database_url: str = "sqlite+aiosqlite:///./data/videos.db"
    video_quality: str = "best"
    max_concurrent_downloads: int = 3  # Number of simultaneous downloads

    # NAS Configuration
    nas_enabled: bool = False
    nas_host: str = ""
    nas_share: str = "video"
    nas_user: str = ""
    nas_password: str = ""
    nas_path: str = "/"  # Subdirectory for regular videos
    nas_shorts_path: str = "/shorts"  # Subdirectory for Shorts (≤60 sec)
    nas_delete_after_upload: bool = True
    shorts_max_duration: int = 60  # Max duration in seconds to consider as Short

    class Config:
        env_file = ".env"


settings = Settings()
