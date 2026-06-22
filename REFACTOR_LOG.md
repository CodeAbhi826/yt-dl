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
- **After:** Signal handlers iterate `active_jobs` (imported from worker) and terminate each process group. Clean exit.
- **Lines:** New signal handler function, `__main__` block

### 2d — Remove Dead Imports
- **Before:** `app.py` imported `active_jobs` (unused), `human_bytes` (unused — stats hardcoded "0.0 B"), `socket` (only used by removed `find_free_port`).
- **After:** Removed all three.
- **Lines:** Import block

### 2e — Delete `templates.py`
- **Before:** `src/templates.py` was never imported anywhere — 178 lines of dead code duplicating `app.py`'s inline templates.
- **After:** Deleted.
- **Lines:** File removed.

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
- **Lines:** `extension/background.js` — lines 43-48, 88-93 removed

### 3f — Add "Download This Page" to Extension Popup
- **Before:** Popup only set default quality — no way to trigger a download from the popup.
- **After:** Added a "Download this page" button that calls `POST /api/add` with the current tab's URL. Shows status feedback.
- **Lines:** `extension/popup.html` (new button), `extension/popup.js` (new handler)

---

## Phase 4 — Template Extraction (`templates/` + `static/`)

**Files created/removed:** `src/templates/`, `src/static/`, removed inline HTML from `app.py`

### 4a — Created `src/static/`
- `style.css` — All CSS variables, layout, card styles, progress bars, tags, buttons
- `theme.js` — Theme toggle logic
- `toast.js` — Toast notification helper
- `dashboard.js` — Queue page JS (with SSE + reactive DOM)
- `stats.js` — Stats page JS
- `logs.js` — Live logs JS
- `search.js` — Search page JS
- `settings.js` — Settings page JS

### 4b — Created `src/templates/`
- `base.html` — Shared layout: nav bar, theme toggle, toast container, head with styles/scripts
- `dashboard.html` — Queue page (extends base.html)
- `settings.html` — Settings page
- `stats.html` — Statistics page
- `logs.html` — Live logs page
- `search.html` — Search page

### 4c — Updated Routes
- All routes changed from `render_template_string(XXX_HTML, ...)` to `render_template("xxx.html", ...)`
- Flask app configured with `template_folder` and `static_folder`

---

## Phase 5 — SSE Queue Stream + Reactive DOM

**Files changed:** `src/app.py`, `src/static/dashboard.js`

### 5a — `/api/queue/stream` SSE Endpoint
- **Before:** Client polled `/api/queue` every 3s via `setInterval` — network overhead even when nothing changed.
- **After:** Server-Sent Events endpoint pushes queue JSON every 1s. Only emits when data actually changes (tracks hash of last sent data).
- **Lines:** `app.py` — new route

### 5b — Reactive DOM Diffing (No Nuke & Pave)
- **Before:** `renderQueue(jobs)` did `tbody.innerHTML = jobs.map(...).join('')` — destroyed all DOM state (checkboxes, CSS transitions).
- **After:** On SSE data, iterates jobs and:
  - If row exists: updates only `.progress-fill` width, speed/ETA text, status tag
  - If new: inserts a new card with `requestAnimationFrame` for smooth entry
  - If removed: fades out card then removes
  - Preserves checkbox selections and bulk-bar state
- **Lines:** `static/dashboard.js` — new `updateQueue(jobs)` function

### 5c — XSS Protection
- **Before:** Template literals like `${j.title}` directly injected user-provided text into DOM — XSS vector.
- **After:** All dynamic text wrapped in `escapeHtml()` function. Applied to title, URL, video ID, error message, file path.
- **Lines:** `static/*.js` — `escapeHtml()` added, all `${}` usages wrapped

### 5d — Progress Bar Animation
- **Before:** `transition: width 0.3s ease` — bar jumped every 3s poll cycle.
- **After:** `transition: width 1s linear` — smooth glide in sync with 1s SSE updates.
- **Lines:** `static/style.css`

### 5e — Heartbeat for Logs SSE
- **Before:** `/api/logs/stream` used `q.get(timeout=30)` — connection dropped after 30s of silence.
- **After:** Added `yield ": heartbeat\n\n"` every 15s. Connection stays alive.
- **Lines:** `app.py` — `/api/logs/stream`

---

## Phase 6 — Card-Based UI Redesign

**Files changed:** `src/static/style.css`, `src/static/dashboard.js`, `src/templates/dashboard.html`

### 6a — Card Layout
- **Before:** HTML `<table>` with rows for each download.
- **After:** CSS grid of `<div class="download-card">` elements. Each card: `#141414` background, 16px border-radius, 1px `#2a2a2a` border.

### 6b — Left Accent Border
- Each card has a 4px left border based on status:
  - Queued: `#666` (grey)
  - Downloading: `#ff2d20` (red)
  - Completed: `#22c55e` (green)
  - Failed: `#dc2626` (red)
  - Cancelled: `#f39c12` (orange)

