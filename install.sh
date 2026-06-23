#!/usr/bin/env bash
# yt-dl Installation Script
# Usage: bash install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.local/share/yt-dl"
SERVICE_DIR="${HOME}/.config/systemd/user"
DESKTOP_DIR="${HOME}/.local/share/applications"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[-]${NC} $1"; }
step()  { echo ""; echo -e "${YELLOW}==>${NC} $1"; }

# ── Welcome ──────────────────────────────────────────────
echo ""
echo "  yt-dl Installer"
echo "  ${SCRIPT_DIR}"
echo ""

# ── Dependency Check ─────────────────────────────────────
step "Checking dependencies"

MISSING=""

if ! command -v python3 &>/dev/null; then
  MISSING+=" python3"
fi

if ! command -v yt-dlp &>/dev/null; then
  MISSING+=" yt-dlp"
fi

if ! python3 -c "import flask" 2>/dev/null; then
  MISSING+=" python-flask"
fi

if ! python3 -c "import dbus" 2>/dev/null; then
  MISSING+=" python-dbus"
fi

if [ -n "$MISSING" ]; then
  warn "Missing dependencies:$MISSING"
  echo "  Install them with:"
  echo "    sudo pacman -S python python-pip yt-dlp"
  echo "    pip install --user flask dbus-python"
  echo ""
  read -rp "  Attempt to install missing packages? [Y/n] " yn
  yn="${yn:-Y}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    if command -v pacman &>/dev/null; then
      sudo pacman -S --noconfirm python python-pip yt-dlp 2>/dev/null || true
    elif command -v apt &>/dev/null; then
      sudo apt update && sudo apt install -y python3 python3-pip yt-dlp 2>/dev/null || true
    elif command -v dnf &>/dev/null; then
      sudo dnf install -y python3 python3-pip yt-dlp 2>/dev/null || true
    else
      warn "Unknown package manager. Install manually."
    fi
    pip install --user flask dbus-python 2>/dev/null || true
    info "Dependencies installed"
  else
    warn "Skipping dependency installation"
  fi
else
  info "All dependencies found"
fi

# ── Download Directory ────────────────────────────────────
step "Download directory"

DEFAULT_DL_DIR="${HOME}/Downloads/yt-dl"
read -rp "  Download location [${DEFAULT_DL_DIR}]: " DL_DIR
DL_DIR="${DL_DIR:-$DEFAULT_DL_DIR}"
mkdir -p "${DL_DIR}"
info "Downloads will be saved to ${DL_DIR}"

# ── Create Directories ────────────────────────────────────
step "Creating directories"

mkdir -p "${INSTALL_DIR}/src"
mkdir -p "${INSTALL_DIR}/data"
mkdir -p "${SERVICE_DIR}"
mkdir -p "${DESKTOP_DIR}"

# ── Copy Source Files ─────────────────────────────────────
step "Copying source files"

cp -r "${SCRIPT_DIR}/src/"*.py "${INSTALL_DIR}/src/"
info "Source files -> ${INSTALL_DIR}/src/"

# Copy templates and static
if [ -d "${SCRIPT_DIR}/src/templates" ]; then
  cp -r "${SCRIPT_DIR}/src/templates" "${INSTALL_DIR}/src/"
  info "Templates -> ${INSTALL_DIR}/src/templates/"
fi
if [ -d "${SCRIPT_DIR}/src/static" ]; then
  cp -r "${SCRIPT_DIR}/src/static" "${INSTALL_DIR}/src/"
  info "Static assets -> ${INSTALL_DIR}/src/static/"
fi

# ── Copy Extension ────────────────────────────────────────
step "Browser extension"

EXT_DIR="${INSTALL_DIR}/extension"
mkdir -p "${EXT_DIR}/icons"
cp "${SCRIPT_DIR}/extension/"*.html "${EXT_DIR}/" 2>/dev/null || true
cp "${SCRIPT_DIR}/extension/"*.js "${EXT_DIR}/" 2>/dev/null || true
cp "${SCRIPT_DIR}/extension/manifest.json "${EXT_DIR}/" 2>/dev/null || true
cp "${SCRIPT_DIR}/extension/icons/"* "${EXT_DIR}/icons/" 2>/dev/null || true
info "Extension -> ${EXT_DIR}"

# Detect browser
BROWSER=""
for b in chromium google-chrome-stable google-chrome brave-browser brave vivaldi edge; do
  if command -v "$b" &>/dev/null; then
    BROWSER="$b"
    break
  fi
done

# ── Install systemd Service ───────────────────────────────
step "Installing systemd service"

cat > "${SERVICE_DIR}/yt-dl.service" << EOF
[Unit]
Description=yt-dl — YouTube download daemon
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/src/app.py
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH=${INSTALL_DIR}/src

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload 2>/dev/null || true
systemctl --user enable yt-dl 2>/dev/null || true
info "systemd service installed: yt-dl"

# ── Install Desktop Entry ─────────────────────────────────
step "Installing desktop entry"

cat > "${DESKTOP_DIR}/yt-dl.desktop" << EOF
[Desktop Entry]
Name=yt-dl
Comment=YouTube download daemon
Exec=${INSTALL_DIR}/src/app.py
Icon=applications-multimedia
Type=Application
Categories=Network;FileTransfer;
Terminal=false
EOF

# ── Install Handler Script ────────────────────────────────
step "Installing handler script"

cp "${SCRIPT_DIR}/yt-dl-handler.sh" "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/yt-dl-handler.sh"
info "Handler -> ${INSTALL_DIR}/yt-dl-handler.sh"

# ── Generate Config ───────────────────────────────────────
step "Generating configuration"

python3 -c "
import json, os
cfg_path = os.path.expanduser('${INSTALL_DIR}/data/config.json')
if not os.path.exists(cfg_path):
    cfg = {
        'download_dir': '${DL_DIR}',
        'concurrent_limit': 3,
        'theme': 'dark',
        'embed_metadata': True,
        'embed_thumbnail': True,
        'embed_chapters': True,
        'embed_subs': True
    }
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, 'w') as f:
        json.dump(cfg, f, indent=2)
    print('  Config created')
else:
    print('  Config exists, skipping')
"

# ── Summary ───────────────────────────────────────────────
step "Installation complete"

echo ""
echo "  Start the daemon:"
echo "    systemctl --user start yt-dl"
echo ""
echo "  Open the dashboard:"
echo "    http://localhost:5000"
echo ""
echo "  Load the extension:"
if [ -n "$BROWSER" ]; then
  echo "    Open ${BROWSER} and navigate to chrome://extensions"
else
  echo "    Open your Chromium-based browser (Chrome, Brave, Edge) and navigate to:"
  echo "    chrome://extensions"
fi
echo "    Enable Developer mode"
echo "    Click 'Load unpacked' and select:"
echo "    ${EXT_DIR}"
echo ""
echo "  Files installed:"
echo "    Daemon:    ${INSTALL_DIR}/"
echo "    Extension: ${EXT_DIR}/"
echo "    Downloads: ${DL_DIR}"
echo "    Service:   ${SERVICE_DIR}/yt-dl.service"
echo ""
