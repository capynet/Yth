import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from smbclient import register_session, open_file, mkdir, stat
from smbclient.shutil import copyfile
from smbclient._os import SMBDirEntry
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import Video

logger = logging.getLogger(__name__)

# Upload queue and workers
upload_queue: asyncio.Queue = asyncio.Queue()
upload_workers: list[asyncio.Task] = []
MAX_CONCURRENT_UPLOADS = 5

# Progress tracking (supports multiple concurrent uploads)
active_uploads: dict[int, dict] = {}  # worker_id -> {video_id, title, bytes_sent, bytes_total, speed}


def get_smb_path(filename: str, is_short: bool = False) -> str:
    """Build SMB path for a file."""
    if is_short:
        nas_path = settings.nas_shorts_path.strip("/")
    else:
        nas_path = settings.nas_path.strip("/")

    if nas_path:
        return f"\\\\{settings.nas_host}\\{settings.nas_share}\\{nas_path}\\{filename}"
    return f"\\\\{settings.nas_host}\\{settings.nas_share}\\{filename}"


def get_smb_dir(is_short: bool = False) -> str:
    """Get SMB directory path."""
    if is_short:
        nas_path = settings.nas_shorts_path.strip("/")
    else:
        nas_path = settings.nas_path.strip("/")

    if nas_path:
        return f"\\\\{settings.nas_host}\\{settings.nas_share}\\{nas_path}"
    return f"\\\\{settings.nas_host}\\{settings.nas_share}"


def is_short_video(duration: int) -> bool:
    """Check if video is a Short based on duration."""
    return duration > 0 and duration <= settings.shorts_max_duration


def init_smb_session():
    """Initialize SMB session with NAS credentials."""
    if not settings.nas_enabled:
        return False

    if not settings.nas_host or not settings.nas_user:
        logger.warning("NAS not configured properly (missing host or user)")
        return False

    try:
        register_session(
            settings.nas_host,
            username=settings.nas_user,
            password=settings.nas_password,
        )
        logger.info(f"SMB session registered for {settings.nas_host}")
        return True
    except Exception as e:
        logger.error(f"Failed to register SMB session: {e}")
        return False


def ensure_nas_directory(is_short: bool = False):
    """Ensure the target directory exists on NAS."""
    path = settings.nas_shorts_path if is_short else settings.nas_path
    if not path or path == "/":
        return True

    try:
        smb_dir = get_smb_dir(is_short)
        # Try to create directory (will fail silently if exists)
        try:
            mkdir(smb_dir)
            logger.info(f"Created NAS directory: {smb_dir}")
        except OSError:
            # Directory likely exists
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to ensure NAS directory: {e}")
        return False


def upload_file_to_nas(local_path: str, remote_filename: str, video_title: str = "", video_id: int = 0, is_short: bool = False, worker_id: int = 0) -> tuple[bool, str]:
    """
    Upload a file to NAS via SMB with progress tracking.
    Returns (success, remote_path or error_message)
    """
    import time

    try:
        # Ensure target directory exists
        ensure_nas_directory(is_short)
        remote_path = get_smb_path(remote_filename, is_short)
        local_size = os.path.getsize(local_path)
        logger.info(f"[Worker {worker_id}] Uploading {local_path} to {remote_path} ({local_size / 1024 / 1024:.1f} MB)")

        # Initialize progress tracking
        active_uploads[worker_id] = {
            "worker_id": worker_id,
            "video_id": video_id,
            "title": video_title[:50],
            "filename": remote_filename,
            "bytes_sent": 0,
            "bytes_total": local_size,
            "speed": 0,
            "started_at": time.time(),
        }

        # Upload with progress tracking using chunks
        chunk_size = 1024 * 1024  # 1MB chunks
        bytes_sent = 0
        last_update = time.time()
        last_bytes = 0

        with open(local_path, 'rb') as local_file:
            with open_file(remote_path, mode='wb') as remote_file:
                while True:
                    chunk = local_file.read(chunk_size)
                    if not chunk:
                        break
                    remote_file.write(chunk)
                    bytes_sent += len(chunk)

                    # Update progress
                    now = time.time()
                    if now - last_update >= 0.5:  # Update every 500ms
                        elapsed = now - last_update
                        speed = (bytes_sent - last_bytes) / elapsed if elapsed > 0 else 0
                        active_uploads[worker_id]["bytes_sent"] = bytes_sent
                        active_uploads[worker_id]["speed"] = speed
                        last_update = now
                        last_bytes = bytes_sent

        # Final progress update
        active_uploads[worker_id]["bytes_sent"] = bytes_sent
        active_uploads[worker_id]["speed"] = 0

        # Verify upload by checking file size
        remote_stat = stat(remote_path)
        remote_size = remote_stat.st_size

        if local_size != remote_size:
            error = f"Size mismatch: local={local_size}, remote={remote_size}"
            logger.error(error)
            if worker_id in active_uploads:
                del active_uploads[worker_id]
            return False, error

        elapsed_total = time.time() - active_uploads[worker_id]["started_at"]
        avg_speed = local_size / elapsed_total if elapsed_total > 0 else 0
        logger.info(f"[Worker {worker_id}] Upload successful: {remote_filename} ({local_size / 1024 / 1024:.1f} MB, {avg_speed / 1024 / 1024:.1f} MB/s)")

        if worker_id in active_uploads:
            del active_uploads[worker_id]
        return True, remote_path

    except Exception as e:
        error = str(e)
        logger.error(f"[Worker {worker_id}] Upload failed for {local_path}: {error}")
        if worker_id in active_uploads:
            del active_uploads[worker_id]
        return False, error


