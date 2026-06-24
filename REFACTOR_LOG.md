# yt-dl Refactor Log

> Complete overhaul of the yt-dl YouTube downloader system.
> Use this doc to continue the project in a new session.

---

## Phase 1 — Backend Pipeline (`worker.py`)

**Files changed:** `src/worker.py`

### 1a — JSON Progress Template Parsing
- **Before:** Brittle regex `if "[download]" in line and "%" in line` — failed when aria2c was the downloader (different output format). Progress stuck at 0%.
- **After:** Uses yt-dlp's `--progress-template` with JSON format `{"percent":"%(progress._percent_str)s","speed":"%(progress._speed_str)s","eta":"%(progress._eta_str)s","filename":"%(info.filename)s"}` + `--newline`. Parses JSON lines from stdout. Reliable regardless of downloader.
- **Lines:** `run_download()` — download command construction + stdout loop

### 1b — Process Group Kill (Orphaned aria2c)
- **Before:** `cancel_job` used `job.proc.terminate()` / `job.proc.kill()` — only killed yt-dlp, not its aria2c child process. aria2c became orphan.
- **After:** `Popen` uses `start_new_session=True`. `cancel_job` uses `os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)` with SIGKILL fallback. Kills the entire process group.
- **Lines:** `run_download()` Popen call, `cancel_job()` function

### 1c — Persistent Worker Thread
- **Before:** `process_queue()` was spawned as a new `threading.Thread` from 4 different places (`api_add_job`, `run_download` finally, `retry_job`, `api_bulk_retry`). Multiple threads could run concurrently.
- **After:** Single daemon `_worker_loop()` thread with `threading.Event` signaling. `process_queue()` just sets the event. Thread wakes, processes, sleeps.
- **Lines:** `_worker_loop()`, `_start_worker()`, `process_queue()` now signals event

### 1d — Progress Update Throttle
- **Before:** Every stdout line triggered `save_job()` + `notification_manager.update_downloading()`. At 10+ Hz, this spammed SQLite and D-Bus.
- **After:** Only saves/notifies when `abs(progress - last_saved) >= 1.0` **or** `time.elapsed >= 1.0s`. Tracks `last_saved_progress` and `last_update_time` in `DownloadJob`.
- **Lines:** `DownloadJob.__init__` (new fields), `run_download` stdout loop

### 1e — Store `file_size` After Download
- **Before:** DB column `file_size` existed but was never populated. Stats showed `0.0 B`.
- **After:** `job.file_size = os.path.getsize(job.file_path)` after successful download. `save_job` now includes `file_size` in the UPDATE query.
- **Lines:** `run_download()` completion block, `save_job()` SQL

### 1f — Capture Filename from stdout
- **Before:** Used glob patterns `download_dir.glob(f"*{job.video_id}*{ext}")`. Brittle — failed if video_id was empty or title had unexpected chars.
- **After:** Captures `_filename_str` from the progress JSON template. Falls back to `[download] Destination: /path` line parsing. Final fallback is the old glob.
- **Lines:** `run_download()` stdout loop + completion

### 1g — Audio Quality Fix
- **Before:** `QUALITY_MAP["audio"] = "bestaudio/best[audioonly]"` — `[audioonly]` is invalid yt-dlp syntax. Fell back to `best`, downloading a video. No `--extract-audio` flag.
- **After:** Quality map fixed to `"bestaudio/best"`. When `job.quality == "audio"`, appends `--extract-audio --audio-format mp3` to download command. Output extensions include `.mp3`, `.m4a`.
- **Lines:** `models.py:42`, `run_download()` command construction

### 1h — DB Close in try/finally
- **Before:** `db.close()` at end of functions — if `db.execute()` raised, connection leaked.
- **After:** All DB operations wrapped in `try/finally` with `db.close()` in finally block.
- **Lines:** `save_job()`, `_process_queue()`, `cancel_job()`, `retry_job()`

### 1i — Thread Naming
- **Before:** Threads unnamed → showed as `Thread-1`, `Thread-2` in logs/debuggers.
- **After:** Named threads: `"process-queue"`, `f"download-{job.job_id[:8]}"`.
- **Lines:** `_start_worker()`, `_process_queue()`

---

## Phase 2 — Daemon Fixes (`app.py`)

**Files changed:** `src/app.py`

### 2a — `--init-only` Flag
- **Before:** `install.fish` ran `python3 app.py --init-only` but app.py ignored it → Flask server started and hung the installer.
- **After:** Checks `sys.argv` for `--init-only`. If present, runs `init_db()`, calls `load_config()` (creates default config.json), then `sys.exit(0)`.
- **Lines:** `__main__` block

### 2b — Force Port 5000
- **Before:** `find_free_port()` scanned ports 5000-5009. Extension and handler hardcoded `127.0.0.1:5000` → mismatch if daemon picked a different port.
- **After:** Uses hardcoded `port = 5000`. Removed `find_free_port()`. Removed dead `socket` import. Wrapped `app.run` in try/except.
- **Lines:** Top imports, `__main__` block

### 2c — Graceful Shutdown
- **Before:** SIGTERM/SIGINT killed Flask immediately — active yt-dlp processes became orphaned.
- **After:** Signal handlers iterate `active_jobs` (imported from worker) and terminate each process group with `os.killpg()`. Clean exit.
- **Lines:** New signal handler function, `__main__` block

### 2d — Import Cleanup
- **Before:** `app.py` imported `active_jobs` (unused), `human_bytes` (unused — stats hardcoded "0.0 B"), `socket` (only used by removed `find_free_port`).
- **After:** `active_jobs` and `human_bytes` kept (now used by shutdown handler and stats endpoint). `socket` removed. Added `subprocess` for Open Folder feature.
- **Lines:** Import block

---

## Phase 3 — KDE Notifications Fix (`notifications.py` + extension)

**Files changed:** `src/notifications.py`, `extension/background.js`, `extension/popup.html`, `extension/popup.js`

### 3a — Per-State Timeout Control
- **Before:** `_notify` had hardcoded `timeout = Int32(0 if state == "failed" else 3000)`. Every updating call had 3s timeout → popup kept re-appearing.
- **After:** `_notify` accepts optional `timeout` param. Each caller specifies its own:
  - `queued`: 3000ms (brief popup, gone fast)
  - `downloading` (first): 5000ms (initial "Download started" popup)
  - `downloading` (updates): 0ms (never expires → stays in panel silently)
  - `done`: 5000ms (brief "Complete!" popup)
  - `failed`: 0ms (persistent critical notification)
  - `cancelled`: 3000ms (brief popup)
