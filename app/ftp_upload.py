import asyncio
import logging
import os
from datetime import datetime
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path

from sqlalchemy import select, and_

from app.config import settings
from app.database import async_session
from app.models import Video

logger = logging.getLogger(__name__)

# Upload queue and workers
ftp_queue: asyncio.Queue = asyncio.Queue()
ftp_workers: list[asyncio.Task] = []
MAX_CONCURRENT_FTP_UPLOADS = 3

# Progress tracking (supports multiple concurrent uploads)
active_ftp_uploads: dict[int, dict] = {}  # worker_id -> {video_id, title, bytes_sent, bytes_total, speed}


def is_short_video(duration: int) -> bool:
    """Check if video is a Short based on duration."""
    return duration > 0 and duration <= settings.shorts_max_duration


def get_ftp_connection() -> FTP:
    """Create and return an FTP connection."""
    if settings.ftp_use_tls:
        ftp = FTP_TLS()
    else:
        ftp = FTP()

    ftp.connect(settings.ftp_host, settings.ftp_port, timeout=30)
    ftp.login(settings.ftp_user, settings.ftp_password)

    if settings.ftp_use_tls:
        ftp.prot_p()  # Enable data channel encryption

    return ftp


def test_ftp_connection() -> tuple[bool, str]:
    """Test FTP connection and return (success, message)."""
    if not settings.ftp_enabled:
        return False, "DISABLED"

    if not settings.ftp_host or not settings.ftp_user:
        return False, "NOT CONFIGURED"

    try:
        ftp = get_ftp_connection()
        ftp.quit()
        return True, "OK"
    except Exception as e:
        error_msg = str(e)
        if "530" in error_msg:  # Login incorrect
            return False, "AUTH FAILED"
        elif "timed out" in error_msg.lower() or "connection refused" in error_msg.lower():
            return False, "UNREACHABLE"
        else:
            logger.error(f"FTP connection test failed: {e}")
            return False, "ERROR"


def ensure_ftp_directory(ftp: FTP, path: str):
    """Ensure the target directory exists on FTP, creating if needed."""
    if not path or path == "/":
        return

    # Remove leading slash and split
    parts = path.strip("/").split("/")
    current = ""

    for part in parts:
        current = f"{current}/{part}"
        try:
            ftp.cwd(current)
        except error_perm:
            try:
                ftp.mkd(current)
                logger.info(f"Created FTP directory: {current}")
            except error_perm:
                pass  # Directory might already exist


def upload_file_to_ftp(local_path: str, remote_filename: str, video_title: str = "", video_id: int = 0, is_short: bool = False, worker_id: int = 0) -> tuple[bool, str]:
    """
    Upload a file to FTP with progress tracking.
    Returns (success, remote_path or error_message)
    """
    import time

    try:
        ftp = get_ftp_connection()

        # Determine target path
        if is_short:
            target_path = settings.ftp_shorts_path.strip("/")
        else:
            target_path = settings.ftp_path.strip("/")

        # Ensure directory exists
        ensure_ftp_directory(ftp, target_path)
        ftp.cwd(f"/{target_path}" if target_path else "/")

        local_size = os.path.getsize(local_path)
        logger.info(f"[FTP Worker {worker_id}] Uploading {local_path} to /{target_path}/{remote_filename} ({local_size / 1024 / 1024:.1f} MB)")

        # Initialize progress tracking
        active_ftp_uploads[worker_id] = {
            "worker_id": worker_id,
            "video_id": video_id,
            "title": video_title[:50],
            "filename": remote_filename,
            "bytes_sent": 0,
            "bytes_total": local_size,
            "speed": 0,
            "started_at": time.time(),
        }

        # Upload with progress tracking
        bytes_sent = [0]  # Use list for closure
        last_update = [time.time()]
        last_bytes = [0]

        def callback(data):
            bytes_sent[0] += len(data)
            now = time.time()
            if now - last_update[0] >= 0.5:  # Update every 500ms
                elapsed = now - last_update[0]
                speed = (bytes_sent[0] - last_bytes[0]) / elapsed if elapsed > 0 else 0
                active_ftp_uploads[worker_id]["bytes_sent"] = bytes_sent[0]
                active_ftp_uploads[worker_id]["speed"] = speed
                last_update[0] = now
                last_bytes[0] = bytes_sent[0]

        with open(local_path, 'rb') as f:
            ftp.storbinary(f'STOR {remote_filename}', f, blocksize=65536, callback=callback)

        # Final progress update
        active_ftp_uploads[worker_id]["bytes_sent"] = bytes_sent[0]
        active_ftp_uploads[worker_id]["speed"] = 0

        # Verify upload
        try:
            remote_size = ftp.size(remote_filename)
            if remote_size and local_size != remote_size:
                error = f"Size mismatch: local={local_size}, remote={remote_size}"
                logger.error(error)
                if worker_id in active_ftp_uploads:
                    del active_ftp_uploads[worker_id]
                ftp.quit()
                return False, error
        except:
            pass  # Some FTP servers don't support SIZE command

        elapsed_total = time.time() - active_ftp_uploads[worker_id]["started_at"]
        avg_speed = local_size / elapsed_total if elapsed_total > 0 else 0
        remote_path = f"/{target_path}/{remote_filename}" if target_path else f"/{remote_filename}"
        logger.info(f"[FTP Worker {worker_id}] Upload successful: {remote_filename} ({local_size / 1024 / 1024:.1f} MB, {avg_speed / 1024 / 1024:.1f} MB/s)")

        if worker_id in active_ftp_uploads:
            del active_ftp_uploads[worker_id]

        ftp.quit()
        return True, remote_path

    except Exception as e:
        error = str(e)
        logger.error(f"[FTP Worker {worker_id}] Upload failed for {local_path}: {error}")
        if worker_id in active_ftp_uploads:
            del active_ftp_uploads[worker_id]
        return False, error


