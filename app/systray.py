#!/usr/bin/env python3
"""
Systray indicator for YT Sync - runs as separate process to avoid GTK version conflicts.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3, GLib

import os
import sys
import signal
from pathlib import Path


class YTSyncTray:
    """System tray indicator."""

    def __init__(self, main_pid: int):
        self.main_pid = main_pid

        # Create indicator
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "yt-sync",
            "yt-sync",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        # Set icon
        icon_paths = [
            Path(__file__).parent.parent / "assets" / "icon.png",
            Path("/usr/share/icons/hicolor/256x256/apps/yt-sync.png"),
        ]

        for icon_path in icon_paths:
            if icon_path.exists():
                self.indicator.set_icon_full(str(icon_path), "YT Sync")
                break
        else:
            self.indicator.set_icon_full("emblem-downloads", "YT Sync")

        # Create menu
        menu = Gtk.Menu()

        # Show window
        show_item = Gtk.MenuItem(label="Show")
        show_item.connect("activate", self.on_show)
        menu.append(show_item)

        # Separator
        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.on_quit)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

        # Make left click also show the menu (same as right click)
        self.indicator.set_secondary_activate_target(show_item)

    def on_show(self, item):
        """Signal main process to show window."""
        try:
            os.kill(self.main_pid, signal.SIGUSR1)
        except ProcessLookupError:
            Gtk.main_quit()

    def on_quit(self, item):
        """Signal main process to quit."""
        try:
            os.kill(self.main_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        Gtk.main_quit()

    def check_parent(self):
        """Check if parent process is still alive."""
        try:
            os.kill(self.main_pid, 0)
            return True
        except ProcessLookupError:
            Gtk.main_quit()
            return False


def main():
    if len(sys.argv) < 2:
        print("Usage: systray.py <main_pid>")
        sys.exit(1)

    main_pid = int(sys.argv[1])

    tray = YTSyncTray(main_pid)

    # Check parent process every 2 seconds
    GLib.timeout_add_seconds(2, tray.check_parent)

    # Handle signals
    signal.signal(signal.SIGTERM, lambda *args: Gtk.main_quit())
    signal.signal(signal.SIGINT, lambda *args: Gtk.main_quit())

    Gtk.main()


if __name__ == "__main__":
    main()