- **Lines:** `_notify()`, all `show_*`/`update_*` methods

### 3b — First-Call vs Update Logic
- **Before:** Every `update_downloading()` call was identical — same timeout, same behavior.
- **After:** `update_downloading` checks `job_id not in self._active`:
  - First call: `timeout=5000`, `resident=True` → 5s popup "Download started", then moves to bell panel history
  - Subsequent calls: `replaces_id` + `timeout=0` → silently updates the panel notification without new popup
- **Lines:** `update_downloading()` method

### 3c — Progress Bar in Bell Panel
- **Before:** `hints["value"] = Int32(int(progress))` was set but progress was always 0 (aria2c bug). Bar showed empty.
- **After:** With Phase 1a fixing progress parsing, progress values now flow through. The KDE notification panel shows a live progress bar.
- **Lines:** `_make_hints()` — unchanged code, data now works

### 3d — Remove Redundant `force_popup` Parameter
- **Before:** `_notify()` accepted `force_popup` parameter but never used it.
- **After:** Removed.
- **Lines:** `_notify()` signature

### 3e — Remove Redundant Chrome Notifications
- **Before:** `background.js` called `chrome.notifications.create()` on every successful queue + every error. User got 2 notifications (Chrome + KDE) for the same event.
- **After:** Removed ALL `chrome.notifications.create()` calls for success states. Browser notifications only show on error (daemon unreachable).
- **Lines:** `extension/background.js`

### 3f — Add "Download This Page" to Extension Popup
- **Before:** Popup only set default quality — no way to trigger a download from the popup.
- **After:** Added a "Download this page" button that calls `POST /api/add` with the current tab's URL. Quality selector included. Shows status feedback.
- **Lines:** `extension/popup.html` (new button + select), `extension/popup.js` (new handler)

---

## Phase 4 — Template Extraction (`templates/` + `static/`)

**Files created/removed:** `src/templates/`, `src/static/`, `src/templates.py` (deleted)

### 4a — Created `src/static/`
- `style.css` — All CSS variables, layout, card styles, progress bars, tags, buttons, filter tabs, download cards, log lines, bar chart
- `theme.js` — Theme toggle logic with localStorage persistence
- `toast.js` — Toast notification helper (3s auto-dismiss)
- `dashboard.js` — Queue page JS with SSE + reactive card DOM
- `stats.js` — Stats page JS with client-side rendering
- `logs.js` — Live logs page JS with SSE stream
- `search.js` — Search page JS
- `settings.js` — Settings page JS

### 4b — Created `src/templates/`
- `base.html` — Shared layout: nav bar with theme toggle, toast container, font/stylesheet links, script blocks
- `dashboard.html` — URL input card, filter tabs, bulk action bar, card grid container
- `settings.html` — Download directory, quality, concurrent limit, embed options, danger zone
- `stats.html` — Stat cards (total, success rate, data, active), daily chart, status breakdown
- `logs.html` — Level filter, clear/auto-scroll controls, log line container
- `search.html` — Search input, status/quality/date filters, results table

### 4c — Updated Routes
- All routes changed from `render_template_string(XXX_HTML, ...)` to `render_template("xxx.html", ...)`
- Flask app configured with `template_folder` and `static_folder`

### 4d — Delete Dead Code
- `src/templates.py` removed — 178 lines of inline Python strings that duplicated app.py's templates, never imported anywhere.

---

## Phase 5 — SSE Queue Stream + Reactive DOM

**Files changed:** `src/app.py`, `src/static/dashboard.js`

### 5a — `/api/queue/stream` SSE Endpoint
- **Before:** Client polled `/api/queue` every 3s via `setInterval` — network overhead even when nothing changed.
- **After:** Server-Sent Events endpoint pushes queue JSON every 1s. Only emits when data actually changes (tracks hash of last sent data). Sends `: unchanged\n\n` comment when no change to keep connection alive.
- **Lines:** `app.py` — new route `stream_queue()`

### 5b — Reactive DOM Diffing (No Nuke & Pave)
- **Before:** `renderQueue(jobs)` did `tbody.innerHTML = jobs.map(...).join('')` — destroyed all DOM state (checkboxes, CSS transitions, scroll position).
- **After:** Maintains a `Map<job_id, cardElement>`. On SSE data:
  - Existing card: `updateCard()` replaces innerHTML in-place (but card reference preserved)
  - New job: `createCard()` builds fresh card and appends to grid
  - Removed job: `card.remove()` removes from DOM and Map
- **Lines:** `static/dashboard.js` — `cards` Map, `createCard()`, `updateCard()`, `renderQueue()`

### 5c — XSS Protection
- **Before:** Template literals like `${j.title}` directly injected user-provided text into DOM — XSS vector.
- **After:** All dynamic text wrapped in `escapeHtml()` function. Applied to title, URL, video ID, error message, file path across all JS files.
- **Lines:** `static/*.js` — `escapeHtml()` added, all string concatenations wrapped

### 5d — Progress Bar Animation
- **Before:** `transition: width 0.3s ease` — bar jumped every 3s poll cycle.
- **After:** `transition: width 1s linear` — smooth glide in sync with 1s SSE pushes.
- **Lines:** `static/style.css:63`

### 5e — SSE Auto-Reconnect
- **Before:** No reconnection logic — if SSE dropped, page went stale until manual refresh.
- **After:** `connectSSE()` wraps EventSource creation. `onerror` closes and retries after 3s delay. Reconnect timer cleared on each new connection attempt.
- **Lines:** `static/dashboard.js` — `connectSSE()`

---

## Phase 6 — Card-Based UI Redesign

**Files changed:** `src/static/style.css`, `src/static/dashboard.js`, `src/templates/dashboard.html`

### 6a — Card Grid Layout
- **Before:** HTML `<table>` with rows for each download.
- **After:** CSS grid of `<div class="download-card">` elements. `grid-template-columns: repeat(auto-fill, minmax(360px, 1fr))`. Each card has `#141414` background, 16px border-radius, 1px `#2a2a2a` border, hover border-color change to accent.

