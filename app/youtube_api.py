"""
YouTube API module - Fetches subscriptions and videos using OAuth2.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

logger = logging.getLogger(__name__)

# Paths for credentials (from settings)
TOKEN_FILE = settings.youtube_token_file
CLIENT_SECRETS_FILE = settings.youtube_client_file

# Cache for YouTube service
_youtube_service = None
_credentials = None

# Quota management (in-memory cache, persisted to DB)
_quota_exceeded = False
_quota_reset_time = None  # When quota will reset (midnight PT)
_quota_used_today = 0  # Estimated quota units used today
_quota_date = None  # Date of quota tracking
_quota_loaded = False  # Whether we've loaded from DB
DAILY_QUOTA_LIMIT = 10000  # Default YouTube API quota limit


def _load_quota_state():
    """Load quota state from database (sync version for startup)."""
    global _quota_used_today, _quota_date, _quota_exceeded, _quota_reset_time, _quota_loaded

    if _quota_loaded:
        return

    try:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
        from app.config import DATA_DIR
        from app.models import AppState

        db_path = DATA_DIR / "data" / "videos.db"
        if not db_path.exists():
            _quota_loaded = True
            return

        engine = create_engine(f"sqlite:///{db_path}")
        with Session(engine) as session:
            result = session.execute(select(AppState).where(AppState.key == "youtube_quota"))
            state = result.scalar_one_or_none()

            if state:
                data = json.loads(state.value)
                saved_date = data.get('date')
                today = datetime.utcnow().date().isoformat()

                if saved_date == today:
                    _quota_used_today = data.get('used', 0)
                    _quota_date = datetime.utcnow().date()
                    _quota_exceeded = data.get('exceeded', False)
                    if data.get('reset_time'):
                        _quota_reset_time = datetime.fromisoformat(data['reset_time'])
                    logger.info(f"Loaded quota state from DB: {_quota_used_today}/{DAILY_QUOTA_LIMIT} used today")
                else:
                    _quota_used_today = 0
                    _quota_date = datetime.utcnow().date()
                    _quota_exceeded = False
                    _quota_reset_time = None
                    logger.info("New day - quota reset")

        _quota_loaded = True
    except Exception as e:
        logger.debug(f"Could not load quota state (DB may not exist yet): {e}")
        _quota_loaded = True


def _save_quota_state():
    """Save quota state to database (sync version)."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.config import DATA_DIR
        from app.models import AppState

        db_path = DATA_DIR / "data" / "videos.db"
        if not db_path.exists():
            return

        data = {
            'date': datetime.utcnow().date().isoformat(),
            'used': _quota_used_today,
            'exceeded': _quota_exceeded,
            'reset_time': _quota_reset_time.isoformat() if _quota_reset_time else None,
        }

        engine = create_engine(f"sqlite:///{db_path}")
        with Session(engine) as session:
            from sqlalchemy import select
            result = session.execute(select(AppState).where(AppState.key == "youtube_quota"))
            state = result.scalar_one_or_none()

            if state:
                state.value = json.dumps(data)
            else:
                state = AppState(key="youtube_quota", value=json.dumps(data))
                session.add(state)

            session.commit()
    except Exception as e:
        logger.error(f"Error saving quota state: {e}")


def mark_quota_exceeded():
    """Mark quota as exceeded and calculate reset time (midnight PT)."""
    global _quota_exceeded, _quota_reset_time
    from datetime import timezone
    import pytz

    _quota_exceeded = True

    # Quota resets at midnight Pacific Time
    pt = pytz.timezone('America/Los_Angeles')
    now_pt = datetime.now(pt)
    tomorrow_pt = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    _quota_reset_time = tomorrow_pt.astimezone(timezone.utc).replace(tzinfo=None)

    _save_quota_state()
    logger.warning(f"YouTube API quota exceeded. Will retry after {tomorrow_pt.strftime('%Y-%m-%d %H:%M %Z')}")


