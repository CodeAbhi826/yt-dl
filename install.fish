#!/usr/bin/env fish
# yt-dl Installation Script
# Run: fish install.fish

set PROJECT_DIR /mnt/storage/yt-dl
set INSTALL_DIR $HOME/.local/share/yt-dl
set CONFIG_DIR $HOME/.config/systemd/user
set DESKTOP_DIR $HOME/.local/share/applications
set BRAVE_EXT $HOME/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl

echo "=== yt-dl Installer ==="
echo "Project: $PROJECT_DIR"
echo "Install: $INSTALL_DIR"
echo ""

# Create directories
mkdir -p $INSTALL_DIR/src
mkdir -p $INSTALL_DIR/data
mkdir -p $CONFIG_DIR
mkdir -p $DESKTOP_DIR
mkdir -p $BRAVE_EXT/icons

# Copy source files
cp $PROJECT_DIR/src/*.py $INSTALL_DIR/src/

# Copy extension
cp $PROJECT_DIR/extension/* $BRAVE_EXT/
cp $PROJECT_DIR/extension/icons/* $BRAVE_EXT/icons/ 2>/dev/null; or true

# Copy config files
cp $PROJECT_DIR/config/yt-dl.service $CONFIG_DIR/
cp $PROJECT_DIR/config/yt-dl.desktop $DESKTOP_DIR/

# Copy handler
cp $PROJECT_DIR/yt-dl-handler.sh $INSTALL_DIR/
chmod +x $INSTALL_DIR/yt-dl-handler.sh

# Create data directories
mkdir -p $INSTALL_DIR/data
mkdir -p /mnt/storage/YouTube

# Initialize database and config
python3 $INSTALL_DIR/src/app.py --init-only 2>/dev/null; or true

# Reload systemd
systemctl --user daemon-reload

# Enable service
systemctl --user enable yt-dl

echo ""
echo "=== Installation Complete ==="
echo "Start daemon: systemctl --user start yt-dl"
echo "Open dashboard: http://localhost:5000"
echo "Load extension: brave://extensions/ -> Load unpacked -> $BRAVE_EXT"
echo ""
echo "Files installed:"
echo "  Daemon: $INSTALL_DIR/src/app.py"
echo "  Worker: $INSTALL_DIR/src/worker.py"
echo "  Notifications: handled by browser extension"
echo "  DB/Config: $INSTALL_DIR/data/"
echo "  Extension: $BRAVE_EXT/"