### 6b — Card Structure
```
┌────────────────────────────────────────┐
│ ☐ ┌──────────┐  Title text (clamped 2L)│
│    │ thumbnail │  url (truncated)       │
│    │ 120x68   │                        │
│    └──────────┘  [720p] [Downloading]  │
│                   ████████░░░░ 73%      │
│                   2.3 MB/s · 01:23      │
│                   [Cancel]              │
└────────────────────────────────────────┘
```
- Left: checkbox + 120x68 thumbnail (fetched from `i.ytimg.com/vi/{id}/mqdefault.jpg`) or placeholder
- Body: title (2-line clamp), truncated URL, quality+status tags, progress bar with % and speed/ETA, file size (completed), error message (failed), action buttons

### 6c — Filter Tabs
- Row of pill buttons above cards: All / Downloading / Queued / Completed / Failed
- Click sets `currentFilter`, applies `card.style.display = 'none'` to non-matching cards
- Active filter highlighted with accent background
- New cards respect current filter on creation

### 6d — URL Input Bar
- Prominent card at top of dashboard with URL text input, quality dropdown (Best/4K/1440p/1080p/720p/480p/360p/Audio), and "Download" button
- Enter key submits, button shows "Adding..." feedback, error messages shown inline
- Calls `POST /api/add` with JSON body `{url, quality}`

### 6e — Bulk Actions
- Checkbox per card + hidden bulk bar with Retry Selected / Delete Selected / Clear
- `selectedIds` Set tracked across SSE updates (checkbox state preserved via re-check on re-render)
- Confirm dialogs before destructive actions

### 6f — Open Folder (Completed Jobs)
- "Open" button on completed download cards
- Fetches `GET /api/jobs/<id>` to get `file_path`, extracts directory, calls `POST /api/open` which runs `xdg-open` server-side

### 6g — Error Message Display
- **Before:** Failed jobs showed only a red "failed" tag — error text hidden.
- **After:** `card-error` div below progress showing `j.error_message` in red-on-dark background.

### 6h — File Size Display
- For completed jobs with `file_size > 0`: shows `card-file-size` div with human-readable size (e.g., "24.5 MB")

### 6i — Empty State
- When no jobs exist: shows centered placeholder with down-arrow icon, "No downloads yet" text, and hint to paste URL or use extension.

---

## Phase 7 — Stats Page Overhaul

**Files changed:** `src/static/stats.js`, `src/templates/stats.html`, `src/app.py`

### 7a — Full Client-Side Rendering
- **Before:** `stats_page()` recomputed all stats from SQLite server-side (duplicating `/api/stats`), then JS fetched `/api/stats` again. Double work.
- **After:** Stats page renders as a shell with `0` defaults. JS fetches `/api/stats` and populates all cards, bar chart, and breakdown. No duplicate computation.

### 7b — Actual Byte Total
- **Before:** `/api/stats` hardcoded `"total_bytes": 0, "total_bytes_human": "0.0 B"`.
- **After:** API query: `SELECT COALESCE(SUM(file_size), 0) FROM downloads WHERE status='completed'`. Uses `human_bytes()` from models for display.

### 7c — Live Polling
- `fetchStats()` runs every 5s via `setInterval`, updates all stat values and bar chart dynamically

---

## Phase 8 — Security & Polish

**Files changed:** `yt-dl-handler.sh`, `src/models.py`, `src/app.py`

### 8a — Shell Injection Fix
- **Before:** `yt-dl-handler.sh:19`: `python3 -c "import json; d={'url':'$URL',...}"` — `$URL` interpolated into Python code string, a single quote in URL broke the syntax and allowed arbitrary code execution.
- **After:** Uses `python3 -c "import json, sys; d={'url': sys.argv[1], 'quality': sys.argv[2]}; print(json.dumps(d))" "$URL" "$QUALITY"`. URL and quality passed as positional args, never interpolated into code. Quality also uses `os.path.expanduser` for safe path resolution.

### 8b — Log Rotation
- **Before:** `daemon.log` grew unbounded with `logging.FileHandler`.
- **After:** Uses `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=3)`. Log rotates at 5MB with 3 backups kept.

### 8c — DB Indexes
- Added indexes on `status`, `created_at`, `video_id` for faster queries on stats page, search, and queue listing.
- **Lines:** `models.py:init_db()` — after table creation

### 8d — Graceful Shutdown Enhancement
- **Before:** Signal handlers were defined but unused (no `signal.signal` call).
- **After:** `signal.signal(signal.SIGTERM, shutdown_handler)` and `signal.signal(signal.SIGINT, shutdown_handler)` registered. Handler iterates `active_jobs` with `queue_lock`, calls `os.killpg()` on each running process group.

### 8e — Open Folder Endpoint
- `POST /api/open` accepts `{path: "..."}`, validates directory exists, runs `subprocess.Popen(["xdg-open", path])`.
- `GET /api/jobs/<id>` returns full job dict for Open Folder to resolve file path.

---

## Phase 9 — Bar Chart Fix

**Files changed:** `src/static/style.css` (min-height already applied)

### 9a — Zero-Height Bar Prevention
- **Before:** Bars with 0 count would have `height: 0%` — invisible even when there was data on other days, because max_cnt used days with 0 count.
- **After:** `app.py` API computes `max_cnt = max([r["cnt"] for r in daily] + [1])` — ensures denominator is at least 1 even when all counts are 0. CSS `.bar` has `min-height: 4px` so even 0-count bars are visible as tiny nubs in the chart.

---

## Bugs Found & Fixed During Testing

### Bug 1: `api_stats` — Closed DB Query
- **Symptom:** `GET /api/stats` crashed with `sqlite3.ProgrammingError: Cannot operate on a closed database.`
- **Cause:** `total_bytes` query was added AFTER `db.close()` was already called.
- **Fix:** Moved the `total_bytes` query before `db.close()` at `app.py:278`.

