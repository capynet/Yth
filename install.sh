#!/bin/bash
# YT Sync Installation Script
# Native installation for Linux and macOS

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="yt-sync"

# Detect OS for sed compatibility
if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_INPLACE="sed -i ''"
else
    SED_INPLACE="sed -i"
fi

echo "==================================="
echo "YT Sync Installation"
echo "==================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Don't run as root. Run as regular user."
    exit 1
fi

# Check dependencies
echo ""
echo "Checking dependencies..."

if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Install with: brew install python3"
    else
        echo "Install with: sudo apt install python3 python3-pip python3-venv"
    fi
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    echo "Python 3.9+ required. Found: $PYTHON_VERSION"
    exit 1
fi
echo "[OK] Python $PYTHON_VERSION"

if ! command -v ffmpeg &> /dev/null; then
    echo "ffmpeg not found."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Install with: brew install ffmpeg"
    else
        echo "Install with: sudo apt install ffmpeg"
    fi
    exit 1
fi
echo "[OK] ffmpeg found"

# Create virtual environment
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment already exists"
fi

# Activate and install dependencies
echo ""
echo "Installing Python dependencies (this may take a minute)..."
source "$SCRIPT_DIR/venv/bin/activate"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "[OK] Dependencies installed"

# Create .env if not exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    echo "Creating .env from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "[!!] Edit .env with your configuration before starting"
fi

# Create required directories
echo ""
echo "Creating directories..."
mkdir -p "$SCRIPT_DIR/downloads"
mkdir -p "$SCRIPT_DIR/data"
echo "[OK] Directories created"

# Make scripts executable
chmod +x "$SCRIPT_DIR/yt-sync"
chmod +x "$SCRIPT_DIR/yt-sync-service"
chmod +x "$SCRIPT_DIR/yt-sync-gui"

# Update shebang to use venv python (macOS/Linux compatible)
echo ""
echo "Configuring scripts to use virtual environment..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync"
    sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync-service"
    sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync-gui"
else
    sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync"
    sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync-service"
    sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/yt-sync-gui"
fi
echo "[OK] Scripts configured"

# Install commands globally
echo ""
echo "Installing commands globally..."
if [ -w /usr/local/bin ]; then
    ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    ln -sf "$SCRIPT_DIR/yt-sync-gui" /usr/local/bin/yt-sync-gui
    echo "[OK] Commands installed to /usr/local/bin/"
else
    echo "Need sudo to install to /usr/local/bin..."
    sudo ln -sf "$SCRIPT_DIR/yt-sync" /usr/local/bin/yt-sync
    sudo ln -sf "$SCRIPT_DIR/yt-sync-gui" /usr/local/bin/yt-sync-gui
    echo "[OK] Commands installed to /usr/local/bin/"
fi

# Ask about systemd service (Linux only)
if [[ "$OSTYPE" != "darwin"* ]]; then
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
        echo "[OK] Systemd service installed and enabled"
        echo ""
        echo "Service commands:"
        echo "  sudo systemctl start yt-sync    # Start the service"
        echo "  sudo systemctl stop yt-sync     # Stop the service"
        echo "  sudo systemctl restart yt-sync  # Restart the service"
        echo "  sudo systemctl status yt-sync   # Check status"
        echo "  journalctl -u yt-sync -f        # View logs"
    fi
fi

echo ""
echo "==================================="
echo "Installation complete!"
echo "==================================="
echo ""
echo "Available commands:"
echo "  yt-sync-service    Start the background service"
echo "  yt-sync            CLI dashboard (terminal)"
echo "  yt-sync-gui        Desktop GUI application"
echo ""
echo "Quick start:"
echo "  1. Edit $SCRIPT_DIR/.env with your NAS configuration"
echo "  2. (Optional) Copy google-client.json for YouTube API"
echo "  3. Start the service:"
if [[ "$OSTYPE" != "darwin"* ]] && [[ "$install_service" =~ ^[Yy]$ ]]; then
    echo "     sudo systemctl start yt-sync"
else
    echo "     yt-sync-service &"
fi
echo "  4. Monitor with CLI: yt-sync"
echo "     Or use the GUI: yt-sync-gui"
echo ""
