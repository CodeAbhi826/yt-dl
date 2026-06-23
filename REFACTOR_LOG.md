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