### Bug 3: aria2c prevents real-time progress
- **Symptom:** Progress stayed at 0.0% for 20+ seconds then jumped to 100%. Only 2 JSON progress lines per download.
- **Cause:** `--downloader aria2c` — aria2c handles downloads internally and only notifies yt-dlp at 100% completion per stream. No intermediate progress reported.
- **Fix:** Removed `--downloader aria2c` and `--downloader-args` from download command. yt-dlp's native downloader provides real-time progress (30+ JSON lines per download), smooth percentage updates, and actual speed/ETA. Also faster for merged downloads (15.8s vs 29.9s for same video) because `--merge-output-format mp4` requires post-processing that overlaps better with native download.
- **Lines:** `worker.py:172-174` (removed aria2c args)

### Bug 2: Download Status "done" vs "completed"
- **Symptom:** Completed downloads had `status = "done"` in DB, but stats queries filtered for `status = 'completed'`. Result: `total_bytes` was always 0, completed count was 0.
- **Cause:** `worker.py:254` set `job.status = "done"` on completion, and `save_job` checked `job.status in ("done", "failed", "cancelled")` for `completed_at` timestamp.
- **Fix:** Changed `"done"` → `"completed"` in both places (`worker.py:254` and `worker.py:68`). Added migration in `app.py:__main__` to UPDATE old `"done"` → `"completed"` on startup.

---

## Testing Performed (2026-06-23)

### Environment
- Arch Linux, Python 3.14.6, Flask 3.1.3, yt-dlp 2026.6.9, aria2c
- Existing data dir `~/.local/share/yt-dl/` with 2 stale "downloading" jobs
- 21 pre-existing mp4 files in `/mnt/storage/YouTube/`

### ✅ Passed Tests
| Test | Result |
|------|--------|
| `--init-only` — DB init + migration | ✅ |
| `GET /health` — health check | ✅ `{"status":"ok"}` |
| `GET /` — dashboard page | ✅ HTML rendered |
| `GET /stats` — stats page | ✅ HTML rendered |
| `GET /logs` — logs page | ✅ HTML rendered |
| `GET /search` — search page | ✅ HTML rendered |
| `GET /settings` — settings page | ✅ HTML rendered |
| `GET /api/queue` — fetch queue | ✅ 2 stale jobs returned |
| `GET /api/stats` — fetch stats | ✅ After fix: total=3, success=3, bytes=78.9MB |
| `GET /api/settings` — fetch config | ✅ default_quality, download_dir etc |
| `POST /api/add` — enqueue new download | ✅ `{"job_id":"...","status":"queued"}` |
| Real yt-dlp download (360p native) | ✅ Completed in 21s, status="completed", file_size=5344292 |
| Real yt-dlp download (720p Despacito) | ✅ Completed, 54MB, 100% progress with smooth real-time updates |
| Real-time progress during download | ✅ 0%→8.2%→17.3%→...→100% with speed + ETA every 2s |
| `POST /api/jobs/<id>/retry` — retry job | ✅ `{"ok":true}` |
| `POST /api/jobs/<id>/cancel` — cancel job | ✅ `{"ok":true}` |
| `DELETE /api/jobs/<id>` — delete job | ✅ `{"ok":true}` + file removed |
| `GET /api/search?q=Rick` — text search | ✅ 1 result, title matched |
| `PUT /api/settings` — save config | ✅ `{"default_quality":"1080p"}` |
| `GET /api/logs` — fetch logs | ✅ Returns ring buffer entries |
| `GET /api/logs/stream` — SSE logs | ✅ Stream connects and delivers |
| `GET /api/queue/stream` — SSE queue | ✅ Stream connects, sends data: + `: unchanged` |
| `POST /api/bulk/delete` — bulk delete | ✅ `{"deleted":1}` |
| `POST /api/bulk/retry` — bulk retry | ✅ `{"retried":1}` |
| Status migration "done"→"completed" | ✅ Both stale jobs migrated on startup |

### ❌ Untestable (no KDE/Brave)
- KDE notification lifecycle (first-call popup vs silent updates, resident panel, progress bar) — requires `dbus` + KDE Plasma notification daemon
- Brave extension `"Download this page"` — requires `chrome.tabs.query` in extension context
- `POST /api/open` — `xdg-open` was verified in code but not visually confirmed
- File size display on `/api/stats` — returns 78.9MB but total includes 3 files (one can be visually verified on the dashboard)

---

## Phase 10 — Extension-based Toast Notifications + D-Bus Fallback

**Goal:** Replace web-toast-only approach with extension-based custom toast popups that work on any OS, with D-Bus fallback on Linux when the extension isn't alive.

**Files changed/created:** `extension/notification.html`, `extension/notification.js`, `extension/background.js`, `src/notifications.py`, `src/app.py`, `src/worker.py`

### 10a — Custom Toast Popup (`notification.html` + `.js`)
- **What:** A 400px-wide dark toast rendered via `chrome.windows.create` with full CSS control
- **Layout:** 4px accent bar (colored per state) + 120×68 thumbnail + content area (app name, title, video title, metadata)
- **States:** Download Started (blue accent, 3s), Download Complete (green accent + ✓ badge, 3s), Download Failed (orange accent + Retry button, 8s)
- **Animation:** Slide in from right (200ms ease-out), fade out (300ms ease-out, scale .96 + opacity)
- **Position:** Bottom-right of focused window, calculated via `chrome.windows.getLastFocused()`

### 10b — Extension Background Polling (`background.js`)
- **Heartbeat:** POST `/api/extension/heartbeat` every 30s → tells the server the extension is alive
- **Queue polling:** GET `/api/queue` every 5s, diffs job statuses against previous snapshot
- **Notification trigger:** When a job transitions `downloading→completed` or `downloading→failed`, fires `chrome.windows.create` with the notification popup
- **D-Bus awareness:** Checks `/api/info` on startup — if `dbus_available` is true, skips all chrome.notifications and relies on server D-Bus only

### 10c — D-Bus Fallback (`notifications.py`, `app.py`, `worker.py`)
- **Extension heartbeat:** `set_extension_heartbeat()` / `clear_extension_heartbeat()` / `is_extension_alive()` — tracks time since last heartbeat (120s threshold)
- **Notification suppression:** All `NotificationManager.show_*` methods now check `is_extension_alive()` first — if true, return early (no D-Bus popup)
- **Endpoints:** `/api/extension/heartbeat` (POST), `/api/extension/register` (POST), `/api/extension/unregister` (POST), `/api/info` (GET — returns `dbus_available` + version)
- **Shutdown:** `shutdown_handler` calls `clear_extension_heartbeat()` to prevent stale heartbeat detection

