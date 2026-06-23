# yt-dl

[![GitHub release](https://img.shields.io/github/v/release/CodeAbhi826/yt-dl?color=ff4d36&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/releases)
[![GitHub stars](https://img.shields.io/github/stars/CodeAbhi826/yt-dl?color=ffcb47&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/stargazers)
[![License](https://img.shields.io/github/license/CodeAbhi826/yt-dl?color=22c55e&labelColor=1a1a1a)](LICENSE)

Self-hosted YouTube download daemon for Linux. Right-click any YouTube link in your browser, select quality, and the video downloads automatically — with KDE native notifications and a real-time web dashboard.

---

## Features

- **Right-click → Download** — Browser extension adds "Download with yt-dl" to YouTube context menus
- **Quality selector** — 144p to 2160p plus audio-only MP3, selectable from the extension popup
- **Real-time dashboard** — Live queue with progress bars, speed, ETA via SSE
- **KDE Plasma notifications** — Custom toast popups when browser is open, D-Bus fallback when closed
- **Concurrent downloads** — Configurable parallel downloads (default 3)
- **Download history & stats** — 7-day bar chart, success rate, total data downloaded
- **Live logs** — Real-time log stream for debugging
- **systemd integration** — Runs as a user service, starts on boot

---

## Quick Start

### Prerequisites

```bash
sudo pacman -S python python-pip yt-dlp
pip install --user flask dbus-python
```

### Install

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
bash install.sh
```

Then load the extension in your browser:
1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select `~/.local/share/yt-dl/extension/`

### Usage

- **Right-click** any YouTube link → **Download with yt-dl**
- **Click the extension icon** on a YouTube page → select quality
- Open `http://localhost:5000` to monitor downloads

---

## Architecture

```
Browser Extension  ──►  Flask Daemon  ──►  yt-dlp
                        (port 5000)
                        SQLite + SSE
                        D-Bus notifications
```

---

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/queue` | GET | List all downloads |
| `GET /api/queue/stream` | GET | SSE live queue |
| `POST /api/add` | POST | Add download `{url, quality}` |
| `POST /api/jobs/<id>/retry` | POST | Retry failed job |
| `POST /api/jobs/<id>/cancel` | POST | Cancel active job |
| `DELETE /api/jobs/<id>` | DELETE | Delete job and file |
| `POST /api/bulk/delete` | POST | Bulk delete `{ids: [...]}` |
| `POST /api/bulk/retry` | POST | Bulk retry `{ids: [...]}` |
| `GET /api/settings` | GET | Get config |
| `PUT /api/settings` | PUT | Update config |
| `POST /api/settings/reset` | POST | Reset config |
| `GET /api/stats` | GET | Download statistics |
| `POST /api/stats/reset` | POST | Clear history |
| `GET /api/info` | GET | Server status |
| `POST /api/extension/heartbeat` | POST | Extension heartbeat |

---

## Configuration

```json
{
  "download_dir": "/mnt/storage/YouTube",
  "concurrent_limit": 3,
  "theme": "dark"
}
```

Edit at `http://localhost:5000/settings` or directly in `~/.local/share/yt-dl/config.json`.

---

## Project Structure

```
├── src/
│   ├── app.py              # Flask server — routes, SSE, templates
│   ├── worker.py           # Download queue — yt-dlp execution
│   ├── notifications.py    # D-Bus notifications + extension heartbeat
│   ├── models.py           # SQLite schema, config, helpers
│   ├── templates/          # Jinja2 HTML templates
│   └── static/             # CSS and JavaScript
├── extension/
│   ├── manifest.json       # Extension manifest (MV3)
│   ├── background.js       # Service worker — context menu, polling
│   ├── popup.html/js       # Quality selector popup
│   ├── notification.html/js # Custom toast popup window
│   └── icons/
├── config/
│   ├── yt-dl.service       # systemd user service
│   └── yt-dl.desktop       # Desktop entry
├── yt-dl-handler.sh        # Right-click handler script
├── install.sh               # Installer script
└── install.fish             # Fish installer (optional)
```

---

## Dependencies

- [Flask](https://flask.palletsprojects.com/) — Python web framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Video download engine
- [SQLite](https://sqlite.org/) — Database
- [D-Bus](https://www.freedesktop.org/wiki/Software/dbus/) — Linux desktop notifications

---

## License

[MIT](LICENSE)