def get_ftp_upload_progress() -> list[dict]:
    """Get progress of all active FTP uploads."""
    result = []
    for worker_id, data in active_ftp_uploads.items():
        bytes_sent = data.get("bytes_sent", 0)
        bytes_total = data.get("bytes_total", 1)
        percent = (bytes_sent / bytes_total * 100) if bytes_total > 0 else 0
        result.append({
            "worker_id": worker_id,
            "video_id": data.get("video_id"),
            "title": data.get("title", "Unknown"),
            "percent": round(percent, 1),
            "bytes_sent": bytes_sent,
            "bytes_total": bytes_total,
            "speed": data.get("speed", 0),
        })
    return result


async def process_ftp_upload(video_id: int, worker_id: int = 0):
    """Process a single video upload to FTP."""
    logger.info(f"[FTP Worker {worker_id}] Processing upload for video_id={video_id}")

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
            video.ftp_status = "error"
            video.ftp_error = "Local file not found"
            await session.commit()
            return

        # Update status
        video.ftp_status = "uploading"
        video.ftp_attempts += 1
        await session.commit()

        # Generate remote filename
        safe_title = "".join(c for c in video.title if c.isalnum() or c in " -_").strip()[:80]
        ext = Path(video.file_path).suffix
        remote_filename = f"{video.youtube_id}_{safe_title}{ext}"

        # Check if it's a Short
        is_short = is_short_video(video.duration)
        if is_short:
            logger.info(f"Video {video.youtube_id} is a Short ({video.duration}s) - uploading to shorts directory")

        # Run upload in executor (blocking operation)
        loop = asyncio.get_event_loop()
        success, result_msg = await loop.run_in_executor(
            None, upload_file_to_ftp, video.file_path, remote_filename, video.title, video.id, is_short, worker_id
        )

        if success:
            video.ftp_status = "uploaded"
            video.ftp_path = result_msg
            video.ftp_uploaded_at = datetime.utcnow()
            video.ftp_error = None
            logger.info(f"FTP upload completed for {video.youtube_id}")

            # Delete local file if configured AND all enabled uploads are complete
            if settings.delete_after_upload and video.file_path:
                # Check if SMB is enabled - if so, only delete if SMB upload is also done
                smb_done = not settings.smb_enabled or video.upload_status == "uploaded"
                if smb_done:
                    try:
                        os.remove(video.file_path)
                        logger.info(f"Deleted local file: {video.file_path}")
                        video.file_path = None
                    except Exception as e:
                        logger.error(f"Failed to delete local file: {e}")
        else:
            video.ftp_status = "error"
            video.ftp_error = result_msg
            logger.error(f"FTP upload failed for {video.youtube_id}: {result_msg}")

        await session.commit()


async def ftp_upload_worker(worker_id: int):
    """Background worker that processes FTP uploads from the queue."""
    logger.info(f"FTP upload worker {worker_id} started")

    while True:
        video_id = await ftp_queue.get()
        logger.info(f"FTP worker {worker_id} processing video_id={video_id}")
        try:
            await process_ftp_upload(video_id, worker_id)
        except Exception as e:
            logger.error(f"FTP worker {worker_id} error for {video_id}: {e}", exc_info=True)
            if worker_id in active_ftp_uploads:
                del active_ftp_uploads[worker_id]
        finally:
            ftp_queue.task_done()


async def start_ftp_worker():
    """Start multiple background FTP upload workers if FTP is enabled."""
    global ftp_workers

    if not settings.ftp_enabled:
        logger.info("FTP upload disabled")
        return

    logger.info(f"Starting {MAX_CONCURRENT_FTP_UPLOADS} FTP upload workers...")

    for i in range(MAX_CONCURRENT_FTP_UPLOADS):
        worker = asyncio.create_task(ftp_upload_worker(i + 1))
        ftp_workers.append(worker)

    logger.info(f"{MAX_CONCURRENT_FTP_UPLOADS} FTP workers started (concurrent uploads enabled)")


async def queue_ftp_upload(video_id: int):
    """Add a video to the FTP upload queue."""
    if not settings.ftp_enabled:
        return

    logger.info(f"Queuing FTP upload: video_id={video_id}")
    await ftp_queue.put(video_id)


async def check_pending_ftp_uploads():
    """Check for completed downloads that need FTP uploading."""
    if not settings.ftp_enabled:
        return

    async with async_session() as session:
        # Find videos that are downloaded but not FTP uploaded
        result = await session.execute(
            select(Video).where(
                and_(
                    Video.status == "completed",
                    Video.ftp_status.in_(["pending", "error"]),
                    Video.file_path.isnot(None),
                    Video.ftp_attempts < 3,  # Max retry attempts
                )
            )
        )
        videos = result.scalars().all()

        for video in videos:
            logger.info(f"Found pending FTP upload: {video.youtube_id}")
            await queue_ftp_upload(video.id)

        if videos:
            logger.info(f"Queued {len(videos)} pending FTP uploads")