def is_quota_exceeded() -> bool:
    """Check if quota is exceeded (and if reset time has passed)."""
    global _quota_exceeded, _quota_reset_time

    if not _quota_exceeded:
        return False

    # Check if reset time has passed
    if _quota_reset_time and datetime.utcnow() > _quota_reset_time:
        logger.info("YouTube API quota reset time passed, re-enabling API calls")
        _quota_exceeded = False
        _quota_reset_time = None
        return False

    return True


def get_quota_status() -> dict:
    """Get quota status for display."""
    return {
        'exceeded': _quota_exceeded,
        'reset_time': _quota_reset_time.isoformat() if _quota_reset_time else None,
    }


def _reset_quota_if_new_day():
    """Reset quota counter if it's a new day."""
    global _quota_used_today, _quota_date, _quota_exceeded
    today = datetime.utcnow().date()
    if _quota_date != today:
        _quota_used_today = 0
        _quota_date = today
        # Also reset exceeded flag on new day
        if _quota_exceeded and _quota_reset_time and datetime.utcnow() > _quota_reset_time:
            _quota_exceeded = False
            _quota_reset_time = None


def add_quota_usage(units: int):
    """Add to quota usage counter."""
    global _quota_used_today
    _reset_quota_if_new_day()
    _quota_used_today += units
    _save_quota_state()


def get_quota_usage() -> tuple[int, int]:
    """Get current quota usage (used, limit)."""
    _load_quota_state()  # Load from DB if not yet loaded
    _reset_quota_if_new_day()
    return _quota_used_today, DAILY_QUOTA_LIMIT


def get_credentials() -> Optional[Credentials]:
    """Load and refresh credentials from token file."""
    global _credentials

    if not os.path.exists(TOKEN_FILE):
        logger.error(f"Token file not found: {TOKEN_FILE}")
        logger.error("Run oauth_setup.py first to authorize the app.")
        return None

    try:
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)

        _credentials = Credentials(
            token=token_data.get('token'),
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data.get('token_uri'),
            client_id=token_data.get('client_id'),
            client_secret=token_data.get('client_secret'),
            scopes=token_data.get('scopes')
        )

        # Refresh if expired
        if _credentials.expired and _credentials.refresh_token:
            logger.info("Refreshing expired credentials...")
            _credentials.refresh(Request())

            # Save updated token
            token_data['token'] = _credentials.token
            with open(TOKEN_FILE, 'w') as f:
                json.dump(token_data, f, indent=2)
            logger.info("Credentials refreshed and saved.")

        return _credentials

    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
        return None


def get_youtube_service():
    """Get authenticated YouTube API service."""
    global _youtube_service

    credentials = get_credentials()
    if not credentials:
        return None

    try:
        _youtube_service = build('youtube', 'v3', credentials=credentials)
        return _youtube_service
    except Exception as e:
        logger.error(f"Error building YouTube service: {e}")
        return None


