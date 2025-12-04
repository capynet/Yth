import asyncio
import logging
from datetime import datetime, date

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import Video
from app.downloader import queue_download

logger = logging.getLogger(__name__)

# Auto-download state
auto_download_enabled: bool = True
last_run: datetime | None = None
last_run_personalized: bool = False
last_run_videos_queued: int = 0
last_run_source: str = "none"  # "api", "scraper", or "none"
subscription_count: int = 0


def get_videos_from_subscriptions(days_back: int = 5) -> tuple[list[dict], bool]:
    """
    Get videos from YouTube API subscriptions.
    Returns (videos, is_authenticated).
    """
    global subscription_count

    try:
        from app.youtube_api import is_api_configured, get_all_subscription_videos, get_subscriptions, update_subscription_count

        if not is_api_configured():
            logger.info("YouTube API not configured, falling back to scraper")
            return [], False

        # Get subscription count for stats
        subs = get_subscriptions()
        subscription_count = len(subs)
        update_subscription_count(subscription_count)  # Cache for status endpoint

        if subscription_count == 0:
            logger.warning("No subscriptions found in YouTube account")
            return [], True  # Authenticated but no subscriptions

        # Get videos from last N days
        videos = get_all_subscription_videos(days_back=days_back, max_per_channel=5)
        return videos, True

    except Exception as e:
        logger.error(f"Error getting subscription videos: {e}")
        return [], False