### 6c — Visual Hierarchy
- Left: Thumbnail from `i.ytimg.com/vi/{id}/mqdefault.jpg` or circular "YT" fallback
- Middle-top: Bold white title + subtext line "720p • yt-dl"
- Middle-center: Smooth CSS-transitioned progress bar
- Middle-bottom: Status line "68% • 45.2 MB/s • 2m left"
- Right: Action buttons (Cancel/Retry/Delete/Open Folder)

### 6d — Progress Bar Colors
- Queued: `#666` grey fill
- Downloading: `#ff2d20` red fill
- Completed: `#22c55e` green fill
- Failed: `#dc2626` red fill

### 6e — Filter Tabs
- Row of buttons above cards: All / Active / Waiting / Done / Failed
- JS filters cards with CSS `display: none` for non-matching
- Active filter highlighted with accent color

### 6f — Global Speed Counter
- Top of dashboard: `⬇ Total: 2.3 MB/s • 📦 Active: 2`
- Computed by summing `job.speed` values for all downloading jobs
- Updates every SSE push

### 6g — Add URL Input
- Prominent input field at top of dashboard
- Quality dropdown + "Download" button
- Calls `POST /api/add`, shows toast on success/error
- Keyboard: Enter to submit

### 6h — Error Message Display
- **Before:** Failed jobs showed only a red "failed" tag — error text hidden.
- **After:** Red banner below the title showing `job.error_message`. Expandable.

### 6i — Open Folder Button
- For completed jobs: "Open Folder" button
- Calls `xdg-open` on the file's parent directory via new `/api/jobs/<id>/open` endpoint
- Alternatively opens the file location in file manager

### 6j — File Size Display
- Shows `human_bytes(j.file_size)` for completed jobs in the status line
- During download, shows estimated size from yt-dlp

### 6k — Theme Toggle Icon Swap
- **Before:** Always showed sun icon regardless of theme.
- **After:** Sun in dark mode (tooltip: "Switch to light"), moon in light mode (tooltip: "Switch to dark").

### 6l — Toast Consistency
- **Before:** Dashboard used `<div id="toast">`, stats page created divs via JS. Different behavior.
- **After:** Single `showToast()` pattern reused across all pages. 3s auto-dismiss.

---

## Phase 7 — Stats Page Overhaul

**Files changed:** `src/static/stats.js`, `src/templates/stats.html`, `src/app.py`

### 7a — Full Client-Side Rendering
- **Before:** `stats_page()` recomputed all stats from SQLite server-side (duplicating `/api/stats`), then JS fetched `/api/stats` again. Double work.
- **After:** Stats page renders as a shell. JS fetches `/api/stats` and populates all cards and charts. No duplicate computation.

### 7b — Zero-Data State
- **Before:** When no downloads existed, showed "0%" success rate — misleading.
- **After:** If `total_downloaded === 0`, shows "No downloads yet" message with a suggestion to add videos. Hides empty stats.

### 7c — Actual Byte Total
- **Before:** `/api/stats` hardcoded `"total_bytes": 0, "total_bytes_human": "0.0 B"`.
- **After:** API query: `SELECT COALESCE(SUM(file_size), 0) FROM downloads WHERE status='completed'`. Uses `human_bytes()` for display.

---

## Phase 8 — Security & Polish

**Files changed:** `yt-dl-handler.sh`, `src/models.py`, `src/app.py`

### 8a — Shell Injection Fix
- **Before:** `yt-dl-handler.sh:19`: `python3 -c "import json; d={'url':'$URL',...}"` — `$URL` interpolated into Python string, single-quote broke it.
- **After:** Uses `python3 -c "..." -- "$URL" "$QUALITY"` with `sys.argv[1]` and `sys.argv[2]`. No shell injection.
- **Lines:** `yt-dl-handler.sh` — entire quality/JSON body generation

### 8b — Log Rotation
- **Before:** `daemon.log` grew unbounded with `FileHandler`.
- **After:** Uses `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=3)`. Log rotates at 5MB.

### 8c — DB Indexes
- Added indexes on `status`, `created_at`, `video_id` for faster queries.
- **Lines:** `models.py:init_db()`

### 8d — Pause/Resume
- New endpoints: `POST /api/jobs/<id>/pause` (sends SIGSTOP) + `POST /api/jobs/<id>/resume` (sends SIGCONT)
- Cards show Pause (active) / Resume (paused) buttons
- **Lines:** `app.py` (routes), `worker.py` (pause_job/resume_job), `static/dashboard.js` (buttons)

---

## Phase 9 — Stats Bar Chart Fix

**Files changed:** `src/static/style.css`, `src/static/stats.js`

### 9a — Zero-Height Bar Fix
- **Before:** Bars with 0 count showed `min-height: 4px` — tiny bars for empty days.
- **After:** Bars with 0 count hidden (no bar shown). Labels for empty days shown in muted color.

### 9b — Date Label Alignment
- **Before:** `.bar-label` used `position: absolute; bottom: -20px; transform: translateX(-50%)` — clipped on narrow screens.
- **After:** Uses `margin-top: 8px; text-align: center; position: static`. No clipping.
