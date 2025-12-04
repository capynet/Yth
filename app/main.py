import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import init_db, get_db, async_session
from app.models import Video
from app.downloader import start_download_worker, queue_download
from app.nas_upload import start_upload_worker, check_pending_uploads
from app.auto_download import start_auto_download_worker, get_stats, auto_download_recommendations

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def reset_stuck_downloads():
    """Reset videos stuck in 'downloading' state from previous run."""
    from sqlalchemy import update
    async with async_session() as session:
        result = await session.execute(
            update(Video)
            .where(Video.status == "downloading")
            .values(status="pending")
        )
        if result.rowcount > 0:
            await session.commit()
            logger.info(f"Reset {result.rowcount} stuck downloads to pending")


async def reset_stuck_uploads():
    """Reset videos stuck in 'uploading' state from previous run."""
    from sqlalchemy import update
    async with async_session() as session:
        result = await session.execute(
            update(Video)
            .where(Video.upload_status == "uploading")
            .values(upload_status="pending")
        )
        if result.rowcount > 0:
            await session.commit()
            logger.info(f"Reset {result.rowcount} stuck uploads to pending")


async def queue_pending_downloads():
    """Queue any pending downloads."""
    async with async_session() as session:
        result = await session.execute(
            select(Video).where(Video.status == "pending")
        )
        pending = result.scalars().all()
        for video in pending:
            await queue_download(video.id)
        if pending:
            logger.info(f"Queued {len(pending)} pending downloads")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application starting...")
    logger.info(f"Download dir: {settings.download_dir}")
    logger.info(f"Video quality: {settings.video_quality}")

    await init_db()
    logger.info("Database initialized")

    # Reset any stuck downloads from previous run
    await reset_stuck_downloads()

    await start_download_worker()
    logger.info("Download worker started")

    # Queue any pending downloads
    await queue_pending_downloads()

    # Reset stuck uploads and start NAS upload worker if enabled
    await reset_stuck_uploads()
    await start_upload_worker()
    if settings.nas_enabled:
        logger.info(f"NAS upload enabled: {settings.nas_host}/{settings.nas_share}")
        await check_pending_uploads()

    # Start auto-download cron (every hour)
    await start_auto_download_worker(interval_seconds=3600)
    logger.info("Auto-download cron started (every 1 hour)")

    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)

    logger.info("Application ready")
    yield

    logger.info("Application shutting down...")


app = FastAPI(title="YT Downloader", lifespan=lifespan)


# ============== API Routes ==============

@app.get("/api/videos")
async def api_list_videos(db: AsyncSession = Depends(get_db)):
    """List all videos in database."""
    result = await db.execute(
        select(Video).order_by(Video.created_at.desc())
    )
    videos = result.scalars().all()

    return {
        "videos": [
            {
                "id": v.id,
                "youtube_id": v.youtube_id,
                "title": v.title,
                "channel": v.channel,
                "duration": v.duration,
                "status": v.status,
                "file_size": v.file_size,
                "error_message": v.error_message,
            }
            for v in videos
        ]
    }


@app.post("/api/download/{youtube_id}")
async def api_download(
    youtube_id: str,
    db: AsyncSession = Depends(get_db),
    title: str = "Unknown",
    channel: str = "Unknown",
    duration: int = 0,
):
    """Start downloading a video."""
    result = await db.execute(
        select(Video).where(Video.youtube_id == youtube_id)
    )
    video = result.scalar_one_or_none()

    if video:
        if video.status == "completed":
            return {"status": "already_downloaded", "video_id": video.id}
        elif video.status in ("pending", "downloading"):
            return {"status": "already_queued", "video_id": video.id}
        video.status = "pending"
        video.error_message = None
    else:
        video = Video(
            youtube_id=youtube_id,
            title=title,
            channel=channel,
            duration=duration,
            status="pending",
        )
        db.add(video)

    await db.commit()
    await db.refresh(video)
    await queue_download(video.id)

    return {"status": "queued", "video_id": video.id}


