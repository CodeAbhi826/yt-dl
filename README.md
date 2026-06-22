# yt-dl — Zero-Friction YouTube Downloader

A complete YouTube downloading system for Arch Linux + KDE Plasma 6.7, featuring a Flask daemon, Brave browser extension, KDE notifications, and a dark-themed web dashboard.

---

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [File Locations](#file-locations)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Brave Browser  │────▶│  Flask Daemon   │────▶│   yt-dlp +      │
│  (Extension)    │     │  (localhost:5000)│     │   aria2c        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  Context Menu   │     │  KDE Plasma     │
│  "Download with  │     │  Notifications  │
│   yt-dl"        │     │                 │
└─────────────────┘     └─────────────────┘
```

**Components:**
- **Flask Daemon** (`app.py`) — REST API + web dashboard
- **Download Worker** (`worker.py`) — yt-dlp execution, queue management
- **Notification Manager** (`notifications.py`) — KDE D-Bus notifications
- **Models** (`models.py`) — SQLite DB + config helpers
- **Brave Extension** — Context menu + popup quality selector
- **Handler Script** — Right-click "Open With" integration

---

## Project Structure

```
/mnt/storage/yt-dl/                    # Project root (development)
├── README.md                          # This file
├── install.fish                       # One-command installer
├── yt-dl-handler.sh                   # Right-click handler
├── src/                               # Python source
│   ├── app.py                         # Flask routes + HTML templates
│   ├── worker.py                      # yt-dlp download execution
│   ├── notifications.py               # KDE Plasma 6.7 D-Bus notifications
│   └── models.py                      # DB schema + config management
├── extension/                         # Brave extension (unpacked)
│   ├── manifest.json                  # Extension manifest v3
│   ├── background.js                  # Service worker (context menu)
│   ├── popup.html                     # Quality selector popup
│   ├── popup.js                       # Popup logic
│   └── icons/                         # Extension icons
│       ├── icon16.png
│       ├── icon48.png
│       └── icon128.png
└── config/                            # System config files
    ├── yt-dl.service                  # systemd user service
    └── yt-dl.desktop                  # Desktop entry

~/.local/share/yt-dl/                # Runtime (after install)
├── src/                             # Symlinked/copied from project
├── data/                            # Runtime data
│   ├── yt-dl.db                     # SQLite database
│   ├── config.json                  # User settings
│   └── daemon.log                   # Application logs
└── yt-dl-handler.sh                 # Right-click script

~/.config/systemd/user/yt-dl.service # systemd service
~/.local/share/applications/yt-dl.desktop # Desktop entry
~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/ # Extension
```

---

## File Locations

| File | Source | Destination | Purpose |
|------|--------|-------------|---------|
| `app.py` | `/mnt/storage/yt-dl/src/` | `~/.local/share/yt-dl/src/` | Flask daemon routes |
| `worker.py` | `/mnt/storage/yt-dl/src/` | `~/.local/share/yt-dl/src/` | Download execution |
| `notifications.py` | `/mnt/storage/yt-dl/src/` | `~/.local/share/yt-dl/src/` | KDE notifications |
| `models.py` | `/mnt/storage/yt-dl/src/` | `~/.local/share/yt-dl/src/` | DB + config |
| `manifest.json` | `/mnt/storage/yt-dl/extension/` | `~/.config/BraveSoftware/.../yt-dl/` | Extension manifest |
| `background.js` | `/mnt/storage/yt-dl/extension/` | `~/.config/BraveSoftware/.../yt-dl/` | Context menu |
| `popup.html/js` | `/mnt/storage/yt-dl/extension/` | `~/.config/BraveSoftware/.../yt-dl/` | Quality popup |
| `yt-dl.service` | `/mnt/storage/yt-dl/config/` | `~/.config/systemd/user/` | systemd service |
| `yt-dl.desktop` | `/mnt/storage/yt-dl/config/` | `~/.local/share/applications/` | Desktop entry |
| `yt-dl-handler.sh` | `/mnt/storage/yt-dl/` | `~/.local/share/yt-dl/` | Right-click handler |
| `yt-dl.db` | Created at runtime | `~/.local/share/yt-dl/data/` | Download history |
| `config.json` | Created at runtime | `~/.local/share/yt-dl/data/` | User settings |

---

## Installation

### Prerequisites

```bash
# Arch Linux
sudo pacman -S python python-flask python-pillow yt-dlp aria2c brave-browser

# KDE Plasma notifications (usually pre-installed)
pip install --user dbus-python
```

### One-Command Install

```fish
cd /mnt/storage/yt-dl
fish install.fish
```

### Manual Install

```fish
# 1. Copy source
mkdir -p ~/.local/share/yt-dl/src
cp /mnt/storage/yt-dl/src/*.py ~/.local/share/yt-dl/src/

# 2. Copy extension
mkdir -p ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl
cp /mnt/storage/yt-dl/extension/* ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/

# 3. Copy configs
mkdir -p ~/.config/systemd/user
cp /mnt/storage/yt-dl/config/yt-dl.service ~/.config/systemd/user/
cp /mnt/storage/yt-dl/config/yt-dl.desktop ~/.local/share/applications/

# 4. Copy handler
cp /mnt/storage/yt-dl/yt-dl-handler.sh ~/.local/share/yt-dl/
chmod +x ~/.local/share/yt-dl/yt-dl-handler.sh

# 5. Enable service
systemctl --user daemon-reload
systemctl --user enable yt-dl
systemctl --user start yt-dl

# 6. Load extension in Brave
# brave://extensions/ → Developer mode → Load unpacked → ~/.config/BraveSoftware/.../yt-dl/
```

---

## Configuration

Settings are stored in `~/.local/share/yt-dl/config.json`:

```json
{
  "download_dir": "/mnt/storage/YouTube",
  "default_quality": "720p",
  "concurrent_limit": 3,
  "theme": "dark",
  "embed_metadata": true,
  "embed_thumbnail": true,
  "embed_chapters": true,
  "embed_subs": true
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `download_dir` | `/mnt/storage/YouTube` | Where videos are saved |
| `default_quality` | `720p` | Default download quality |
| `concurrent_limit` | `3` | Max simultaneous downloads |
| `theme` | `dark` | Dashboard theme (dark/light) |
| `embed_metadata` | `true` | Embed title/description |
| `embed_thumbnail` | `true` | Embed thumbnail as cover art |
| `embed_chapters` | `true` | Embed chapter markers |
| `embed_subs` | `true` | Embed English subtitles |

Change via dashboard (`http://localhost:5000/settings`) or API.

---

## Usage

### Method 1: Brave Extension (Recommended)

1. **Right-click** any YouTube link → **"Download with yt-dl"**
2. **Click extension icon** on a YouTube video page
3. **Select quality** in popup (144p to 2160p + Audio)

### Method 2: Dashboard

1. Open `http://localhost:5000`
2. Use **Queue** page to monitor/cancel downloads
3. Use **Stats** page to view history (with reset button)
4. Use **Settings** page to change download location/quality

### Method 3: Handler Script

```bash
# Right-click handler (configured in KDE file associations)
~/.local/share/yt-dl/yt-dl-handler.sh "https://www.youtube.com/watch?v=..."
```

### Method 4: API (curl)

```bash
curl -X POST http://localhost:5000/api/add \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtube.com/watch?v=...","quality":"1080p"}'
```

---

## API Reference

### Queue Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/queue` | GET | List all downloads |
| `POST /api/add` | POST | Add new download |
| `POST /api/jobs/<id>/retry` | POST | Retry failed job |
| `POST /api/jobs/<id>/cancel` | POST | Cancel active job |
| `DELETE /api/jobs/<id>` | DELETE | Delete job + file |
| `POST /api/bulk/delete` | POST | Bulk delete (body: `{ids: [...]}`) |
| `POST /api/bulk/retry` | POST | Bulk retry (body: `{ids: [...]}`) |

### Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/settings` | GET | Get current config |
| `PUT /api/settings` | PUT | Update config |
| `POST /api/settings/reset` | POST | Reset to defaults |

### Stats & Logs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/stats` | GET | Download statistics |
| `POST /api/stats/reset` | POST | Clear all history |
| `GET /api/logs` | GET | Recent log lines |
| `GET /api/logs/stream` | GET | SSE live log stream |
| `GET /api/search?q=...` | GET | Search downloads |

### Pages

| Route | Description |
|-------|-------------|
| `/` | Queue dashboard |
| `/settings` | Settings page |
| `/stats` | Statistics page |
| `/logs` | Live logs page |
| `/search` | Search page |

---

## Troubleshooting

### Daemon won't start

```bash
# Check logs
journalctl --user -u yt-dl -f

# Check syntax
python3 -m py_compile ~/.local/share/yt-dl/src/app.py

# Check port conflict
lsof -i :5000
```

### Extension not loading

```bash
# Check extension files exist
ls ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/

# Check icons exist
ls ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/icons/

# Reload in brave://extensions/
```

### No notifications

```bash
# Check D-Bus
qdbus org.freedesktop.Notifications /org/freedesktop/Notifications org.freedesktop.Notifications.GetCapabilities

# Check KDE notification settings
systemsettings5 kcm_notifications
```

### Downloads not starting

```bash
# Check yt-dlp installed
which yt-dlp

# Check aria2c installed
which aria2c

# Check download directory writable
touch /mnt/storage/YouTube/test && rm /mnt/storage/YouTube/test
```

### Database errors

```bash
# Reset database (WARNING: loses history)
rm ~/.local/share/yt-dl/data/yt-dl.db
systemctl --user restart yt-dl
```

---

## Design Spec

- **Background**: `#0a0a0a` (dark), `#f5f5f5` (light)
- **Cards**: `#141414` (dark), `#ffffff` (light)
- **Accent**: `#ff2d20` (red)
- **Success**: `#22c55e` (green)
- **Font**: Inter, 11px uppercase labels (letter-spacing: 2px)
- **Border radius**: 16px cards, 12px thumbnails
- **Progress bars**: 6px height, `#ff2d20` fill

---

## License

Personal use only. Built for Arch Linux + KDE Plasma 6.7 + Brave.