### 10d — Removed Progress Notifications
- Removed `notification_manager.update_downloading()` call from worker progress loop — only Dashboard UI shows real-time progress now

### 10e — Cleaner Filenames
- Changed yt-dlp output template from `"%(title)s [%(id)s].%(ext)s"` to `"%(title)s.%(ext)s"` — filenames no longer include the video ID
- Updated fallback file search to prefer title-based glob over video_id glob

---

## Phase 11 — README Rewrite

**Files changed:** `README.md`, `REFACTOR_LOG.md`

- Rewrote README in the style of MeTube/ytDownloader/VidBee with badges, architecture diagram, feature list, installation instructions, API reference, and notification system documentation
- Removed outdated references (aria2c, search page, embed settings from UI)
- Updated REFACTOR_LOG.md with Phase 10 and Phase 11 changelogs

---

## Phase 12 — Extension Popup Premium Redesign + Live Status

**Files changed:** `extension/popup.html`, `extension/popup.js`

### 12a — Premium Desktop-Utility Popup UI
- **Before:** Basic 320px dark popup with 3-column grid, borders on buttons, no header branding
- **After:** 320px ultra-dark floating panel (`#111111` bg, 22px corner radius, 0 12px 40px shadow)
- **Header:** 42px red circle icon with download arrow SVG + "yt-dl" title + green status dot + three-dot menu
- **Divider:** 5% opacity white separator
- **Grid:** 4-column layout (144p/240p/360p/480p top, 720p/1080p/1440p/2160p bottom)
- **Buttons:** Capsule style (18px radius, 54px tall), `#1b1b1b` surface, `#242424` hover, `#ff2d20` active with red glow shadow
- **Audio:** Centered 120px capsule below grid
- **Typography:** Inter/SF Pro, 16px bold title, 11px uppercase label (2px letter-spacing), 14px button text
- **Style influences:** Raycast, Arc Browser, Linear, Warp Terminal — no Material, no glassmorphism

### 12b — Live Connection Status
- **Before:** "Connected" was hardcoded text — no actual server check
- **After:** Fetches `/api/info` on popup open (3s timeout), shows green dot + "Connected" on success, red dot + "Disconnected" on failure. Polls every 10s.
- **Lines:** `popup.js` — `checkConnection()`

### 12c — Popup Width Adjustments
- Started at 280px (felt squashed), widened to 320px
- Increased corner radius to 32px, then settled at 22px
- Removed `.popup-card` wrapper, simplified to body-level styles for cross-browser consistency

---

## Phase 13 — Installer Rewrite + README Overhaul

**Files changed:** `install.sh` (created), `install.fish` (kept as fallback), `README.md`, `LICENSE` (created)

### 13a — `install.sh` (Bash Installer)
- **Problem:** `install.fish` required the Fish shell — not everyone has it installed
- **Solution:** Created `install.sh` with bash, works on any distro
- **Features:**
  - Auto-detects script directory (works from anywhere, not just git root)
  - Checks dependencies (python3, yt-dlp, flask, dbus-python)
  - Multi-distro package installation (pacman/apt/dnf detection)
  - Prompts for download directory with sensible default (`~/Downloads/yt-dl`)
  - Copies source files, templates, static assets, extension files
  - Detects installed browser (chromium, chrome, brave, edge, vivaldi)
  - Dynamically generates systemd service file with correct install path
  - Creates desktop entry and handler script
  - Generates default config.json with user's chosen download directory
  - Clear summary with next steps

### 13b — README Fixes
- Removed KDE-specific wording → "Desktop notifications"
- Removed pacman-specific prerequisites → `pip install --user flask dbus-python yt-dlp`
- Removed Brave-only wording → "Chromium-based browsers (Chrome, Brave, Edge)"
- Removed rickroll image
- Removed "inspired by" line
- Added MIT license badge + LICENSE file
- Clean competitor-level format with badges, features, quick start, API table, project structure, dependencies

---

## Current Project Status (2026-06-23)

### Complete
- Backend pipeline — worker.py with yt-dlp, JSON progress, process group kill, persistent thread, throttled updates
- Flask daemon — app.py with all routes, SSE streaming, graceful shutdown, migration
- Notifications — Two-tier: extension custom toasts (browser open) + D-Bus fallback (browser closed)
- Dashboard — Card-based reactive UI, filter tabs, bulk actions, progress bars, SSE-driven
- Stats page — Client-rendered with daily bar chart, live polling
- Logs page — Real-time SSE log streaming with level filters
- Settings page — Download directory, concurrent limit, theme toggle
- Extension — Context menu, quality popup, custom toast notifications, heartbeat polling
- Installer — bash install.sh with dependency checking and multi-distro support
- systemd integration — User service, auto-restart on failure
- Security — Shell injection fixed, log rotation, DB indexes
- README — Professional documentation with MIT license

### What's Left (Minor Improvements)
- **Notification popup redesign** — `notification.html` could match the new premium popup style (currently still the old basic dark card)
- **No daemon status badge in nav** — Dashboard/Stats/Logs pages don't show server connection status like the popup does
- **Desktop notifications page** — Could add a `/notifications` settings panel to configure notification preferences
- **Pause/Resume** — Currently only cancel, no pause/resume (yt-dlp limitation)
- **Playlist support** — Currently single-video only, no playlist/ channel download
- **Dark/light mode toggle in extension** — Popup doesn't respect theme setting
- **Extension settings sync** — Default quality is stored per-browser, not synced to server config

### Verdict
Project is **feature-complete for a v1.0**. What's listed above is polish/niche features, not blockers. The core loop (right-click → download → dashboard → notification) works end-to-end.

---

## Phase 14 — 9-Point Roadmap (Tier 1-3 Features)

**Goal:** Move from personal tool to competitive self-hosted downloader with stability, ease of setup, and parity with MeTube/ytDownloader.