def get_subscriptions() -> list[dict]:
    """
    Get all subscribed channels.

    Returns list of dicts with:
    - channel_id: YouTube channel ID
    - channel_title: Channel name
    - uploads_playlist_id: Playlist ID for channel uploads
    """
    # Check quota before making API calls
    if is_quota_exceeded():
        logger.warning("Skipping get_subscriptions - quota exceeded")
        return []

    youtube = get_youtube_service()
    if not youtube:
        return []

    subscriptions = []
    next_page_token = None

    try:
        while True:
            request = youtube.subscriptions().list(
                part='snippet',
                mine=True,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            add_quota_usage(1)  # subscriptions.list costs 1 unit

            for item in response.get('items', []):
                snippet = item.get('snippet', {})
                resource = snippet.get('resourceId', {})

                subscriptions.append({
                    'channel_id': resource.get('channelId'),
                    'channel_title': snippet.get('title'),
                    'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
                })

            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break

        logger.info(f"Found {len(subscriptions)} subscriptions")
        return subscriptions

    except HttpError as e:
        # Check if quota exceeded
        if e.resp.status == 403 and 'quotaExceeded' in str(e):
            mark_quota_exceeded()
        else:
            logger.error(f"YouTube API error getting subscriptions: {e}")
        return []


def get_channel_uploads_playlist(channel_id: str) -> Optional[str]:
    """Get the uploads playlist ID for a channel."""
    youtube = get_youtube_service()
    if not youtube:
        return None

    try:
        request = youtube.channels().list(
            part='contentDetails',
            id=channel_id
        )
        response = request.execute()
        add_quota_usage(1)  # channels.list costs 1 unit

        items = response.get('items', [])
        if items:
            return items[0]['contentDetails']['relatedPlaylists']['uploads']
        return None

    except HttpError as e:
        logger.error(f"Error getting uploads playlist for {channel_id}: {e}")
        return None


def get_recent_videos_from_channel(channel_id: str, days_back: int = 5, max_results: int = 10) -> list[dict]:
    """
    Get recent videos from a channel.

    Args:
        channel_id: YouTube channel ID
        days_back: Only include videos from the last N days
        max_results: Maximum videos to return per channel

    Returns list of dicts with video info.
    """
    youtube = get_youtube_service()
    if not youtube:
        return []

    # Get uploads playlist ID
    uploads_playlist_id = get_channel_uploads_playlist(channel_id)
    if not uploads_playlist_id:
        return []

    cutoff_date = datetime.utcnow() - timedelta(days=days_back)
    videos = []

    try:
        request = youtube.playlistItems().list(
            part='snippet,contentDetails',
            playlistId=uploads_playlist_id,
            maxResults=max_results
        )
        response = request.execute()
        add_quota_usage(1)  # playlistItems.list costs 1 unit

        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            content_details = item.get('contentDetails', {})

            # Parse publish date
            published_at_str = snippet.get('publishedAt', '')
            try:
                published_at = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
                published_at = published_at.replace(tzinfo=None)  # Make naive for comparison
            except:
                published_at = datetime.utcnow()

            # Skip old videos
            if published_at < cutoff_date:
                continue

            video_id = content_details.get('videoId')
            if not video_id:
                continue

            videos.append({
                'youtube_id': video_id,
                'title': snippet.get('title', 'Unknown'),
                'channel': snippet.get('channelTitle', 'Unknown'),
                'channel_id': snippet.get('channelId', channel_id),
                'thumbnail': snippet.get('thumbnails', {}).get('high', {}).get('url',
                             snippet.get('thumbnails', {}).get('default', {}).get('url', '')),
                'published_at': published_at.isoformat(),
                'duration': 0,  # Not available in playlist items
            })

        return videos

    except HttpError as e:
        logger.error(f"Error getting videos from channel {channel_id}: {e}")
        return []


def get_video_details(video_ids: list[str]) -> dict[str, dict]:
    """
    Get detailed info for videos (duration, live status, etc).

    Args:
        video_ids: List of YouTube video IDs

    Returns dict mapping video_id to details.
    """
    youtube = get_youtube_service()
    if not youtube or not video_ids:
        return {}

    details = {}

    try:
        # API allows up to 50 IDs per request
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]

            request = youtube.videos().list(
                part='contentDetails,statistics,snippet',
                id=','.join(batch)
            )
            response = request.execute()
            add_quota_usage(1)  # videos.list costs 1 unit

            for item in response.get('items', []):
                video_id = item['id']
                content_details = item.get('contentDetails', {})
                snippet = item.get('snippet', {})

                # Parse duration (ISO 8601 format like PT4M13S)
                duration_str = content_details.get('duration', 'PT0S')
                duration_seconds = parse_duration(duration_str)

                # Check if it's a live stream
                live_status = snippet.get('liveBroadcastContent', 'none')
                is_live = live_status in ('live', 'upcoming')

                details[video_id] = {
                    'duration': duration_seconds,
                    'view_count': int(item.get('statistics', {}).get('viewCount', 0)),
                    'is_live': is_live,
                    'live_status': live_status,
                }

        return details

    except HttpError as e:
        logger.error(f"Error getting video details: {e}")
        return {}


