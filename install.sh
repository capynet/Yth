#!/bin/bash
# Tube Sync Installation Script
# Automatic installation for Linux and macOS

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="tubesync"

echo "==================================="
echo "Tube Sync Installation"
echo "==================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Don't run as root. Run as regular user."
    exit 1
fi

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [ -f /etc/debian_version ]; then
        echo "debian"
    elif [ -f /etc/fedora-release ]; then
        echo "fedora"
    elif [ -f /etc/arch-release ]; then
        echo "arch"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)
echo "Detected OS: $OS"

# Install system dependencies
install_dependencies() {
    echo ""
    echo "Installing system dependencies..."

    case $OS in
        debian)
            PACKAGES="python3 python3-pip python3-venv ffmpeg libmpv2"
            MISSING=""

            for pkg in $PACKAGES; do
                if ! dpkg -s "$pkg" &> /dev/null; then
                    MISSING="$MISSING $pkg"
                fi
            done

            if [ -n "$MISSING" ]; then
                echo "Installing:$MISSING"
                sudo apt update -qq
                sudo apt install -y $MISSING

                # Create symlink for libmpv.so.1 if needed (Flet compatibility)
                if [ -f /usr/lib/x86_64-linux-gnu/libmpv.so.2 ] && [ ! -f /usr/lib/x86_64-linux-gnu/libmpv.so.1 ]; then
                    sudo ln -sf /usr/lib/x86_64-linux-gnu/libmpv.so.2 /usr/lib/x86_64-linux-gnu/libmpv.so.1
                fi
            else
                echo "[OK] All system dependencies already installed"
            fi
            ;;
        fedora)
            PACKAGES="python3 python3-pip ffmpeg mpv-libs"
            sudo dnf install -y $PACKAGES
            ;;
        arch)
            PACKAGES="python python-pip ffmpeg mpv"
            sudo pacman -S --needed --noconfirm $PACKAGES
            ;;
        macos)
            if ! command -v brew &> /dev/null; then
                echo "Homebrew not found. Please install it first:"
                echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
                exit 1
            fi

            PACKAGES="python3 ffmpeg mpv"
            for pkg in $PACKAGES; do
                if ! brew list "$pkg" &> /dev/null; then
                    brew install "$pkg"
                fi
            done
            ;;
        *)
            echo "Unknown OS. Please install manually:"
            echo "  - Python 3.9+"
            echo "  - ffmpeg"
            echo "  - libmpv (mpv-libs)"
            read -p "Continue anyway? [y/N] " continue_install
            if [[ ! "$continue_install" =~ ^[Yy]$ ]]; then
                exit 1
            fi
            ;;
    esac
}

# Check Python version
check_python() {
    echo ""
    echo "Checking Python..."

    if ! command -v python3 &> /dev/null; then
        echo "Python 3 not found after installation. Please check your system."
        exit 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
        echo "Python 3.9+ required. Found: $PYTHON_VERSION"
        exit 1
    fi
    echo "[OK] Python $PYTHON_VERSION"
}

# Check ffmpeg
check_ffmpeg() {
    if ! command -v ffmpeg &> /dev/null; then
        echo "ffmpeg not found after installation. Please check your system."
        exit 1
    fi
    echo "[OK] ffmpeg found"
}

# Setup virtual environment
setup_venv() {
    echo ""
    echo "Setting up Python virtual environment..."

    if [ -d "$SCRIPT_DIR/venv" ]; then
        # Verify venv is valid
        if [ ! -f "$SCRIPT_DIR/venv/bin/activate" ]; then
            echo "Removing corrupted virtual environment..."
            rm -rf "$SCRIPT_DIR/venv"
        fi
    fi

    if [ ! -d "$SCRIPT_DIR/venv" ]; then
        python3 -m venv "$SCRIPT_DIR/venv"
        echo "[OK] Virtual environment created"
    else
        echo "[OK] Virtual environment already exists"
    fi
}

# Install Python dependencies
install_python_deps() {
    echo ""
    echo "Installing Python dependencies (this may take a minute)..."
    source "$SCRIPT_DIR/venv/bin/activate"
    pip install --upgrade pip -q
    pip install -r "$SCRIPT_DIR/requirements.txt" -q
    echo "[OK] Dependencies installed"
}

