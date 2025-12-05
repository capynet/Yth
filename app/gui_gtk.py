#!/usr/bin/env python3
"""
Tube Sync GUI - Integrated desktop application.
All-in-one: GUI + Downloads + Auto-sync, with systray support.
"""

import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Pango

import sys
import asyncio
import threading
import logging
import signal
import subprocess
from pathlib import Path
from datetime import datetime

# Setup logging to both console and file
def setup_logging():
    """Configure logging to console and file."""
    from app.config import DATA_DIR

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "tubesync.log"

    # Create formatters
    log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    formatter = logging.Formatter(log_format)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler with rotation (keep last 5 MB)
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=3
    )
    file_handler.setLevel(logging.INFO)  # Only INFO and above to file
    file_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Silence noisy libraries
    logging.getLogger('aiosqlite').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)

    return log_file

LOG_FILE = setup_logging()
logger = logging.getLogger(__name__)
logger.info(f"Logging to: {LOG_FILE}")

# App imports
from app.config import load_config, save_config, CONFIG_DIR, DATA_DIR, ensure_directories
from app.database import init_db, async_session, get_db
from app.models import Video
from app.auto_download import get_stats, auto_download_recommendations, subscription_count
from app.downloader import get_download_progress, start_download_worker, download_queue
from app.smb_upload import get_upload_progress, start_upload_worker, check_pending_uploads, test_smb_connection
from app.ftp_upload import get_ftp_upload_progress, start_ftp_worker, check_pending_ftp_uploads, test_ftp_connection

