#!/bin/bash
# YT Sync Installation Script
# Native installation (no Docker)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="yt-sync"

echo "==================================="
echo "YT Sync Installation"
echo "==================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "⚠️  Don't run as root. Run as regular user."
    exit 1
fi

# Check dependencies
echo ""
echo "Checking dependencies..."

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install with: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python $PYTHON_VERSION found"

if ! command -v ffmpeg &> /dev/null; then
    echo "❌ ffmpeg not found. Install with: sudo apt install ffmpeg"
    exit 1
fi
echo "✓ ffmpeg found"

# Create virtual environment
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate and install dependencies
echo ""
echo "Installing Python dependencies..."
source "$SCRIPT_DIR/venv/bin/activate"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "✓ Dependencies installed"

# Create .env if not exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    echo "Creating .env from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "⚠️  Edit .env with your configuration before starting"
fi

# Create required directories
echo ""
echo "Creating directories..."
mkdir -p "$SCRIPT_DIR/downloads"
mkdir -p "$SCRIPT_DIR/data"
echo "✓ Directories created"

# Make scripts executable
chmod +x "$SCRIPT_DIR/yt-sync"
chmod +x "$SCRIPT_DIR/yt-sync-service"

# Update shebang to use venv python
echo ""
echo "Configuring scripts to use virtual environment..."
sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync"
sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync-service"
echo "✓ Scripts configured"

# Install CLI globally
echo ""
echo "Installing 'yt-sync' command globally..."
if [ -w /usr/local/bin ]; then
    ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    echo "✓ 'yt-sync' installed to /usr/local/bin/yt-sync"
else
    echo "Need sudo to install to /usr/local/bin..."
    sudo ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    echo "✓ 'yt-sync' installed to /usr/local/bin/yt-sync"
fi

# Ask about systemd service
echo ""
read -p "Install systemd service for auto-start? [y/N] " install_service

if [[ "$install_service" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Creating systemd service..."

    # Create service file from template
    SERVICE_FILE="/tmp/yt-sync.service"
    cp "$SCRIPT_DIR/yt-sync.service" "$SERVICE_FILE"

    # Replace placeholders
    sed -i "s|__USER__|$USER|g" "$SERVICE_FILE"
    sed -i "s|__GROUP__|$(id -gn)|g" "$SERVICE_FILE"
    sed -i "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SERVICE_FILE"

    sudo mv "$SERVICE_FILE" /etc/systemd/system/yt-sync.service
    sudo systemctl daemon-reload
    sudo systemctl enable yt-sync
    echo "✓ Systemd service installed and enabled"
    echo ""
    echo "Service commands:"
    echo "  sudo systemctl start yt-sync    # Start the service"
    echo "  sudo systemctl stop yt-sync     # Stop the service"
    echo "  sudo systemctl restart yt-sync  # Restart the service"
    echo "  sudo systemctl status yt-sync   # Check status"
    echo "  journalctl -u yt-sync -f        # View logs"
fi

echo ""
echo "==================================="
echo "Installation complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. Edit $SCRIPT_DIR/.env with your configuration"
echo "2. Copy your google-client.json and youtube_token.json (if using YouTube API)"
echo "3. Start the service:"
if [[ "$install_service" =~ ^[Yy]$ ]]; then
    echo "   sudo systemctl start yt-sync"
else
    echo "   $SCRIPT_DIR/yt-sync-service"
fi
echo "4. Use 'yt-sync' to monitor (watch mode is default)"
echo ""