@app.get("/api/videos/{video_id}/status")
async def api_video_status(video_id: int, db: AsyncSession = Depends(get_db)):
    """Get status of a video."""
    result = await db.execute(
        select(Video).where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return {
        "id": video.id,
        "status": video.status,
        "error_message": video.error_message,
    }


@app.delete("/api/videos/{video_id}")
async def api_delete_video(video_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a video and its file."""
    result = await db.execute(
        select(Video).where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.file_path and os.path.exists(video.file_path):
        os.remove(video.file_path)

    await db.execute(delete(Video).where(Video.id == video_id))
    await db.commit()

    return {"status": "deleted"}


@app.get("/api/uploads")
async def api_list_uploads(db: AsyncSession = Depends(get_db)):
    """List all videos with their upload status."""
    result = await db.execute(
        select(Video).where(Video.status == "completed").order_by(Video.downloaded_at.desc())
    )
    videos = result.scalars().all()

    return {
        "nas_enabled": settings.nas_enabled,
        "uploads": [
            {
                "id": v.id,
                "youtube_id": v.youtube_id,
                "title": v.title,
                "upload_status": v.upload_status,
                "upload_error": v.upload_error,
                "uploaded_at": v.uploaded_at.isoformat() if v.uploaded_at else None,
                "nas_path": v.nas_path,
                "upload_attempts": v.upload_attempts,
            }
            for v in videos
        ]
    }


@app.post("/api/uploads/{video_id}/retry")
async def api_retry_upload(video_id: int, db: AsyncSession = Depends(get_db)):
    """Retry a failed upload."""
    if not settings.nas_enabled:
        raise HTTPException(status_code=400, detail="NAS uploads not enabled")

    result = await db.execute(
        select(Video).where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.upload_status == "uploaded":
        return {"status": "already_uploaded"}

    if not video.file_path or not os.path.exists(video.file_path):
        raise HTTPException(status_code=400, detail="Local file not found")

    video.upload_status = "pending"
    video.upload_error = None
    await db.commit()

    from app.nas_upload import queue_upload
    await queue_upload(video.id)

    return {"status": "queued"}


@app.get("/api/stats")
async def api_stats():
    """Get download/upload statistics."""
    return await get_stats()


@app.post("/api/auto-download/run")
async def api_trigger_auto_download():
    """Manually trigger auto-download of subscriptions."""
    queued = await auto_download_recommendations()
    return {
        "status": "completed",
        "videos_queued": queued,
    }


@app.get("/api/auto-download/status")
async def api_auto_download_status():
    """Get auto-download status."""
    from app.auto_download import (
        auto_download_enabled, last_run, last_run_personalized,
        last_run_videos_queued, last_run_source, subscription_count
    )

    # Check YouTube API status
    try:
        from app.youtube_api import is_api_configured, get_api_status
        api_configured = is_api_configured()
        if api_configured:
            api_status = get_api_status()
        else:
            api_status = {"configured": False}
    except Exception:
        api_configured = False
        api_status = {"configured": False, "error": "Module not available"}

    return {
        "enabled": auto_download_enabled,
        "source": last_run_source,
        "api_configured": api_configured,
        "subscription_count": api_status.get("subscription_count", subscription_count),
        "quota_exceeded": api_status.get("quota_exceeded", False),
        "quota_reset_time": api_status.get("quota_reset_time"),
        "last_run": last_run.isoformat() if last_run else None,
        "last_run_personalized": last_run_personalized,
        "last_run_queued": last_run_videos_queued,
    }


@app.get("/api/youtube-api/status")
async def api_youtube_api_status():
    """Get YouTube API configuration status."""
    try:
        from app.youtube_api import get_api_status
        return get_api_status()
    except Exception as e:
        return {"configured": False, "error": str(e)}


@app.get("/api/uploads/progress")
async def api_upload_progress():
    """Get progress of all active uploads."""
    from app.nas_upload import get_upload_progress
    uploads = get_upload_progress()

    # Add percent to each upload
    for upload in uploads:
        upload["percent"] = round(upload["bytes_sent"] / upload["bytes_total"] * 100, 1) if upload["bytes_total"] > 0 else 0

    return {
        "uploading": len(uploads) > 0,
        "active_count": len(uploads),
        "uploads": uploads,
    }


@app.get("/api/downloads/progress")
async def api_download_progress():
    """Get progress of all active downloads."""
    from app.downloader import get_download_progress
    downloads = get_download_progress()
    return {
        "active_count": len(downloads),
        "downloads": downloads,
    }
