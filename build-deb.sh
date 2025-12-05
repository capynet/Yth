#!/bin/bash
# Build .deb package for Tube Sync
# Usage: ./build-deb.sh [version]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${VERSION:-${1:-0.1.0}}"
PACKAGE_NAME="tubesync"
ARCH="amd64"
BUILD_DIR="$SCRIPT_DIR/build/deb"
PACKAGE_DIR="$BUILD_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}"
INSTALL_DIR="/opt/tubesync"

echo "==================================="
echo "Building Tube Sync .deb package"
echo "Version: $VERSION"
echo "==================================="

# Check dependencies
if ! command -v dpkg-deb &> /dev/null; then
    echo "dpkg-deb not found. Install with: sudo apt install dpkg"
    exit 1
fi

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$PACKAGE_DIR"

# Create directory structure
mkdir -p "$PACKAGE_DIR/DEBIAN"
mkdir -p "$PACKAGE_DIR$INSTALL_DIR/app"
mkdir -p "$PACKAGE_DIR$INSTALL_DIR/venv"
mkdir -p "$PACKAGE_DIR/usr/local/bin"
mkdir -p "$PACKAGE_DIR/usr/share/applications"
mkdir -p "$PACKAGE_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$PACKAGE_DIR/usr/share/icons/hicolor/scalable/apps"

echo "Creating virtual environment for package..."
# Use --system-site-packages to access PyGObject from system
python3 -m venv --system-site-packages "$PACKAGE_DIR$INSTALL_DIR/venv"
source "$PACKAGE_DIR$INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip -q
# Install only pip packages (PyGObject comes from system)
pip install yt-dlp smbprotocol google-api-python-client google-auth-oauthlib pytz aiosqlite sqlalchemy -q
deactivate

# Make venv world-readable so non-root users can run the GUI
chmod -R a+rX "$PACKAGE_DIR$INSTALL_DIR/venv"

echo "Copying application files..."
# Copy app files
cp -r "$SCRIPT_DIR/app/"* "$PACKAGE_DIR$INSTALL_DIR/app/"
cp "$SCRIPT_DIR/requirements.txt" "$PACKAGE_DIR$INSTALL_DIR/"
cp "$SCRIPT_DIR/oauth_setup.py" "$PACKAGE_DIR$INSTALL_DIR/"

# Create GUI wrapper script
cat > "$PACKAGE_DIR$INSTALL_DIR/tubesync-gui" << 'SCRIPT'
#!/opt/tubesync/venv/bin/python3
"""
Tube Sync GUI - Desktop application for monitoring and configuring Tube Sync.
"""

import sys
import os

# Add the app directory to path
sys.path.insert(0, '/opt/tubesync')
os.chdir('/opt/tubesync')

from app.gui_gtk import main

if __name__ == "__main__":
    main()
SCRIPT

chmod +x "$PACKAGE_DIR$INSTALL_DIR/tubesync-gui"

# Create symlink in /usr/local/bin
ln -sf "$INSTALL_DIR/tubesync-gui" "$PACKAGE_DIR/usr/local/bin/tubesync-gui"
ln -sf "$INSTALL_DIR/tubesync-gui" "$PACKAGE_DIR/usr/local/bin/tubesync"

# Create desktop entry
cat > "$PACKAGE_DIR/usr/share/applications/tubesync.desktop" << 'DESKTOP'
[Desktop Entry]
Name=Tube Sync
Comment=YouTube Video Downloader with SMB/FTP Support
Exec=/usr/local/bin/tubesync-gui
Icon=tubesync
Terminal=false
Type=Application
Categories=AudioVideo;Network;
Keywords=youtube;download;video;
StartupWMClass=com.tubesync.app
DESKTOP

# Copy icons from assets
if [ -f "$SCRIPT_DIR/assets/icon.svg" ]; then
    cp "$SCRIPT_DIR/assets/icon.svg" "$PACKAGE_DIR/usr/share/icons/hicolor/scalable/apps/tubesync.svg"
fi
if [ -f "$SCRIPT_DIR/assets/icon.png" ]; then
    cp "$SCRIPT_DIR/assets/icon.png" "$PACKAGE_DIR/usr/share/icons/hicolor/256x256/apps/tubesync.png"
    mkdir -p "$PACKAGE_DIR$INSTALL_DIR/assets"
    cp "$SCRIPT_DIR/assets/icon.png" "$PACKAGE_DIR$INSTALL_DIR/assets/icon.png"
fi

# Create DEBIAN control file
cat > "$PACKAGE_DIR/DEBIAN/control" << CONTROL
Package: $PACKAGE_NAME
Version: $VERSION
Section: video
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.10), python3-venv, python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, libadwaita-1-0, gir1.2-ayatanaappindicator3-0.1, ffmpeg
Maintainer: Capynet <capynet@users.noreply.github.com>
Description: YouTube Video Downloader with SMB/FTP Support
 Automatic YouTube video downloader with SMB/FTP upload support.
 Modern GTK4/Libadwaita desktop application with system tray.
 .
 Features:
  - Auto-downloads videos from YouTube subscriptions
  - Parallel downloads and uploads via SMB/FTP
  - Separates Shorts into dedicated folder
  - System tray with background operation
  - YouTube API quota tracking
Homepage: https://github.com/capynet/tubesync
CONTROL

# Create postinst script
cat > "$PACKAGE_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e

# Update icon cache
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
fi

# Update desktop database
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

echo ""
echo "==================================="
echo "Tube Sync installed successfully!"
echo "==================================="
echo ""
echo "To start:"
echo "  1. Run: tubesync-gui (or find 'Tube Sync' in your applications)"
echo "  2. Configure SMB/FTP settings in the app"
echo "  3. Set up YouTube API credentials if needed"
echo ""
echo "Data is stored in: ~/.local/share/tubesync/"
echo "Config is stored in: ~/.config/tubesync/"
echo ""
POSTINST
chmod +x "$PACKAGE_DIR/DEBIAN/postinst"

# Create postrm script
cat > "$PACKAGE_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
set -e

case "$1" in
    remove|purge)
        # Update icon cache
        if command -v gtk-update-icon-cache &> /dev/null; then
            gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
        fi

        # Update desktop database
        if command -v update-desktop-database &> /dev/null; then
            update-desktop-database /usr/share/applications 2>/dev/null || true
        fi
        ;;
esac

if [ "$1" = "purge" ]; then
    # Remove app directory
    rm -rf /opt/tubesync

    echo ""
    echo "Note: User data preserved in ~/.local/share/tubesync/"
    echo "      and ~/.config/tubesync/"
    echo "Remove manually if no longer needed."
    echo ""
fi
POSTRM
chmod +x "$PACKAGE_DIR/DEBIAN/postrm"

# Calculate installed size
INSTALLED_SIZE=$(du -sk "$PACKAGE_DIR" | cut -f1)
echo "Installed-Size: $INSTALLED_SIZE" >> "$PACKAGE_DIR/DEBIAN/control"

# Build the package
echo ""
echo "Building .deb package..."
dpkg-deb --build --root-owner-group "$PACKAGE_DIR"

# Move to dist folder
mkdir -p "$SCRIPT_DIR/dist"
mv "$BUILD_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb" "$SCRIPT_DIR/dist/"

# Cleanup
rm -rf "$BUILD_DIR"

echo ""
echo "==================================="
echo "Package built successfully!"
echo "==================================="
echo ""
echo "Output: $SCRIPT_DIR/dist/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
echo ""
echo "Install with:"
echo "  sudo dpkg -i dist/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
echo "  sudo apt-get install -f  # Install dependencies if needed"
echo ""
