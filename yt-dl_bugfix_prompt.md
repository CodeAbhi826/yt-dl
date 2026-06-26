# DeepSeek Prompt — Fix All Bugs in `CodeAbhi826/yt-dl`

## Context

You are going to fix bugs in the `yt-dl` project (https://github.com/CodeAbhi826/yt-dl). It is a self-hosted YouTube download daemon built on Flask + yt-dlp + SQLite, with a Chrome MV3 browser extension and a live web dashboard. The codebase is small (~5 Python files, ~6 JS files, ~5 HTML templates).

### Repo layout
```
yt-dl/
├── src/
│   ├── app.py            # Flask server, all API routes, SSE streams
│   ├── worker.py         # Download queue, yt-dlp subprocess, webhook, cancel/pause/resume
│   ├── models.py         # SQLite schema, config load/save, QUALITY_MAP, human_bytes
│   └── updater.py        # Background yt-dlp auto-updater (24h loop)
├── extension/
│   ├── manifest.json     # MV3 manifest
│   ├── background.js     # Service worker: context menu, alarm-based polling, toast injection
│   ├── popup.html / popup.js
│   └── notification.html / notification.js
├── src/templates/        # dashboard.html, stats.html, logs.html, settings.html, base.html
├── src/static/           # dashboard.js, stats.js, logs.js, theme.js, toast.js, style.css
├── Dockerfile, docker-compose.yml
├── install.sh, install.fish, yt-dl-handler.sh
└── config/yt-dl.service, config/yt-dl.desktop
```

### Stack
- Python 3.12, Flask (dev server, threaded=True), yt-dlp (subprocess), SQLite
- Chrome Extension Manifest V3 (service worker, chrome.alarms, chrome.scripting)
- SSE for live queue + live logs

### Goal
Apply every fix below. Do not refactor unrelated code. Do not change public API contracts unless a fix explicitly requires it. Preserve all existing behavior that is correct. After fixing, briefly summarize each change with file:line references.

---

## Bugs to fix

### 🔴 HIGH SEVERITY (data loss, broken features, security)

---

#### Bug 1 — Cancelling a download marks it as "failed" instead of "cancelled"

**File:** `src/worker.py` — `cancel_job()` (~line 303) and `run_download()` (~line 263-286)

**Problem:**
`cancel_job()` (under `queue_lock`) kills the process group, sets `job.status = "cancelled"`, deletes from `active_jobs`, and calls `save_job(job)`. But the `run_download()` thread is still blocked in `proc.wait()`. When the kill takes effect, `proc.wait()` returns a non-zero code, and this branch executes:

```python
if proc.returncode == 0 and job.file_path:
    job.status = "completed"
    ...
else:
    job.status = "failed"   # overwrites "cancelled"
    if not job.error_message:
        job.error_message = f"yt-dlp exited {proc.returncode}"
```

Then the `finally` block calls `save_job(job)` again, persisting `"failed"`. So the user clicks "Cancel" but sees the job flip to "FAILED" with a confusing "yt-dlp exited -15" message.

**Why it matters:** Cancel is a deliberate user action; surfacing it as a failure spams the failed list, breaks "Clear completed" UX, fires the webhook with `event: "failed"`, and poisons retry-all.

**Exact fix:**
In `run_download()`, before the returncode branch, check if the job was already cancelled:

```python
proc.wait()

# After process exits, re-acquire lock to check status atomically
with queue_lock:
    already_done = job.job_id not in active_jobs and job.status in ("cancelled",)

if already_done:
    # cancel_job already finalized this job — don't overwrite
    return

if not job.file_path or not os.path.exists(...) or os.path.getsize(...) == 0:
    ...

if proc.returncode == 0 and job.file_path:
    job.status = "completed"
    ...
else:
    job.status = "failed"
    ...
```

Alternatively (simpler): in `cancel_job()`, set `job.status = "cancelled"` AND a flag like `job.cancelled = True` on the object, and check that flag in `run_download()` before falling through to the failed branch.

---

#### Bug 2 — `started_at` is overwritten on every progress save

**File:** `src/worker.py:87` (`save_job`)

**Problem:**
```python
started_at=datetime.now().isoformat() if job.status == "downloading" else None,
```
`save_job` is called every ~1 second during download. Each call stamps `started_at = now`. So the stored `started_at` ends up being the **last** progress-update time, not the actual start time.

**Why it matters:** Stats page computes download duration as `completed_at - started_at`, which now reports a duration of ~0 seconds. Duration analytics are meaningless.

**Exact fix:**
Add a `started_at` field to `DownloadJob.__init__` (parse from row, or None). In `run_download()`, set `job.started_at = datetime.now().isoformat()` once, right before `proc = subprocess.Popen(...)`. In `save_job`, change the line to:
```python
started_at=job.started_at if job.status == "downloading" else None,
```
Wait — that would still null it when status changes. Better:
```python
started_at=job.started_at,
completed_at=job.completed_at,
```
And only set `job.completed_at` when transitioning to completed/failed/cancelled.

---

#### Bug 3 — Saving Settings silently resets the user's theme to "dark"

**File:** `src/templates/settings.html:136` (inside `saveSettings()`)

**Problem:**
```js
theme: document.getElementById("theme") ? document.getElementById("theme").value : "dark"
```
There is **no** element with `id="theme"` in `settings.html`. So this expression always evaluates to `"dark"`. Every time the user clicks "Save Settings", the PUT `/api/settings` request includes `theme: "dark"`, overwriting whatever they had chosen via the nav-bar theme toggle.

**Why it matters:** Users who switch to light theme lose their preference the first time they save any setting. Silent data loss.

**Exact fix — option A (read current DOM state):**
```js
theme: document.documentElement.getAttribute("data-theme") || "dark",
```

**Exact fix — option B (add a theme selector to settings):**
Add a `<select id="theme">` with `dark`/`light` options to the Settings page (near the other form fields), and keep the existing line as-is.

Pick option A unless the user asks for a UI control.

---

#### Bug 4 — Webhooks silently fail in Docker (no `curl` in image)

**Files:** `Dockerfile` and `src/worker.py:38` (`_fire_webhook`)

**Problem:**
`_fire_webhook()` shells out to `curl`:
```python
subprocess.run(["curl", "-s", "-X", "POST", url, ...])
```
But the Dockerfile only installs `ffmpeg`:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ...
```
`python:3.12-slim` does NOT include `curl`. So in the recommended Docker deployment, **every webhook call fails silently** with `FileNotFoundError`. The README prominently advertises webhook support (Home Assistant, Discord, Ntfy, Jellyfin).

**Why it matters:** A headline feature is broken in the headline deployment method. Users will assume their webhook URL is wrong and waste time debugging.

**Exact fix — option A (add curl to Dockerfile):**
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*
```

**Exact fix — option B (preferred — use Python stdlib, remove curl dependency):**
Replace the `subprocess.run(["curl", ...])` call in `_fire_webhook` with `urllib.request`:
```python
import urllib.request
import urllib.error

def _fire_webhook(job):
    try:
        cfg = load_config()
        url = cfg.get("webhook_url", "").strip()
        if not url:
            return
        payload = json.dumps({...}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        logger.warning(f"Webhook error: {e}")
```
This removes the external dependency entirely. Pick option B.

---

#### Bug 5 — Playlist handler hardcodes YouTube URLs for every entry

**File:** `src/app.py:473`

**Problem:**
```python
eurl = f"https://www.youtube.com/watch?v={eid}"
```
When a playlist is detected (from ANY site yt-dlp supports — SoundCloud, Twitch, Bandcamp, etc.), every entry's URL is rewritten to `youtube.com/watch?v=<id>`. For non-YouTube playlists, `<id>` is the site-specific ID, and the resulting URL is invalid — yt-dlp will fail on every entry.

**Why it matters:** Playlist support is advertised as working on 1000+ sites. In practice, only YouTube playlists work. Non-YouTube playlists produce 100% failed jobs.

**Exact fix:**
Use the entry's original URL from the `--flat-playlist --dump-json` output:
```python
eurl = entry.get("url") or entry.get("webpage_url") or entry.get("original_url")
if not eurl:
    # Fallback: only valid for YouTube
    if entry.get("ie_key") == "Youtube" or "youtube" in (entry.get("extractor_key", "") or "").lower():
        eurl = f"https://www.youtube.com/watch?v={eid}"
    else:
        logger.warning(f"Skipping playlist entry with no URL: {entry}")
        continue
```

Also, the dedup check `if eid in downloaded` only catches YouTube IDs. For multi-site support, dedup on the entry URL instead:
```python
existing_urls = {r["url"] for r in db.execute("SELECT DISTINCT url FROM downloads WHERE status='completed'").fetchall()}
...
if eurl in existing_urls:
    continue
```

---

#### Bug 6 — `file_path` is stored as basename, not absolute path

**File:** `src/worker.py:183-187, 229-233`

**Problem:**
The yt-dlp progress template uses `%(info.filename)s`:
```python
progress_template = (
    '{"percent":"%(progress._percent_str)s",'
    '"speed":"%(progress._speed_str)s",'
    '"eta":"%(progress._eta_str)s",'
    '"filename":"%(info.filename)s"}'
)
```
In yt-dlp, `info.filename` is the **basename** (e.g. `"My Video.mp4"`), not the full path. The worker stores it directly:
```python
filename = data.get("filename", "")
if filename:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".mp4", ".mkv", ".mp3", ".m4a"):
        job.file_path = filename   # basename only!
```
Then later:
```python
if not job.file_path or not os.path.exists(job.file_path) or os.path.getsize(job.file_path) == 0:
```
`os.path.exists("My Video.mp4")` is evaluated relative to the daemon's CWD — which is NOT the download dir. The check fails, the code falls through to the fragile glob-based fallback, and even if the glob succeeds, `job.file_path` is now an absolute path. But if the glob fails (e.g. unusual output pattern), `file_path` stays as the basename, the webhook payload reports `"file_path": "My Video.mp4"`, and the dashboard's "Open" button won't work.

**Why it matters:** Inconsistent path representation breaks webhooks, file deletion, and the "open folder" feature. Also makes the glob fallback trigger every time, which is slow and buggy (see Bug 13).

**Exact fix:**
Use `%(info.filepath)s` (absolute path) instead of `%(info.filename)s` (basename) in the progress template:
```python
progress_template = (
    '{"percent":"%(progress._percent_str)s",'
    '"speed":"%(progress._speed_str)s",'
    '"eta":"%(progress._eta_str)s",'
    '"filepath":"%(info.filepath)s"}'
)
```
And in the parser:
```python
filepath = data.get("filepath", "")
if filepath:
    job.file_path = filepath
```
This also makes the glob fallback (Bug 13) rarely necessary.

---

#### Bug 7 — Browser extension doesn't handle right-click on `<video>` / `<audio>` elements

**File:** `extension/background.js:298`

**Problem:**
The context menu is registered for `['link', 'video', 'audio']`:
```js
chrome.contextMenus.create({
  id: 'yt-dl-download',
  title: 'Download with yt-dl',
  contexts: ['link', 'video', 'audio']
});
```
But the click handler only reads `info.linkUrl`:
```js
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== 'yt-dl-download') return;
  const url = info.linkUrl;
  if (!url) return;
  ...
});
```
For `video` and `audio` contexts, `info.linkUrl` is undefined — only `info.srcUrl` is set (the media URL). So right-clicking a `<video>` on YouTube/Twitter/TikTok and picking "Download with yt-dl" silently does nothing.

**Why it matters:** The primary advertised interaction ("Right-click any link or video → Download with yt-dl") is broken for the most common case (right-clicking the video itself).

**Exact fix:**
```js
const url = info.linkUrl || info.srcUrl || info.pageUrl;
if (!url) return;
```

---

#### Bug 8 — Service-worker restarts flood the user with notifications

**File:** `extension/background.js:4` and `processJobs()` (lines 38-67)

**Problem:**
```js
let prevJobs = {};
```
`prevJobs` lives in memory. MV3 service workers are killed after ~30s of idleness and restarted on the next event. On every restart, `prevJobs = {}`, so the first poll sees every existing job as "new". The `if (!prev)` branch then fires a toast for every downloading job (started toast), every completed job (completed toast), etc. With 200 jobs in history, that's 200 toasts after every browser restart or SW resurrection.

**Why it matters:** Extremely poor UX. Users will disable the extension after the first restart.

**Exact fix:**
Persist `prevJobs` in `chrome.storage.local`. Load on startup, save after each poll:
```js
let prevJobs = {};

async function loadPrevJobs() {
  const { prevJobs: stored } = await chrome.storage.local.get('prevJobs');
  prevJobs = stored || {};
}

async function savePrevJobs() {
  await chrome.storage.local.set({ prevJobs });
}

// On SW startup:
loadPrevJobs();

// In processJobs, after building `seen`:
async function processJobs(jobs) {
  const seen = {};
  for (const job of jobs) {
    const prev = prevJobs[job.id];
    seen[job.id] = true;
    if (!prev) {
      // Only fire if job is actually new (created in last 60s) — avoid storm on first poll after restart
      // OR: skip first poll entirely after SW restart by tracking a "ready" flag
      if (prevJobs.__initialized) {
        if (job.status === 'downloading') showNotification('started', job);
        else if (job.status === 'completed') showNotification('completed', job);
      }
    } else {
      // transitions as before
      if (prev === 'downloading' && job.status === 'completed') showNotification('completed', job);
      else if (prev === 'downloading' && job.status === 'failed') showNotification('failed', job);
      else if (prev === 'queued' && job.status === 'downloading') showNotification('started', job);
    }
    prevJobs[job.id] = job.status;
  }
  for (const id in prevJobs) {
    if (!seen[id] && id !== '__initialized') delete prevJobs[id];
  }
  prevJobs.__initialized = true;
  savePrevJobs();
}
```

The `__initialized` flag ensures the first poll after a restart just hydrates state without firing toasts.

---

#### Bug 9 — Browser extension does not send API key → broken when `YTDL_API_KEY` is set

**Files:** `extension/background.js` (all `fetch` calls), `extension/popup.js`

**Problem:**
README says: "Optional API key auth for all write endpoints". The daemon's `require_auth` decorator checks `Authorization: Bearer <key>`. But every fetch in the extension omits the auth header:
```js
fetch(`${API_URL}/api/add`, { method: 'POST', headers: {...}, body: ... })
fetch(`${API_URL}/api/jobs/${id}/retry`, { method: 'POST' })
fetch(`${API_URL}/api/extension/heartbeat`, { method: 'POST' })
fetch(`${API_URL}/api/queue`)
fetch(`${API_URL}/api/jobs/${data.jobId}/retry`, { method: 'POST' })  // inside injected toast
```
If `YTDL_API_KEY` is set (which the README and `settings.html` warn users to do), every write request returns 401. The extension appears "connected" (popup check uses `/api/info` which has no auth) but every download attempt silently fails.

**Why it matters:** Security-conscious users who follow the README's advice get a broken extension.

**Exact fix:**
1. Add an API key field to `popup.html` and persist it via `chrome.storage.local`.
2. Add a helper `getAuthHeaders()` that returns `{ "Authorization": "Bearer " + key }` if a key is stored, else `{}`.
3. Spread the auth header into every fetch call's `headers`:
```js
async function apiFetch(path, opts = {}) {
  const { apiKey } = await chrome.storage.local.get('apiKey');
  const headers = { ...(opts.headers || {}) };
  if (apiKey) headers['Authorization'] = 'Bearer ' + apiKey;
  return fetch(`${API_URL}${path}`, { ...opts, headers });
}
```
4. Replace all `fetch(\`${API_URL}/...\`, ...)` calls with `apiFetch('/...', ...)`.
5. Also update the injected toast's retry button to use the same pattern (it currently hardcodes `fetch('http://127.0.0.1:5000/api/jobs/.../retry', ...)`).
6. Add a small "API Key" input to `popup.html` below the quality grid.

---

#### Bug 10 — Chrome alarm `periodInMinutes: 0.1` is silently clamped to 0.5

**File:** `extension/background.js:14, 19`

**Problem:**
```js
chrome.alarms.create('poll', { periodInMinutes: 0.1 });
```
Chrome MV3 enforces a minimum alarm period of 0.5 minutes (30 seconds) for unpacked/packed extensions (Chrome 120+). `0.1` is silently bumped to `0.5`. So the poll runs every 30 seconds, not every 6 seconds as the code implies. Notifications are delayed up to 30s after a download completes.

**Why it matters:** Delayed notifications defeat the purpose of "real-time" toasts. Users will think downloads are stalling.

**Exact fix:**
Either explicitly set `0.5` (and document the 30s notification latency), or use a different mechanism for tighter polling. Recommended: keep alarm at `0.5` for efficiency, AND have the daemon push notifications via SSE (the dashboard already uses SSE). Simpler immediate fix:
```js
chrome.alarms.create('poll', { periodInMinutes: 0.5 });
```
And update comments to reflect "poll every 30s".

---

#### Bug 11 — `_worker_loop` has a lost-wakeup race on `Event.clear()`

**File:** `src/worker.py:105-112`

**Problem:**
```python
def _worker_loop():
    while True:
        _queue_event.wait()
        _queue_event.clear()
        try:
            _process_queue()
        except Exception as e:
            logger.exception(f"Queue processing error: {e}")
```
Between `_queue_event.wait()` returning and `_queue_event.clear()` being called, another thread may call `process_queue()` → `_queue_event.set()`. That signal is then lost when `clear()` runs. If no further jobs finish to re-set the event, queued jobs can sit indefinitely.

**Why it matters:** Under rare timing, downloads get stuck in "queued" and never start until the next manual action.

**Exact fix:**
Use a `threading.Condition` with a counter, or re-check after clearing:
```python
def _worker_loop():
    while True:
        _queue_event.wait()
        # Drain any signals that arrived during processing by clearing AFTER re-check
        while _queue_event.is_set():
            _queue_event.clear()
            try:
                _process_queue()
            except Exception as e:
                logger.exception(f"Queue processing error: {e}")
        # Loop back to wait — if event was set during processing, is_set() was True, we processed again
```
Better: switch to a `queue.Queue` with a sentinel. Simplest acceptable fix is the loop above.

---

#### Bug 12 — Webhook fires for cancelled jobs

**File:** `src/worker.py:299`

**Problem:**
```python
finally:
    with queue_lock:
        if job.job_id in active_jobs:
            del active_jobs[job.job_id]
    save_job(job)
    _fire_webhook(job)   # unconditional
    process_queue()
```
`_fire_webhook` is called for every terminal state, including `"cancelled"`. The README documents the webhook as firing on "complete/fail". Downstream integrations (Home Assistant, Discord bots) receive unexpected `"cancelled"` events and may mis-handle them (e.g., a Discord bot posts "Download failed" when the user explicitly cancelled).

**Why it matters:** Webhook contract violation. Breaks downstream automations.

**Exact fix:**
```python
finally:
    with queue_lock:
        if job.job_id in active_jobs:
            del active_jobs[job.job_id]
    save_job(job)
    if job.status in ("completed", "failed"):
        _fire_webhook(job)
    process_queue()
```

---

#### Bug 13 — Fallback file finder doesn't search subdirectories

**File:** `src/worker.py:265-274`

**Problem:**
When the output pattern is `%(channel)s/%(title)s.%(ext)s` (a documented example), files land in `download_dir/<channel>/<title>.<ext>`. But the fallback uses `download_dir.glob(...)`:
```python
for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
    video_files.extend(download_dir.glob(f"*{safe_title}*{ext}"))
```
`glob` does NOT recurse. So subdirectory files are never found. The job ends up "completed" with `file_path = None` (or stale basename from progress events).

**Why it matters:** The README explicitly recommends `%(channel)s/%(title)s.%(ext)s` for media servers — but with that pattern, file tracking breaks.

**Exact fix:**
Use `rglob` instead of `glob`:
```python
for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
    video_files.extend(download_dir.rglob(f"*{safe_title}*{ext}"))
if not video_files:
    for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
        video_files.extend(download_dir.rglob(f"*{job.video_id}*{ext}"))
```
Also, with Bug 6 fixed (using `%(info.filepath)s`), this fallback should rarely trigger — but when it does, it must recurse.

---

#### Bug 14 — `bulk/retry` re-queues completed/cancelled jobs without filtering

**File:** `src/app.py:261-274`

**Problem:**
```python
db.execute(f"UPDATE downloads SET status='queued', progress=0, error_message=NULL, retry_count=retry_count+1 WHERE job_id IN ({placeholders})", tuple(ids))
```
No status filter. If the user selects a completed job (or a cancelled one) and clicks "Bulk Retry", the daemon re-downloads the file, overwriting it.

**Why it matters:** Silent data destruction. A user who selects "all" and clicks retry will re-download hundreds of completed videos.

**Exact fix:**
Add a status filter:
```python
db.execute(
    f"UPDATE downloads SET status='queued', progress=0, error_message=NULL, retry_count=retry_count+1 "
    f"WHERE job_id IN ({placeholders}) AND status IN ('failed', 'cancelled')",
    tuple(ids)
)
```
Also adjust the response count to reflect actual retries:
```python
return jsonify({"retried": c.rowcount})  # was: len(ids)
```

---

#### Bug 15 — Non-YouTube URLs collide on `job_id`

**File:** `src/app.py:432-492`

**Problem:**
```python
video_id = ""
m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
if m:
    video_id = m.group(1)
...
job_id = f"job_{int(time.time() * 1000)}_{video_id or 'unknown'}"
```
The video_id regex is YouTube-specific (11-char ID). For TikTok/Twitter/etc., `video_id` is empty, so `job_id = f"job_{int(time.time() * 1000)}_unknown"`. Two non-YouTube links queued in the same millisecond produce the same `job_id`, violating the `UNIQUE` constraint → second INSERT throws `sqlite3.IntegrityError` → 500 error.

Also breaks playlist dedup: `if eid in downloaded` is fine for YouTube, but for multi-site playlists the entry IDs collide.

**Why it matters:** Cannot rapidly queue cross-site URLs. Limits the "1000+ sites" promise.

**Exact fix:**
Use a more unique suffix:
```python
import uuid
job_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
```
Or include a hash of the URL:
```python
import hashlib
url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
job_id = f"job_{int(time.time() * 1000)}_{url_hash}"
```
Keep the YouTube 11-char extraction as `video_id` (for thumbnail display) but don't use it for uniqueness.

---

### 🟡 MEDIUM SEVERITY (UX issues, race conditions, edge cases)

---

#### Bug 16 — Timezone inconsistency: `created_at` (UTC) vs `started_at`/`completed_at` (local, no tz)

**Files:** `src/models.py:71`, `src/worker.py:87-88`, `src/static/dashboard.js:32`

**Problem:**
- `created_at` uses SQLite's `CURRENT_TIMESTAMP` → **UTC**.
- `started_at`/`completed_at` use `datetime.now().isoformat()` → **local time, no tz suffix**.
- Dashboard's `timeAgo()` appends `'Z'` to both, treating both as UTC.

Result: For a server in IST (UTC+5:30), a job that completed 5 minutes ago shows as "5h 35m ago" on the dashboard.

**Why it matters:** Every "time ago" label is wrong by the server's UTC offset. Confuses users.

**Exact fix:**
Use UTC everywhere:
```python
from datetime import datetime, timezone
# In save_job:
started_at=job.started_at,   # set once when starting, format: datetime.now(timezone.utc).isoformat()
completed_at=datetime.now(timezone.utc).isoformat() if job.status in (...) else None,
```
Apply the same to `started_at` initialization in `run_download`.

Also, in `dashboard.js`, the `timeAgo` function already appends `'Z'` for non-Zulu strings — that's correct, but only if the server actually stores UTC. With the fix above, it will be.

---

#### Bug 17 — `yt-dl-handler.sh` checks the wrong health endpoint

**File:** `yt-dl-handler.sh:13`

**Problem:**
```bash
if ! curl -s http://localhost:5000/api/health > /dev/null 2>&1; then
    systemctl --user start yt-dl
    sleep 2
fi
```
The actual endpoint is `/health` (no `/api` prefix). `curl -s` to `/api/health` returns 404 but exits 0 (curl only fails on network errors). So this check **always passes** regardless of daemon state. The `systemctl --user start yt-dl` and `sleep 2` lines never execute when the daemon is down — but they ALSO never execute when it's up (because curl exits 0 on 404). The check is effectively a no-op.

Wait — actually `curl -s` exits 0 on HTTP 404. So the `!` negates to false, and the body never runs. If the daemon is down (connection refused), curl exits non-zero, `!` negates to true, body runs. So actually... the logic works by accident for the "daemon down" case but the endpoint path is wrong.

Re-examining: `curl -s` with a 404 still returns exit 0 (curl succeeded in making the HTTP request). So if the daemon is UP, the script proceeds to the curl POST below (which works). If the daemon is DOWN, curl fails to connect, exits 7 (connection refused), `!` is true, body runs: start daemon + sleep. OK so the logic is correct for the binary case.

But there's still a subtle bug: the script uses `/api/health` which returns 404. A future change might add `/api/health` as a valid endpoint, or someone might add `--fail` to curl to make 4xx/5xx return non-zero. As-is, the check works by accident.

**Why it matters:** Subtle correctness issue that will break if curl flags change or if `/api/health` is added later. Also, the README and API reference list `/health`, not `/api/health` — so this is also a documentation inconsistency.

**Exact fix:**
Use the correct endpoint AND use `--fail` to make HTTP errors fail curl:
```bash
if ! curl -sf http://localhost:5000/health > /dev/null 2>&1; then
    systemctl --user start yt-dl
    sleep 2
fi
```

---

#### Bug 18 — `pause_job` / `resume_job` are dead code with latent bugs

**File:** `src/worker.py:330-349`, imported in `src/app.py:30`

**Problem:**
`pause_job` and `resume_job` are defined in `worker.py` and imported in `app.py`:
```python
from worker import (
    process_queue, cancel_job, retry_job, active_jobs, pause_job, resume_job,
    queue_lock
)
```
But no Flask route uses them. There's no `/api/jobs/<id>/pause` endpoint. They're dead code.

They also have latent bugs:
```python
def pause_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)   # no try/except
                ...
```
If the process has just exited (race between `.poll()` and `getpgid`), `ProcessLookupError` is raised inside the lock, propagating up.

**Why it matters:** Dead code is maintenance burden. Latent crash bugs hide until someone wires up the endpoints.

**Exact fix — option A (remove dead code):**
Delete `pause_job` and `resume_job` from `worker.py`, remove them from the import in `app.py`.

**Option B (wire them up + fix the bug):**
Add routes:
```python
@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
@require_auth
def api_pause_job(job_id):
    if pause_job(job_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not active"}), 404

@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
@require_auth
def api_resume_job(job_id):
    if resume_job(job_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not active"}), 404
```
And fix the latent bug:
```python
def pause_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                    logger.info(f"Paused job: {job_id}")
                    return True
                except (ProcessLookupError, PermissionError):
                    return False
    return False
```
Same for `resume_job`. Pick option A unless pause/resume is a planned feature.

---

#### Bug 19 — `stream_queue` SSE leaks the DB connection on exception

**File:** `src/app.py:541-565`

**Problem:**
```python
def event_stream():
    last_hash = ""
    while True:
        try:
            db = get_db()
            rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
            db.close()
            ...
        except Exception as e:
            logger.error(f"SSE error: {e}")
            yield ": error\n\n"
        time.sleep(1)
```
If `db.execute(...)` or `fetchall()` raises (e.g., DB locked, disk full), `db.close()` is skipped. Over time with many SSE clients and repeated errors, file descriptors accumulate.

**Why it matters:** Resource leak. Long-running daemons with SSE dashboard tabs open will eventually exhaust FDs.

**Exact fix:**
Use `try/finally` or context manager:
```python
db = get_db()
try:
    rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
finally:
    db.close()
```
Or, since `sqlite3.Connection` supports context manager (for transactions, not for closing), wrap it:
```python
with closing(get_db()) as db:
    rows = db.execute(...).fetchall()
```
(`from contextlib import closing`)

---

#### Bug 20 — `api_update_settings` accepts arbitrary keys + no validation

**File:** `src/app.py:280-289`

**Problem:**
```python
@app.route("/api/settings", methods=["PUT"])
@require_auth
def api_update_settings():
    cfg = load_config()
    updates = request.get_json() or {}
    cfg.update(updates)   # no whitelist, no validation
    save_config(cfg)
    if "max_log_lines" in updates:
        ring_log.max_lines = cfg["max_log_lines"]
    return jsonify(cfg)
```
Two problems:
1. **No whitelist:** A client can inject `{"foo": "bar"}` or worse, overwrite `download_dir` with a malicious path, `concurrent_limit` with `1000000`, etc. `cfg.update(updates)` accepts anything.
2. **No type validation:** `concurrent_limit` could be set to `"banana"` or `-5`. The worker would then `LIMIT "banana"` in SQL → crash. Or `LIMIT -5` → SQLite error. Or `concurrent_limit = 1000000` → 1M parallel yt-dlp processes → OOM kill.

**Why it matters:** Authenticated user (or anyone if no API key) can crash or DoS the daemon via a single PUT.

**Exact fix:**
Whitelist allowed keys and validate types:
```python
ALLOWED_SETTINGS = {
    "download_dir": str,
    "default_quality": str,
    "concurrent_limit": int,
    "theme": str,
    "output_pattern": str,
    "embed_metadata": bool,
    "embed_thumbnail": bool,
    "embed_chapters": bool,
    "embed_subs": bool,
    "playlist_limit": int,
    "max_log_lines": int,
    "webhook_url": str,
}

VALID_QUALITIES = {"144p","240p","360p","480p","720p","1080p","1440p","2160p","best","audio"}

@app.route("/api/settings", methods=["PUT"])
@require_auth
def api_update_settings():
    cfg = load_config()
    updates = request.get_json() or {}
    for key, value in updates.items():
        if key not in ALLOWED_SETTINGS:
            continue  # silently ignore unknown keys
        expected = ALLOWED_SETTINGS[key]
        if not isinstance(value, expected):
            return jsonify({"error": f"Invalid type for {key}: expected {expected.__name__}"}), 400
        if key == "concurrent_limit" and not (1 <= value <= 20):
            return jsonify({"error": "concurrent_limit must be 1-20"}), 400
        if key == "playlist_limit" and not (1 <= value <= 1000):
            return jsonify({"error": "playlist_limit must be 1-1000"}), 400
        if key == "default_quality" and value not in VALID_QUALITIES:
            return jsonify({"error": "Invalid quality"}), 400
        if key == "theme" and value not in ("dark", "light"):
            return jsonify({"error": "theme must be 'dark' or 'light'"}), 400
        cfg[key] = value
    save_config(cfg)
    if "max_log_lines" in updates:
        ring_log.max_lines = cfg["max_log_lines"]
    return jsonify(cfg)
```

---

#### Bug 21 — `api_logs` `count` param not validated (negative crashes)

**File:** `src/app.py:368-372`

**Problem:**
```python
@app.route("/api/logs")
def api_logs():
    level = request.args.get("level", "ALL")
    count = int(request.args.get("count", 100))
    return jsonify(ring_log.get_lines(count=count, level_filter=level))
```
And in `RingBufferLogHandler.get_lines`:
```python
if count:
    lines = lines[-count:]
```
If a client sends `?count=-1`, `lines[-(-1):]` = `lines[1:]` — actually no, `lines[-1:]` returns the LAST element, so `count=-1` returns the last log line. That's actually a weird but not crashing behavior.

But `?count=abc` raises `ValueError` → 500. And `?count=999999999` returns all lines anyway (buffer is 500). So the issues are: no error handling for non-int, no upper bound.

**Why it matters:** Minor — unhandled 500 errors pollute logs.

**Exact fix:**
```python
try:
    count = int(request.args.get("count", 100))
    count = max(1, min(count, 1000))
except (ValueError, TypeError):
    return jsonify({"error": "count must be an integer"}), 400
```

---

#### Bug 22 — `api_add_job` blocks Flask thread for up to 30s on playlist detection

**File:** `src/app.py:440-490`

**Problem:**
```python
result = subprocess.run(
    ["yt-dlp", "--flat-playlist", "--dump-json", "--no-download", url],
    capture_output=True, text=True, timeout=30
)
```
This runs synchronously inside the HTTP request handler. For a 200-video YouTube playlist, `yt-dlp --flat-playlist` can take 5-15 seconds. Flask's dev server is `threaded=True` (unlimited threads), so each concurrent playlist request spawns a thread that's blocked for up to 30s. 10 simultaneous playlist submissions = 10 blocked threads, each running a yt-dlp subprocess.

**Why it matters:** Easy DoS. Also degrades UX — the POST `/api/add` doesn't return until playlist enumeration is done, so the browser extension hangs.

**Exact fix:**
Enqueue the playlist-detection work as a background job:
1. Create the parent job immediately with `status='queued'` and `title='Detecting playlist...'`, return its `job_id` to the caller immediately.
2. Spawn a thread that runs `yt-dlp --flat-playlist`, then inserts child jobs and updates the parent.
3. Add a new status `"enumerating"` so the dashboard can show "Detecting playlist..." in the UI.

Simpler alternative: keep the synchronous flow but cap the timeout at 10s and run with a smaller buffer:
```python
result = subprocess.run(
    ["yt-dlp", "--flat-playlist", "--dump-json", "--no-download",
     "--playlist-end", "50", url],
    capture_output=True, text=True, timeout=10
)
```
This at least bounds resource use. The proper fix is the background approach.

---

#### Bug 23 — API key comparison is not constant-time

**File:** `src/app.py:136`

**Problem:**
```python
if auth != f"Bearer {API_KEY}":
    return jsonify({"error": "Unauthorized"}), 401
```
String `!=` comparison short-circuits on the first differing byte. A remote attacker can extract the API key byte-by-byte via timing differences (each wrong byte returns faster than a correct one). On localhost this is negligible; if `YTDL_BIND=0.0.0.0` (Docker default), it's exploitable across the network with enough samples.

**Why it matters:** Auth bypass via timing attack when daemon is exposed.

**Exact fix:**
```python
import hmac
if not hmac.compare_digest(auth, f"Bearer {API_KEY}"):
    return jsonify({"error": "Unauthorized"}), 401
```

---

#### Bug 24 — `api_open_path` allows opening any directory on the host

**File:** `src/app.py:248-259`

**Problem:**
```python
@app.route("/api/open", methods=["POST"])
@require_auth
def api_open_path():
    data = request.get_json() or {}
    path = data.get("path", "")
    if not path or not os.path.isdir(path):
        return jsonify({"error": "Invalid path"}), 400
    try:
        subprocess.Popen(["xdg-open", path])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```
The only validation is `os.path.isdir(path)`. Any authenticated user (or anyone if no API key set, which is the default) can pass `path=/etc`, `path=/root`, `path=/var/log` — `xdg-open` will open the file manager there. While not directly RCE, it leaks the existence and structure of arbitrary directories.

**Why it matters:** Information disclosure. Especially concerning because the default has no auth.

**Exact fix:**
Restrict to the configured download directory (and its subdirs):
```python
@app.route("/api/open", methods=["POST"])
@require_auth
def api_open_path():
    data = request.get_json() or {}
    path = data.get("path", "")
    cfg = load_config()
    download_dir = os.path.realpath(cfg.get("download_dir", ""))
    real_path = os.path.realpath(path)
    if not real_path.startswith(download_dir + os.sep) and real_path != download_dir:
        return jsonify({"error": "Path outside download directory"}), 403
    if not os.path.isdir(real_path):
        return jsonify({"error": "Invalid path"}), 400
    try:
        subprocess.Popen(["xdg-open", real_path])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

---

#### Bug 25 — `process_queue` is wrapped in a redundant `threading.Thread` (3 places)

**File:** `src/app.py:273, 484, 502`

**Problem:**
```python
threading.Thread(target=process_queue, daemon=True).start()
```
`process_queue()` is defined as:
```python
def process_queue():
    _queue_event.set()
    _start_worker()
```
It returns immediately — it doesn't block. Spawning a thread to call it is wasteful and confusing. Worse, if 100 callers all call this concurrently, you spawn 100 threads that each do nothing but set an event and call `_start_worker()` (which itself checks if the worker is alive and only starts one).

**Why it matters:** Wasteful thread creation. Not a correctness bug, but ugly.

**Exact fix:**
Replace all three occurrences with a direct call:
```python
process_queue()
```

---

#### Bug 26 — Docker container runs as root

**File:** `Dockerfile`

**Problem:**
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ...
RUN pip install --no-cache-dir flask yt-dlp
WORKDIR /app
COPY src/ ./src/
EXPOSE 5000
ENV PYTHONPATH=/app/src
ENV YTDL_BIND=0.0.0.0
CMD ["python3", "-u", "src/app.py"]
```
No `USER` directive. The Flask process runs as root inside the container. If there's any RCE (e.g., command injection via URL passed to yt-dlp), the attacker gets root in the container, which is a stepping stone to container escape.

Also, the volume `yt-dl-data:/root/.local/share/yt-dl` is created with root ownership — files written there can't be read by the host user without sudo.

**Why it matters:** Security best practice violation. Containers should not run as root.

**Exact fix:**
```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask yt-dlp

RUN useradd -m -u 1000 ytdl

WORKDIR /app
COPY --chown=ytdl:ytdl src/ ./src/

USER ytdl

EXPOSE 5000
ENV PYTHONPATH=/app/src
ENV YTDL_BIND=0.0.0.0
ENV HOME=/home/ytdl

CMD ["python3", "-u", "src/app.py"]
```
And update `docker-compose.yml` to mount the volume at `/home/ytdl/.local/share/yt-dl`:
```yaml
volumes:
  - yt-dl-data:/home/ytdl/.local/share/yt-dl
  - ./downloads:/mnt/storage/YouTube
```

---

#### Bug 27 — `api_upload_cookies` leaks absolute server path in response

**File:** `src/app.py:312`

**Problem:**
```python
return jsonify({"ok": True, "path": str(COOKIES_PATH)})
```
`COOKIES_PATH` is `~/.local/share/yt-dl/cookies.txt` — exposes the absolute filesystem path of the server. Minor info disclosure, but unnecessary.

**Why it matters:** Helps an attacker enumerate the server's filesystem layout.

**Exact fix:**
```python
return jsonify({"ok": True})
```

---

#### Bug 28 — `cfg.update(updates)` interaction with `max_log_lines` skips when set to 0

**File:** `src/app.py:287-288`

**Problem:**
```python
if "max_log_lines" in updates:
    ring_log.max_lines = cfg["max_log_lines"]
```
If the user sets `max_log_lines: 0` (perhaps to disable logging), `ring_log.max_lines = 0`. But `RingBufferLogHandler` uses `deque(maxlen=0)`, which silently drops every log entry. The daemon appears to stop logging. Worse, the existing deque isn't resized (the `max_lines` attribute is set, but the deque was created with `maxlen=500` originally).

**Why it matters:** Setting `max_log_lines` doesn't actually resize the existing deque. The configuration value is effectively ignored after init.

**Exact fix:**
Recreate the deque when `max_log_lines` changes:
```python
if "max_log_lines" in updates and isinstance(cfg["max_log_lines"], int) and cfg["max_log_lines"] > 0:
    with ring_log.buf_lock:
        new_buffer = deque(maxlen=cfg["max_log_lines"])
        new_buffer.extend(ring_log.buffer)
        ring_log.buffer = new_buffer
    ring_log.max_lines = cfg["max_log_lines"]
```
Also reject 0 and negative values in validation (Bug 20 fix).

---

### 🟢 LOW SEVERITY (cosmetic, dead code, inconsistencies)

---

#### Bug 29 — Version numbers are inconsistent across the codebase

**Files:**
- `src/app.py:158` — `"version": "1.1"`
- `src/app.py:592` — `"yt-dl v1.0 started"`
- `extension/manifest.json:4` — `"version": "1.0.0"`
- README badge — pulls from GitHub releases

**Why it matters:** Makes version reporting unreliable for debugging.

**Exact fix:**
Define a single `__version__` constant in a new `src/_version.py`:
```python
__version__ = "1.1.0"
```
Import in `app.py`, use in `/api/info` and the startup log line. Update `manifest.json` to `"1.1.0"`. Keep them in sync going forward.

---

#### Bug 30 — `install.sh` hardcodes `/usr/bin/python3`

**File:** `install.sh:138`

**Problem:**
```bash
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/src/app.py
```
On systems where python3 is elsewhere (Homebrew `/opt/homebrew/bin/python3`, pyenv `~/.pyenv/shims/python3`, NixOS), the service fails to start with "Exec format error" or "No such file".

**Why it matters:** Installer breaks on non-standard Python layouts.

**Exact fix:**
```bash
PYTHON_BIN="$(command -v python3)"
...
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/src/app.py
```

---

#### Bug 31 — `install.sh` still checks for `python-dbus` (deleted dependency)

**File:** `install.sh:44-46`

**Problem:**
```bash
if ! python3 -c "import dbus" 2>/dev/null; then
  MISSING+=" python-dbus"
fi
```
Per `REFACTOR_LOG.md`, `notifications.py` (which used `dbus`) was deleted. The `dbus` import check is now stale — it warns users about a missing dependency that isn't actually needed.

**Why it matters:** Confuses new users. They install `dbus-python` (which is non-trivial on some distros) for no reason.

**Exact fix:**
Delete the dbus check block (lines 44-46).

---

#### Bug 32 — `install.fish` references deleted `notifications.py`

**File:** `install.fish:60`

**Problem:**
```fish
echo "  Notifications: $INSTALL_DIR/src/notifications.py"
```
`notifications.py` was deleted (per `REFACTOR_LOG.md`). The installer prints a path to a non-existent file.

**Why it matters:** Misleading output. Users may try to inspect/configure a file that doesn't exist.

**Exact fix:**
Remove line 60, or replace with:
```fish
echo "  Notifications: handled by browser extension"
```

---

#### Bug 33 — Toast appears on whatever tab is active when download completes, not the tab where it was triggered

**File:** `extension/background.js:103`

**Problem:**
```js
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => { ... });
```
When a download finishes, the toast is injected into whichever tab is currently active. If the user triggered the download on tab A, then switched to tab B, the toast pops up on tab B (e.g., their online banking page). Injecting toast CSS/HTML into arbitrary pages can also break site layouts (the toast container has `z-index: 2147483647`).

**Why it matters:** Confusing UX. Potential CSS conflicts on sensitive sites.

**Exact fix:**
Track the source tab ID when the download is triggered:
```js
const sourceTabs = {};  // job_id -> tabId

// In context menu handler:
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  ...
  const body = await res.json();
  sourceTabs[body.job_id] = tab.id;
  ...
});

// In showNotification:
function showNotification(type, job) {
  const targetTabId = sourceTabs[job.id];
  if (targetTabId) {
    chrome.scripting.executeScript({
      target: { tabId: targetTabId },
      func: injectToast,
      args: [data]
    }).catch(() => fallbackNative(type, job, titles[type] || 'yt-dl'));
    delete sourceTabs[job.id];  // cleanup
    return;
  }
  // No source tab (e.g., added via curl) — use native notification
  fallbackNative(type, job, titles[type] || 'yt-dl');
}
```

---

#### Bug 34 — `manifest.json` requests `<all_urls>` host permission (overly broad)

**File:** `extension/manifest.json:15-20`

**Problem:**
```json
"host_permissions": [
  "http://127.0.0.1:5000/*",
  "*://*.youtube.com/*",
  "*://youtu.be/*",
  "<all_urls>"
]
```
`<all_urls>` grants the extension permission to read/modify every site the user visits. This triggers Chrome Web Store review warnings and scares privacy-conscious users. The extension only actually fetches from `127.0.0.1:5000` and injects toasts into the active tab — it doesn't need to read content from arbitrary sites.

**Why it matters:** Privacy concern. Hinders Web Store approval.

**Exact fix:**
Remove `<all_urls>`. Keep only what's needed:
```json
"host_permissions": [
  "http://127.0.0.1:5000/*",
  "http://localhost:5000/*"
]
```
The `activeTab` permission (already present) is sufficient for `chrome.scripting.executeScript` into the active tab after a user gesture (right-click). If you need to inject toasts without a user gesture (e.g., from the alarm-based poll), you'll need to add specific host permissions for the sites you want notifications on, OR use `chrome.notifications` (native) as the default and only inject toasts when triggered by the context menu.

---

#### Bug 35 — `safe_title` sanitization has a dead `replace("/", "⧸")`

**File:** `src/worker.py:267`

**Problem:**
```python
safe_title = "".join(c for c in (job.title or "") if c.isalnum() or c in " _-.")[:60].replace("/", "⧸")
```
The list comprehension already filters to `isalnum() or c in " _-."` — `/` is not in that set, so it's removed before the `.replace("/", "⧸")` runs. The replace is a no-op.

**Why it matters:** Dead code. Misleading — a reader might think `/` is being preserved as `⧸` for cross-filesystem compatibility, but it's actually being stripped.

**Exact fix:**
Remove the `.replace(...)`:
```python
safe_title = "".join(c for c in (job.title or "") if c.isalnum() or c in " _-.")[:60]
```
Or, if the intent was to preserve slashes (for the `%(channel)s/%(title)s` pattern), rethink the fallback to operate on the channel+title path components separately. But for a glob pattern, stripping `/` is correct — so just remove the dead replace.

---

#### Bug 36 — `--format` is passed to `--dump-json --no-download` (ignored, wasteful)

**File:** `src/worker.py:155`

**Problem:**
```python
info_cmd = ["yt-dlp", "--format", format_str, "--dump-json", "--no-download", job.url]
```
`--dump-json --no-download` causes yt-dlp to print info JSON and exit without downloading. The `--format` flag has no effect on info extraction (it only matters during actual download). It's wasted CLI parsing.

**Why it matters:** Cosmetic. Slightly slower startup. Misleading to readers who think the format is being validated.

**Exact fix:**
```python
info_cmd = ["yt-dlp", "--dump-json", "--no-download", job.url]
```

---

#### Bug 37 — `api_add_job` loads full config from disk just to read `default_quality`

**File:** `src/app.py:425`

**Problem:**
```python
quality = data.get("quality", load_config().get("default_quality", "720p"))
```
`load_config()` reads `~/.local/share/yt-dl/config.json` from disk on every POST. The browser extension always sends `quality` (from the popup selection), so this fallback rarely fires. But when it does, it's a synchronous disk read in the request path.

**Why it matters:** Minor performance issue under load.

**Exact fix:**
Read quality with a sensible default, then load config only if needed:
```python
quality = data.get("quality")
if not quality:
    quality = load_config().get("default_quality", "720p")
```
Or cache the config in memory with a file watcher. The simpler fix above is fine.

---

#### Bug 38 — `api_queue` returns only latest 200 jobs (no pagination)

**File:** `src/app.py:179-184`

**Problem:**
```python
rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
```
Hard limit of 200. Users with longer history can never see older jobs via the API. The dashboard also can't show them.

**Why it matters:** Limits usefulness for power users.

**Exact fix:**
Add optional `offset` and `limit` query params:
```python
@app.route("/api/queue")
def api_queue():
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid pagination params"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    db.close()
    return jsonify([job_to_dict(r) for r in rows])
```

---

#### Bug 39 — Stats daily chart pads with empty bars when there's <7 days of data

**File:** `src/app.py:335-336`

**Problem:**
```python
while len(daily_bars) < 7:
    daily_bars.insert(0, {"label": "", "pct": 0, "count": 0})
```
If the daemon has only been running for 2 days, the chart shows 5 empty bars on the left followed by 2 real bars. The empty bars have no label, so they look like a rendering bug.

**Why it matters:** Confusing visualization for new installs.

**Exact fix:**
Generate all 7 day labels explicitly, then fill in zeros for days with no data:
```python
from datetime import timedelta
today = datetime.utcnow().date()
day_map = {r["day"]: r["cnt"] for r in daily}
daily_bars = []
for i in range(6, -1, -1):
    day = today - timedelta(days=i)
    day_str = day.isoformat()
    cnt = day_map.get(day_str, 0)
    daily_bars.append({
        "label": day_str[5:],  # MM-DD
        "pct": int(cnt / max(max(day_map.values(), default=1), 1) * 100),
        "count": cnt
    })
```

---

#### Bug 40 — Status breakdown "Other" includes active/queued jobs, skewing the chart

**File:** `src/app.py:339-343`

**Problem:**
```python
{"label": "Other", "count": total - success - failed, "color": "#666666", ...}
```
"Other" lumps together queued, downloading, and cancelled. If 5 jobs are queued and 1 completed, "Other" is 83% — the chart looks like the daemon is failing.

**Why it matters:** Misleading stats. Users will think downloads aren't working.

**Exact fix:**
Add separate buckets for active/cancelled:
```python
cancelled = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='cancelled'").fetchone()["c"]
active = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status IN ('queued','downloading')").fetchone()["c"]
other = total - success - failed - cancelled - active

status_breakdown = [
    {"label": "Completed", "count": success, "color": "#22c55e", "pct": round(success/total*100,1) if total else 0},
    {"label": "Failed", "count": failed, "color": "#dc2626", "pct": round(failed/total*100,1) if total else 0},
    {"label": "Cancelled", "count": cancelled, "color": "#f59e0b", "pct": round(cancelled/total*100,1) if total else 0},
    {"label": "Active/Queued", "count": active, "color": "#3ea6ff", "pct": round(active/total*100,1) if total else 0},
    {"label": "Other", "count": other, "color": "#666666", "pct": round(other/total*100,1) if total else 0},
]
```

---

#### Bug 41 — README's cookie export example is incorrect

**File:** `README.md:206-208`

**Problem:**
```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://youtube.com"
```
This command tells yt-dlp to read cookies from Chrome (`--cookies-from-browser chrome`) AND read/write a cookies file (`--cookies cookies.txt`). In yt-dlp, when both are specified, the browser cookies are loaded into the session but **NOT** exported to the `--cookies` file. The user ends up with an empty `cookies.txt` and assumes the upload feature is broken.

**Why it matters:** Following the documented instructions produces a broken cookies file.

**Exact fix:**
Use yt-dlp's `--cookies` flag with a real cookies file, OR document using a browser extension like "Get cookies.txt LOCALLY" to export. Replace the README section with:
```bash
# Option A: use yt-dlp to dump browser cookies to a file
yt-dlp --cookies-from-browser chrome:Default --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
# Note: yt-dlp writes the session cookies to cookies.txt after the request

# Option B (recommended): use a browser extension like "Get cookies.txt LOCALLY"
# Export cookies for youtube.com, save as cookies.txt, upload via Settings page
```
Verify the exact behavior with current yt-dlp docs — the `--cookies` + `--cookies-from-browser` interaction has changed across versions.

---

#### Bug 42 — SSE `stream_queue` uses Python's `hash()` which is randomized per process

**File:** `src/app.py:551`

**Problem:**
```python
current_hash = str(hash(data))
```
Python's `hash()` for strings is randomized per process (PYTHONHASHSEED). Within a single SSE generator, this works (we just compare to `last_hash` in the same generator). But:
1. It's slow for large data (200 job dicts serialized → big string → big hash).
2. If the daemon restarts, the hash base changes, but `last_hash` resets too, so first comparison is always "changed" — fine.
3. Misleading to readers who might copy this pattern elsewhere.

Not a critical bug — just fragile and inefficient.

**Why it matters:** Performance under load. Subtle correctness if anyone refactors.

**Exact fix:**
Use `hashlib.md5` for a fast, deterministic hash:
```python
import hashlib
current_hash = hashlib.md5(data.encode()).hexdigest()
```
Or just compare lengths + a sampling of bytes. MD5 is fine here (not a security context).

---

## Implementation order

Apply fixes in this order to avoid merge conflicts:

1. **Bug 29** (version constant) — touches `app.py` top, do first.
2. **Bug 2** (started_at) — touches `worker.py` `DownloadJob.__init__` and `save_job`.
3. **Bug 1, Bug 12, Bug 13** (cancel/fail, webhook on cancel, glob fallback) — all in `worker.py` `run_download` / `cancel_job`.
4. **Bug 6** (file_path → filepath) — `worker.py` progress template.
5. **Bug 5, Bug 14, Bug 15** (playlist URL, bulk_retry filter, job_id collision) — `app.py` queue routes.
6. **Bug 19, Bug 20, Bug 21, Bug 22, Bug 23, Bug 24, Bug 25, Bug 27, Bug 28** (SSE leak, settings validation, logs count, playlist DoS, constant-time auth, open_path, redundant threads, cookie path leak, max_log_lines) — `app.py` API routes.
7. **Bug 16** (timezone) — `worker.py` + `models.py` + `dashboard.js`.
8. **Bug 18** (pause/resume dead code) — `worker.py` + `app.py` imports.
9. **Bug 3, Bug 39, Bug 40** (theme reset, chart padding, status breakdown) — `settings.html`, `app.py` `/api/stats`, `dashboard.js`.
10. **Bug 7, Bug 8, Bug 9, Bug 10, Bug 33, Bug 34** (extension bugs) — `background.js`, `popup.html/js`, `manifest.json`.
11. **Bug 4, Bug 26** (Dockerfile curl + non-root) — `Dockerfile`, `docker-compose.yml`.
12. **Bug 17** (handler.sh health endpoint) — `yt-dl-handler.sh`.
13. **Bug 30, Bug 31, Bug 32** (install scripts) — `install.sh`, `install.fish`.
14. **Bug 35, Bug 36, Bug 37, Bug 38, Bug 41, Bug 42** (cleanup) — `worker.py`, `app.py`, `README.md`.

## Testing checklist after fixes

- [ ] Cancel an active download → status becomes "cancelled", not "failed". Webhook does NOT fire.
- [ ] Start a download → wait → check `started_at` is the original start time, not the last progress update.
- [ ] Toggle theme to light → go to Settings → change unrelated setting → Save → theme stays light.
- [ ] Run `docker compose up` → trigger a download with webhook_url set → webhook fires (check daemon log for "Webhook error" absence).
- [ ] Queue a non-YouTube playlist (e.g., SoundCloud) → all entries get correct site-specific URLs.
- [ ] After a download completes, check `file_path` in DB is an absolute path.
- [ ] Right-click a `<video>` element on youtube.com → "Download with yt-dl" works (uses `srcUrl`).
- [ ] Restart Chrome → no toast storm.
- [ ] Set `YTDL_API_KEY=secret` → load extension → set API key in popup → all operations work.
- [ ] Cancel a job → webhook does not fire.
- [ ] Set output_pattern to `%(channel)s/%(title)s.%(ext)s` → download completes → `file_path` correctly points to subdirectory.
- [ ] Bulk-retry a selection that includes completed jobs → completed jobs are NOT re-queued.
- [ ] Queue 100 non-YouTube URLs rapidly → no IntegrityError.
- [ ] Dashboard "time ago" labels show correct relative time (no 5h offset on IST server).
- [ ] `bash yt-dl-handler.sh <url>` when daemon is down → starts daemon, queues download.
- [ ] PUT `/api/settings` with `{"concurrent_limit": 9999}` → 400 error.
- [ ] PUT `/api/settings` with `{"evil_key": "x"}` → ignored, not stored.
- [ ] GET `/api/logs?count=abc` → 400 error.
- [ ] GET `/api/logs?count=-5` → 400 error.
- [ ] POST `/api/open` with `{"path": "/etc"}` → 403.
- [ ] `docker compose up` → `docker exec yt-dl id` → `uid=1000(ytdl)`, not root.
- [ ] POST `/api/settings/cookies` → response does NOT include `"path"`.
- [ ] PUT `/api/settings` with `{"max_log_lines": 100}` → ring buffer actually resizes (verify by adding 200 log entries, only 100 stored).
- [ ] `/api/info` returns version "1.1.0", matches `manifest.json`.
- [ ] Stats page with 2 days of data → chart shows 5 zero bars with date labels (not empty), 2 real bars.
- [ ] Stats status breakdown → separate "Cancelled" and "Active/Queued" buckets.
- [ ] `manifest.json` has no `<all_urls>`.
- [ ] Chrome alarm `poll` is `0.5` minutes, not `0.1`.
- [ ] `install.sh` runs cleanly on a system with python3 in `/usr/local/bin`.
- [ ] `install.sh` does not warn about missing `python-dbus`.
- [ ] `install.fish` does not reference `notifications.py`.
- [ ] README cookie instructions actually produce a non-empty `cookies.txt`.

---

## 🔴 CRITICAL PRIORITY — Dashboard layout regression investigation

### Background

The user reported that the dashboard used to render as a 2-column grid for downloading jobs and a 3-column grid for queued / completed / failed jobs, but now renders as a single-column vertical list ("straight one card going down").

### Root-cause analysis (already performed)

The 2-col / 3-col grid layout was **introduced** in commit `c3a01ab` ("Redesign dashboard UI per spec: 2-col downloading, 3-col queued/recent/failed, compact cards, section labels, footer"). The relevant code is:

- `src/static/dashboard.js` — `buildSection()` renders `<div class="grid-2">` or `<div class="grid-3">` wrappers.
- `src/static/style.css`:
  ```css
  .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
  @media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
  ```

At HEAD (`ed5409b`), this layout is **unchanged** — the only commits after `c3a01ab` that touched dashboard files are `ed5409b` (failed-card text tweak) and `82fbc97` (log CSS class renames). Neither affects the grid.

### The actual bug

The `@media (max-width: 900px)` breakpoint collapses both grids to a single column. `900px` is too narrow for a desktop dashboard — a maximized browser on a 1366px laptop with a 200px side panel already drops below 900px content width, and many users keep their browser window narrower than the screen.

### Fix (Bug 43 — HIGHEST PRIORITY)

**File:** `src/static/style.css`

Lower the breakpoint to `640px` (typical phone width) so the grids stay multi-column on any desktop window, and add a middle tier for tablets:

```css
.grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }

/* Tablet: 2 columns for the 3-col grid */
@media (max-width: 1024px) {
  .grid-3 { grid-template-columns: repeat(2, 1fr); }
}
/* Phone: single column */
@media (max-width: 640px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}
```

Also add a CSS rule so that when a grid has only one child, the card stretches to full width (instead of leaving an empty half):

```css
/* When a grid section has exactly one card, let it span all columns */
.grid-2 > :only-child,
.grid-3 > :only-child {
  grid-column: 1 / -1;
}
```

### Verification

Open the dashboard at these viewport widths and verify:
- 1440px → grid-2 shows 2 cols, grid-3 shows 3 cols ✓
- 1024px → grid-2 shows 2 cols, grid-3 shows 2 cols ✓
- 800px  → grid-2 shows 2 cols, grid-3 shows 2 cols (was: 1 col) ✓
- 640px  → all grids show 1 col ✓
- With exactly 1 downloading job at 1440px → card spans full width (was: card on left, empty right) ✓

---

## Additional UI bugs (Bugs 44–67)

These are NEW bugs found during the UI audit, not in the original 42.

---

### Bug 44 — "Pause all" button actually CANCELS all downloads

**File:** `src/static/dashboard.js:192-200`

**Problem:**
```js
const pauseBtn = document.getElementById("pause-all");
if (pauseBtn) {
  pauseBtn.addEventListener("click", function() {
    const dl = prevJobs.filter(j => j.status === 'downloading');
    if (!dl.length) return;
    dl.forEach(j => cancelJob(j.id));   // cancels, not pauses!
    showToast("Cancelled " + dl.length + " active");
  });
}
```

The button is labeled "Pause all" but calls `cancelJob()` for each downloading job. Cancelling is a **destructive** action — it kills the yt-dlp process, discards partial downloads, and marks the job as `cancelled`. The user expects "pause" to be reversible (resume later).

**Why it matters:** Destructive action hidden behind a non-destructive label. Users will lose partial downloads thinking they can resume.

**Exact fix — option A (rename to "Cancel all"):**
```js
// In dashboard.html:
<span class="footer-action footer-action-danger" id="pause-all">Cancel all</span>
```
And update the toast message to "Cancelled N active".

**Option B (implement real pause/resume):**
Re-add `pause_job` / `resume_job` to `worker.py` (using `SIGSTOP`/`SIGCONT` on the process group), wire up `/api/jobs/<id>/pause` and `/api/jobs/<id>/resume` endpoints, add a "Resume all" button, and change the per-card "Cancel" button to a "Pause" toggle. This is a larger feature — pick option A unless pause/resume is explicitly needed.

Recommended: **option A** (rename to "Cancel all").

---

### Bug 45 — "Clear completed" button silently DELETES downloaded files

**File:** `src/static/dashboard.js:182-190`

**Problem:**
```js
const clearBtn = document.getElementById("clear-completed");
if (clearBtn) {
  clearBtn.addEventListener("click", function() {
    const completed = prevJobs.filter(j => j.status === 'completed');
    if (!completed.length) return;
    fetch("/api/bulk/delete", {method:"POST", ...})  // DELETES FILES
      .then(r => r.json()).then(d => { showToast("Cleared " + d.deleted + " completed"); });
  });
}
```

`/api/bulk/delete` removes both the DB record AND the file from disk (`os.remove(row["file_path"])`). The button label "Clear completed" sounds like it just clears the UI list — but it actually deletes the user's downloaded videos. No confirmation dialog.

**Why it matters:** Silent data loss. User clicks "Clear completed" expecting to tidy the list, loses all downloaded files.

**Exact fix:**
1. Add a confirmation dialog that explicitly mentions file deletion:
```js
clearBtn.addEventListener("click", function() {
  const completed = prevJobs.filter(j => j.status === 'completed');
  if (!completed.length) return;
  if (!confirm(`Delete ${completed.length} completed download(s) AND their files from disk?`)) return;
  fetch("/api/bulk/delete", ...)
    .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " files"); });
});
```

2. Rename the button to "Delete completed" to match the actual behavior:
```html
<!-- In dashboard.html -->
<span class="footer-action footer-action-danger" id="clear-completed">Delete completed</span>
```

3. Consider adding a separate "Hide completed" button that only removes the DB record (calls a new `/api/bulk/hide` endpoint that does `DELETE FROM downloads WHERE job_id IN (...) AND status='completed'` WITHOUT touching the file). This gives users both options.

---

### Bug 46 — Completed card has "Delete" but no "Open" button

**File:** `src/static/dashboard.js:87-102` (`buildRecentCard`)

**Problem:**
```js
function buildRecentCard(j) {
  ...
  return '<div class="q-card completed" ...>'
    + ...
    + '<div class="q-bottom" style="justify-content:flex-end;">'
    + '<span class="q-cancel" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</span>'
    + '</div></div></div>';
}
```

The completed card only has a "Delete" button. There's no way to open the downloaded file or its containing folder from the dashboard. The old dashboard (pre-`c3a01ab`) had an "Open" button that called `openFolder()`. The `/api/open` endpoint exists and (after Bug 24 fix) is secured — but no UI element calls it.

**Why it matters:** Poor UX. Users must manually navigate their file manager to the download directory.

**Exact fix:**
Add an "Open" button to `buildRecentCard` that calls `/api/open` with the file's directory:
```js
function buildRecentCard(j) {
  ...
  const openBtn = j.file_path
    ? '<span class="q-retry" onclick="openFolder(\'' + escapeHtml(j.id) + '\')">Open</span>'
    : '';
  return '<div class="q-card completed" ...>'
    + ...
    + '<div class="q-bottom" style="justify-content:flex-end; gap:12px;">'
    + openBtn
    + '<span class="q-cancel" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')">Delete</span>'
    + '</div></div></div>';
}

function openFolder(jobId) {
  // Find the job in prevJobs to get its file_path
  const job = prevJobs.find(j => j.id === jobId);
  if (!job || !job.file_path) return;
  // Extract directory from file path
  const dir = job.file_path.substring(0, job.file_path.lastIndexOf('/'));
  fetch("/api/open", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: dir})
  }).then(r => r.json()).then(d => {
    if (d.error) showToast(d.error, "error");
  });
}
```

---

### Bug 47 — `deleteJob()` confirmation dialog is vague

**File:** `src/static/dashboard.js:175-178`

**Problem:**
```js
function deleteJob(id) {
  if (!confirm("Delete?")) return;
  fetch("/api/jobs/" + id, {method:"DELETE"})...
}
```

The dialog just says "Delete?" — doesn't mention that the downloaded FILE will also be deleted from disk.

**Why it matters:** Same as Bug 45 — silent file deletion without informed consent.

**Exact fix:**
```js
function deleteJob(id) {
  const job = prevJobs.find(j => j.id === id);
  const hasFile = job && job.file_path && job.status === 'completed';
  const msg = hasFile
    ? "Delete this download AND remove the file from disk?"
    : "Delete this download record?";
  if (!confirm(msg)) return;
  fetch("/api/jobs/" + id, {method:"DELETE"})...
}
```

---

### Bug 48 — Card "mp4" chip shows even for audio downloads

**File:** `src/static/dashboard.js:61, 73, 92, 108`

**Problem:**
```js
// buildDownloadingCard:
'<div class="dl-meta"><span class="dl-chip">' + escapeHtml(j.quality) + '</span><span class="dl-chip">mp4</span></div>'
// buildQueueCard:
const meta = [j.quality, 'mp4', sizeInfo].filter(Boolean).join(' • ');
// buildRecentCard:
const meta = [j.quality, 'mp4', sizeInfo, ...].filter(Boolean).join(' • ');
// buildFailedCard:
const meta = [j.quality, 'mp4', 'FAILED', ...].filter(Boolean).join(' • ');
```

Every card shows "mp4" regardless of the actual quality/format. An `audio` quality download produces an `.mp3` file, not `.mp4`. The chip is misleading.

**Why it matters:** Minor, but confusing — user downloads "audio" and sees "mp4" on the card.

**Exact fix:**
```js
function formatChip(j) {
  return j.quality === 'audio' ? 'mp3' : 'mp4';
}
```
Then replace all hardcoded `'mp4'` with `formatChip(j)` (or `escapeHtml(formatChip(j))` in HTML contexts).

---

### Bug 49 — Thumbnails load from i.ytimg.com for non-YouTube videos

**File:** `src/static/dashboard.js:44, 70, 89, 106`

**Problem:**
```js
const thumb = j.video_id
  ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="dl-thumb">'
  : '<div class="dl-thumb-placeholder">YT</div>';
```

For non-YouTube downloads (TikTok, Twitter, etc.), `video_id` may still be set (yt-dlp extracts a site-specific ID). The code unconditionally constructs a `i.ytimg.com` URL, which returns a default "no preview" image — broken thumbnail.

**Why it matters:** Broken thumbnails for every non-YouTube download.

**Exact fix:**
Only use the YouTube thumbnail URL if the URL looks like YouTube:
```js
function buildThumb(videoId, url) {
  if (videoId && url && /youtube\.com|youtu\.be/.test(url)) {
    return '<img src="https://i.ytimg.com/vi/' + escapeHtml(videoId) + '/mqdefault.jpg" class="dl-thumb">';
  }
  return '<div class="dl-thumb-placeholder">YT</div>';
}
```
Apply to all four card builders. Alternatively, store the thumbnail URL from yt-dlp's info JSON (`info.get("thumbnail")`) during `run_download` and use that.

---

### Bug 50 — Theme flash (FOUC) on page load

**File:** `src/templates/base.html:2`, `src/static/theme.js:9-10`

**Problem:**
1. Server renders `<html data-theme="{{ theme }}">` using the config-file theme.
2. Browser parses HTML, begins rendering (e.g., dark theme).
3. At the END of `<body>`, `theme.js` loads and reads `localStorage.getItem("yt-dl-theme")`.
4. If localStorage has a DIFFERENT theme (e.g., "light"), it calls `setAttribute("data-theme", "light")`.

Between steps 2 and 4, the user sees a flash of the server-theme before the client-theme applies. This is a classic FOUC (Flash Of Unstyled Content) issue.

**Why it matters:** Visual jolt on every page load if the user has toggled theme on one device but the server config still has the default.

**Exact fix:**
Move the localStorage check into an inline `<script>` in `<head>`, BEFORE any CSS loads:
```html
<!-- In base.html <head>, BEFORE the <link> to style.css -->
<script>
  (function() {
    try {
      var t = localStorage.getItem('yt-dl-theme');
      if (t) document.documentElement.setAttribute('data-theme', t);
    } catch(e) {}
  })();
</script>
<link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
```
Then remove the bottom-of-body script from `theme.js` (keep only the `toggleTheme` function).

---

### Bug 51 — Logs page shows duplicate entries (SSE + fetch race)

**File:** `src/static/logs.js:36-38`

**Problem:**
```js
const evtSource = new EventSource("/api/logs/stream");
evtSource.onmessage = function(e) { appendLog(JSON.parse(e.data)); };
filterLogs();
```

Sequence:
1. `EventSource` is created → server sends the last 50 log entries as an initial burst.
2. `onmessage` fires for each → `appendLog()` adds them to the DOM.
3. `filterLogs()` runs → clears the DOM (`innerHTML = ""`) → fetches 100 logs from `/api/logs`.
4. Between step 3's clear and the fetch resolving, more SSE events may arrive and be appended.
5. When the fetch resolves, 100 logs are appended — but some of those may already have been appended via SSE in step 4.

Result: duplicate log entries in the UI.

**Why it matters:** Confusing — the same log line appears twice.

**Exact fix:**
Create the EventSource AFTER the initial fetch completes, and dedup by timestamp+message:
```js
let evtSource = null;

function startSSE() {
  evtSource = new EventSource("/api/logs/stream");
  evtSource.onmessage = function(e) {
    appendLog(JSON.parse(e.data));
  };
  evtSource.onerror = function() {
    evtSource.close();
    setTimeout(startSSE, 3000);
  };
}

// Fetch initial logs first, THEN start SSE
fetch("/api/logs?level=ALL&count=100")
  .then(r => r.json())
  .then(data => {
    data.forEach(appendLog);
    startSSE();
  });

// Dedup helper in appendLog:
const seenKeys = new Set();
function appendLog(entry) {
  if (currentLevel !== "ALL" && entry.level !== currentLevel) return;
  const key = entry.time + "|" + entry.message;
  if (seenKeys.has(key)) return;
  seenKeys.add(key);
  // ... rest of appendLog
}
```

---

### Bug 52 — Two independent connection monitors can disagree

**Files:** `src/templates/base.html:31-37` (checkConn, 15s interval), `src/static/dashboard.js:205-213` (SSE onerror, 3s retry)

**Problem:**
The nav bar's connection indicator polls `/api/info` every 15s. The dashboard's SSE stream retries every 3s on error. These two monitors are independent — the SSE can reconnect successfully while the nav indicator still shows "disconnected" for up to 15s.

**Why it matters:** Confusing status — user sees "disconnected" in the nav but the dashboard is updating live.

**Exact fix:**
Have the SSE status drive the nav indicator. In `dashboard.js`:
```js
function connectSSE() {
  if (sseTimer) { clearTimeout(sseTimer); sseTimer = null; }
  setConnStatus('connecting');
  const source = new EventSource("/api/queue/stream");
  source.onopen = function() { setConnStatus('connected'); };
  source.onmessage = function(e) {
    if (e.data === ": unchanged") return;
    try { renderDashboard(JSON.parse(e.data)); } catch (err) {}
  };
  source.onerror = function() {
    source.close();
    setConnStatus('disconnected');
    sseTimer = setTimeout(connectSSE, 3000);
  };
}

function setConnStatus(status) {
  const dot = document.getElementById('conn-dot');
  const text = document.getElementById('conn-text');
  if (!dot || !text) return;
  if (status === 'connected') {
    dot.style.background = '#22c55e';
    text.textContent = 'connected';
  } else if (status === 'disconnected') {
    dot.style.background = '#ef4444';
    text.textContent = 'disconnected';
  } else {
    dot.style.background = '#f59e0b';
    text.textContent = 'connecting';
  }
}
```
Then REMOVE the `checkConn()` function and `setInterval(checkConn, 15000)` from `base.html`. The SSE connection is the single source of truth.

For pages that don't have SSE (stats, settings, logs), keep a lightweight `/api/info` poll but use the same `setConnStatus` function.

---

### Bug 53 — Stats chart `.bar-chart` CSS class is dead code

**File:** `src/static/style.css:137`, `src/templates/stats.html:15`

**Problem:**
CSS defines `.bar-chart`:
```css
.bar-chart { display: flex; align-items: flex-end; gap: 8px; height: 180px; padding: 0 8px; }
```
But the HTML uses inline styles instead:
```html
<div id="daily-chart" style="height:200px; display:flex; align-items:flex-end; gap:8px; padding:20px 0;"></div>
```
The `.bar-chart` class is never applied — dead code. The inline styles differ from the CSS (200px vs 180px height, `padding:20px 0` vs `padding:0 8px`).

**Why it matters:** Maintenance confusion. A developer reading the CSS would think `.bar-chart` controls the chart, but it doesn't.

**Exact fix:**
Use the class instead of inline styles. Update the CSS to match the intended values:
```css
.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 200px;
  padding: 20px 0;
}
```
And the HTML:
```html
<div id="daily-chart" class="bar-chart"></div>
```

---

### Bug 54 — No pagination on dashboard (only latest 200 jobs visible)

**File:** `src/static/dashboard.js:127-158` (`renderDashboard`)

**Problem:**
The dashboard fetches `/api/queue` (which returns the latest 200 jobs) and renders everything. If the user has 500+ jobs in history, the oldest 300+ are invisible from the dashboard. There's no "Load more" button or pagination control.

**Why it matters:** Power users lose access to their download history.

**Exact fix:**
Add a "Load more" button at the bottom of the dashboard that fetches the next page:
```js
let currentOffset = 0;
const PAGE_SIZE = 200;

async function loadMore() {
  currentOffset += PAGE_SIZE;
  const r = await fetch(`/api/queue?limit=${PAGE_SIZE}&offset=${currentOffset}`);
  const olderJobs = await r.json();
  if (olderJobs.length === 0) {
    document.getElementById('load-more').style.display = 'none';
    return;
  }
  // Append to the RECENT section
  prevJobs = prevJobs.concat(olderJobs);
  renderDashboard(prevJobs);
}

// In dashboard.html, add after the sections div:
// <div style="text-align:center; padding:20px;">
//   <button class="btn btn-secondary" id="load-more" onclick="loadMore()">Load more</button>
// </div>
```

---

### Bug 55 — No empty state for stats page

**File:** `src/templates/stats.html`, `src/static/stats.js`

**Problem:**
When there are zero downloads, the stats page shows "0" for every metric, an empty chart, and empty breakdown. No friendly "No downloads yet" message.

**Why it matters:** Looks broken on a fresh install.

**Exact fix:**
In `renderStats()`:
```js
function renderStats(data) {
  if (data.total_downloaded === 0) {
    document.getElementById('stats-content').style.display = 'none';
    document.getElementById('stats-empty').style.display = '';
    return;
  }
  document.getElementById('stats-content').style.display = '';
  document.getElementById('stats-empty').style.display = 'none';
  // ... existing render logic
}
```
And in `stats.html`, add an empty-state div:
```html
<div id="stats-empty" style="display:none; text-align:center; padding:80px 0; color:var(--text-muted);">
  <p style="font-size:16px; font-weight:600; color:var(--text);">No downloads yet</p>
  <p style="margin-top:8px;">Download some videos to see statistics here.</p>
</div>
<div id="stats-content">
  <!-- existing stats cards -->
</div>
```

---

### Bug 56 — No client-side validation on settings form

**File:** `src/templates/settings.html`

**Problem:**
The `concurrent_limit` input has `min="1" max="10"` but the user can type any number (e.g., 999 or -5). The server validates (after Bug 20 fix) and returns 400, but the client doesn't show inline error feedback — it just silently fails to save.

**Why it matters:** Poor UX — user clicks "Save", nothing happens (or rather, the PUT returns 400 but the toast says "Settings saved" because `saveSettings()` doesn't check the response status).

**Exact fix:**
1. Add client-side validation:
```js
function saveSettings() {
  const concurrent = parseInt(document.getElementById("concurrent_limit").value);
  if (isNaN(concurrent) || concurrent < 1 || concurrent > 20) {
    showToast("Concurrent limit must be 1-20", "error");
    return;
  }
  const playlistLimit = parseInt(document.getElementById("playlist_limit").value);
  if (isNaN(playlistLimit) || playlistLimit < 1 || playlistLimit > 1000) {
    showToast("Playlist limit must be 1-1000", "error");
    return;
  }
  // ... proceed with fetch
}
```

2. Check the response status and show appropriate toast:
```js
fetch("/api/settings", {method:"PUT", ...})
  .then(r => {
    if (!r.ok) return r.json().then(err => { throw new Error(err.error); });
    return r.json();
  })
  .then(d => showToast("Settings saved"))
  .catch(err => showToast("Error: " + err.message, "error"));
```

---

### Bug 57 — "Save Settings" button doesn't disable during save

**File:** `src/templates/settings.html:124-140` (`saveSettings()`)

**Problem:**
The user can click "Save Settings" multiple times rapidly, sending multiple PUT requests. Each request overwrites the config, and if the user changed a field between clicks, intermediate state may be saved.

**Why it matters:** Race condition on config writes.

**Exact fix:**
```js
async function saveSettings() {
  const btn = document.querySelector('.btn-primary');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    // ... validation + fetch
    const r = await fetch("/api/settings", {method:"PUT", ...});
    if (!r.ok) throw new Error((await r.json()).error);
    showToast("Settings saved");
  } catch (err) {
    showToast("Error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Settings';
  }
}
```

---

### Bug 58 — "Clear Display" button in logs is misleading

**File:** `src/templates/logs.html:9`, `src/static/logs.js:29`

**Problem:**
```js
function clearLogs() { linesDiv.innerHTML = ""; }
```
The button "Clear Display" only clears the visual DOM. It does NOT clear the server-side ring buffer or the log file. Users might expect it to clear the daemon's log history.

**Why it matters:** Misleading label — user thinks logs are cleared, but they reappear on page refresh.

**Exact fix — option A (rename):**
```html
<button class="btn btn-secondary" onclick="clearLogs()">Clear View</button>
```

**Option B (add a real "clear server logs" button):**
Add a `POST /api/logs/clear` endpoint that clears the ring buffer, and a second button that calls it. This is a larger feature — pick option A unless explicitly needed.

Recommended: **option A** (rename).

---

### Bug 59 — Nav bar not responsive on narrow screens

**File:** `src/templates/base.html:12-19`, `src/static/style.css:24`

**Problem:**
The nav bar has: brand + 4 nav links + connection status + theme toggle. On screens <768px, the links and status text overflow or wrap awkwardly. There's no hamburger menu or responsive layout.

**Why it matters:** Mobile/tablet users can't navigate the dashboard.

**Exact fix:**
Add a responsive nav that collapses to a hamburger menu on narrow screens:
```css
@media (max-width: 768px) {
  .nav { flex-wrap: wrap; height: auto; padding: 12px 16px; }
  .nav-links { order: 3; width: 100%; margin-left: 0; margin-top: 8px; justify-content: space-around; }
  .nav-link { font-size: 12px; padding: 6px 10px; }
  #conn-status { display: none; }  /* hide text, keep dot */
}
```

---

### Bug 60 — `theme.js` PUT to `/api/settings` has no error handling

**File:** `src/static/theme.js:7`

**Problem:**
```js
fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({theme:next})});
```
No `.then()` or `.catch()`. If the server returns an error (e.g., 400 after Bug 20 validation — though `theme` is whitelisted, so this shouldn't happen), or the network is down, the local state diverges from the server. On next page load, the server renders the old theme, causing a flash.

**Why it matters:** Silent failure leads to theme desync between client and server.

**Exact fix:**
```js
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  localStorage.setItem("yt-dl-theme", next);
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({theme:next})})
    .catch(() => {
      // Revert on failure
      html.setAttribute("data-theme", current);
      localStorage.setItem("yt-dl-theme", current);
    });
}
```

---

### Bug 61 — Failed card click navigates to /logs (entire card is clickable)

**File:** `src/static/dashboard.js:109`

**Problem:**
```js
return '<div class="q-card failed" data-id="..." style="cursor:pointer" onclick="window.location.href=\'/logs\'">'
```
The entire failed card is clickable and navigates to `/logs`. This is unexpected — the user might click the card to select it, or click the "Retry" button (which has `event.stopPropagation()`). Clicking anywhere else on the card (e.g., the title) jumps to the logs page, which is jarring.

**Why it matters:** Unexpected navigation. No visual affordance that the card is clickable (besides `cursor:pointer`).

**Exact fix:**
Remove the card-level onclick and add a small "View logs" link in the card footer instead:
```js
return '<div class="q-card failed" data-id="...">'
  + ...
  + '<div class="q-bottom" style="justify-content:space-between;">'
  + '<span class="q-retry" onclick="event.stopPropagation();retryJob(\'' + escapeHtml(j.id) + '\')">Retry</span>'
  + '<span class="q-cancel" onclick="window.location.href=\'/logs\'">View logs →</span>'
  + '</div></div></div>';
```

---

### Bug 62 — SSE queue stream polls DB every 1s per client (N+1 problem)

**File:** `src/app.py:541-565` (`stream_queue`)

**Problem:**
```python
while True:
    try:
        with closing(get_db()) as db:
            rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
        data = json.dumps([job_to_dict(r) for r in rows])
        current_hash = hashlib.md5(data.encode()).hexdigest()
        if current_hash != last_hash:
            yield "data: " + data + "\n\n"
            ...
    time.sleep(1)
```
Every second, every connected SSE client triggers a `SELECT * FROM downloads LIMIT 200` query + JSON serialization + MD5 hash. With 5 dashboard tabs open, that's 5 queries/second. The hash optimization skips the yield, but the DB query and serialization still run.

**Why it matters:** O(N) DB load where N = number of dashboard tabs. Memory and CPU grow linearly with concurrent viewers.

**Exact fix:**
Move the polling to a single background thread that broadcasts to all subscribers (like `RingBufferLogHandler` already does for logs):
```python
class QueueBroadcaster:
    def __init__(self):
        self.subscribers = []
        self.sub_lock = threading.Lock()
        self._last_hash = ""
        self._last_data = "[]"

    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="queue-broadcast").start()

    def _poll_loop(self):
        while True:
            try:
                with closing(get_db()) as db:
                    rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
                data = json.dumps([job_to_dict(r) for r in rows])
                h = hashlib.md5(data.encode()).hexdigest()
                if h != self._last_hash:
                    self._last_hash = h
                    self._last_data = data
                    self._broadcast(data)
            except Exception as e:
                logger.error(f"Queue broadcast error: {e}")
            time.sleep(1)

    def _broadcast(self, data):
        with self.sub_lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put(data, block=False)
                except:
                    dead.append(q)
            for q in dead:
                if q in self.subscribers:
                    self.subscribers.remove(q)

    def subscribe(self):
        q = queue.Queue(maxsize=10)
        with self.sub_lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.sub_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

queue_broadcaster = QueueBroadcaster()
queue_broadcaster.start()

@app.route("/api/queue/stream")
def stream_queue():
    def event_stream():
        q = queue_broadcaster.subscribe()
        try:
            # Send initial state
            yield "data: " + queue_broadcaster._last_data + "\n\n"
            while True:
                data = q.get(timeout=30)
                yield "data: " + data + "\n\n"
        except:
            pass
        finally:
            queue_broadcaster.unsubscribe(q)
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
```
Now the DB is queried once per second total, regardless of how many clients are connected.

---

### Bug 63 — `human_bytes()` in models.py crashes on negative input

**File:** `src/models.py:155-163`

**Problem:**
```python
def human_bytes(b: int) -> str:
    if b == 0:
        return "0.0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
```
If `b` is negative (shouldn't happen, but could if `file_size` is corrupted or a future bug stores negative), `abs(b) < 1024` is true immediately, and it returns `"-5.0 B"`. Not a crash, but the function signature says `b: int` and after `b /= 1024` it becomes a float — type inconsistency.

**Why it matters:** Minor type/contract issue.

**Exact fix:**
```python
def human_bytes(b) -> str:
    if b is None or b == 0:
        return "0.0 B"
    b = float(b)
    if b < 0:
        return "-" + human_bytes(-b)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
```

---

### Bug 64 — Dashboard card heights are fixed (don't adapt to long titles)

**File:** `src/static/style.css:43, 66`

**Problem:**
```css
.dl-card { ... height: 160px; ... }
.q-card { ... height: 120px; ... }
```
Fixed heights. If a video title is very long, it gets clamped (`-webkit-line-clamp: 2`) — but if the title + meta + progress bar don't fit in 160px, the card content overflows or gets cut. With very short titles, there's wasted vertical space.

**Why it matters:** Inconsistent card appearance — some cards look cramped, others have empty space.

**Exact fix:**
Use `min-height` instead of `height`:
```css
.dl-card { ... min-height: 160px; ... }
.q-card { ... min-height: 120px; ... }
```
This lets cards grow to fit content while maintaining a consistent minimum.

---

### Bug 65 — `escapeHtml` in dashboard.js doesn't escape single quotes

**File:** `src/static/dashboard.js:1-6`

**Problem:**
```js
function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = t;
  return d.innerHTML;
}
```
`textContent → innerHTML` escapes `&`, `<`, `>`, but NOT `'` or `"`. Job IDs are then interpolated into inline `onclick="cancelJob('...')"` handlers. If a job ID ever contains a `'`, the HTML breaks. After Bug 15 fix (UUID-based IDs), this is unlikely — but video titles are also interpolated into `title="..."` attributes, and a title containing `"` would break the attribute.

**Why it matters:** XSS / HTML injection via crafted video titles.

**Exact fix:**
```js
function escapeHtml(t) {
  if (!t) return '';
  const d = document.createElement("div");
  d.textContent = String(t);
  return d.innerHTML
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;');
}
```
Or better: stop using inline `onclick` handlers entirely. Use event delegation:
```js
document.getElementById('sections').addEventListener('click', function(e) {
  const cancelBtn = e.target.closest('.dl-cancel, .q-cancel');
  if (cancelBtn) {
    cancelJob(cancelBtn.dataset.jobId);
    return;
  }
  const retryBtn = e.target.closest('.q-retry');
  if (retryBtn) {
    retryJob(retryBtn.dataset.jobId);
    return;
  }
});
```
And in the card builders, use `data-job-id="..."` instead of `onclick="cancelJob('...')"`.

---

### Bug 66 — Stats page "Reset Stats" button doesn't confirm file deletion

**File:** `src/templates/stats.html:6`, `src/static/stats.js:45-52`

**Problem:**
```js
document.getElementById("reset-stats-btn").addEventListener("click", function() {
  if (!confirm("Reset all statistics?")) return;
  ...
  fetch("/api/stats/reset", {method: "POST"})...
});
```
The confirmation says "Reset all statistics?" but `/api/stats/reset` runs `DELETE FROM downloads` — which deletes ALL job records. It does NOT delete the files (only the DB records), but the user might think "reset statistics" only clears the aggregate counts, not the entire job history.

**Why it matters:** Unexpected data loss — the user loses their entire download history (DB records) from a button labeled "Reset Stats".

**Exact fix:**
Rename the button and clarify the confirmation:
```html
<button id="reset-stats-btn" ...>Clear History</button>
```
```js
if (!confirm("Delete ALL download records from the database? Files on disk will NOT be deleted. This cannot be undone.")) return;
```

---

### Bug 67 — `progress-template` uses `%(info.filepath)s` but yt-dlp may not populate it during early progress

**File:** `src/worker.py:183-188` (after Bug 6 fix)

**Problem:**
After Bug 6's fix changed the template from `%(info.filename)s` to `%(info.filepath)s`, there's a subtle issue: `%(info.filepath)s` is only available AFTER yt-dlp has resolved the final output path. During the early "Downloading fragment X" phase, the filepath may not be set yet, and `%(info.filepath)s` renders as `NA`. The parser then sets `job.file_path = "NA"`, which fails the `os.path.exists()` check and triggers the glob fallback — exactly what Bug 6 was trying to avoid.

**Why it matters:** The file path may still be wrong for some downloads, re-introducing the glob-fallback fragility.

**Exact fix:**
Guard against `NA`:
```python
filepath = data.get("filepath", "")
if filepath and filepath != "NA":
    job.file_path = filepath
```
Also check `os.path.exists` only on real paths:
```python
if not job.file_path or job.file_path == "NA" or not os.path.exists(job.file_path) or os.path.getsize(job.file_path) == 0:
    # ... glob fallback
```

---

## Updated implementation order (additions)

After completing bugs 1–42, apply:
- **Bug 43** (CRITICAL — layout breakpoint) — `src/static/style.css`
- **Bugs 44–47** (destructive button fixes) — `src/static/dashboard.js`, `src/templates/dashboard.html`
- **Bug 48** (mp4/mp3 chip) — `src/static/dashboard.js`
- **Bug 49** (non-YouTube thumbnails) — `src/static/dashboard.js`
- **Bug 50** (theme FOUC) — `src/templates/base.html`, `src/static/theme.js`
- **Bug 51** (logs SSE race) — `src/static/logs.js`
- **Bug 52** (connection monitor unification) — `src/templates/base.html`, `src/static/dashboard.js`
- **Bug 53** (dead .bar-chart CSS) — `src/static/style.css`, `src/templates/stats.html`
- **Bug 54** (dashboard pagination) — `src/static/dashboard.js`, `src/templates/dashboard.html`
- **Bug 55** (stats empty state) — `src/templates/stats.html`, `src/static/stats.js`
- **Bugs 56–57** (settings validation + button state) — `src/templates/settings.html`
- **Bug 58** (Clear Display rename) — `src/templates/logs.html`
- **Bug 59** (responsive nav) — `src/static/style.css`
- **Bug 60** (theme.js error handling) — `src/static/theme.js`
- **Bug 61** (failed card click) — `src/static/dashboard.js`
- **Bug 62** (SSE N+1) — `src/app.py`
- **Bug 63** (human_bytes) — `src/models.py`
- **Bug 64** (card heights) — `src/static/style.css`
- **Bug 65** (escapeHtml + event delegation) — `src/static/dashboard.js`
- **Bug 66** (Reset Stats confirmation) — `src/templates/stats.html`, `src/static/stats.js`
- **Bug 67** (filepath NA guard) — `src/worker.py`

## Updated test checklist (additions)

- [ ] Dashboard at 800px viewport → grids show 2 cols (was: 1 col). [Bug 43]
- [ ] Dashboard at 600px viewport → grids show 1 col. [Bug 43]
- [ ] Dashboard with 1 downloading job at 1440px → card spans full width. [Bug 43]
- [ ] "Pause all" button is renamed "Cancel all" (or actually pauses). [Bug 44]
- [ ] "Clear completed" shows confirmation mentioning file deletion. [Bug 45]
- [ ] "Clear completed" button is renamed "Delete completed". [Bug 45]
- [ ] Completed card has an "Open" button that opens the file's directory. [Bug 46]
- [ ] `deleteJob()` confirmation mentions file deletion when the job has a file. [Bug 47]
- [ ] Audio download card shows "mp3" chip, not "mp4". [Bug 48]
- [ ] Non-YouTube download shows "YT" placeholder, not broken image. [Bug 49]
- [ ] No theme flash on page load when localStorage differs from server config. [Bug 50]
- [ ] Logs page shows no duplicates after page load. [Bug 51]
- [ ] Nav connection indicator matches SSE connection state within 1s. [Bug 52]
- [ ] `.bar-chart` CSS class is used (not inline styles). [Bug 53]
- [ ] "Load more" button appears on dashboard with >200 jobs. [Bug 54]
- [ ] Stats page shows "No downloads yet" on fresh install. [Bug 55]
- [ ] Settings form rejects `concurrent_limit: 999` client-side. [Bug 56]
- [ ] "Save Settings" button disables and shows "Saving..." during save. [Bug 57]
- [ ] "Clear Display" button in logs is renamed "Clear View". [Bug 58]
- [ ] Nav bar wraps gracefully at 768px viewport. [Bug 59]
- [ ] Theme toggle reverts on server error. [Bug 60]
- [ ] Failed card is not entirely clickable; "View logs" is a separate link. [Bug 61]
- [ ] Open 5 dashboard tabs → DB query rate stays at 1/sec (not 5/sec). [Bug 62]
- [ ] `human_bytes(-5)` returns `"-5.0 B"`, not a crash. [Bug 63]
- [ ] Long video titles don't cause card content overflow. [Bug 64]
- [ ] Video title containing `"` or `'` doesn't break the card HTML. [Bug 65]
- [ ] "Reset Stats" button is renamed "Clear History" with explicit confirmation. [Bug 66]
- [ ] Download where yt-dlp reports `filepath: "NA"` doesn't set `file_path = "NA"`. [Bug 67]

## Deliverable

After applying all fixes:
1. Run the full test checklist above (42 original + 25 new = 67 items).
2. Provide a `CHANGELOG.md` entry summarizing all 67 fixes with file references.
3. Commit each bug fix as a separate git commit with message format: `fix(#N): short description` where N is the bug number above.
4. Open a single PR with all commits, linking back to this prompt.

