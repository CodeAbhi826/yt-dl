<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg">
  <img alt="yt-dl" src="https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg" width="80">
</picture>

# yt-dl

[![GitHub release](https://img.shields.io/github/v/release/CodeAbhi826/yt-dl?color=ff4d36&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/releases)
[![GitHub stars](https://img.shields.io/github/stars/CodeAbhi826/yt-dl?color=ffcb47&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/stargazers)
[![GitHub downloads](https://img.shields.io/github/downloads/CodeAbhi826/yt-dl/total?color=3ea6ff&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/releases)

yt-dl is a self-hosted, zero-friction YouTube downloader for Linux desktop users. It pairs a lightweight Flask daemon with a Brave browser extension — right-click any YouTube link, select quality, and the video downloads automatically. A dark-themed web dashboard shows real-time progress, queue management, and download history.

Built for **KDE Plasma 6** with native D-Bus notifications, custom toast popups via the browser extension, and a compact utility aesthetic throughout.

---

## Features

- **Right-click → Download** — Brave extension adds "Download with yt-dl" to YouTube link context menus
- **Quality selector** — Extension popup lets you choose 144p to 2160p, plus audio-only MP3
- **Real-time dashboard** — SSE-powered live queue with progress bars, speed, ETA per download
- **Dark KDE-native UI** — Cards, progress bars, stats, and logs in a Plasma 6 Breeze Dark aesthetic
- **Cross-platform notifications** — Custom KDE-style toast popups when Brave is open; D-Bus fallback when it's not
- **Concurrent downloads** — Configurable parallel downloads (default 3)
- **Download history & stats** — 7-day bar chart, success rate, total data downloaded, per-file metadata
- **Embed options** — Thumbnail, metadata, chapters, and subtitles embedded automatically
- **Live logs** — Real-time log stream via SSE for debugging
- **systemd integration** — Runs as a `--user` service, starts on boot

---

## Architecture

```
┌──────────────────┐     ┌───────────────────────┐     ┌──────────────┐
│  Brave Extension │────▶│  Flask Daemon          │────▶│   yt-dlp     │
│  (context menu + │     │  (localhost:5000)      │     │              │
│   quality popup) │     │  SSE + REST API        │     └──────────────┘
└────────┬─────────┘     │  SQLite DB             │
         │               │  D-Bus notifications   │
         │               └───────────┬───────────┘
         │                           │
         ▼                           ▼
┌──────────────────┐     ┌───────────────────────┐
│ Custom toast     │     │ KDE Plasma 6          │
│ notifications    │     │ D-Bus fallback        │
│ (extension popup)│     │ notifications         │
└──────────────────┘     └───────────────────────┘
```

---

## Screenshots

| Dashboard | Extension Popup |
|:--:|:--:|
| _Queue page — downloading, queued, recent, and failed cards_ | _Quality selector — 144p to 2160p + Audio_ |

| Stats | Settings |
|:--:|:--:|
| _7-day bar chart, success rate, status breakdown_ | _Download directory, concurrent limit_ |

---

## Installation

### Prerequisites

```bash
# Arch Linux
sudo pacman -S python python-flask yt-dlp brave-browser

# KDE D-Bus (usually pre-installed)
pip install --user dbus-python
```

### Quick Install

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
fish install.fish
```

### Manual Install

```bash
# Copy source
mkdir -p ~/.local/share/yt-dl/src
cp src/*.py ~/.local/share/yt-dl/src/

# Copy extension
mkdir -p ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/
cp extension/* ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/

# Install systemd service
mkdir -p ~/.config/systemd/user
cp config/yt-dl.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now yt-dl

# Install desktop entry
cp config/yt-dl.desktop ~/.local/share/applications/

# Install handler script
mkdir -p ~/.local/share/yt-dl
cp yt-dl-handler.sh ~/.local/share/yt-dl/
chmod +x ~/.local/share/yt-dl/yt-dl-handler.sh

# Load extension in Brave
# → brave://extensions/
# → Developer mode
# → Load unpacked
# → ~/.config/BraveSoftware/Brave-Browser/Default/Extensions/yt-dl/
```

---

## Configuration

Settings are stored in `~/.local/share/yt-dl/config.json` and editable via the web dashboard at `http://localhost:5000/settings`.

```json
{
  "download_dir": "/mnt/storage/YouTube",
  "concurrent_limit": 3,
  "theme": "dark"
}
```

| Variable | Default | Description |
|----------|---------|-------------|
| `download_dir` | `/mnt/storage/YouTube` | Where downloaded files are saved |
| `concurrent_limit` | `3` | Max simultaneous downloads (1–10) |
| `theme` | `dark` | Dashboard theme (`dark` / `light`) |

---

## Usage

### Method 1: Brave Extension (Recommended)

1. **Right-click** any YouTube link → **Download with yt-dl**
2. Or click the extension icon on a YouTube page → select quality → download starts automatically
3. A KDE-style toast notification appears when the download completes or fails

### Method 2: Web Dashboard

Open `http://localhost:5000` to:
- Monitor live progress of active downloads
- View queued, completed, and failed jobs
- Retry failed downloads, cancel active ones
- Browse stats, history, and live logs

### Method 3: Right-click Handler

Register `yt-dl-handler.sh` in KDE file associations to send YouTube links directly:

```bash
~/.local/share/yt-dl/yt-dl-handler.sh "https://www.youtube.com/watch?v=..."
```

### Method 4: API

```bash
curl -X POST http://localhost:5000/api/add \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtube.com/watch?v=...","quality":"1080p"}'
```

---

## Project Structure

```
├── src/
│   ├── app.py              # Flask daemon — routes, SSE, templates
│   ├── worker.py            # Download worker — yt-dlp execution, queue
│   ├── notifications.py     # D-Bus notifications + extension heartbeat
│   ├── models.py            # SQLite schema, config, helpers
│   ├── templates/           # Jinja2 templates (dashboard, stats, logs, settings)
│   └── static/              # CSS, JS (dashboard, stats, logs, theme, toast)
├── extension/
│   ├── manifest.json        # Brave/Chrome extension manifest v3
│   ├── background.js        # Service worker — context menu, polling, notifications
│   ├── popup.html           # Quality selector popup
│   ├── popup.js             # Popup logic
│   ├── notification.html    # Custom toast popup window
│   ├── notification.js      # Toast auto-dismiss, retry handler
│   └── icons/               # Extension icons (16, 48, 128)
├── config/
│   ├── yt-dl.service        # systemd user service
│   └── yt-dl.desktop        # Desktop entry
├── yt-dl-handler.sh         # Headless right-click handler
└── install.fish             # One-command installer
```

---

## API Reference

### Queue

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/queue` | GET | List all downloads |
| `GET /api/queue/stream` | GET | SSE stream for live queue updates |
| `POST /api/add` | POST | Queue a new download `{url, quality}` |
| `POST /api/jobs/<id>/retry` | POST | Retry a failed download |
| `POST /api/jobs/<id>/cancel` | POST | Cancel an active download |
| `DELETE /api/jobs/<id>` | DELETE | Delete job and file |
| `POST /api/bulk/delete` | POST | Delete multiple jobs `{ids: [...]}` |
| `POST /api/bulk/retry` | POST | Retry multiple jobs `{ids: [...]}` |

### Settings & Stats

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/settings` | GET | Get current configuration |
| `PUT /api/settings` | PUT | Update configuration |
| `POST /api/settings/reset` | POST | Reset configuration to defaults |
| `GET /api/stats` | GET | Download statistics and history |
| `POST /api/stats/reset` | POST | Clear all download history |
| `GET /api/logs` | GET | Recent log entries |
| `GET /api/logs/stream` | GET | SSE stream for live logs |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/info` | GET | Server info — D-Bus status, version |
| `POST /api/extension/heartbeat` | POST | Extension heartbeat (prevents D-Bus duplication) |
| `POST /api/open` | POST | Open download directory via `xdg-open` |
| `GET /health` | GET | Health check |

### Web Pages

| Route | Description |
|-------|-------------|
| `/` | Queue dashboard |
| `/stats` | Statistics and history |
| `/logs` | Live log stream |
| `/settings` | Configuration page |

---

## Notifications

yt-dl uses a two-tier notification system:

| Scenario | Notification type | Look |
|:--|:--|:--|
| **Brave open** | Custom toast via `chrome.windows.create` | 400px dark card with accent bar, thumbnail, title, metadata, 3s auto-dismiss |
| **Brave closed** | D-Bus fallback via server | Native KDE Plasma 6 popup with action buttons (Retry / Dismiss) |
| **Download failed** | Persistent toast / D-Bus with Retry button | Stays until dismissed manually |

The extension sends a heartbeat every 30 seconds. When the server detects a live heartbeat, it skips its own D-Bus notifications to prevent duplicates.

---

## Tech Stack

- **[Flask](https://flask.palletsprojects.com/)** — Python web framework
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — Video download engine (1000+ supported sites)
- **[SQLite](https://sqlite.org/)** — Download history and state
- **[D-Bus](https://www.freedesktop.org/wiki/Software/dbus/)** — Linux desktop notifications
- **[Brave/Chrome Extensions API](https://developer.chrome.com/docs/extensions/)** — Context menu, popup, custom windows
- **[Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html)** — Live dashboard updates

---

## Troubleshooting

```bash
# Check daemon status
systemctl --user status yt-dl

# View logs
journalctl --user -u yt-dl -f

# Test D-Bus notifications
notify-send --app-name=yt-dl "Test" "Hello from yt-dl"

# Reset database (clears history)
rm ~/.local/share/yt-dl/data/yt-dl.db
systemctl --user restart yt-dl
```

---

## License

Personal use. Built for Arch Linux + KDE Plasma 6 + Brave Browser.

---

*Inspired by [MeTube](https://github.com/alexta69/metube), [ytDownloader](https://github.com/aandrew-me/ytdownloader), and [VidBee](https://github.com/nexmoe/VidBee).*