def parse_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration (PT4M13S) to seconds."""
    import re

    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

    return hours * 3600 + minutes * 60 + seconds


def get_all_subscription_videos(days_back: int = 5, max_per_channel: int = 5) -> list[dict]:
    """
    Get recent videos from ALL subscribed channels.

    Args:
        days_back: Only include videos from the last N days
        max_per_channel: Max videos per channel

    Returns list of all videos, sorted by publish date (newest first).
    """
    import time

    subscriptions = get_subscriptions()
    if not subscriptions:
        logger.warning("No subscriptions found or API not configured")
        return []

    all_videos = []
    video_ids = []

    total_subs = len(subscriptions)
    logger.info(f"Fetching videos from {total_subs} subscribed channels (rate limited)...")

    for i, sub in enumerate(subscriptions):
        channel_id = sub.get('channel_id')
        if not channel_id:
            continue

        # Rate limiting: 200ms between requests to avoid API saturation
        if i > 0:
            time.sleep(0.2)

        # Log progress every 20 channels
        if i > 0 and i % 20 == 0:
            logger.info(f"Progress: {i}/{total_subs} channels processed...")

        videos = get_recent_videos_from_channel(
            channel_id,
            days_back=days_back,
            max_results=max_per_channel
        )

        for video in videos:
            all_videos.append(video)
            video_ids.append(video['youtube_id'])

    # Get duration details for all videos
    if video_ids:
        logger.info(f"Fetching details for {len(video_ids)} videos...")
        details = get_video_details(video_ids)

        for video in all_videos:
            vid = video['youtube_id']
            if vid in details:
                video['duration'] = details[vid].get('duration', 0)
                video['is_live'] = details[vid].get('is_live', False)

    # Filter out live streams
    live_count = sum(1 for v in all_videos if v.get('is_live', False))
    if live_count > 0:
        logger.info(f"Filtering out {live_count} live streams")
        all_videos = [v for v in all_videos if not v.get('is_live', False)]

    # Sort by publish date (newest first)
    all_videos.sort(key=lambda x: x.get('published_at', ''), reverse=True)

    logger.info(f"Found {len(all_videos)} recent videos from subscriptions (excluding live)")
    return all_videos


def is_api_configured() -> bool:
    """Check if YouTube API is properly configured."""
    return os.path.exists(TOKEN_FILE)


# Cache for subscription count to avoid quota spam
_cached_subscription_count = 0


def get_api_status() -> dict:
    """Get status of YouTube API configuration (uses cached subscription count)."""
    global _cached_subscription_count

    quota_status = get_quota_status()

    status = {
        'configured': False,
        'token_file_exists': os.path.exists(TOKEN_FILE),
        'client_file_exists': os.path.exists(CLIENT_SECRETS_FILE),
        'credentials_valid': False,
        'subscription_count': _cached_subscription_count,
        'quota_exceeded': quota_status['exceeded'],
        'quota_reset_time': quota_status['reset_time'],
    }

    if not status['token_file_exists']:
        status['error'] = 'Token file not found. Run oauth_setup.py first.'
        return status

    credentials = get_credentials()
    if credentials:
        status['credentials_valid'] = True
        status['configured'] = True

        # Use cached count - don't call API here
        status['subscription_count'] = _cached_subscription_count

    return status


def update_subscription_count(count: int):
    """Update the cached subscription count."""
    global _cached_subscription_count
    _cached_subscription_count = count


def get_api_status_full() -> dict:
    """Get full status including fresh subscription count (calls API)."""
    status = {
        'configured': False,
        'token_file_exists': os.path.exists(TOKEN_FILE),
        'client_file_exists': os.path.exists(CLIENT_SECRETS_FILE),
        'credentials_valid': False,
        'subscription_count': 0,
    }

    if not status['token_file_exists']:
        status['error'] = 'Token file not found. Run oauth_setup.py first.'
        return status

    credentials = get_credentials()
    if credentials:
        status['credentials_valid'] = True
        status['configured'] = True

        # Try to get subscription count (calls API)
        try:
            subs = get_subscriptions()
            status['subscription_count'] = len(subs)
            update_subscription_count(len(subs))
        except Exception as e:
            status['error'] = str(e)

    return status
