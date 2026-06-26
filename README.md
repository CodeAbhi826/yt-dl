<div align="center">

<h1>yt-dl</h1>

<p>Self-hosted download daemon with a browser extension, live dashboard, and desktop notifications.<br>Right-click any link → pick quality → it downloads. That's it.</p>

[![Release](https://img.shields.io/github/v/release/CodeAbhi826/yt-dl?color=ff2d20&labelColor=1a1a1a&logo=github&label=release)](https://github.com/CodeAbhi826/yt-dl/releases)
[![License](https://img.shields.io/github/license/CodeAbhi826/yt-dl?color=3b82f6&labelColor=1a1a1a)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed?labelColor=1a1a1a&logo=docker&logoColor=white)](https://github.com/CodeAbhi826/yt-dl/pkgs/container/yt-dl)
[![Python](https://img.shields.io/badge/python-3.12+-3776ab?labelColor=1a1a1a&logo=python&logoColor=white)](https://python.org)
[![yt-dlp](https://img.shields.io/badge/powered%20by-yt--dlp-ff0000?labelColor=1a1a1a)](https://github.com/yt-dlp/yt-dlp)

</div>

---

## What is this?

Most YouTube downloaders make you paste a URL, wait, and download manually each time. yt-dl works differently — it runs silently in the background, and a browser extension lets you queue downloads with a single right-click from any site. A live web dashboard shows progress, speed, and ETA in real time.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), so it supports **1000+ sites** — YouTube, Twitter/X, TikTok, Instagram, Twitch, SoundCloud, and more.

---

## Features

**Browser Extension**
- Right-click any link or video → **Download with yt-dl**
- Quality selector popup (144p → 4K + MP3)
- Toast notifications for completed/failed downloads
- Works on any site yt-dlp supports

**Daemon**
- Concurrent downloads (configurable, default 3)
- Playlist support — auto-detects, queues individually, skips duplicates (up to 200 videos)
- Auto-updates yt-dlp every 24h in the background
- Cookies support for age-restricted content
- Webhook on job completion — integrate with Home Assistant, Discord, Ntfy, Jellyfin, anything
- Optional API key auth for all write endpoints
- Configurable output patterns for media servers (`%(channel)s/%(title)s.%(ext)s`)

**Dashboard** (`http://localhost:5000`)
- Live queue with progress bars, speed, ETA via SSE
- 7-day download history chart + success rate stats
- Real-time log stream with level filtering
- Bulk retry / bulk delete
- Light and dark theme

**Notifications**
- Desktop toasts via browser extension (no browser required on KDE/GNOME — uses D-Bus)
- Gracefully falls back to extension-only on non-Linux or Docker

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
docker compose up -d
```

Then load the browser extension:

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select the `extension/` folder

Dashboard: `http://localhost:5000`

### Native Linux

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
bash install.sh
```

The installer handles dependencies, sets up a systemd user service, and enables autostart on login.

---

## Configuration

All settings are editable at `http://localhost:5000/settings` or via the API.

| Setting | Default | Description |
|---|---|---|
| `download_dir` | `/mnt/storage/YouTube` | Where files are saved |
| `default_quality` | `720p` | Default quality for new downloads |
| `concurrent_limit` | `3` | Max parallel downloads |
| `playlist_limit` | `200` | Max videos queued from a playlist |
| `output_pattern` | `%(title)s.%(ext)s` | yt-dlp filename template |
| `embed_metadata` | `true` | Embed title, description, tags |
| `embed_thumbnail` | `true` | Embed video thumbnail |
| `embed_chapters` | `true` | Embed chapter markers |
| `embed_subs` | `true` | Embed English subtitles |
| `webhook_url` | _(empty)_ | POST to this URL on job complete/fail |
| `theme` | `dark` | UI theme (`dark` / `light`) |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `YTDL_API_KEY` | _(none)_ | If set, all write endpoints require `Authorization: Bearer <key>` |
| `YTDL_BIND` | `127.0.0.1` | Host to bind (set to `0.0.0.0` in Docker automatically) |
| `YTDL_PORT` | `5000` | Port to listen on |

---

## Webhook

Set `webhook_url` in settings to receive a POST request whenever a download completes or fails:

```json
{
  "event": "completed",
  "job_id": "job_1234567890_dQw4w9WgXcQ",
  "title": "Never Gonna Give You Up",
  "quality": "1080p",
  "file_path": "/mnt/storage/YouTube/Never Gonna Give You Up.mp4",
  "file_size": 142606336,
  "error": null
}
```

Works with Home Assistant automations, Discord bots, Ntfy/Gotify push notifications, Jellyfin library scan triggers, or any HTTP endpoint.

---

## API Reference

All endpoints return JSON. Auth header required on write endpoints if `YTDL_API_KEY` is set:
```
Authorization: Bearer <your-api-key>
```

### Queue
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/queue` | List all jobs (latest 200) |
| `GET` | `/api/queue/stream` | SSE live queue stream |
| `POST` | `/api/add` | Add download `{ url, quality }` |

### Jobs
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/jobs/<id>` | Get job details |
| `POST` | `/api/jobs/<id>/retry` | Retry a failed job |
| `POST` | `/api/jobs/<id>/cancel` | Cancel active job |
| `DELETE` | `/api/jobs/<id>` | Delete job and file |
| `POST` | `/api/bulk/retry` | Bulk retry `{ ids: [...] }` |
| `POST` | `/api/bulk/delete` | Bulk delete `{ ids: [...] }` |

### Settings
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings` | Get current config |
| `PUT` | `/api/settings` | Update config |
| `POST` | `/api/settings/reset` | Reset to defaults |
| `GET` | `/api/settings/cookies` | Check cookies.txt status |
| `POST` | `/api/settings/cookies` | Upload cookies.txt |
| `DELETE` | `/api/settings/cookies` | Remove cookies.txt |

### Stats & System
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/stats` | Download stats + 7-day chart data |
| `POST` | `/api/stats/reset` | Clear all history |
| `GET` | `/api/info` | Server status, yt-dlp version, disk usage |
| `GET` | `/api/logs` | Fetch log entries (`?level=ERROR&count=100`) |
| `GET` | `/api/logs/stream` | SSE live log stream |
| `GET` | `/api/search` | Search jobs (`?q=&status=&quality=&date=`) |

### Example: queue a download

```bash
curl -X POST http://localhost:5000/api/add \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "quality": "1080p"}'
```

```json
{ "job_id": "job_1234567890_dQw4w9WgXcQ", "status": "queued" }
```

---

## Quality Options

| Value | Format |
|---|---|
| `144p` – `2160p` | VP9 video + best audio, merged to MP4 |
| `best` | Best available VP9 stream |
| `audio` | Best audio only, converted to MP3 |

---

## Age-Restricted Content

Export your browser cookies and upload the file at `http://localhost:5000/settings`:

```bash
# Using yt-dlp's built-in cookie exporter
# Option A: export browser cookies to a file
yt-dlp --cookies-from-browser chrome:Default --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Option B (recommended): use a browser extension like "Get cookies.txt LOCALLY"
# Export cookies for youtube.com, save as cookies.txt
```

Then upload `cookies.txt` via the Settings page. The daemon uses it automatically for all subsequent downloads.

---

## Project Structure

```
yt-dl/
├── src/
│   ├── app.py              # Flask server + all API routes
│   ├── worker.py           # Download queue, yt-dlp subprocess, webhook
│   ├── models.py           # DB schema, config, QUALITY_MAP
│   ├── notifications.py    # D-Bus + extension notification manager
│   └── updater.py          # Background yt-dlp auto-updater
├── extension/
│   ├── manifest.json       # MV3 manifest (Chrome/Brave/Edge)
│   ├── background.js       # Service worker + chrome.alarms
│   ├── popup.html/js       # Quality selector popup
│   └── notification.html/js # Toast notification window
├── Dockerfile
├── docker-compose.yml
└── install.sh              # Native Linux installer
```

---

## Stack

- [Flask](https://flask.palletsprojects.com/) — Python web framework
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Download engine (1000+ sites)
- [SQLite](https://sqlite.org/) — Job database
- [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) — Live dashboard updates
- [D-Bus](https://www.freedesktop.org/wiki/Software/dbus/) — Linux desktop notifications (optional)
- Chrome Extension Manifest V3

---

## License

[MIT](LICENSE) — do whatever you want with it.
