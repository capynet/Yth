#!/usr/bin/env python3
"""
YT Downloader CLI - Monitor your YouTube downloader stats in real-time.
Usage: python ytcli.py [-w] [-i 2]

Like htop but for your YouTube downloads!
"""

import argparse
import sys
import time
import threading
from datetime import datetime

try:
    import httpx
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    print("Missing dependencies. Install with: pip install httpx rich")
    sys.exit(1)

API_URL = "http://localhost:8000"
console = Console()

# Global state
last_fetch_time = None
is_fetching = False


# Cache for API data
_cache = {
    "stats": None,
    "auto_status": None,
    "upload_progress": None,
    "download_progress": None,
}


def fetch_all_data():
    """Fetch all data from API in one go."""
    global last_fetch_time, is_fetching, _cache
    is_fetching = True

    try:
        with httpx.Client(timeout=15) as client:
            try:
                stats_resp = client.get(f"{API_URL}/api/stats")
                _cache["stats"] = stats_resp.json() if stats_resp.status_code == 200 else {"error": "API error"}
            except:
                _cache["stats"] = _cache["stats"] or {"error": "Connection failed"}

            try:
                auto_resp = client.get(f"{API_URL}/api/auto-download/status")
                _cache["auto_status"] = auto_resp.json() if auto_resp.status_code == 200 else {}
            except:
                _cache["auto_status"] = _cache["auto_status"] or {}

            try:
                progress_resp = client.get(f"{API_URL}/api/uploads/progress")
                _cache["upload_progress"] = progress_resp.json() if progress_resp.status_code == 200 else {}
            except:
                _cache["upload_progress"] = {}

            try:
                dl_progress_resp = client.get(f"{API_URL}/api/downloads/progress")
                _cache["download_progress"] = dl_progress_resp.json() if dl_progress_resp.status_code == 200 else {}
            except:
                _cache["download_progress"] = {}

        last_fetch_time = datetime.now()
    finally:
        is_fetching = False

    return _cache


def get_cached_data():
    """Get cached data without fetching."""
    return _cache


def format_bytes(bytes_val):
    """Format bytes to human readable."""
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / 1024 / 1024 / 1024:.1f} GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / 1024 / 1024:.1f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val} B"


def format_speed(bytes_per_sec):
    """Format speed to human readable."""
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def create_progress_bar(percent, width=40):
    """Create a progress bar as a Text object with proper styling."""
    filled = int(width * percent / 100)
    empty = width - filled
    bar = Text()
    bar.append('█' * filled, style="green")
    bar.append('░' * empty, style="dim")
    return bar