def get_upload_progress() -> list[dict]:
    """Get progress of all active uploads."""
    return list(active_uploads.values())


async def process_upload(video_id: int, worker_id: int = 0):
    """Process a single video upload to NAS."""
    logger.info(f"[Worker {worker_id}] Processing upload for video_id={video_id}")

    async with async_session() as session:
        result = await session.execute(
            select(Video).where(Video.id == video_id)
        )
        video = result.scalar_one_or_none()

        if not video:
            logger.error(f"Video not found: {video_id}")
            return

        if not video.file_path or not os.path.exists(video.file_path):
            logger.error(f"Video file not found: {video.file_path}")
            video.upload_status = "error"
            video.upload_error = "Local file not found"
            await session.commit()
            return

        # Update status
        video.upload_status = "uploading"
        video.upload_attempts += 1
        await session.commit()

        # Generate remote filename
        safe_title = "".join(c for c in video.title if c.isalnum() or c in " -_").strip()[:80]
        ext = Path(video.file_path).suffix
        remote_filename = f"{video.youtube_id}_{safe_title}{ext}"

        # Check if it's a Short (≤60 seconds)
        is_short = is_short_video(video.duration)
        if is_short:
            logger.info(f"Video {video.youtube_id} is a Short ({video.duration}s) - uploading to shorts directory")

        # Run upload in executor (blocking operation)
        loop = asyncio.get_event_loop()
        success, result_msg = await loop.run_in_executor(
            None, upload_file_to_nas, video.file_path, remote_filename, video.title, video.id, is_short, worker_id
        )

        if success:
            video.upload_status = "uploaded"
            video.nas_path = result_msg
            video.uploaded_at = datetime.utcnow()
            video.upload_error = None
            logger.info(f"Upload completed for {video.youtube_id}")

            # Delete local file if configured
            if settings.nas_delete_after_upload:
                try:
                    os.remove(video.file_path)
                    logger.info(f"Deleted local file: {video.file_path}")
                    video.file_path = None
                except Exception as e:
                    logger.error(f"Failed to delete local file: {e}")
        else:
            video.upload_status = "error"
            video.upload_error = result_msg
            logger.error(f"Upload failed for {video.youtube_id}: {result_msg}")

        await session.commit()


async def upload_worker(worker_id: int):
    """Background worker that processes uploads from the queue."""
    logger.info(f"Upload worker {worker_id} started")

    # Initialize SMB session (each worker needs its own session for thread safety)
    if not init_smb_session():
        logger.error(f"Worker {worker_id}: Failed to initialize SMB session, worker disabled")
        return

    # Ensure both directories exist (videos and shorts)
    ensure_nas_directory(is_short=False)
    ensure_nas_directory(is_short=True)

    while True:
        video_id = await upload_queue.get()
        logger.info(f"Upload worker {worker_id} processing video_id={video_id}")
        try:
            await process_upload(video_id, worker_id)
        except Exception as e:
            logger.error(f"Upload worker {worker_id} error for {video_id}: {e}", exc_info=True)
            # Clean up progress on error
            if worker_id in active_uploads:
                del active_uploads[worker_id]
        finally:
            upload_queue.task_done()


async def start_upload_worker():
    """Start multiple background upload workers if NAS is enabled."""
    global upload_workers

    if not settings.nas_enabled:
        logger.info("NAS upload disabled")
        return

    logger.info(f"Starting {MAX_CONCURRENT_UPLOADS} NAS upload workers...")

    for i in range(MAX_CONCURRENT_UPLOADS):
        worker = asyncio.create_task(upload_worker(i + 1))
        upload_workers.append(worker)

    logger.info(f"{MAX_CONCURRENT_UPLOADS} upload workers started (concurrent uploads enabled)")


async def queue_upload(video_id: int):
    """Add a video to the upload queue."""
    if not settings.nas_enabled:
        return

    logger.info(f"Queuing upload: video_id={video_id}")
    await upload_queue.put(video_id)


async def check_pending_uploads():
    """Check for completed downloads that need uploading."""
    if not settings.nas_enabled:
        return

    async with async_session() as session:
        # Find videos that are downloaded but not uploaded
        result = await session.execute(
            select(Video).where(
                and_(
                    Video.status == "completed",
                    Video.upload_status.in_(["pending", "error"]),
                    Video.file_path.isnot(None),
                    Video.upload_attempts < 3,  # Max retry attempts
                )
            )
        )
        videos = result.scalars().all()

        for video in videos:
            logger.info(f"Found pending upload: {video.youtube_id}")
            await queue_upload(video.id)

        if videos:
            logger.info(f"Queued {len(videos)} pending uploads")
