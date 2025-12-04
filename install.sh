#!/bin/bash
# YT Downloader Installation Script
# Run this script after cloning the repository

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="yt-downloader"

echo "==================================="
echo "YT Downloader Installation"
echo "==================================="

# Check dependencies
echo ""
echo "Checking dependencies..."

if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker first."
    exit 1
fi
echo "✓ Docker found"

if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose first."
    exit 1
fi
echo "✓ Docker Compose found"

# Create .env if not exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    echo "Creating .env from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "⚠️  Please edit .env with your configuration before starting"
fi

# Create required directories
echo ""
echo "Creating directories..."
mkdir -p "$SCRIPT_DIR/downloads"
mkdir -p "$SCRIPT_DIR/data"
echo "✓ Directories created"

# Make yt-sync script executable
chmod +x "$SCRIPT_DIR/yt-sync"

# Install CLI globally
echo ""
echo "Installing 'yt-sync' command globally..."
if [ -w /usr/local/bin ]; then
    ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    echo "✓ 'yt-sync' command installed to /usr/local/bin/yt-sync"
else
    echo "Need sudo to install to /usr/local/bin..."
    sudo ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    echo "✓ 'yt-sync' command installed to /usr/local/bin/yt-sync"
fi

# Ask about systemd service
echo ""
read -p "Install systemd service for auto-start? [y/N] " install_service

if [[ "$install_service" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Creating systemd service..."

    # Create systemd service file
    cat > /tmp/yt-downloader.service << EOF
[Unit]
Description=YT Downloader Service
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

    sudo mv /tmp/yt-downloader.service /etc/systemd/system/yt-downloader.service
    sudo systemctl daemon-reload
    sudo systemctl enable yt-downloader
    echo "✓ Systemd service installed and enabled"
    echo ""
    echo "Service commands:"
    echo "  sudo systemctl start yt-downloader   # Start the service"
    echo "  sudo systemctl stop yt-downloader    # Stop the service"
    echo "  sudo systemctl restart yt-downloader # Restart the service"
    echo "  sudo systemctl status yt-downloader  # Check status"
fi

# Build Docker image
echo ""
read -p "Build Docker image now? [Y/n] " build_now

if [[ ! "$build_now" =~ ^[Nn]$ ]]; then
    echo ""
    echo "Building Docker image..."
    cd "$SCRIPT_DIR"
    docker compose build
    echo "✓ Docker image built"
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
    echo "   sudo systemctl start yt-downloader"
else
    echo "   cd $SCRIPT_DIR && docker compose up -d"
fi
echo "4. Use 'yt-sync' to monitor in real-time (watch mode is default)"
echo ""