def create_dashboard(watch_mode=False):
    """Create the dashboard display."""
    data = get_cached_data()
    stats = data["stats"] or {}
    auto_status = data["auto_status"] or {}
    upload_progress = data["upload_progress"] or {}
    download_progress = data["download_progress"] or {}

    if not stats or "error" in stats:
        error_msg = stats.get("error", "No data yet") if stats else "Connecting..."
        return Panel(
            f"[yellow]Connecting to API...[/yellow]\n\n"
            f"Make sure the app is running at {API_URL}\n"
            f"Error: {error_msg}",
            title="YT Downloader",
            border_style="yellow"
        )

    # Calculate download section size based on active downloads
    active_downloads = download_progress.get("downloads", [])
    download_section_size = max(4, 3 + len(active_downloads) * 2) if active_downloads else 4

    # Calculate upload section size based on active uploads
    active_uploads_count = len(upload_progress.get("uploads", []))
    upload_section_size = max(4, 3 + active_uploads_count * 2) if active_uploads_count > 0 else 4

    # Create main layout
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="download", size=download_section_size),
        Layout(name="upload", size=upload_section_size),
    )

    layout["main"].split_row(
        Layout(name="downloads"),
        Layout(name="uploads"),
        Layout(name="auto"),
    )

    # Header with status
    now = datetime.now().strftime("%H:%M:%S")
    header_text = Text()
    header_text.append("YT Downloader", style="bold cyan")
    header_text.append(f"  {now}", style="dim")

    # Show fetch status
    if is_fetching:
        header_text.append("  ⟳ ", style="yellow")
    elif last_fetch_time:
        elapsed = (datetime.now() - last_fetch_time).seconds
        header_text.append(f"  ↻{elapsed}s", style="dim green" if elapsed < 5 else "dim yellow")

    if watch_mode:
        header_text.append("  Ctrl+C=quit", style="dim")

    layout["header"].update(Panel(header_text, box=box.SIMPLE))

    # Downloads table
    dl = stats.get("downloads", {})
    downloads_table = Table(box=box.ROUNDED, show_header=False, expand=True)
    downloads_table.add_column("Metric", style="cyan")
    downloads_table.add_column("Value", justify="right")

    downloads_table.add_row("Completed", f"[green]{dl.get('completed', 0)}[/green]")
    downloads_table.add_row("Downloading", f"[yellow]{dl.get('downloading', 0)}[/yellow]")
    downloads_table.add_row("Pending", f"[blue]{dl.get('pending', 0)}[/blue]")
    downloads_table.add_row("Errors", f"[red]{dl.get('errors', 0)}[/red]")
    downloads_table.add_row("Today", f"[bold]{dl.get('today', 0)}[/bold]")
    downloads_table.add_row("Total Size", f"{stats.get('total_size_mb', 0):.0f} MB")

    layout["downloads"].update(Panel(downloads_table, title="Downloads", border_style="green"))

    # Uploads table
    up = stats.get("uploads", {})
    uploads_table = Table(box=box.ROUNDED, show_header=False, expand=True)
    uploads_table.add_column("Metric", style="cyan")
    uploads_table.add_column("Value", justify="right")

    uploads_table.add_row("Uploaded", f"[green]{up.get('uploaded', 0)}[/green]")
    uploads_table.add_row("Pending", f"[yellow]{up.get('pending', 0)}[/yellow]")
    uploads_table.add_row("Errors", f"[red]{up.get('errors', 0)}[/red]")
    uploads_table.add_row("Today", f"[bold]{up.get('today', 0)}[/bold]")

    layout["uploads"].update(Panel(uploads_table, title="NAS Uploads", border_style="blue"))

    # Auto-download status
    auto_table = Table(box=box.ROUNDED, show_header=False, expand=True)
    auto_table.add_column("Metric", style="cyan")
    auto_table.add_column("Value", justify="right")

    api_configured = auto_status.get("api_configured", False)
    quota_exceeded = auto_status.get("quota_exceeded", False)

    if quota_exceeded:
        api_status_str = "[red]QUOTA EXCEEDED[/red]"
    elif api_configured:
        api_status_str = "[green]OK[/green]"
    else:
        api_status_str = "[red]NOT CONFIGURED[/red]"

    auto_table.add_row("YouTube API", api_status_str)

    sub_count = auto_status.get("subscription_count", 0)
    auto_table.add_row("Subscriptions", f"[cyan]{sub_count}[/cyan]")

    ad = stats.get("auto_download", {})
    last_run = ad.get("last_run", "Never")
    if last_run and last_run != "Never":
        last_run = last_run.split("T")[1][:8] if "T" in last_run else last_run

    auto_table.add_row("Last Run", str(last_run))
    auto_table.add_row("Last Queued", str(ad.get("last_run_queued", 0)))

    # Show quota reset time if exceeded
    if quota_exceeded:
        reset_time = auto_status.get("quota_reset_time", "")
        if reset_time:
            # Parse and format reset time
            try:
                from datetime import datetime as dt
                reset_dt = dt.fromisoformat(reset_time.replace('Z', '+00:00'))
                reset_str = reset_dt.strftime("%H:%M")
            except:
                reset_str = "midnight PT"
            auto_table.add_row("Resets at", f"[yellow]{reset_str}[/yellow]")

    layout["auto"].update(Panel(auto_table, title="Auto-Download", border_style="magenta"))

    # Download progress section
    if active_downloads:
        download_text = Text()
        for i, dl in enumerate(active_downloads):
            if i > 0:
                download_text.append("\n")
            title = dl.get("title", "Unknown")[:45]
            percent = dl.get("percent", 0)
            speed = dl.get("speed", 0)
            status = dl.get("status", "downloading")
            eta = dl.get("eta", 0)

            # Status icon
            if status == "processing":
                download_text.append("⚙️  ", style="yellow")
                download_text.append(f"{title}", style="bold white")
                download_text.append(" (processing...)", style="dim yellow")
            else:
                download_text.append("📥 ", style="cyan")
                download_text.append(f"{title}\n", style="bold white")
                download_text.append("   ")
                download_text.append_text(create_progress_bar(percent, 45))
                download_text.append(f" {percent:.1f}%", style="bold")
                if speed > 0:
                    download_text.append(f"  •  {format_speed(speed)}", style="bold cyan")
                if eta > 0:
                    mins, secs = divmod(int(eta), 60)
                    download_text.append(f"  •  {mins}:{secs:02d}", style="dim")

        layout["download"].update(Panel(download_text, title="[cyan]⬇ Downloading[/cyan]", border_style="cyan"))
    else:
        # No active downloads - check pending from stats
        stats_dl = stats.get("downloads", {})
        pending_count = stats_dl.get("pending", 0)
        if pending_count > 0:
            idle_text = Text()
            idle_text.append(f"⏳ {pending_count} videos in queue...", style="dim cyan")
            layout["download"].update(Panel(idle_text, title="[dim]Downloads[/dim]", border_style="dim"))
        else:
            idle_text = Text()
            idle_text.append("✓ No active downloads", style="dim green")
            layout["download"].update(Panel(idle_text, title="[dim]Downloads[/dim]", border_style="dim"))

    # Upload progress section (always visible) - supports multiple concurrent uploads
    active_uploads_list = upload_progress.get("uploads", [])
    has_active_upload = len(active_uploads_list) > 0

    if has_active_upload:
        upload_text = Text()
        for i, upl in enumerate(active_uploads_list):
            if i > 0:
                upload_text.append("\n")
            percent = upl.get("percent", 0)
            bytes_sent = upl.get("bytes_sent", 0)
            bytes_total = upl.get("bytes_total", 1)
            speed = upl.get("speed", 0)
            title = upl.get("title", "Unknown")[:45]

            upload_text.append("📤 ", style="yellow")
            upload_text.append(f"{title}\n", style="bold white")
            upload_text.append("   ")
            upload_text.append_text(create_progress_bar(percent, 45))
            upload_text.append(f" {percent:.1f}%", style="bold")
            if speed > 0:
                upload_text.append(f"  •  {format_speed(speed)}", style="bold cyan")

        layout["upload"].update(Panel(upload_text, title=f"[yellow]⬆ Uploading to NAS ({len(active_uploads_list)})[/yellow]", border_style="yellow"))
    else:
        # No active upload
        pending = up.get("pending", 0)
        if pending > 0:
            idle_text = Text()
            idle_text.append(f"⏳ {pending} videos waiting to upload...", style="dim yellow")
            layout["upload"].update(Panel(idle_text, title="[dim]NAS Upload[/dim]", border_style="dim"))
        else:
            idle_text = Text()
            idle_text.append("✓ No pending uploads", style="dim green")
            layout["upload"].update(Panel(idle_text, title="[dim]NAS Upload[/dim]", border_style="dim"))

    return layout


