from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    channel: Mapped[str] = mapped_column(String(200))
    duration: Mapped[int] = mapped_column(Integer, default=0)  # seconds
    thumbnail: Mapped[str] = mapped_column(String(500), default="")
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, downloading, completed, error
    error_message: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # NAS Upload tracking
    upload_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, uploading, uploaded, error
    upload_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    nas_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Path on NAS
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