**Files changed/created:** `src/updater.py`, `src/app.py`, `src/worker.py`, `src/models.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `extension/popup.js`, `extension/popup.html`, `extension/manifest.json`, `extension/store/PUBLISH.md`, `src/templates/base.html`, `src/templates/settings.html`, `src/templates/logs.html`, `src/static/style.css`

### 14a — Auto-update yt-dlp (Background)
- **Created:** `src/updater.py`
- **What:** Background daemon thread that runs `pip install --upgrade yt-dlp` on startup then every 24h
- **Graceful:** Logs success/failure to ring log, no crash on failure, handles timeout and externally-managed-environment errors
- **No UI:** Fully automatic — no buttons, no settings

### 14b — Docker
- **Created:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- **Base:** `python:3.14-slim` with ffmpeg, Flask, yt-dlp, dbus-python installed
- **Volumes:** `yt-dl-data` for DB/config, bind mount for download directory
- **Port:** 5000 mapped to host
- **Env:** `YTDL_API_KEY` passed through for auth

### 14c — Cookies Upload
- **3 new endpoints:** `GET/POST/DELETE /api/settings/cookies`
- **Settings card:** Upload button + status indicator + remove button
- **Worker integration:** If `~/.local/share/yt-dl/cookies.txt` exists, appends `--cookies <path>` to yt-dlp command

### 14d — Playlist Parsing
- **Built into `api_add_job`:** Detects `list=` or `/playlist/` in URL
- **Uses** `yt-dlp --flat-playlist --dump-json` to extract entries
- **Dedup:** Checks completed downloads by video_id, skips existing
- **Limit:** 50 videos max per playlist
- **Returns** `{"status": "playlist", "title": ..., "total": N, "added": N, "skipped": N}`

### 14e — API Key Auth
- **Env var:** `YTDL_API_KEY`
- **Decorator** `@require_auth` applied to all POST/PUT/DELETE routes via regex substitution in app.py
- **`/api/info`** returns `auth_required: bool`
- **UI warning:** Settings page shows banner if no API key set
- **Header:** `Authorization: Bearer <key>`

### 14f — Media Server Naming
- **New config key:** `output_pattern` (default `%(title)s.%(ext)s`)
- **Settings field:** Text input with example `%(channel)s/%(title)s.%(ext)s` for Plex/Jellyfin channel subdirectories
- **Worker:** Reads `output_pattern` from `cfg` instead of hardcoded string

### 14g — Extension Theme Sync
- **Popup fetches** `GET /api/settings`, reads `theme` field
- **Applies** `data-theme="light"` on `<html>` with CSS `:root` + `[data-theme="light"]` variable overrides
- **Light vars:** `#ffffff` bg, `#f5f5f5` surface, `#1a1a1a` text, `#71717a` secondary
- **Cache:** Stores theme in `chrome.storage.local` for instant load

### 14h — Dashboard Connection Indicator
- **Nav bar:** Green/red dot + "connected"/"disconnected" text
- **Pings** `/api/info` on page load and every 15s
- **Inline in base.html** — no separate JS file needed

### 14i — Web Store Assets
- **Created:** `extension/store/PUBLISH.md` with full submission guide
- **Updated manifest.json:** Added `homepage_url`, `default_title`, richer description
- **Guide includes:** Screenshot requirements, promo tile sizes, store listing copy, permissions justification, Firefox-specific manifest notes

### Bugs Fixed During Phase 14
- **Logging deadlock:** `RingBufferLogHandler` was a `logging.Handler` subclass but its `self.lock` overrode `Handler`'s internal lock → deadlock on first emit. Renamed to `self.buf_lock`.
- **Flask access log noise:** Suppressed werkzeug logger to WARNING. Changed ring buffer from stdout/stderr redirect to proper `logging.Handler` attached to yt-dl logger.
- **Log level mismatch:** CSS had `log-WARN`, `log-SUCCESS`, `log-PROGRESS` classes that never matched Python's standard levels (WARNING, INFO, DEBUG, ERROR, CRITICAL). Replaced with correct class names.

## Phase 15 — Code Review Bug Fixes (June 24)

**Goal:** Fix all issues found during full code review — Docker compat, install script, extension SW persistence, cross-env D-Bus safety, audio flags, config hygiene, webhook support.

**Files changed:** `Dockerfile`, `src/notifications.py`, `src/worker.py`, `src/models.py`, `src/app.py`, `src/updater.py`, `install.sh`, `extension/background.js`, `extension/manifest.json`