# Autostart file location
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "tubesync.desktop"
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Tube Sync
Comment=YouTube Video Downloader
Exec={exec_path}
Icon=tubesync
Terminal=false
Categories=AudioVideo;Network;
StartupWMClass=com.tubesync.app
X-GNOME-Autostart-enabled=true
"""


def is_autostart_enabled() -> bool:
    """Check if autostart is enabled."""
    return AUTOSTART_FILE.exists()


def set_autostart(enabled: bool, exec_path: str = None):
    """Enable or disable autostart."""
    if enabled:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        if exec_path is None:
            exec_path = str(Path(__file__).parent.parent / "tubesync-gui")
        content = DESKTOP_ENTRY.format(exec_path=exec_path)
        AUTOSTART_FILE.write_text(content)
        logger.info(f"Autostart enabled: {AUTOSTART_FILE}")
    else:
        if AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
            logger.info("Autostart disabled")


def format_speed(bytes_per_sec: int) -> str:
    """Format speed to human readable."""
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


class AsyncBackend:
    """Manages async operations in a background thread."""

    def __init__(self):
        self.loop = None
        self.thread = None
        self.running = False

    def start(self):
        """Start the async backend in a background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Async backend started")

    def _run_loop(self):
        """Run the async event loop."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize database and workers
        self.loop.run_until_complete(self._init())

        # Run forever - the loop will sleep when there's nothing to do
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    async def _init(self):
        """Initialize database and background workers."""
        from sqlalchemy import update

        ensure_directories()
        await init_db()

        # Reset stuck downloads/uploads from previous run
        async with async_session() as session:
            result = await session.execute(
                update(Video)
                .where(Video.status == "downloading")
                .values(status="pending")
            )
            if result.rowcount > 0:
                await session.commit()
                logger.info(f"Reset {result.rowcount} stuck downloads to pending")

            result = await session.execute(
                update(Video)
                .where(Video.upload_status == "uploading")
                .values(upload_status="pending")
            )
            if result.rowcount > 0:
                await session.commit()
                logger.info(f"Reset {result.rowcount} stuck uploads to pending")

        await start_download_worker()
        await start_upload_worker()
        await check_pending_uploads()
        await start_ftp_worker()
        await check_pending_ftp_uploads()
        logger.info("Backend initialized: DB, download and upload workers ready")

    def run_coroutine(self, coro, callback=None):
        """Run a coroutine in the backend loop, optionally calling callback with result."""
        if not self.loop:
            logger.warning("run_coroutine called but loop is None")
            return

        logger.debug("run_coroutine: scheduling coroutine")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        if callback:
            def on_done(f):
                try:
                    result = f.result()
                    logger.debug("run_coroutine: coroutine completed, scheduling callback")
                    # Use timeout_add instead of idle_add - returns False to run only once
                    GLib.timeout_add(0, lambda: (callback(result), False)[1])
                except Exception as e:
                    logger.error(f"Coroutine error: {e}", exc_info=True)
                    GLib.timeout_add(0, lambda: (callback(None), False)[1])

            future.add_done_callback(on_done)

    def stop(self):
        """Stop the backend."""
        self.running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


class StatCard(Gtk.Frame):
    """A card widget displaying statistics with optional settings button."""

    def __init__(self, title: str, on_settings_clicked=None):
        super().__init__()
        self.set_margin_start(5)
        self.set_margin_end(5)
        self.set_margin_top(5)
        self.set_margin_bottom(5)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Title row with optional settings button
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_row.set_hexpand(True)

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("dim-label")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_hexpand(True)
        title_row.append(title_label)

        if on_settings_clicked:
            settings_btn = Gtk.Button()
            settings_btn.set_icon_name("emblem-system-symbolic")
            settings_btn.add_css_class("flat")
            settings_btn.add_css_class("circular")
            settings_btn.set_valign(Gtk.Align.CENTER)
            settings_btn.connect("clicked", on_settings_clicked)
            title_row.append(settings_btn)

        box.append(title_row)

        self.main_value = Gtk.Label(label="0")
        self.main_value.add_css_class("title-1")
        self.main_value.set_halign(Gtk.Align.START)
        box.append(self.main_value)

        box.append(Gtk.Separator())

        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self.details_box)

        self.set_child(box)
        self.detail_labels = {}

    def add_detail(self, key: str, label: str):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_hexpand(True)

        label_widget = Gtk.Label(label=label)
        label_widget.add_css_class("dim-label")
        label_widget.set_halign(Gtk.Align.START)
        label_widget.set_hexpand(True)

        value_widget = Gtk.Label(label="0")
        value_widget.set_halign(Gtk.Align.END)

        row.append(label_widget)
        row.append(value_widget)
        self.details_box.append(row)
        self.detail_labels[key] = value_widget

    def set_main_value(self, value: str, css_class: str = None):
        self.main_value.set_label(value)
        for cls in ["success", "error", "accent"]:
            self.main_value.remove_css_class(cls)
        if css_class:
            self.main_value.add_css_class(css_class)

    def set_detail(self, key: str, value: str, css_class: str = None):
        if key in self.detail_labels:
            self.detail_labels[key].set_label(value)
            for cls in ["success", "error", "warning"]:
                self.detail_labels[key].remove_css_class(cls)
            if css_class:
                self.detail_labels[key].add_css_class(css_class)


class ProgressItem(Gtk.Box):
    """A progress item with title and progress bar."""

    def __init__(self, title: str, percent: float, speed: int, is_upload: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_margin_bottom(8)
        self.is_upload = is_upload

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        icon_name = "go-up-symbolic" if is_upload else "go-down-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        header.append(icon)

        self.title_label = Gtk.Label(label=title[:50])
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_hexpand(True)
        self.title_label.set_halign(Gtk.Align.START)
        header.append(self.title_label)

        self.percent_label = Gtk.Label(label=f"{percent:.1f}%")
        self.percent_label.add_css_class("heading")
        header.append(self.percent_label)

        self.speed_label = Gtk.Label(label=format_speed(speed) if speed > 0 else "")
        self.speed_label.add_css_class("dim-label")
        header.append(self.speed_label)

        self.append(header)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(percent / 100)
        if is_upload:
            self.progress_bar.add_css_class("accent")
        self.append(self.progress_bar)

    def update(self, title: str, percent: float, speed: int):
        """Update progress values without recreating widget."""
        self.title_label.set_label(title[:50])
        self.percent_label.set_label(f"{percent:.1f}%")
        self.speed_label.set_label(format_speed(speed) if speed > 0 else "")
        self.progress_bar.set_fraction(percent / 100)


class YTSyncWindow(Adw.ApplicationWindow):
    """Main application window."""

    def __init__(self, app, backend: AsyncBackend):
        super().__init__(application=app)
        self.backend = backend
        self.set_title("Tube Sync")
        self.set_default_size(1050, 560)
        self.set_size_request(1050, 560)

        self.running = True
        self._refresh_pending = False  # Prevent overlapping refreshes

        # Track progress items for efficient updates
        self._download_items: dict[int, ProgressItem] = {}  # worker_id -> ProgressItem
        self._upload_items: dict[str, ProgressItem] = {}  # video_id -> ProgressItem
        self._ftp_items: dict[str, ProgressItem] = {}  # video_id -> ProgressItem
        self._empty_download_label = None
        self._empty_upload_label = None
        self._empty_ftp_label = None

        # Create UI
        self.create_ui()

        # Start refresh loop every 10 seconds for stats (DB queries)
        GLib.timeout_add(10000, self.refresh_data)

        # Fast refresh loop for progress bars (100ms) - reads from memory only
        GLib.timeout_add(100, self.refresh_progress_bars)

        # Handle close - hide to systray instead
        self.connect("close-request", self.on_close_request)

    def on_close_request(self, window):
        """Hide window instead of closing (systray behavior)."""
        self.set_visible(False)
        return True  # Prevent actual close

    def create_ui(self):
        """Create the main UI."""
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Tube Sync"))

        # Settings button
        settings_btn = Gtk.Button()
        settings_btn.set_icon_name("emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self.on_settings_clicked)
        header.pack_end(settings_btn)


        # Force sync button
        sync_btn = Gtk.Button()
        sync_btn.set_icon_name("emblem-synchronizing-symbolic")
        sync_btn.set_tooltip_text("Sync now")
        sync_btn.connect("clicked", self.on_sync_clicked)
        header.pack_start(sync_btn)

        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_container.append(header)

        dashboard = self.create_dashboard()
        dashboard.set_hexpand(True)
        dashboard.set_vexpand(True)
        main_container.append(dashboard)

        self.set_content(main_container)

    def on_quit_clicked(self, button):
        """Actually quit the application."""
        self.running = False
        self.backend.stop()
        self.get_application().quit()

    def on_sync_clicked(self, button):
        """Force a sync now."""
        logger.info("Manual sync triggered")
        self.backend.run_coroutine(auto_download_recommendations())

    def on_settings_clicked(self, button):
        """Open general settings dialog."""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("General Settings")
        dialog.set_default_size(400, 320)
        dialog.set_resizable(False)

        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        dialog_header = Adw.HeaderBar()
        dialog_header.set_title_widget(Gtk.Label(label="General Settings"))
        dialog_box.append(dialog_header)

        dialog_box.append(self.create_settings_content(dialog))

        dialog.set_content(dialog_box)
        dialog.present()

    def create_dashboard(self) -> Gtk.Widget:
        """Create dashboard view."""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(25)
        box.set_margin_end(25)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_hexpand(True)
        box.set_vexpand(True)

        # Connection status
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.connection_icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic")
        self.connection_label = Gtk.Label(label="Running")
        self.connection_label.add_css_class("success")
        status_box.append(self.connection_icon)
        status_box.append(self.connection_label)
        box.append(status_box)

        box.append(Gtk.Separator())

        # Stats cards
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        cards_box.set_homogeneous(True)
        cards_box.set_hexpand(True)

        self.downloads_card = StatCard("Downloads", self.on_downloads_settings)
        self.downloads_card.add_detail("pending", "Pending")
        self.downloads_card.add_detail("errors", "Errors")
        self.downloads_card.add_detail("today", "Today")
        self.downloads_card.add_detail("size", "Total Size")
        cards_box.append(self.downloads_card)

        self.uploads_card = StatCard("SMB Uploads", self.on_smb_settings)
        self.uploads_card.add_detail("smb", "SMB")
        self.uploads_card.add_detail("pending", "Pending")
        self.uploads_card.add_detail("errors", "Errors")
        self.uploads_card.add_detail("today", "Today")
        cards_box.append(self.uploads_card)

        self.ftp_card = StatCard("FTP Uploads", self.on_ftp_settings)
        self.ftp_card.add_detail("ftp", "FTP")
        self.ftp_card.add_detail("pending", "Pending")
        self.ftp_card.add_detail("errors", "Errors")
        self.ftp_card.add_detail("today", "Today")
        cards_box.append(self.ftp_card)

        self.auto_card = StatCard("YouTube API", self.on_youtube_settings)
        self.auto_card.add_detail("quota", "Quota")
        self.auto_card.add_detail("last_run", "Last Sync")
        self.auto_card.add_detail("queued", "Last Queued")
        cards_box.append(self.auto_card)

        box.append(cards_box)
        box.append(Gtk.Separator())

        # Progress sections
        progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        progress_box.set_homogeneous(True)
        progress_box.set_hexpand(True)
        progress_box.set_vexpand(True)

        # Downloads progress
        dl_frame = Gtk.Frame()
        dl_frame.set_margin_top(10)
        dl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        dl_box.set_margin_start(10)
        dl_box.set_margin_end(10)
        dl_box.set_margin_top(10)
        dl_box.set_margin_bottom(10)

        dl_title = Gtk.Label(label="Active Downloads")
        dl_title.add_css_class("heading")
        dl_title.set_halign(Gtk.Align.START)
        dl_box.append(dl_title)

        self.downloads_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        dl_box.append(self.downloads_progress_box)
        dl_frame.set_child(dl_box)
        progress_box.append(dl_frame)

        # Uploads progress
        ul_frame = Gtk.Frame()
        ul_frame.set_margin_top(10)
        ul_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ul_box.set_margin_start(10)
        ul_box.set_margin_end(10)
        ul_box.set_margin_top(10)
        ul_box.set_margin_bottom(10)

        ul_title = Gtk.Label(label="SMB Uploads")
        ul_title.add_css_class("heading")
        ul_title.set_halign(Gtk.Align.START)
        ul_box.append(ul_title)

        self.uploads_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ul_box.append(self.uploads_progress_box)
        ul_frame.set_child(ul_box)
        progress_box.append(ul_frame)

        # FTP Uploads progress
        ftp_frame = Gtk.Frame()
        ftp_frame.set_margin_top(10)
        ftp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ftp_box.set_margin_start(10)
        ftp_box.set_margin_end(10)
        ftp_box.set_margin_top(10)
        ftp_box.set_margin_bottom(10)

        ftp_title = Gtk.Label(label="FTP Uploads")
        ftp_title.add_css_class("heading")
        ftp_title.set_halign(Gtk.Align.START)
        ftp_box.append(ftp_title)

        self.ftp_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ftp_box.append(self.ftp_progress_box)
        ftp_frame.set_child(ftp_box)
        progress_box.append(ftp_frame)

        box.append(progress_box)

        scroll.set_child(box)
        return scroll

    def create_settings_content(self, dialog) -> Gtk.Widget:
        """Create general settings content for modal dialog."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(20)

        # Title
        title = Gtk.Label(label="General Settings")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.START)
        box.append(title)

        box.append(Gtk.Separator())

        self._settings_dialog = dialog
        config = load_config()

        # Autostart option
        autostart_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        autostart_row.set_hexpand(True)
        autostart_label = Gtk.Label(label="Start at login")
        autostart_label.set_hexpand(True)
        autostart_label.set_halign(Gtk.Align.START)
        self.autostart_switch = Gtk.Switch()
        self.autostart_switch.set_active(is_autostart_enabled())
        self.autostart_switch.set_valign(Gtk.Align.CENTER)
        autostart_row.append(autostart_label)
        autostart_row.append(self.autostart_switch)
        box.append(autostart_row)

        box.append(Gtk.Separator())

        # Shorts duration
        self.shorts_duration = Gtk.SpinButton.new_with_range(15, 180, 5)
        self.shorts_duration.set_value(config.get("shorts_max_duration", 60))
        box.append(self.create_field("Max Shorts Duration (seconds)", self.shorts_duration))

        box.append(Gtk.Separator())

        # Delete after upload option
        delete_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        delete_row.set_hexpand(True)
        delete_label = Gtk.Label(label="Delete local files after upload")
        delete_label.set_hexpand(True)
        delete_label.set_halign(Gtk.Align.START)
        self.delete_after_upload = Gtk.Switch()
        self.delete_after_upload.set_active(config.get("delete_after_upload", True))
        self.delete_after_upload.set_valign(Gtk.Align.CENTER)
        delete_row.append(delete_label)
        delete_row.append(self.delete_after_upload)
        box.append(delete_row)

        # Explanation for delete option
        delete_info = Gtk.Label(label="Files are deleted only after all enabled uploads complete")
        delete_info.add_css_class("dim-label")
        delete_info.set_halign(Gtk.Align.START)
        box.append(delete_info)

        box.append(Gtk.Separator())

        # Info text
        info_label = Gtk.Label(label="Use the gear icon on each card to configure\nDownloads, SMB, FTP, and YouTube API settings.")
        info_label.add_css_class("dim-label")
        info_label.set_halign(Gtk.Align.START)
        box.append(info_label)

        box.append(Gtk.Separator())

        # Save button
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self.on_save_settings)
        save_row.append(save_btn)

        self.save_status = Gtk.Label(label="")
        save_row.append(self.save_status)

        box.append(save_row)

        # Config path info
        config_path = Gtk.Label(label=f"Config: {CONFIG_DIR}/config.json")
        config_path.add_css_class("dim-label")
        config_path.set_halign(Gtk.Align.START)
        box.append(config_path)

        return box

    def create_field(self, label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_hexpand(True)

        lbl = Gtk.Label(label=label)
        lbl.set_halign(Gtk.Align.START)
        lbl.add_css_class("dim-label")
        box.append(lbl)

        widget.set_hexpand(True)
        box.append(widget)

        return box

    def on_save_settings(self, button):
        """Save general settings to config file."""
        current_config = load_config()

        current_config.update({
            "shorts_max_duration": int(self.shorts_duration.get_value()),
            "delete_after_upload": self.delete_after_upload.get_active(),
        })

        try:
            save_config(current_config)
            set_autostart(self.autostart_switch.get_active())
            from app.config import settings
            settings.reload()
            self.save_status.set_label("Saved!")
            self.save_status.remove_css_class("error")
            self.save_status.add_css_class("success")
        except Exception as ex:
            self.save_status.set_label(f"Error: {ex}")
            self.save_status.remove_css_class("success")
            self.save_status.add_css_class("error")

    # ==================== DOWNLOADS SETTINGS ====================
    def on_downloads_settings(self, button):
        """Open downloads settings dialog."""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("Download Settings")
        dialog.set_default_size(400, 300)
        dialog.set_resizable(False)

        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Download Settings"))
        dialog_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)

        config = load_config()

        # Download directory
        self._dl_download_dir = Gtk.Entry()
        self._dl_download_dir.set_text(config.get("download_dir", ""))
        content.append(self.create_field("Download Directory", self._dl_download_dir))

        # Video quality
        quality_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        quality_label = Gtk.Label(label="Video Quality")
        quality_label.set_halign(Gtk.Align.START)
        quality_label.add_css_class("dim-label")
        quality_box.append(quality_label)

        self._dl_quality = Gtk.DropDown.new_from_strings(["best", "1080p", "720p", "480p"])
        qualities = ["best", "1080p", "720p", "480p"]
        current_quality = config.get("video_quality", "best")
        if current_quality in qualities:
            self._dl_quality.set_selected(qualities.index(current_quality))
        quality_box.append(self._dl_quality)
        content.append(quality_box)

        # Concurrent downloads
        self._dl_concurrent = Gtk.SpinButton.new_with_range(1, 10, 1)
        self._dl_concurrent.set_value(config.get("max_concurrent_downloads", 3))
        content.append(self.create_field("Max Concurrent Downloads", self._dl_concurrent))

        content.append(Gtk.Separator())

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self._save_downloads_settings(dialog))
        content.append(save_btn)

        dialog_box.append(content)
        dialog.set_content(dialog_box)
        dialog.present()

    def _save_downloads_settings(self, dialog):
        config = load_config()
        qualities = ["best", "1080p", "720p", "480p"]
        config.update({
            "download_dir": self._dl_download_dir.get_text(),
            "video_quality": qualities[self._dl_quality.get_selected()],
            "max_concurrent_downloads": int(self._dl_concurrent.get_value()),
        })
        save_config(config)
        from app.config import settings
        settings.reload()
        dialog.close()

    # ==================== SMB SETTINGS ====================
    def on_smb_settings(self, button):
        """Open SMB settings dialog."""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("SMB Settings")
        dialog.set_default_size(450, 450)
        dialog.set_resizable(False)

        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="SMB Settings"))
        dialog_box.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)

        config = load_config()

        # SMB Enabled
        enabled_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        enabled_row.set_hexpand(True)
        enabled_label = Gtk.Label(label="Enable SMB Upload")
        enabled_label.set_hexpand(True)
        enabled_label.set_halign(Gtk.Align.START)
        self._smb_enabled = Gtk.Switch()
        self._smb_enabled.set_active(config.get("smb_enabled", False))
        self._smb_enabled.set_valign(Gtk.Align.CENTER)
        enabled_row.append(enabled_label)
        enabled_row.append(self._smb_enabled)
        content.append(enabled_row)

        # Host and Share
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row1.set_homogeneous(True)

        self._smb_host = Gtk.Entry()
        self._smb_host.set_placeholder_text("192.168.1.100")
        self._smb_host.set_text(config.get("smb_host", ""))
        row1.append(self.create_field("SMB Host", self._smb_host))

        self._smb_share = Gtk.Entry()
        self._smb_share.set_text(config.get("smb_share", "video"))
        row1.append(self.create_field("Share Name", self._smb_share))
        content.append(row1)

        # User and Password
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row2.set_homogeneous(True)

        self._smb_user = Gtk.Entry()
        self._smb_user.set_text(config.get("smb_user", ""))
        row2.append(self.create_field("Username", self._smb_user))

        self._smb_password = Gtk.PasswordEntry()
        self._smb_password.set_text(config.get("smb_password", ""))
        self._smb_password.set_show_peek_icon(True)
        row2.append(self.create_field("Password", self._smb_password))
        content.append(row2)

        # Paths
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row3.set_homogeneous(True)

        self._smb_path = Gtk.Entry()
        self._smb_path.set_text(config.get("smb_path", "/youtube"))
        row3.append(self.create_field("Videos Path", self._smb_path))

        self._smb_shorts_path = Gtk.Entry()
        self._smb_shorts_path.set_text(config.get("smb_shorts_path", "/shorts"))
        row3.append(self.create_field("Shorts Path", self._smb_shorts_path))
        content.append(row3)

        content.append(Gtk.Separator())

        # Status label for connection test
        self._smb_status_label = Gtk.Label(label="")
        self._smb_status_label.set_halign(Gtk.Align.START)
        content.append(self._smb_status_label)

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self._save_smb_settings(dialog))
        content.append(save_btn)

        scroll.set_child(content)
        dialog_box.append(scroll)
        dialog.set_content(dialog_box)
        dialog.present()

    def _save_smb_settings(self, dialog):
        config = load_config()
        config.update({
            "smb_enabled": self._smb_enabled.get_active(),
            "smb_host": self._smb_host.get_text(),
            "smb_share": self._smb_share.get_text(),
            "smb_user": self._smb_user.get_text(),
            "smb_password": self._smb_password.get_text(),
            "smb_path": self._smb_path.get_text(),
            "smb_shorts_path": self._smb_shorts_path.get_text(),
        })
        save_config(config)
        from app.config import settings
        settings.reload()

        # Test connection if enabled
        if self._smb_enabled.get_active():
            self._smb_status_label.set_label("Testing connection...")
            self._smb_status_label.remove_css_class("success")
            self._smb_status_label.remove_css_class("error")

            # Run test in background
            def test_and_update():
                ok, status = test_smb_connection()
                GLib.idle_add(lambda: self._update_smb_status(ok, status, dialog))

            import threading
            threading.Thread(target=test_and_update, daemon=True).start()
        else:
            dialog.close()

    def _update_smb_status(self, ok, status, dialog):
        if ok:
            self._smb_status_label.set_label(f"Connection OK: {status}")
            self._smb_status_label.add_css_class("success")
            def close_and_refresh():
                dialog.close()
                self.refresh_data()
            GLib.timeout_add(1500, close_and_refresh)
        else:
            self._smb_status_label.set_label(f"Connection failed: {status}")
            self._smb_status_label.add_css_class("error")

    # ==================== FTP SETTINGS ====================
    def on_ftp_settings(self, button):
        """Open FTP settings dialog."""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("FTP Settings")
        dialog.set_default_size(450, 500)
        dialog.set_resizable(False)

        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="FTP Settings"))
        dialog_box.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)

        config = load_config()

        # FTP Enabled
        enabled_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        enabled_row.set_hexpand(True)
        enabled_label = Gtk.Label(label="Enable FTP Upload")
        enabled_label.set_hexpand(True)
        enabled_label.set_halign(Gtk.Align.START)
        self._ftp_enabled = Gtk.Switch()
        self._ftp_enabled.set_active(config.get("ftp_enabled", False))
        self._ftp_enabled.set_valign(Gtk.Align.CENTER)
        enabled_row.append(enabled_label)
        enabled_row.append(self._ftp_enabled)
        content.append(enabled_row)

        # Host and Port
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row1.set_homogeneous(True)

        self._ftp_host = Gtk.Entry()
        self._ftp_host.set_placeholder_text("ftp.example.com")
        self._ftp_host.set_text(config.get("ftp_host", ""))
        row1.append(self.create_field("FTP Host", self._ftp_host))

        self._ftp_port = Gtk.Entry()
        self._ftp_port.set_text(str(config.get("ftp_port", 21)))
        row1.append(self.create_field("Port", self._ftp_port))
        content.append(row1)

        # User and Password
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row2.set_homogeneous(True)

        self._ftp_user = Gtk.Entry()
        self._ftp_user.set_text(config.get("ftp_user", ""))
        row2.append(self.create_field("Username", self._ftp_user))

        self._ftp_password = Gtk.PasswordEntry()
        self._ftp_password.set_text(config.get("ftp_password", ""))
        self._ftp_password.set_show_peek_icon(True)
        row2.append(self.create_field("Password", self._ftp_password))
        content.append(row2)

        # Paths
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        row3.set_homogeneous(True)

        self._ftp_path = Gtk.Entry()
        self._ftp_path.set_text(config.get("ftp_path", "/youtube"))
        row3.append(self.create_field("Videos Path", self._ftp_path))

        self._ftp_shorts_path = Gtk.Entry()
        self._ftp_shorts_path.set_text(config.get("ftp_shorts_path", "/shorts"))
        row3.append(self.create_field("Shorts Path", self._ftp_shorts_path))
        content.append(row3)

        # TLS
        tls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tls_row.set_hexpand(True)
        tls_label = Gtk.Label(label="Use TLS (FTPS)")
        tls_label.set_hexpand(True)
        tls_label.set_halign(Gtk.Align.START)
        self._ftp_use_tls = Gtk.Switch()
        self._ftp_use_tls.set_active(config.get("ftp_use_tls", False))
        self._ftp_use_tls.set_valign(Gtk.Align.CENTER)
        tls_row.append(tls_label)
        tls_row.append(self._ftp_use_tls)
        content.append(tls_row)

        content.append(Gtk.Separator())

        # Status label for connection test
        self._ftp_status_label = Gtk.Label(label="")
        self._ftp_status_label.set_halign(Gtk.Align.START)
        content.append(self._ftp_status_label)

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self._save_ftp_settings(dialog))
        content.append(save_btn)

        scroll.set_child(content)
        dialog_box.append(scroll)
        dialog.set_content(dialog_box)
        dialog.present()

    def _save_ftp_settings(self, dialog):
        config = load_config()
        config.update({
            "ftp_enabled": self._ftp_enabled.get_active(),
            "ftp_host": self._ftp_host.get_text(),
            "ftp_port": int(self._ftp_port.get_text() or 21),
            "ftp_user": self._ftp_user.get_text(),
            "ftp_password": self._ftp_password.get_text(),
            "ftp_path": self._ftp_path.get_text(),
            "ftp_shorts_path": self._ftp_shorts_path.get_text(),
            "ftp_use_tls": self._ftp_use_tls.get_active(),
        })
        save_config(config)
        from app.config import settings
        settings.reload()

        # Test connection if enabled
        if self._ftp_enabled.get_active():
            self._ftp_status_label.set_label("Testing connection...")
            self._ftp_status_label.remove_css_class("success")
            self._ftp_status_label.remove_css_class("error")

            # Run test in background
            def test_and_update():
                from app.ftp_upload import test_ftp_connection
                ok, status = test_ftp_connection()
                GLib.idle_add(lambda: self._update_ftp_status(ok, status, dialog))

            import threading
            threading.Thread(target=test_and_update, daemon=True).start()
        else:
            dialog.close()

    def _update_ftp_status(self, ok, status, dialog):
        if ok:
            self._ftp_status_label.set_label(f"Connection OK: {status}")
            self._ftp_status_label.add_css_class("success")
            def close_and_refresh():
                dialog.close()
                self.refresh_data()
            GLib.timeout_add(1500, close_and_refresh)
        else:
            self._ftp_status_label.set_label(f"Connection failed: {status}")
            self._ftp_status_label.add_css_class("error")

    # ==================== YOUTUBE API SETTINGS ====================
    def on_youtube_settings(self, button):
        """Open YouTube API settings dialog."""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("YouTube API Settings")
        dialog.set_default_size(450, 350)
        dialog.set_resizable(False)

        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="YouTube API Settings"))
        dialog_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)

        config = load_config()

        # Client file
        self._yt_client_file = Gtk.Entry()
        self._yt_client_file.set_text(config.get("youtube_client_file", ""))
        content.append(self.create_field("Google Client JSON", self._yt_client_file))

        # Token file
        self._yt_token_file = Gtk.Entry()
        self._yt_token_file.set_text(config.get("youtube_token_file", ""))
        content.append(self.create_field("YouTube Token JSON", self._yt_token_file))

        content.append(Gtk.Separator())

        # Status info
        from app.youtube_api import is_api_configured
        status_label = Gtk.Label()
        if is_api_configured():
            status_label.set_label("Status: Configured and ready")
            status_label.add_css_class("success")
        else:
            status_label.set_label("Status: Not configured - run oauth_setup.py")
            status_label.add_css_class("warning")
        status_label.set_halign(Gtk.Align.START)
        content.append(status_label)

        # Info text
        info_label = Gtk.Label(label="To configure YouTube API:\n1. Create credentials at Google Cloud Console\n2. Download client JSON to the path above\n3. Run: python oauth_setup.py")
        info_label.set_halign(Gtk.Align.START)
        info_label.add_css_class("dim-label")
        info_label.set_wrap(True)
        content.append(info_label)

        content.append(Gtk.Separator())

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: self._save_youtube_settings(dialog))
        content.append(save_btn)

        dialog_box.append(content)
        dialog.set_content(dialog_box)
        dialog.present()

    def _save_youtube_settings(self, dialog):
        config = load_config()
        config.update({
            "youtube_client_file": self._yt_client_file.get_text(),
            "youtube_token_file": self._yt_token_file.get_text(),
        })
        save_config(config)
        from app.config import settings
        settings.reload()
        dialog.close()

    def refresh_data(self) -> bool:
        """Refresh dashboard data."""
        if not self.running:
            logger.debug("refresh_data: not running, stopping refresh loop")
            return False

        # Skip if previous refresh still pending
        if self._refresh_pending:
            logger.debug("refresh_data: previous refresh still pending, skipping")
            return True

        self._refresh_pending = True
        logger.debug("refresh_data: starting refresh")

        # Get stats from backend
        self.backend.run_coroutine(get_stats(), self._on_stats_received)

        return True

    def _on_stats_received(self, stats):
        """Handle stats received from backend."""
        logger.debug(f"_on_stats_received: got stats type={type(stats)}")
        self._refresh_pending = False
        try:
            if stats:
                self.update_dashboard(stats)
            logger.debug("_on_stats_received: dashboard updated successfully")
        except Exception as e:
            logger.error(f"Error updating dashboard: {e}", exc_info=True)

    def update_dashboard(self, stats: dict):
        """Update dashboard with stats."""
        if not stats:
            return

        # Downloads card
        dl = stats.get("downloads", {})
        self.downloads_card.set_main_value(str(dl.get("completed", 0)), "success")
        self.downloads_card.set_detail("pending", str(dl.get("pending", 0)))
        self.downloads_card.set_detail("errors", str(dl.get("errors", 0)), "error" if dl.get("errors", 0) > 0 else None)
        self.downloads_card.set_detail("today", str(dl.get("today", 0)))
        self.downloads_card.set_detail("size", f"{stats.get('total_size_mb', 0):.0f} MB")

        # Uploads card
        up = stats.get("uploads", {})
        self.uploads_card.set_main_value(str(up.get("uploaded", 0)), "success")
        self.uploads_card.set_detail("pending", str(up.get("pending", 0)))
        self.uploads_card.set_detail("errors", str(up.get("errors", 0)), "error" if up.get("errors", 0) > 0 else None)
        self.uploads_card.set_detail("today", str(up.get("today", 0)))

        # Test SMB connectivity
        smb_ok, smb_status = test_smb_connection()
        self.uploads_card.set_detail("smb", smb_status, "success" if smb_ok else "error")

        # FTP card
        ftp = stats.get("ftp", {})
        self.ftp_card.set_main_value(str(ftp.get("uploaded", 0)), "success")
        self.ftp_card.set_detail("pending", str(ftp.get("pending", 0)))
        self.ftp_card.set_detail("errors", str(ftp.get("errors", 0)), "error" if ftp.get("errors", 0) > 0 else None)
        self.ftp_card.set_detail("today", str(ftp.get("today", 0)))

        # Test FTP connectivity
        ftp_ok, ftp_status = test_ftp_connection()
        self.ftp_card.set_detail("ftp", ftp_status, "success" if ftp_ok else "error")

        # YouTube API card
        auto = stats.get("auto_download", {})
        self.auto_card.set_main_value(str(auto.get("subscription_count", 0)))

        from app.youtube_api import is_api_configured, is_quota_exceeded, get_quota_usage
        quota_used, quota_limit = get_quota_usage()
        if is_quota_exceeded():
            self.auto_card.set_detail("quota", "EXCEEDED", "error")
        elif not is_api_configured():
            self.auto_card.set_detail("quota", "NOT CONFIGURED", "warning")
        else:
            quota_pct = (quota_used / quota_limit * 100) if quota_limit > 0 else 0
            quota_class = "error" if quota_pct > 90 else "warning" if quota_pct > 70 else "success"
            self.auto_card.set_detail("quota", f"{quota_used:,}/{quota_limit:,}", quota_class)

        lr = auto.get("last_run", "Never")
        if lr and lr != "Never" and "T" in str(lr):
            lr = str(lr).split("T")[1][:8]
        self.auto_card.set_detail("last_run", str(lr) if lr else "Never")
        self.auto_card.set_detail("queued", str(auto.get("last_run_queued", 0)))

        # Update progress displays
        self._update_progress_boxes()

    def _update_progress_boxes(self):
        """Update download/upload progress boxes efficiently."""
        try:
            # Downloads progress
            active_downloads = get_download_progress()
            current_worker_ids = set()

            if active_downloads:
                # Remove empty label if present
                if self._empty_download_label:
                    self.downloads_progress_box.remove(self._empty_download_label)
                    self._empty_download_label = None

                for dl_item in active_downloads:
                    worker_id = dl_item.get("worker_id", 0)
                    current_worker_ids.add(worker_id)
                    title = dl_item.get("title", "Unknown")
                    percent = dl_item.get("percent", 0)
                    speed = dl_item.get("speed", 0)

                    if worker_id in self._download_items:
                        # Update existing item
                        self._download_items[worker_id].update(title, percent, speed)
                    else:
                        # Create new item
                        item = ProgressItem(title, percent, speed, is_upload=False)
                        self._download_items[worker_id] = item
                        self.downloads_progress_box.append(item)

                # Remove items that are no longer active
                for worker_id in list(self._download_items.keys()):
                    if worker_id not in current_worker_ids:
                        self.downloads_progress_box.remove(self._download_items[worker_id])
                        del self._download_items[worker_id]
            else:
                # Clear all download items
                for item in self._download_items.values():
                    self.downloads_progress_box.remove(item)
                self._download_items.clear()

                # Show empty label
                if not self._empty_download_label:
                    self._empty_download_label = Gtk.Label(label="No active downloads")
                    self._empty_download_label.add_css_class("dim-label")
                    self.downloads_progress_box.append(self._empty_download_label)

            # Uploads progress
            active_uploads = get_upload_progress()
            current_upload_ids = set()

            if active_uploads:
                # Remove empty label if present
                if self._empty_upload_label:
                    self.uploads_progress_box.remove(self._empty_upload_label)
                    self._empty_upload_label = None

                for up_item in active_uploads:
                    upload_id = str(up_item.get("video_id", up_item.get("title", "")))
                    current_upload_ids.add(upload_id)
                    title = up_item.get("title", "Unknown")
                    percent = up_item.get("percent", 0)
                    speed = up_item.get("speed", 0)

                    if upload_id in self._upload_items:
                        # Update existing item
                        self._upload_items[upload_id].update(title, percent, speed)
                    else:
                        # Create new item
                        item = ProgressItem(title, percent, speed, is_upload=True)
                        self._upload_items[upload_id] = item
                        self.uploads_progress_box.append(item)

                # Remove items that are no longer active
                for upload_id in list(self._upload_items.keys()):
                    if upload_id not in current_upload_ids:
                        self.uploads_progress_box.remove(self._upload_items[upload_id])
                        del self._upload_items[upload_id]
            else:
                # Clear all upload items
                for item in self._upload_items.values():
                    self.uploads_progress_box.remove(item)
                self._upload_items.clear()

                # Show empty label
                if not self._empty_upload_label:
                    self._empty_upload_label = Gtk.Label(label="No pending uploads")
                    self._empty_upload_label.add_css_class("dim-label")
                    self.uploads_progress_box.append(self._empty_upload_label)

            # FTP Uploads progress
            active_ftp = get_ftp_upload_progress()
            current_ftp_ids = set()

            if active_ftp:
                # Remove empty label if present
                if self._empty_ftp_label:
                    self.ftp_progress_box.remove(self._empty_ftp_label)
                    self._empty_ftp_label = None

                for ftp_item in active_ftp:
                    ftp_id = str(ftp_item.get("video_id", ftp_item.get("title", "")))
                    current_ftp_ids.add(ftp_id)
                    title = ftp_item.get("title", "Unknown")
                    percent = ftp_item.get("percent", 0)
                    speed = ftp_item.get("speed", 0)

                    if ftp_id in self._ftp_items:
                        # Update existing item
                        self._ftp_items[ftp_id].update(title, percent, speed)
                    else:
                        # Create new item
                        item = ProgressItem(title, percent, speed, is_upload=True)
                        self._ftp_items[ftp_id] = item
                        self.ftp_progress_box.append(item)

                # Remove items that are no longer active
                for ftp_id in list(self._ftp_items.keys()):
                    if ftp_id not in current_ftp_ids:
                        self.ftp_progress_box.remove(self._ftp_items[ftp_id])
                        del self._ftp_items[ftp_id]
            else:
                # Clear all FTP items
                for item in self._ftp_items.values():
                    self.ftp_progress_box.remove(item)
                self._ftp_items.clear()

                # Show empty label
                if not self._empty_ftp_label:
                    self._empty_ftp_label = Gtk.Label(label="No pending FTP uploads")
                    self._empty_ftp_label.add_css_class("dim-label")
                    self.ftp_progress_box.append(self._empty_ftp_label)

        except Exception as e:
            logger.error(f"_update_progress_boxes error: {e}", exc_info=True)

    def refresh_progress_bars(self) -> bool:
        """Fast refresh for progress bars only (no DB queries)."""
        if not self.running:
            return False

        try:
            self._update_progress_boxes()
        except Exception as e:
            logger.error(f"refresh_progress_bars error: {e}")

        return True