def background_fetcher(interval: float, stop_event: threading.Event):
    """Background thread that fetches data periodically."""
    while not stop_event.is_set():
        fetch_all_data()
        stop_event.wait(interval)


def main():
    parser = argparse.ArgumentParser(description="YT Downloader CLI Dashboard")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode - refresh continuously like htop")
    parser.add_argument("--interval", "-i", type=float, default=2, help="Refresh interval in seconds (default: 2)")
    args = parser.parse_args()

    if args.watch:
        # Initial fetch
        fetch_all_data()

        # Start background fetcher thread
        stop_event = threading.Event()
        fetcher_thread = threading.Thread(
            target=background_fetcher,
            args=(args.interval, stop_event),
            daemon=True
        )
        fetcher_thread.start()

        try:
            # Live display updates 4 times per second for smooth UI
            with Live(
                create_dashboard(watch_mode=True),
                console=console,
                refresh_per_second=4,
                screen=True,
            ) as live:
                while True:
                    time.sleep(0.25)
                    live.update(create_dashboard(watch_mode=True))
        except KeyboardInterrupt:
            stop_event.set()
            console.print("\n[dim]Bye![/dim]")
    else:
        # Single run mode
        fetch_all_data()
        console.print(create_dashboard(watch_mode=False))


if __name__ == "__main__":
    main()