### 15a — Docker
- `python:3.14-slim` → `python:3.12-slim` (3.14 tag didn't exist, build would fail immediately)
- Removed `dbus-python` from pip install (not needed in Docker, was a dead dependency)
- `pip install flask yt-dlp` — both packages available on PyPI
- `ENV YTDL_BIND=0.0.0.0` so container binds on all interfaces

### 15b — Guarded D-Bus Import
- `notifications.py: import dbus` → `try: import dbus as _dbus_module` with `_dbus_import_ok` flag
- Daemon no longer crashes on machines without `dbus-python` (including Docker)
- Clean fallback: logs "dbus-python not installed — D-Bus disabled", sets `_iface = None`
- All `dbus.XXX` calls replaced with `_dbus_module.XXX`

### 15c — Hardcoded Path Fix
- `_on_action` Open Folder used `os.system('xdg-open "/mnt/storage/YouTube" &')` — ignored user's configured `download_dir`
- Fixed: reads `load_config()["download_dir"]` at runtime

### 15d — Worker COOKIES_PATH Hygiene
- `worker.py` hardcoded `Path.home() / ".local/share/yt-dl/cookies.txt"` — duplicated from `app.py`/`models.py`
- Fixed: imports `DATA_DIR` from `models`, constructs `COOKIES_PATH = DATA_DIR / "cookies.txt"`

### 15e — Audio Flag Conflict
- `--merge-output-format mp4` and `--extract-audio` were both in the command unconditionally
- yt-dlp doesn't like mixing these — can produce unexpected behavior for audio-only jobs
- Fixed: `--merge-output-format mp4` only appended for non-audio jobs

### 15f — Invalid yt-dlp Syntax
- `QUALITY_MAP["audio"] = "bestaudio/best[audioonly]"` — `[audioonly]` is not a valid yt-dlp format filter
- Fixed: `"bestaudio/best"`

### 15g — Missing Config Keys
- `DEFAULT_CONFIG` was missing `playlist_limit` and `max_log_lines` — both were hardcoded in `app.py`
- Fixed: added `"playlist_limit": 200`, `"max_log_lines": 500`, `"webhook_url": ""`

### 15h — functools.wraps
- `@require_auth` manually set `wrapper.__name__ = f.__name__` instead of using decorator utils
- Could cause Flask duplicate endpoint errors in edge cases
- Fixed: `@functools.wraps(f)` — also added `import functools`

### 15i — Playlist Limit from Config
- Hardcoded `entries[:50]` in playlist handler
- Fixed: reads `cfg.get("playlist_limit", 200)` from config

### 15j — Configurable Host/Port
- Host `127.0.0.1` and port `5000` were hardcoded at bottom of `app.py`
- Incompatible with Docker (needs `0.0.0.0`)
- Fixed: reads `YTDL_BIND` and `YTDL_PORT` env vars (defaults: `127.0.0.1`, `5000`)
- Dockerfile sets `ENV YTDL_BIND=0.0.0.0`

### 15k — pip → sys.executable
- `updater.py` called bare `pip install` — targets system pip, not the venv/python running the app
- Fixed: `[sys.executable, "-m", "pip", "install", ...]`

### 15l — install.sh Syntax Error
- Line 115: `cp "${SCRIPT_DIR}/extension/manifest.json "${EXT_DIR}/"` — missing closing `"` after `manifest.json`
- Since `set -euo pipefail`, this kills the entire install before extension is copied
- Fixed: `cp "${SCRIPT_DIR}/extension/manifest.json" "${EXT_DIR}/"`

### 15m — Extension Service Worker Persistence
- `setInterval` for heartbeat (30s) and poll (5s) is unreliable in MV3 — service worker gets evicted on inactivity (~30s)
- Fixed: `chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 })` and `chrome.alarms.create('poll', { periodInMinutes: 0.1 })` — survives eviction
- Added `"alarms"` to `manifest.json` permissions
- Removed `dbusAvailable` variable (always false server-side now), removed `startHeartbeat()`, `startPolling()` interval functions

### 15n — Webhook Support (Bonus)
- **New config key:** `webhook_url` in `DEFAULT_CONFIG` (empty string = disabled)
- **New function:** `_fire_webhook(job)` in `worker.py` — fires `curl -X POST` with JSON payload on job completion/failure
- **Payload:** `{event, job_id, title, quality, file_path, file_size, error}`
- **Called in** `finally` block of `run_download()` — after notifications, before `process_queue()`
- Differentiator: none of MeTube/TubeArchivist/Pinchflat support webhooks

### Bugs Fixed During Phase 15
- **Docker build failure:** `python:3.14-slim` doesn't exist on Docker Hub. Build failed immediately.
- **Daemon crash on no dbus-python:** Unconditional `import dbus` crashed the daemon on plain Debian/Ubuntu/Docker. Fixed with guarded import.
- **Open Folder ignored user config:** Always opened `/mnt/storage/YouTube` regardless of configured `download_dir`. Fixed.
- **COOKIES_PATH duplication:** `worker.py` had independent path logic from `models.py`. Fixed.
- **Audio quality syntax:** `best[audioonly]` is not valid yt-dlp, silently falls back to `best`. Fixed.
- **Service worker eviction:** Extension notifications stopped after ~30s of inactivity. `setInterval` is unreliable in MV3. Fixed with `chrome.alarms`.
- **Install.sh broken:** Missing quote killed extension copy on native install. Fixed.

## Current Project Status (2026-06-24)

### All Features Complete
- Backend pipeline — worker.py (yt-dlp, JSON progress, process group kill, persistent thread)
- Flask daemon — app.py (all routes, SSE, graceful shutdown, auth, YTDL_BIND/YTDL_PORT)
- Notifications — Extension custom popup toasts only (D-Bus disabled, guarded import)
- Dashboard — Card-based reactive UI, SSE-driven, filter tabs, bulk actions
- Stats — Client-rendered bar chart, live polling
- Logs — Real-time SSE with level filters
- Extension — Context menu, quality popup, theme sync, connection status, alarms-based persistence
- Playlist support — Auto-detect, individual jobs, dedup, configurable limit
- Cookies support — Upload via Settings, passed to yt-dlp
- Docker — python:3.12-slim, ffmpeg, YTDL_BIND env
- Auto-update — Background yt-dlp updater (yt-dlp -U then sys.executable -m pip)
- Auth — Optional YTDL_API_KEY, functools.wraps decorator
- Media naming — Configurable output patterns
- Webhook — POST on completion/failure, configurable URL
- Installer — bash install.sh (multi-distro)
- systemd — User service, auto-restart
- Security — Shell injection fixed, log rotation, DB indexes
- README — Professional documentation with MIT license

### Bugs Fixed (2026-06-24)
- **Notification race:** Extension `initNotificationSystem()` skipped polling entirely when D-Bus was available. User only got slow D-Bus notifications instead of extension popups. Fixed: extension now always polls and shows popups regardless of D-Bus status.
- **D-Bus init crash:** `add_signal_receiver()` threw because it requires a GLib main loop. Entire D-Bus init failed, `_iface` stayed None, all Notify() calls silently skipped. Fixed: split init so basic Notify works without action signals.
- **D-Bus disabled:** Turned off entirely per user preference. Extension is the only notification path now. (D-Bus code left in place for future re-enable.)
- **Missing "started" transition:** Extension only tracked `downloading → completed/failed`. `queued → downloading` showed no notification. Fixed: added `started` notification type for download start.
- **Popup window crash on null focus:** `chrome.windows.getLastFocused` callback's `win` can be null. Fixed: added fallback to `screen.availWidth/Height`.
- **Worker filename capture with `/` in title:** Titles like "M/V" caused `Path.glob("*M/V*")` to interpret `/` as directory separator. Fixed: `.replace("/", "⧸")` in glob safe_title.
- **Auto-updater on Arch:** `pip install` blocked by PEP 668. Fixed: try `yt-dlp -U` first, fallback to pip with `--break-system-packages`. Also: bare `pip` → `sys.executable -m pip` for correct venv targeting.
- **Invalid URL accepted:** "not-a-url" created a job instead of returning 400. Fixed: added URL scheme validation.
- **Docker build failure:** `python:3.14-slim` doesn't exist. Fixed: `3.12-slim`. Also removed `dbus-python` dependency.
- **Daemon crash on no dbus-python:** Unconditional `import dbus` killed daemon on Docker/bare Ubuntu. Fixed: guarded import.
- **Open Folder ignored user config:** Always opened hardcoded path. Fixed: reads from config at runtime.
- **COOKIES_PATH duplication:** `worker.py` had independent path logic. Fixed: imports `DATA_DIR` from models.
- **Audio flag conflict:** `--merge-output-format mp4` + `--extract-audio` both in command. Fixed: conditional.
- **Invalid yt-dlp syntax:** `best[audioonly]` is not valid. Fixed: `bestaudio/best`.
- **Extension SW eviction:** `setInterval` died on SW eviction, notifications stopped. Fixed: `chrome.alarms` API.
- **Install script broken:** Missing `"` on manifest.json path. Fixed.

### Known Limitations
- **Pause/Resume:** Not supported (yt-dlp limitation)
- **Web Store:** Guide provided but not yet submitted (requires $5 Chrome Web Store fee)
- **Firefox:** Requires beta build with MV3 background.scripts support


### Post-Phase 15 Cleanup — Settings UI, Version Bump, Docker Env

**Issue:** `saveSettings()` only sent `download_dir`, `concurrent_limit`, `output_pattern`. The new fields `webhook_url`, `playlist_limit`, `default_quality`, `embed_*`, `theme` were never saved — users couldn't configure them at all.

**Fix:** Rewrote the inline `saveSettings()` in `settings.html` to send every config key. Added input fields for `webhook_url` and `playlist_limit` to the settings page (they were missing from the HTML entirely). Added a Default Quality dropdown and Embed Options checkboxes section. The settings route now passes all config values as template variables so the form is pre-populated on load.

**Additional fixes:**
- Bumped `version` in `/api/info` from `1.0` → `1.1`
- Added `YTDL_BIND=0.0.0.0` to `docker-compose.yml` environment
- Removed dead `src/static/settings.js` (all logic moved inline into settings.html template)

**Files changed:** `docker-compose.yml`, `src/templates/settings.html`, `src/app.py`, `src/static/settings.js` (dead code)

---

## Phase 16 — In-Page Toast Overlay + Dead Code Removal (June 24)

**Goal:** Replace OS-level notification popups (`chrome.windows.create`) and native browser notifications (`chrome.notifications.create`) with an in-page toast that slides in from bottom-right inside whatever tab you're currently on.

### 16a — `chrome.scripting.executeScript` Toast Injection

- **Problem:** `chrome.windows.create` with `type: 'panel'` still gets KDE window decorations on Linux (title bar, borders). `chrome.notifications.create` shows as a Brave-branded OS notification. Neither feels like an app-integrated notification.
- **Content script approach failed:** `content_scripts` in `manifest.json` only inject into newly-loaded pages, not already-open tabs. All existing tabs had no listener, so `chrome.tabs.sendMessage` always rejected, falling back to `chrome.notifications.create`.
- **Solution:** Use `chrome.scripting.executeScript` with `func: injectToast` (serialized function + args) to inject a self-contained toast-builder into every open tab on-demand.
- **How it works:**
  1. `showNotification()` builds a `data` object (type, title, meta, thumb, jobId)
  2. Queries tabs with `{ active: true, currentWindow: true }` (shows toast only on the tab you're looking at)
  3. Calls `chrome.scripting.executeScript({ target: { tabId }, func: injectToast, args: [data] })`
  4. `injectToast` runs inside the tab's isolated world:
     - Creates `<style id="ytdl-toast-style">` with all CSS embedded (once per page)
     - Creates/reuses `<div id="ytdl-toast-container">` fixed bottom-right
     - Builds toast DOM: accent bar → thumbnail → app name → title → video title → meta row → retry button
     - Animates in with `@keyframes ytdl-slide-in` (300ms, translateX + scale)
     - Auto-dismisses after 3s (5s for failed) with `ytdl-slide-out` animation
  5. If ALL tabs fail (chrome:// page, no tabs open), falls back to `chrome.notifications.create`

### 16b — Immediate "Queued" Toast on Right-Click

- Previously, the context menu handler queued the job but didn't show any visual feedback until the next poll cycle (6s later).
- Now: on successful `POST /api/add`, injects an immediate "queued" toast on the right-clicked tab using the same `injectToast` function.
- Single-video: shows "Added to Queue" with quality. Playlist: shows playlist title.
- Extension now feels responsive instantly rather than waiting for polling.

### 16c — Context Menu Enhancement

- Added `'video'` and `'audio'` to `contexts` in `background.js` — right-clicking directly on embedded video players or audio elements now shows "Download with yt-dl", not just links.

### 16d — `notifications.py` Complete Removal

- **Context:** D-Bus was deliberately disabled (user preference). All `NotificationManager.show_*` methods returned immediately without calling `_notify`. The entire file was dead code.
- **Deleted:** `src/notifications.py` (185 lines)
- **Cleanup in `src/worker.py`:**
  - Moved `logger = logging.getLogger("yt-dl")` above `_fire_webhook` (was previously after it — worked by accident since Python resolves names at call time)
  - Removed `from notifications import NotificationManager`
  - Removed `notification_manager` global variable
  - Removed `set_notification_manager()` function
  - Removed all `if notification_manager:` guards (4 blocks: show_queued, update_downloading, show_done/show_failed, show_cancelled)
- **Cleanup in `src/app.py`:**
  - Removed `NotificationManager, set_action_callbacks, set_extension_heartbeat, clear_extension_heartbeat, is_extension_alive, dbus_available` from imports
  - Removed `nm = NotificationManager()` and `set_notification_manager(nm)` and `set_action_callbacks(...)`
  - Removed `clear_extension_heartbeat()` from shutdown handler
  - Hardcoded `"dbus_available": False` in `/api/info` response
  - Stripped `set_extension_heartbeat()` / `clear_extension_heartbeat()` from extension endpoints (heartbeat/register/unregister) — they now just log and return `{"ok": True}`

**Files changed:** `src/notifications.py` (deleted), `src/worker.py`, `src/app.py`, `extension/background.js`, `extension/manifest.json`, `extension/toast.css` (deleted), `extension/toast.js` (deleted)

### Manifest Change

- Removed `content_scripts` entry (no longer needed — toast is injected via `scripting.executeScript`)
- Added `"scripting"` permission