class YTSyncApp(Adw.Application):
    """Main application with systray support."""

    def __init__(self):
        super().__init__(
            application_id="com.ytsync.app",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.backend = None
        self.window = None
        self.indicator = None

        # Keep app running even when window is hidden
        self.hold()

    def create_systray(self):
        """Launch systray as separate process (GTK3) to avoid version conflicts."""
        systray_script = Path(__file__).parent / "systray.py"
        if not systray_script.exists():
            logger.warning(f"Systray script not found: {systray_script}")
            return

        # Launch systray process with our PID
        self.systray_process = subprocess.Popen(
            [sys.executable, str(systray_script), str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info(f"Systray process started (PID: {self.systray_process.pid})")

        # Handle SIGUSR1 to show window
        signal.signal(signal.SIGUSR1, self._on_show_signal)

    def _on_show_signal(self, signum, frame):
        """Handle signal to show window."""
        GLib.idle_add(self._show_window)

    def _show_window(self):
        """Show window from main thread."""
        if self.window:
            self.window.set_visible(True)
            self.window.present()
        return False

    def _cleanup_systray(self):
        """Terminate systray process."""
        if hasattr(self, 'systray_process') and self.systray_process:
            self.systray_process.terminate()
            try:
                self.systray_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.systray_process.kill()

    def do_activate(self):
        """Handle app activation."""
        if not self.window:
            # Start backend
            self.backend = AsyncBackend()
            self.backend.start()

            # Wait for backend to initialize
            import time
            time.sleep(0.5)

            # Start auto-download loop
            self.backend.run_coroutine(self._start_auto_download())

            # Create window
            self.window = YTSyncWindow(self, self.backend)

            # Create systray indicator
            self.create_systray()

        self.window.present()

    async def _start_auto_download(self):
        """Start the auto-download background loop."""
        from app.auto_download import auto_download_loop
        asyncio.create_task(auto_download_loop(3600))  # Every hour

    def do_shutdown(self):
        """Clean up on shutdown."""
        self._cleanup_systray()
        if self.window:
            self.window.running = False
        if self.backend:
            self.backend.stop()
        Adw.Application.do_shutdown(self)


def main():
    """Run the application."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = YTSyncApp()
    app.run(None)


if __name__ == "__main__":
    main()
