import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import yt_dlp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import Video
from app.nas_upload import queue_upload

logger = logging.getLogger(__name__)

# Progress tracking for active downloads
active_downloads: dict[int, dict] = {}  # worker_id -> {video_id, title, percent, speed, eta, status}


def get_format_string() -> str:
    """Get yt-dlp format string based on quality setting."""
    quality = settings.video_quality

    # More flexible format selection with fallbacks
    if quality == "best":
        return "bestvideo+bestaudio/best"
    elif quality == "1080p":
        return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    elif quality == "720p":
        return "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    elif quality == "480p":
        return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    else:
        return "best"


def get_download_progress() -> list[dict]:
    """Get progress of all active downloads."""
    return list(active_downloads.values())


def _create_progress_hook(worker_id: int, video_title: str, video_id: int):
    """Create a progress hook function for yt-dlp."""
    last_update = {"time": 0, "bytes": 0}

    def progress_hook(d):
        now = time.time()
        status = d.get("status", "")

        if status == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            speed = d.get("speed", 0) or 0
            eta = d.get("eta", 0) or 0

            # Calculate percent
            percent = (downloaded / total * 100) if total > 0 else 0

            active_downloads[worker_id] = {
                "worker_id": worker_id,
                "video_id": video_id,
                "title": video_title[:50],
                "status": "downloading",
                "percent": round(percent, 1),
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "speed": speed,
                "eta": eta,
            }

        elif status == "finished":
            active_downloads[worker_id] = {
                "worker_id": worker_id,
                "video_id": video_id,
                "title": video_title[:50],
                "status": "processing",
                "percent": 100,
                "downloaded_bytes": d.get("downloaded_bytes", 0),
                "total_bytes": d.get("downloaded_bytes", 0),
                "speed": 0,
                "eta": 0,
            }

    return progress_hook


def get_download_opts(output_path: str, progress_hook=None) -> dict:
    """Get yt-dlp options for downloading."""
    opts = {
        "format": get_format_string(),
        "outtmpl": output_path,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "ignoreerrors": False,
        # Use player clients that don't require JS runtime
        "extractor_args": {"youtube": {"player_client": ["android_sdkless", "web_safari"]}},
        # Embed metadata (title, description, etc.) into the MP4 file
        # This makes Plex show the clean title instead of the filename
        "writethumbnail": False,
        "embedmetadata": True,
        # Subtitles: download manual subs only (no auto-generated), embed in MP4
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["es", "en"],
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            },
            {
                "key": "FFmpegMetadata",
                "add_metadata": True,
            },
            {
                "key": "FFmpegEmbedSubtitle",
            },
        ],
    }

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    return opts


async def download_video(video_id: int, worker_id: int = 0):
    """Download a video in the background."""
    logger.info(f"Starting download for video_id={video_id}")

    async with async_session() as session:
        result = await session.execute(
            select(Video).where(Video.id == video_id)
        )
        video = result.scalar_one_or_none()

        if not video:
            logger.error(f"Video not found in database: video_id={video_id}")
            return

        logger.info(f"Downloading: {video.title} ({video.youtube_id})")

        video.status = "downloading"
        await session.commit()

        # Initialize progress tracking
        active_downloads[worker_id] = {
            "worker_id": worker_id,
            "video_id": video_id,
            "title": video.title[:50],
            "status": "starting",
            "percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed": 0,
            "eta": 0,
        }

        try:
            safe_title = "".join(c for c in video.title if c.isalnum() or c in " -_").strip()[:100]
            output_path = os.path.join(
                settings.download_dir,
                f"{video.youtube_id}_{safe_title}.%(ext)s"
            )
            logger.debug(f"Output path: {output_path}")

            url = f"https://www.youtube.com/watch?v={video.youtube_id}"
            progress_hook = _create_progress_hook(worker_id, video.title, video_id)
            opts = get_download_opts(output_path, progress_hook)

            logger.info(f"Starting yt-dlp download for {url}")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _sync_download, url, opts)

            # Find the downloaded file
            download_dir = Path(settings.download_dir)
            file_found = False
            for ext in ["mp4", "mkv", "webm"]:
                pattern = f"{video.youtube_id}_*.{ext}"
                files = list(download_dir.glob(pattern))
                if files:
                    file_path = files[0]
                    video.file_path = str(file_path)
                    video.file_size = file_path.stat().st_size
                    file_found = True
                    logger.info(f"Download completed: {file_path} ({video.file_size / 1024 / 1024:.1f} MB)")
                    break

            if not file_found:
                logger.warning(f"Download completed but file not found for {video.youtube_id}")

            video.status = "completed"
            video.downloaded_at = datetime.utcnow()
            await session.commit()

            # Clean up progress tracking
            if worker_id in active_downloads:
                del active_downloads[worker_id]

            # Queue for NAS upload if enabled
            if settings.nas_enabled and file_found:
                await queue_upload(video.id)
            return

        except Exception as e:
            logger.error(f"Error downloading {video.youtube_id}: {e}", exc_info=True)
            video.status = "error"
            video.error_message = str(e)[:1000]

            # Clean up progress tracking on error
            if worker_id in active_downloads:
                del active_downloads[worker_id]

        await session.commit()


def _sync_download(url: str, opts: dict):
    """Synchronous download function to run in executor."""
    logger.debug(f"_sync_download called for {url}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    logger.debug(f"_sync_download completed for {url}")


# Background task queue
download_queue: asyncio.Queue = asyncio.Queue()
download_workers: list[asyncio.Task] = []


async def download_worker(worker_id: int):
    """Worker that processes downloads from the queue."""
    logger.info(f"Download worker {worker_id} started")
    while True:
        video_id = await download_queue.get()
        logger.info(f"Worker {worker_id} processing download: video_id={video_id}")
        try:
            await download_video(video_id, worker_id)
        except Exception as e:
            logger.error(f"Worker {worker_id} error downloading {video_id}: {e}", exc_info=True)
            # Clean up on exception
            if worker_id in active_downloads:
                del active_downloads[worker_id]
        finally:
            download_queue.task_done()


async def start_download_worker():
    """Start multiple background download workers for concurrent downloads."""
    global download_workers
    num_workers = settings.max_concurrent_downloads
    logger.info(f"Starting {num_workers} download workers...")

    for i in range(num_workers):
        worker = asyncio.create_task(download_worker(i + 1))
        download_workers.append(worker)

    logger.info(f"{num_workers} download workers started (concurrent downloads enabled)")


async def queue_download(video_id: int):
    """Add a video to the download queue."""
    logger.info(f"Queuing download: video_id={video_id}")
    await download_queue.put(video_id)
