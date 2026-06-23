# yt-dl

[![GitHub release](https://img.shields.io/github/v/release/CodeAbhi826/yt-dl?color=ff2d20&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/releases)
[![GitHub stars](https://img.shields.io/github/stars/CodeAbhi826/yt-dl?color=22c55e&labelColor=1a1a1a&logo=github)](https://github.com/CodeAbhi826/yt-dl/stargazers)
[![License](https://img.shields.io/github/license/CodeAbhi826/yt-dl?color=3b82f6&labelColor=1a1a1a)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed?labelColor=1a1a1a&logo=docker)](https://github.com/CodeAbhi826/yt-dl/pkgs/container/yt-dl)

Self-hosted YouTube download daemon. Right-click any link, pick quality, and it downloads — with desktop notifications and a live dashboard.

---

## Features

- **Right-click → Download** — Browser extension for YouTube context menus
- **144p to 2160p + audio MP3** — Quality selector in the extension popup
- **Live dashboard** — SSE-driven queue with progress bars, speed, ETA
- **Desktop notifications** — Extension toasts (browser open) or D-Bus (Linux)
- **Playlist support** — Auto-detects playlists, creates individual jobs, skips duplicates
- **Cookies support** — Upload cookies.txt in Settings for age-restricted content
- **Auto-update yt-dlp** — Background updater runs every 24h
- **Media server naming** — Configurable output patterns (`%(channel)s/%(title)s.%(ext)s`)
- **API key auth** — Optional `YTDL_API_KEY` env var for securing endpoints
- **Concurrent downloads** — Configurable parallel downloads (default 3)
- **Download history & stats** — 7-day bar chart, success rate, total data
- **Live logs** — Real-time log stream with level filtering
- **Docker** — Official image with ffmpeg bundled

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
docker compose up -d
```

Then load the extension:
1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select `extension/`

### Native (Linux)

```bash
git clone https://github.com/CodeAbhi826/yt-dl.git
cd yt-dl
bash install.sh
```

Open `http://localhost:5000` to monitor downloads.

---

## Architecture

```
Browser Extension  ──►  Flask Daemon  ──►  yt-dlp
                        (port 5000)
                        SQLite + SSE
                        Notifications (extension → D-Bus fallback)
```

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `download_dir` | `/mnt/storage/YouTube` | Where videos are saved |
| `concurrent_limit` | 3 | Max parallel downloads |
| `theme` | `dark` | UI theme (`dark` / `light`) |
| `output_pattern` | `%(title)s.%(ext)s` | yt-dlp output template |
| `embed_metadata` | `true` | Embed video metadata |
| `embed_thumbnail` | `true` | Embed thumbnail |
| `embed_chapters` | `true` | Embed chapters |
| `embed_subs` | `true` | Embed English subtitles |

Edit at `http://localhost:5000/settings`.

---

## API

### Queue
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/queue` | List all downloads |
| GET | `/api/queue/stream` | SSE live queue |
| POST | `/api/add` | Add download `{url, quality}` |

### Jobs
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/<id>` | Get job details |
| POST | `/api/jobs/<id>/retry` | Retry failed job |
| POST | `/api/jobs/<id>/cancel` | Cancel active job |
| DELETE | `/api/jobs/<id>` | Delete job and file |
| POST | `/api/bulk/retry` | Bulk retry `{ids}` |
| POST | `/api/bulk/delete` | Bulk delete `{ids}` |

### Settings
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get config |
| PUT | `/api/settings` | Update config |
| POST | `/api/settings/reset` | Reset to defaults |
| GET | `/api/settings/cookies` | Check cookies status |
| POST | `/api/settings/cookies` | Upload cookies.txt |
| DELETE | `/api/settings/cookies` | Remove cookies |

### Stats & System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stats` | Download statistics |
| POST | `/api/stats/reset` | Clear history |
| GET | `/api/info` | Server status + auth info |
| GET | `/api/logs` | Fetch log entries |
| GET | `/api/logs/stream` | SSE live logs |

### Extension
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/extension/heartbeat` | Extension heartbeat |
| POST | `/api/extension/register` | Register extension |
| POST | `/api/extension/unregister` | Unregister extension |

### Auth
If `YTDL_API_KEY` is set, all POST/PUT/DELETE endpoints require:
```
Authorization: Bearer <your-api-key>
```

---

## Project Structure

```
├── src/
│   ├── app.py              # Flask server
│   ├── worker.py           # yt-dlp download queue
│   ├── notifications.py    # Extension + D-Bus notifications
│   ├── models.py           # DB schema, config, helpers
│   ├── updater.py          # Auto yt-dlp updater
│   ├── templates/          # Jinja2 templates
│   └── static/             # CSS + JS
├── extension/
│   ├── manifest.json       # MV3 manifest
│   ├── background.js       # Service worker
│   ├── popup.html/js       # Quality selector
│   ├── notification.html/js # Toast popup
│   ├── icons/
│   └── store/              # Web store assets
├── Dockerfile              # Docker image
├── docker-compose.yml      # Docker Compose
└── install.sh              # Native installer
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