async def auto_download_recommendations():
    """
    Automatically download videos from subscriptions.
    Uses YouTube API if configured, falls back to scraper.
    """
    global last_run, last_run_personalized, last_run_videos_queued, last_run_source

    logger.info("Auto-download: Starting subscription check")
    last_run = datetime.utcnow()

    # Try YouTube API first (subscriptions)
    videos, is_authenticated = get_videos_from_subscriptions(days_back=5)

    if not is_authenticated:
        logger.warning("Auto-download: YouTube API not configured. Run oauth_setup.py first.")
        last_run_personalized = False
        last_run_source = "none"
        last_run_videos_queued = 0
        return 0

    last_run_personalized = True
    last_run_source = "api"
    logger.info(f"Auto-download: Found {len(videos)} videos from {subscription_count} subscriptions")

    if not videos:
        logger.info("Auto-download: No new videos from subscriptions")
        last_run_videos_queued = 0
        return 0

    logger.info(f"Auto-download: Processing {len(videos)} videos")

    queued_count = 0
    async with async_session() as session:
        for video in videos:
            youtube_id = video["youtube_id"]

            # Check if already in database
            result = await session.execute(
                select(Video).where(Video.youtube_id == youtube_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Skip if already downloaded or in progress
                if existing.status in ("completed", "pending", "downloading"):
                    continue
                # Retry if error
                if existing.status == "error":
                    existing.status = "pending"
                    existing.error_message = None
                    await session.commit()
                    await queue_download(existing.id)
                    queued_count += 1
                    logger.info(f"Auto-download: Retrying {youtube_id}")
            else:
                # Create new video entry
                new_video = Video(
                    youtube_id=youtube_id,
                    title=video.get("title", "Unknown"),
                    channel=video.get("channel", "Unknown"),
                    duration=video.get("duration", 0),
                    thumbnail=video.get("thumbnail", ""),
                    status="pending",
                )
                session.add(new_video)
                await session.commit()
                await session.refresh(new_video)
                await queue_download(new_video.id)
                queued_count += 1
                logger.info(f"Auto-download: Queued {youtube_id} - {video.get('title', '')[:50]}")

    last_run_videos_queued = queued_count
    logger.info(f"Auto-download: Queued {queued_count} new videos")
    return queued_count


async def auto_download_loop(interval_seconds: int = 3600):
    """
    Background loop that runs auto-download every interval.
    Default: every hour (3600 seconds)
    """
    logger.info(f"Auto-download loop started (interval: {interval_seconds}s)")

    # Wait 30 seconds before first run to let the server start
    logger.info("Auto-download: Waiting 30s before first run...")
    await asyncio.sleep(30)

    while True:
        if auto_download_enabled:
            try:
                await auto_download_recommendations()
            except Exception as e:
                logger.error(f"Auto-download error: {e}", exc_info=True)
        else:
            logger.debug("Auto-download is disabled, skipping")

        await asyncio.sleep(interval_seconds)


async def start_auto_download_worker(interval_seconds: int = 3600):
    """Start the auto-download background worker."""
    asyncio.create_task(auto_download_loop(interval_seconds))
    logger.info("Auto-download worker started")


async def get_stats() -> dict:
    """Get download/upload statistics."""
    async with async_session() as session:
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())

        # Total counts
        total_result = await session.execute(select(func.count(Video.id)))
        total_videos = total_result.scalar()

        # By status
        completed_result = await session.execute(
            select(func.count(Video.id)).where(Video.status == "completed")
        )
        completed = completed_result.scalar()

        pending_result = await session.execute(
            select(func.count(Video.id)).where(Video.status == "pending")
        )
        pending = pending_result.scalar()

        downloading_result = await session.execute(
            select(func.count(Video.id)).where(Video.status == "downloading")
        )
        downloading = downloading_result.scalar()

        error_result = await session.execute(
            select(func.count(Video.id)).where(Video.status == "error")
        )
        errors = error_result.scalar()

        # Today's downloads
        today_downloaded_result = await session.execute(
            select(func.count(Video.id)).where(
                and_(
                    Video.status == "completed",
                    Video.downloaded_at >= today_start
                )
            )
        )
        today_downloaded = today_downloaded_result.scalar()

        # Upload stats
        uploaded_result = await session.execute(
            select(func.count(Video.id)).where(Video.upload_status == "uploaded")
        )
        uploaded = uploaded_result.scalar()

        upload_pending_result = await session.execute(
            select(func.count(Video.id)).where(
                and_(
                    Video.status == "completed",
                    Video.upload_status.in_(["pending", "uploading"])
                )
            )
        )
        upload_pending = upload_pending_result.scalar()

        upload_error_result = await session.execute(
            select(func.count(Video.id)).where(Video.upload_status == "error")
        )
        upload_errors = upload_error_result.scalar()

        # Today's uploads
        today_uploaded_result = await session.execute(
            select(func.count(Video.id)).where(
                and_(
                    Video.upload_status == "uploaded",
                    Video.uploaded_at >= today_start
                )
            )
        )
        today_uploaded = today_uploaded_result.scalar()

        # Total file size (for completed videos with file_size)
        size_result = await session.execute(
            select(func.sum(Video.file_size)).where(Video.file_size.isnot(None))
        )
        total_size = size_result.scalar() or 0

        # Recent errors
        recent_errors_result = await session.execute(
            select(Video).where(Video.status == "error").order_by(Video.created_at.desc()).limit(5)
        )
        recent_errors = [
            {"youtube_id": v.youtube_id, "title": v.title[:30], "error": v.error_message[:50] if v.error_message else "Unknown"}
            for v in recent_errors_result.scalars().all()
        ]

        return {
            "total_videos": total_videos,
            "downloads": {
                "completed": completed,
                "pending": pending,
                "downloading": downloading,
                "errors": errors,
                "today": today_downloaded,
            },
            "uploads": {
                "uploaded": uploaded,
                "pending": upload_pending,
                "errors": upload_errors,
                "today": today_uploaded,
            },
            "total_size_mb": round(total_size / 1024 / 1024, 1) if total_size else 0,
            "auto_download": {
                "enabled": auto_download_enabled,
                "last_run": last_run.isoformat() if last_run else None,
                "last_run_personalized": last_run_personalized,
                "last_run_queued": last_run_videos_queued,
                "source": last_run_source,
                "subscription_count": subscription_count,
            },
            "recent_errors": recent_errors,
        }