# Create configuration
setup_config() {
    if [ ! -f "$SCRIPT_DIR/.env" ]; then
        echo ""
        echo "Creating .env from .env.example..."
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo "[!!] Edit .env with your configuration before starting"
    fi
}

# Create directories
create_directories() {
    echo ""
    echo "Creating directories..."
    mkdir -p "$SCRIPT_DIR/downloads"
    mkdir -p "$SCRIPT_DIR/data"
    echo "[OK] Directories created"
}

# Configure scripts
configure_scripts() {
    echo ""
    echo "Configuring scripts..."

    chmod +x "$SCRIPT_DIR/tubesync"
    chmod +x "$SCRIPT_DIR/tubesync-service"
    chmod +x "$SCRIPT_DIR/tubesync-gui"

    # Update shebang to use venv python
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync"
        sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync-service"
        sed -i '' "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync-gui"
    else
        sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync"
        sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync-service"
        sed -i "1s|.*|#!$SCRIPT_DIR/venv/bin/python3|" "$SCRIPT_DIR/tubesync-gui"
    fi
    echo "[OK] Scripts configured"
}

# Install commands globally
install_global_commands() {
    echo ""
    echo "Installing commands globally..."

    if [ -w /usr/local/bin ]; then
        ln -sf "$SCRIPT_DIR/tubesync" /usr/local/bin/tubesync
        ln -sf "$SCRIPT_DIR/tubesync-gui" /usr/local/bin/tubesync-gui
        echo "[OK] Commands installed to /usr/local/bin/"
    else
        echo "Need sudo to install to /usr/local/bin..."
        sudo ln -sf "$SCRIPT_DIR/tubesync" /usr/local/bin/tubesync
        sudo ln -sf "$SCRIPT_DIR/tubesync-gui" /usr/local/bin/tubesync-gui
        echo "[OK] Commands installed to /usr/local/bin/"
    fi
}

# Setup systemd service (Linux only)
setup_systemd() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        return
    fi

    echo ""
    read -p "Install systemd service for auto-start? [y/N] " install_service

    if [[ "$install_service" =~ ^[Yy]$ ]]; then
        echo ""
        echo "Creating systemd service..."

        SERVICE_FILE="/tmp/tubesync.service"
        cp "$SCRIPT_DIR/tubesync.service" "$SERVICE_FILE"

        sed -i "s|__USER__|$USER|g" "$SERVICE_FILE"
        sed -i "s|__GROUP__|$(id -gn)|g" "$SERVICE_FILE"
        sed -i "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SERVICE_FILE"

        sudo mv "$SERVICE_FILE" /etc/systemd/system/tubesync.service
        sudo systemctl daemon-reload
        sudo systemctl enable tubesync
        echo "[OK] Systemd service installed and enabled"
        echo ""
        echo "Service commands:"
        echo "  sudo systemctl start tubesync    # Start the service"
        echo "  sudo systemctl stop tubesync     # Stop the service"
        echo "  sudo systemctl restart tubesync  # Restart the service"
        echo "  sudo systemctl status tubesync   # Check status"
        echo "  journalctl -u tubesync -f        # View logs"
        SYSTEMD_INSTALLED=true
    fi
}

# Print completion message
print_completion() {
    echo ""
    echo "==================================="
    echo "Installation complete!"
    echo "==================================="
    echo ""
    echo "Available commands:"
    echo "  tubesync-service    Start the background service"
    echo "  tubesync            CLI dashboard (terminal)"
    echo "  tubesync-gui        Desktop GUI application"
    echo ""
    echo "Quick start:"
    echo "  1. Edit $SCRIPT_DIR/.env with your NAS configuration"
    echo "  2. (Optional) Copy google-client.json for YouTube API"
    echo "  3. Start the service:"
    if [[ "$SYSTEMD_INSTALLED" == "true" ]]; then
        echo "     sudo systemctl start tubesync"
    else
        echo "     tubesync-service &"
    fi
    echo "  4. Monitor with CLI: tubesync"
    echo "     Or use the GUI: tubesync-gui"
    echo ""
}

# Main installation flow
main() {
    install_dependencies
    check_python
    check_ffmpeg
    setup_venv
    install_python_deps
    setup_config
    create_directories
    configure_scripts
    install_global_commands
    setup_systemd
    print_completion
}

main
