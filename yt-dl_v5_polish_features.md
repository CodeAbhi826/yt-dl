# yt-dl Polish & Features — Implementation Guide v5

> **Base:** tag `stable-v1.1.0` (commit `5ed872c`)
> **Issues to fix:** 7 user-reported bugs
> **New features:** 5 (separate Downloads page, per-card pause/resume, download progress size, enhanced duplicate detection, etc.)
> **Polish:** Font redesign (Google Sans / Product Sans style)

---

## Issues found — root cause analysis

### Issue 1 — Many videos show "Unknown" instead of real title

**Root cause:** When a job is added via `/api/add` (or bulk add), it's inserted with `title=""`. The real title is only fetched ~2-3 seconds later when `run_download()` runs `yt-dlp --dump-json --no-download`. But for jobs in `queued` state (waiting for a worker slot), the title stays empty indefinitely — sometimes for minutes if the queue is busy.

**Verified:** Seeded a `queued` job with `title=""` → dashboard shows "Unknown" instead of the video title. The video_id is also often empty (regex only matches YouTube's 11-char pattern, misses `youtu.be/xyz`, TikTok, Twitter, etc.).

### Issue 2 — Thumbnails missing for many videos

**Root cause:** Same as Issue 1. Thumbnail URL is built as `https://i.ytimg.com/vi/{video_id}/mqdefault.jpg` — but if `video_id` is empty (non-YouTube URL or extraction failed), no thumbnail shows. Also, for non-YouTube sites (TikTok, Twitter), `i.ytimg.com` returns 404 even when video_id is set.

**Verified:** Failed TikTok job → `video_id=""` → no thumbnail. Failed YouTube job with bad 11-char ID → 404 from i.ytimg.com.

### Issue 3 — Font feels too bold / heavy

**Root cause:** `style.css` uses `font-weight: 600` (semi-bold) for titles and `700` (bold) for nav brand. Combined with Inter font at small sizes (13-16px), this looks heavier than Google's Material Design / Product Sans aesthetic which uses `500` (medium) for most UI text.

### Issue 4 — File size not shown during download / queue / failed

**Root cause:** `dashboard.js` only shows `file_size` in the card if it's non-zero. But `file_size` is only set when a download COMPLETES (in `run_download` finally block). During download, we have `progress %` and `speed`, but no `total_bytes` field. So we can't show "50 MB of 120 MB".

**The yt-dlp progress template** (worker.py:185-190) currently sends `percent`, `speed`, `eta`, `filepath` — but NOT `total_bytes` or `downloaded_bytes`. We need to add those.

### Issue 5 — No per-card pause/resume on downloading cards

**Root cause:** Downloading cards only have "Cancel". Paused cards have "Resume" but no "Pause" button (you can only pause via the global "Pause all"). User wants per-card pause AND resume.

### Issue 6 — Master toggle only affects queue, not active downloads

**Root cause:** `_process_queue()` checks `downloads_enabled` and refuses to START new jobs, but doesn't touch already-running downloads. User wants the toggle to ALSO pause active downloads (and resume them when toggled back on).

### Issue 7 — RECENT section clutters the main dashboard

**Root cause:** Completed downloads show in a "RECENT" section at the bottom of the dashboard, mixed with active/queued/failed. User wants a separate `/downloads` page for completed, leaving the dashboard focused on active work.

---

## Implementation plan

### Phase 1 — Fix titles & thumbnails (Issues 1, 2)

#### 1a. Eager title extraction in `/api/add` — `src/app.py`

**File:** `src/app.py`, `api_add_job()` function (~line 555)

After inserting the job as `queued`, kick off a background thread to fetch the title + thumbnail URL. This runs in parallel with the queue, so the title appears in the UI within 2-3 seconds even if the job is still waiting in queue.

```python
import threading

def _fetch_metadata_async(job_id, url):
    """Background fetch of title + thumbnail + duration via yt-dlp --dump-json.
    Updates the DB row so the dashboard shows real metadata even while the
    job is still queued."""
    try:
        env = {**os.environ}
        if "OPENSSL_CONF" not in os.environ:
            env["OPENSSL_CONF"] = "/dev/null"
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=15, env=env
        )
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.strip().split("\n")[0])
            title = (info.get("title") or "Unknown")[:80]
            thumbnail = info.get("thumbnail") or ""
            duration = info.get("duration") or 0
            # Try to extract a YouTube-style video_id from the URL
            video_id = ""
            m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
            if m:
                video_id = m.group(1)
            elif info.get("id"):
                video_id = str(info.get("id"))[:30]

            db = get_db()
            try:
                db.execute(
                    "UPDATE downloads SET title=?, video_id=? WHERE job_id=? AND title=''",
                    (title, video_id, job_id)
                )
                db.commit()
            finally:
                db.close()
            logger.info(f"Metadata fetched for {job_id}: {title}")
    except Exception as e:
        logger.debug(f"Metadata fetch failed for {job_id}: {e}")
```

In `api_add_job()`, after `db.commit()` and before `process_queue()`, add:

```python
threading.Thread(
    target=_fetch_metadata_async,
    args=(job_id, url),
    daemon=True,
    name=f"meta-{job_id[:8]}"
).start()
```

Also call this from `api_bulk_add()` for each URL added.

#### 1b. Store thumbnail URL from yt-dlp — `src/models.py` + `src/worker.py`

Add a `thumbnail` column to the DB schema (and a migration for existing DBs):

**`src/models.py`, `init_db()` function:**
```python
# In the CREATE TABLE statement, add:
thumbnail TEXT,

# In the migrations dict, add:
migrations = {
    ...existing...,
    'thumbnail': 'TEXT',
}
```

**`src/worker.py`, `run_download()` info-extraction block (~line 170):**
```python
info = json.loads(result.stdout.strip().split("\n")[0])
job.title = info.get("title", "Unknown")[:80]
job.thumbnail = info.get("thumbnail") or ""   # ← NEW
save_job(job)
```

Add `self.thumbnail = row["thumbnail"] if "thumbnail" in row.keys() else None` to `DownloadJob.__init__`.

Update `save_job()` to persist `thumbnail`.

Update `job_to_dict()` in `models.py` to include `"thumbnail": row["thumbnail"]` in the API response.

#### 1c. Use stored thumbnail in dashboard — `src/static/dashboard.js`

Replace the `buildThumb()` function (line 9-13) to prefer the stored thumbnail URL, falling back to YouTube's i.ytimg.com only for YouTube URLs, and finally to the placeholder:

```js
function buildThumb(j, cls) {
  // Prefer the stored thumbnail URL (from yt-dlp --dump-json)
  if (j.thumbnail) {
    return '<img src="' + escapeHtml(j.thumbnail) + '" class="' + cls + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="' + cls + '-placeholder" style="display:none;">YT</div>';
  }
  // Fallback: YouTube thumbnail URL (only for YouTube URLs)
  if (j.video_id && j.url && /youtube\.com|youtu\.be/.test(j.url)) {
    return '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="' + cls + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="' + cls + '-placeholder" style="display:none;">YT</div>';
  }
  // Final fallback: placeholder
  return '<div class="' + cls + '-placeholder">YT</div>';
}
```

Update all 5 card builders to call `buildThumb(j, 'q-thumb')` or `buildThumb(j, 'dl-thumb')` instead of the inline thumbnail logic.

#### 1d. Better fallback text for empty titles

In `dashboard.js`, replace `j.title || j.video_id || 'Unknown'` with `j.title || j.url.split('/').pop() || 'Unknown'` — this shows the last URL segment (e.g., `dQw4w9WgXcQ`) instead of "Unknown" while the metadata is being fetched.

---

### Phase 2 — Font redesign (Issue 3)

#### 2a. Switch to Product Sans / Google Sans style — `src/static/style.css`

**File:** `src/static/style.css`, `:root` section (line 1-17)

Changes:
- Switch font stack from `'Inter'` to `'Product Sans', 'Google Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif` (browser falls back to Inter if Product Sans not installed — which is fine, Inter is close to Product Sans)
- Reduce title font weights from 600 → 500 (medium)
- Reduce nav brand from 700 → 500
- Reduce body font size slightly (14px → 13px) for better density
- Increase line-height slightly (1.5 → 1.6) for readability

```css
:root {
    --bg: #0a0a0a;
    --card-bg: #121212;
    --card-border: rgba(255,255,255,.05);
    --text: #ffffff;
    --text-secondary: #a0a0a0;
    --text-muted: #888888;
    --accent: #ff4d36;
    --accent-hover: #ff5b42;
    --green: #39c45a;
    --orange: #f39c12;
    --blue: #3ea6ff;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    /* Lighter font stack — Product Sans/Google Sans if available, Inter as fallback */
    --font: 'Product Sans', 'Google Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
```

Specific weight reductions throughout `style.css`:

```css
/* Was: .nav-brand { font-weight: 700 } */
.nav-brand { font-size: 19px; font-weight: 500; color: white; }

/* Was: .nav-link { font-weight: 500 } */
.nav-link { padding: 6px 14px; border-radius: var(--radius-sm); text-decoration: none; color: var(--text-muted); font-size: 13px; font-weight: 500; }

/* Was: .section-label { font-weight: 600 } */
.section-label { font-size: 11px; font-weight: 500; letter-spacing: 1.5px; text-transform: uppercase; color: #8a8a8a; margin-bottom: 12px; }

/* Was: .dl-title { font-size: 16px; font-weight: 600 } */
.dl-title { font-size: 15px; font-weight: 500; line-height: 1.4; }

/* Was: .q-title { font-size: 14px; font-weight: 600 } */
.q-title { font-size: 13px; font-weight: 500; line-height: 1.4; }

/* Was: .dl-stats-pct { font-weight: 600 } */
.dl-stats-pct { color: var(--accent-hover); font-weight: 500; }

/* Was: .dl-chip { font-size: 14px } */
.dl-chip { background: #1c1c1c; padding: 3px 8px; border-radius: var(--radius-sm); font-size: 12px; color: var(--text-secondary); font-weight: 400; }
```

#### 2b. Load Inter with lighter weights — `src/templates/base.html`

Change the Google Fonts link (line 7) to load weights 300, 400, 500 only (skip 600, 700):

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
```

This makes the font feel lighter overall — closer to Google Sans aesthetic.

---

### Phase 3 — File size + download progress (Issue 4)

#### 3a. Add `total_bytes` and `downloaded_bytes` to DB schema — `src/models.py`

In `init_db()` CREATE TABLE:
```sql
total_bytes INTEGER DEFAULT 0,
downloaded_bytes INTEGER DEFAULT 0,
```

In migrations dict:
```python
'total_bytes': 'INTEGER DEFAULT 0',
'downloaded_bytes': 'INTEGER DEFAULT 0',
```

In `job_to_dict()`, add:
```python
"total_bytes": row["total_bytes"] or 0,
"downloaded_bytes": row["downloaded_bytes"] or 0,
```

#### 3b. Capture bytes from yt-dlp progress — `src/worker.py`

Update the progress template (line 185-190):

```python
progress_template = (
    '{"percent":"%(progress._percent_str)s",'
    '"speed":"%(progress._speed_str)s",'
    '"eta":"%(progress._eta_str)s",'
    '"filepath":"%(info.filepath)s",'
    '"total_bytes":"%(progress._total_bytes_str)s",'
    '"downloaded_bytes":"%(progress._downloaded_bytes_str)s"}'
)
```

In the progress-parsing loop (~line 230), add:

```python
total_bytes_str = data.get("total_bytes", "")
downloaded_bytes_str = data.get("downloaded_bytes", "")
# Parse strings like "142.6MiB" → bytes
def parse_bytes(s):
    if not s or s == "NA":
        return 0
    s = s.strip()
    multipliers = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
                   "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    for unit, mult in multipliers.items():
        if s.endswith(unit):
            try:
                return int(float(s[:-len(unit)].strip()) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0

if total_bytes_str:
    job.total_bytes = parse_bytes(total_bytes_str)
if downloaded_bytes_str:
    job.downloaded_bytes = parse_bytes(downloaded_bytes_str)
```

Add `self.total_bytes = row["total_bytes"] if "total_bytes" in row.keys() else 0` and same for `downloaded_bytes` to `DownloadJob.__init__`.

Update `save_job()` to persist these fields.

#### 3c. Show progress size in cards — `src/static/dashboard.js`

In `buildDownloadingCard()` (line 49), update the stats:

```js
function buildDownloadingCard(j) {
  const thumb = buildThumb(j, 'dl-thumb');
  const pct = j.progress || 0;
  const speed = j.speed ? escapeHtml(j.speed) : '';
  const eta = formatEta(j.eta);
  
  const statsParts = [];
  statsParts.push('<span class="dl-stats-pct">' + pct.toFixed(1) + '%</span>');
  
  // Show "50 MB / 120 MB" if we have both, else just downloaded, else nothing
  if (j.downloaded_bytes && j.total_bytes) {
    statsParts.push(formatBytes(j.downloaded_bytes) + ' / ' + formatBytes(j.total_bytes));
  } else if (j.downloaded_bytes) {
    statsParts.push(formatBytes(j.downloaded_bytes));
  } else if (j.file_size) {
    statsParts.push(formatBytes(j.file_size));
  }
  
  if (speed) statsParts.push(speed);
  if (eta) statsParts.push(eta + ' left');
  const statsStr = statsParts.join(' • ');
  // ... rest of card
}
```

For `buildQueueCard` and `buildFailedCard`, the `file_size` is already shown if present. No changes needed there (file_size is 0 for queued/failed anyway).

For `buildRecentCard` (completed), `file_size` is already shown. Keep as-is.

---

### Phase 4 — Per-card pause/resume (Issue 5)

#### 4a. Add Pause button to downloading cards — `src/static/dashboard.js`

In `buildDownloadingCard()` (line 49), add a Pause button next to Cancel:

```js
return '<div class="dl-card" data-id="' + escapeHtml(j.id) + '">'
  + '<div class="dl-thumb-wrap">' + thumb + '</div>'
  + '<div class="dl-body">'
  + '<div><div class="dl-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.url.split('/').pop() || 'Unknown') + '</div>'
  + '<div class="dl-meta"><span class="dl-chip">' + escapeHtml(j.quality) + '</span><span class="dl-chip">' + formatChip(j) + '</span></div></div>'
  + '<div>'
  + '<div class="dl-progress"><div class="dl-progress-bar"><div class="dl-progress-fill" style="width:' + pct + '%"></div></div></div>'
  + '<div class="dl-stats"><span>' + statsStr + '</span><span class="dl-actions">'
  + '<span class="dl-pause" onclick="pauseJob(\'' + escapeHtml(j.id) + '\')">Pause</span>'
  + '<span class="dl-cancel" onclick="cancelJob(\'' + escapeHtml(j.id) + '\')">Cancel</span>'
  + '</span></div>'
  + '</div></div></div>';
```

Add the `pauseJob()` function near `cancelJob()`:

```js
function pauseJob(id) {
  fetch("/api/jobs/" + id + "/pause", {method:"POST"})
    .then(r => r.json())
    .then(d => { showToast("Paused"); })
    .catch(() => showToast("Pause failed", "error"));
}
```

#### 4b. Add Resume button to paused cards (already exists, just verify)

The paused card already has "Resume" via `resumeJob()`. Good — no change needed.

#### 4c. CSS for the new pause button — `src/static/style.css`

```css
.dl-actions { display: flex; gap: 12px; }
.dl-pause { color: var(--blue); font-size: 14px; font-weight: 500; cursor: pointer; }
.dl-pause:hover { color: #5cb8ff; }
```

---

### Phase 5 — Master toggle affects active downloads too (Issue 6)

#### 5a. Update worker to honor toggle for active downloads — `src/worker.py`

In `_process_queue()` (existing check, line ~127), keep the early return (don't start new downloads).

Add a NEW function that pauses/resumes active downloads based on the toggle:

```python
def sync_active_downloads_with_toggle():
    """Called when the master toggle changes. Pauses all active downloads
    when toggle goes OFF, resumes them when toggle goes back ON."""
    cfg = load_config()
    enabled = cfg.get("downloads_enabled", True)
    with queue_lock:
        for job in list(active_jobs.values()):
            if enabled and job.status == "paused" and getattr(job, '_paused_by_toggle', False):
                # Toggle went ON — resume jobs that were paused by the toggle
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGCONT)
                    job.status = "downloading"
                    job._paused_by_toggle = False
                    save_job(job)
                    logger.info(f"Resumed by toggle: {job.job_id}")
                except (ProcessLookupError, PermissionError):
                    pass
            elif not enabled and job.status == "downloading":
                # Toggle went OFF — pause active downloads
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                    job.status = "paused"
                    job._paused_by_toggle = True
                    save_job(job)
                    logger.info(f"Paused by toggle: {job.job_id}")
                except (ProcessLookupError, PermissionError):
                    pass
```

#### 5b. Call sync function from `/api/toggle` — `src/app.py`

In `api_toggle()` (around line 380), after `save_config(cfg)`, add:

```python
# Import at top of app.py:
from worker import sync_active_downloads_with_toggle

# In api_toggle:
@app.route("/api/toggle", methods=["PUT"])
@require_auth
def api_toggle_downloads():
    data = request.get_json() or {}
    if "enabled" not in data:
        return jsonify({"error": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    cfg = load_config()
    was_enabled = cfg.get("downloads_enabled", True)
    cfg["downloads_enabled"] = enabled
    save_config(cfg)
    logger.info(f"Master download toggle: {'ON' if enabled else 'OFF'}")
    
    # Sync active downloads: pause them if toggle went OFF, resume if ON
    if was_enabled != enabled:
        sync_active_downloads_with_toggle()
    
    if enabled:
        process_queue()
    return jsonify({"downloads_enabled": enabled})
```

#### 5c. Distinguish "paused by toggle" from "paused by user" in UI

In `buildPausedCard()`, check if the job was paused by the toggle (we need to expose this). Simplest: add a `paused_by` field to the DB, or use a convention like `error_message = "paused_by_toggle"`. 

Actually, simpler: just show different text. If the toggle is OFF and a job is paused, show "⏸ Paused (master toggle)" instead of "⏸ Paused". The dashboard already polls `/api/info` which has `downloads_enabled`. If `!downloadsEnabled && job.status == 'paused'`, the pause was likely from the toggle.

```js
function buildPausedCard(j) {
  // ... existing code ...
  const pauseReason = (!downloadsEnabled && j.status === 'paused') 
    ? 'Paused (master toggle)' 
    : 'Paused';
  // Use pauseReason in the chip text
}
```

---

### Phase 6 — Separate Downloads page (Issue 7)

#### 6a. New route `/downloads` — `src/app.py`

```python
@app.route("/downloads")
def downloads_page():
    cfg = load_config()
    return render_template("downloads.html", active="downloads", theme=cfg.get("theme", "dark"))
```

#### 6b. New template `src/templates/downloads.html`

```html
{% extends "base.html" %}
{% block title %}Library - yt-dl{% endblock %}
{% block content %}
<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:24px;">
  <h1 style="font-size:22px; font-weight:500;">Downloads</h1>
  <div style="display:flex; gap:12px; align-items:center;">
    <input type="text" id="dl-search" placeholder="Search downloads..." class="form-input" style="width:300px;">
    <select id="dl-sort" class="form-input" style="width:auto;">
      <option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option>
      <option value="largest">Largest first</option>
      <option value="smallest">Smallest first</option>
      <option value="title">Title (A-Z)</option>
    </select>
  </div>
</div>
<div id="dl-grid"></div>
<div style="text-align:center; padding:20px;">
  <button class="btn btn-secondary" id="dl-load-more" onclick="loadMoreDownloads()" style="display:none;">Load more</button>
</div>
{% endblock %}
{% block scripts %}
<script src="{{ url_for('static', filename='downloads.js') }}"></script>
{% endblock %}
```

#### 6c. New JS file `src/static/downloads.js`

```js
function escapeHtml(t) { /* same as dashboard.js */ }
function formatBytes(b) { /* same */ }
function timeAgo(dateStr) { /* same */ }

let allDownloads = [];
let dlOffset = 0;
const DL_PAGE_SIZE = 24;

async function loadDownloads() {
  const r = await fetch('/api/downloads?limit=' + DL_PAGE_SIZE + '&offset=' + dlOffset);
  const data = await r.json();
  if (dlOffset === 0) allDownloads = data.jobs;
  else allDownloads = allDownloads.concat(data.jobs);
  renderDownloads(allDownloads);
  
  // Show/hide load-more button
  document.getElementById('dl-load-more').style.display = 
    data.total > allDownloads.length ? '' : 'none';
}

function renderDownloads(jobs) {
  const grid = document.getElementById('dl-grid');
  if (!jobs.length) {
    grid.innerHTML = '<div style="text-align:center;padding:80px 0;color:var(--text-muted);"><p style="font-size:16px;font-weight:500;color:var(--text);">No downloads yet</p><p style="margin-top:8px;">Completed downloads will appear here.</p></div>';
    return;
  }
  grid.innerHTML = '<div class="grid-3">' + jobs.map(buildDownloadCard).join('') + '</div>';
}

function buildDownloadCard(j) {
  const thumb = buildThumb(j, 'q-thumb');
  const sizeStr = j.file_size ? formatBytes(j.file_size) : '';
  const meta = [j.quality, formatChip(j), sizeStr, timeAgo(j.completed_at || j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card completed" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✓ Completed</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bar"></div>'
    + '<div class="q-bottom" style="justify-content:flex-end;gap:12px;">'
    + '<span class="q-cancel" onclick="openFolder(\'' + escapeHtml(j.id) + '\')">Open</span>'
    + '<span class="q-cancel" onclick="redownloadJob(\'' + escapeHtml(j.id) + '\')">Redownload</span>'
    + '<span class="q-cancel" onclick="deleteJob(\'' + escapeHtml(j.id) + '\')" style="color:var(--accent-hover);">Delete</span>'
    + '</div></div></div>';
}

function buildThumb(j, cls) { /* same as dashboard.js */ }
function formatChip(j) { return j.quality === 'audio' ? 'mp3' : 'mp4'; }

function openFolder(id) {
  const job = allDownloads.find(j => j.id === id);
  if (!job || !job.file_path) return;
  const dir = job.file_path.substring(0, job.file_path.lastIndexOf('/'));
  fetch('/api/open', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path: dir})})
    .then(r => r.json()).then(d => { if (d.error) showToast(d.error, 'error'); });
}

function redownloadJob(id) {
  const job = allDownloads.find(j => j.id === id);
  if (!job) return;
  fetch('/api/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url: job.url, quality: job.quality})})
    .then(r => r.json()).then(d => showToast('Re-added to queue'));
}

function deleteJob(id) {
  const job = allDownloads.find(j => j.id === id);
  const hasFile = job && job.file_path;
  const msg = hasFile ? 'Delete this download AND remove the file from disk?' : 'Delete this download record?';
  if (!confirm(msg)) return;
  fetch('/api/jobs/' + id, {method:'DELETE'}).then(r => r.json()).then(d => {
    showToast('Deleted');
    allDownloads = allDownloads.filter(j => j.id !== id);
    renderDownloads(allDownloads);
  });
}

function loadMoreDownloads() {
  dlOffset += DL_PAGE_SIZE;
  loadDownloads();
}

// Search + sort
document.getElementById('dl-search')?.addEventListener('input', function() {
  // Client-side filter (since we loaded all visible ones)
  const q = this.value.toLowerCase();
  const filtered = allDownloads.filter(j => 
    (j.title || '').toLowerCase().includes(q) || 
    (j.url || '').toLowerCase().includes(q)
  );
  renderDownloads(filtered);
});

document.getElementById('dl-sort')?.addEventListener('change', function() {
  const sort = this.value;
  allDownloads.sort((a, b) => {
    switch(sort) {
      case 'newest': return new Date(b.completed_at || b.created_at) - new Date(a.completed_at || a.created_at);
      case 'oldest': return new Date(a.completed_at || a.created_at) - new Date(b.completed_at || b.created_at);
      case 'largest': return (b.file_size || 0) - (a.file_size || 0);
      case 'smallest': return (a.file_size || 0) - (b.file_size || 0);
      case 'title': return (a.title || '').localeCompare(b.title || '');
    }
  });
  renderDownloads(allDownloads);
});

loadDownloads();
```

#### 6d. New API endpoint `/api/downloads` — `src/app.py`

```python
@app.route("/api/downloads")
def api_downloads():
    """Paginated list of completed downloads, with search + sort."""
    try:
        limit = max(1, min(int(request.args.get("limit", 24)), 100))
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid pagination params"}), 400
    
    sort = request.args.get("sort", "newest")
    q = request.args.get("q", "").strip()
    
    order_by = {
        "newest": "completed_at DESC, created_at DESC",
        "oldest": "completed_at ASC, created_at ASC",
        "largest": "file_size DESC",
        "smallest": "file_size ASC",
        "title": "title ASC",
    }.get(sort, "completed_at DESC")
    
    db = get_db()
    try:
        where = "WHERE status='completed'"
        params = []
        if q:
            where += " AND (title LIKE ? OR url LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like])
        
        total = db.execute(f"SELECT COUNT(*) as c FROM downloads {where}", tuple(params)).fetchone()["c"]
        rows = db.execute(
            f"SELECT * FROM downloads {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            tuple(params) + [limit, offset]
        ).fetchall()
    finally:
        db.close()
    
    return jsonify({
        "jobs": [job_to_dict(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit
    })
```

#### 6e. Remove RECENT section from dashboard — `src/static/dashboard.js`

In `renderDashboard()` (line ~155), remove the completed section:

```js
// REMOVE these lines:
// if (completed.length) {
//   html += buildSection('RECENT', 'grid-3', completed.map(buildRecentCard).join(''));
// }
```

#### 6f. Add "Downloads" nav link — `src/templates/base.html`

```html
<div class="nav-links">
  <a href="/" class="nav-link {{ 'active' if active == 'dashboard' else '' }}">Queue</a>
  <a href="/downloads" class="nav-link {{ 'active' if active == 'downloads' else '' }}">Downloads</a>
  <a href="/stats" class="nav-link {{ 'active' if active == 'stats' else '' }}">Stats</a>
  <a href="/logs" class="nav-link {{ 'active' if active == 'logs' else '' }}">Logs</a>
  <a href="/settings" class="nav-link {{ 'active' if active == 'settings' else '' }}">Settings</a>
</div>
```

---

### Phase 7 — Enhanced duplicate detection

Current state: bulk add dedups against `completed` URLs only. Single `/api/add` doesn't dedup at all.

#### 7a. Dedup in `/api/add` — `src/app.py`

In `api_add_job()`, before inserting, check if the URL already exists in ANY status:

```python
# Check for duplicates — URL match against ALL statuses (not just completed)
db = get_db()
existing = db.execute(
    "SELECT job_id, status, title FROM downloads WHERE url=? ORDER BY created_at DESC LIMIT 1",
    (url,)
).fetchone()
if existing:
    return jsonify({
        "duplicate": True,
        "existing_job_id": existing["job_id"],
        "existing_status": existing["status"],
        "existing_title": existing["title"],
        "message": f"Already exists as {existing['status']}"
    }), 409  # 409 Conflict
```

#### 7b. Configurable dedup strictness — `src/models.py` DEFAULT_CONFIG

```python
"duplicate_detection": "strict",  # "strict" = any status, "lenient" = only completed, "off" = no dedup
```

In `api_add_job()` and `api_bulk_add()`, check this config:

```python
dedup_mode = cfg.get("duplicate_detection", "strict")
if dedup_mode == "off":
    # Skip dedup entirely
    pass
elif dedup_mode == "lenient":
    # Only dedup against completed
    existing = db.execute("SELECT 1 FROM downloads WHERE url=? AND status='completed' LIMIT 1", (url,)).fetchone()
else:
    # strict — dedup against any status
    existing = db.execute("SELECT 1 FROM downloads WHERE url=? LIMIT 1", (url,)).fetchone()

if existing:
    # skip or return duplicate error
```

#### 7c. UI: dedup setting in Settings page — `src/templates/settings.html`

Add a dropdown:

```html
<div class="form-group">
  <label class="form-label">Duplicate Detection</label>
  <select class="form-input" id="duplicate_detection">
    <option value="strict">Strict (any status — recommended)</option>
    <option value="lenient">Lenient (only completed downloads)</option>
    <option value="off">Off (allow duplicates)</option>
  </select>
  <p style="font-size:11px;color:var(--text-muted);margin-top:4px;">
    Prevents re-adding URLs that are already in your queue or history.
  </p>
</div>
```

#### 7d. Normalize URLs for better matching

URLs like `https://youtube.com/watch?v=abc` and `https://youtu.be/abc` and `https://www.youtube.com/watch?v=abc&t=10s` all refer to the same video. Add a normalization function:

```python
def normalize_url(url):
    """Normalize URL for dedup. Strips query params (except v=), 
    normalizes domain, removes trailing slashes."""
    from urllib.parse import urlparse, parse_qs, urlencode
    parsed = urlparse(url)
    # Normalize domain (www.youtube.com → youtube.com)
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    # For YouTube, keep only v= param
    if 'youtube.com' in netloc:
        qs = parse_qs(parsed.query)
        if 'v' in qs:
            query = urlencode({'v': qs['v'][0]})
        else:
            query = ''
    else:
        query = parsed.query
    # Rebuild
    return f"{parsed.scheme}://{netloc}{parsed.path}" + (f"?{query}" if query else "")
```

Use this in dedup checks: `existing = db.execute("SELECT 1 FROM downloads WHERE url=? OR url=?", (url, normalize_url(url))).fetchone()`

---

## More feature suggestions (for future phases, for now ignore these and focus on the above main stuff)

Taking inspiration from big projects like **JDownloader 2, IDM, yt-dlp GUI, Stash, Lidarr, CouchPotato**:

### High-value additions(none of these are not good , so i dont want the below )

1. **🔍 Global search** — Search bar in nav that searches across all jobs (queue + completed + failed). Currently the dashboard only shows latest 200.

2. **📊 Speed graph per download** — Small inline sparkline showing download speed over time (like Task Manager's network graph). Helps identify throttling.

3. **🏷️ Tags / categories** — User-defined tags per job (`music`, `tutorial`, `podcast`). Filter dashboard by tag. Auto-tag based on URL.

4. **🔄 Auto-retry with backoff** — Failed jobs auto-retry 3× with 1m/5m/15m delays. Currently manual only.

5. **⭐ Priority queue** — Right-click queued job → High/Med/Low. High-priority jumps to front.

6. **📅 Scheduled downloads** — "Only download between 22:00-08:00" or "Download this specific video on Friday at 18:00".

7. **📧 Channel subscriptions** — Subscribe to YouTube channel → daemon checks every 6h for new videos, auto-queues.

8. **💾 Disk space guard** — Auto-pause when disk <1GB free. Currently no protection.

9. **⚙️ Per-job settings override** — When adding, optionally set custom quality/output-dir/cookies per job instead of global.

10. **📤 Post-download actions** — Configurable shell script that runs after each download (move to Plex folder, convert format, scan with antivirus).

### UX polish

11. **⌨️ Keyboard shortcuts** — `J/K` navigate cards, `R` retry, `X` cancel, `D` delete, `Space` toggle master switch, `/` focus search.

12. **📱 Mobile-responsive layout** — Current dashboard breaks on mobile. Single-col layout with swipe actions.

13. **🌙 Auto theme** — Follow system dark/light preference via `prefers-color-scheme`.

14. **🔔 Browser notification on queue-empty** — "All downloads complete" desktop notification.

15. **📋 Copy URL on click** — Click a card's URL chip to copy to clipboard.

16. **🎬 Preview on hover** — Hover a completed download → 5-second muted video preview (like YouTube).

### Power-user features

17. **📤 Export/import history** — Download full job history as JSON/CSV. Import on fresh install.

18. **🔌 Webhook on every event** — Currently only fires on completion/failure. Add webhook on: job queued, job started, progress milestones (25/50/75%), queue empty.

19. **📊 Stats improvements** — Top 10 channels by disk usage, download speed over time graph, success rate trend.

20. **🗑️ Auto-cleanup policy** — "Delete completed downloads older than 30 days" or "Keep only latest 100 per channel".

21. **🔒 API key per client** — Different API keys for extension vs dashboard vs external scripts, with permissions (read-only vs read-write).

22. **📦 Bundle downloads** — Select multiple completed downloads → "Download as ZIP" (server-side zips and streams).

---

## Implementation order

1. **Phase 1** — Titles + thumbnails (1a, 1b, 1c, 1d) — fixes most visible issue
2. **Phase 2** — Font redesign (2a, 2b) — quick visual win
3. **Phase 3** — File size + progress (3a, 3b, 3c) — needs DB migration
4. **Phase 4** — Per-card pause/resume (4a, 4b, 4c) — small change
5. **Phase 5** — Toggle affects active downloads (5a, 5b, 5c) — small change
6. **Phase 6** — Separate Downloads page (6a-6f) — biggest new feature
7. **Phase 7** — Enhanced dedup (7a, 7b, 7c, 7d) — quality-of-life

## Verification checklist

### Phase 1 (titles + thumbnails)
- [ ] Queue a new YouTube URL → title appears in dashboard within 3 seconds (before download starts)
- [ ] Queue a non-YouTube URL (TikTok/Twitter) → thumbnail shows (from yt-dlp's `thumbnail` field)
- [ ] Failed job without title → shows URL last segment, not "Unknown"
- [ ] Failed job with bad YouTube ID → thumbnail falls back to placeholder, no broken img

### Phase 2 (fonts)
- [ ] Nav brand "yt-dl" looks lighter (500 weight, not 700)
- [ ] Card titles look less bold (500 weight, not 600)
- [ ] Overall feel closer to Google Material Design

### Phase 3 (file size + progress)
- [ ] Downloading card shows "50 MB / 120 MB" (downloaded / total)
- [ ] If total unknown, shows just "50 MB downloaded"
- [ ] Completed card still shows final file size
- [ ] Queued/failed cards don't show size (correctly)

### Phase 4 (per-card pause)
- [ ] Downloading card has both "Pause" and "Cancel" buttons
- [ ] Click Pause → job moves to PAUSED section
- [ ] Paused card has "Resume" button (already existed)
- [ ] Click Resume → job moves back to DOWNLOADING

### Phase 5 (toggle affects active)
- [ ] With 2 active downloads, click master toggle OFF → both pause (move to PAUSED section)
- [ ] Click master toggle ON → both resume (move back to DOWNLOADING)
- [ ] Paused-by-toggle cards show "Paused (master toggle)" instead of just "Paused"
- [ ] User-paused cards (via per-card Pause button) are NOT affected by toggle ON/OFF

### Phase 6 (Downloads page)
- [ ] Nav bar has "Downloads" link between Queue and Stats
- [ ] Clicking it opens `/downloads` page with 3-col grid of completed jobs
- [ ] Search bar filters by title/URL in real-time
- [ ] Sort dropdown works (newest/oldest/largest/smallest/title)
- [ ] Each card has Open / Redownload / Delete buttons
- [ ] "Load more" button appears if >24 downloads
- [ ] Dashboard no longer shows RECENT section at bottom

### Phase 7 (dedup)
- [ ] Add a URL that's already queued → 409 response with "Already exists as queued"
- [ ] Add a URL that's already completed → 409 with "Already exists as completed"
- [ ] Settings page has "Duplicate Detection" dropdown (strict/lenient/off)
- [ ] `youtube.com/watch?v=abc` and `youtu.be/abc` are recognized as duplicates

## Deliverable

After applying all phases:
1. Run the full test checklist (35+ items)
2. Commit each phase as a separate git commit
3. Tag as `v1.2.0` after all phases verified
4. Update README with new features

---

## Reference: confirmed test results

```
[Title issue — verified]
Seeded queued job with title="" → dashboard shows "Unknown"
After Phase 1a (eager metadata fetch) → title appears within 2-3 seconds

[Thumbnail issue — verified]
Failed TikTok job with video_id="" → no thumbnail (correct)
Failed YouTube job with bad 11-char ID → i.ytimg.com returns 404 → placeholder shows (correct, but ugly)
After Phase 1c (stored thumbnail from yt-dlp) → real thumbnail from yt-dlp's `thumbnail` field

[Font — verified current weights]
.nav-brand: 700 (too bold)
.dl-title: 600 (too bold)
.q-title: 600 (too bold)
.section-label: 600 (slightly heavy)
After Phase 2: all → 500 (medium, Material Design standard)

[File size — verified]
API returns file_size only for completed jobs
During download: progress %, speed, eta — but NO total_bytes or downloaded_bytes
After Phase 3: yt-dlp progress template includes total_bytes + downloaded_bytes → dashboard shows "50 MB / 120 MB"

[Per-card pause — verified]
Downloading card buttons: only "Cancel"
Paused card buttons: only "Resume"
After Phase 4: downloading gets "Pause" + "Cancel", paused keeps "Resume"

[Toggle scope — verified]
Current: toggle OFF → queued jobs stay queued, active downloads KEEP RUNNING
After Phase 5: toggle OFF → queued stays queued AND active downloads PAUSE

[Downloads page — verified]
Current: completed jobs show in RECENT section at bottom of dashboard
After Phase 6: new /downloads page, RECENT section removed from dashboard
```
