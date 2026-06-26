# DeepSeek Prompt — Complete Bug-Fix Guide for `CodeAbhi826/yt-dl`

> **Audited commit:** `9714fc7` (HEAD as of 2026-06-26, "fix: phantomjs openSSL provider crash via OPENSSL_CONF env")
> **Previous commit:** `ed5409b`
> **Total bugs:** 68 (16 high-severity, 13 medium, 14 low + 25 UI-specific)
> **Repo:** https://github.com/CodeAbhi826/yt-dl

---

## Context

`yt-dl` is a self-hosted YouTube download daemon: Flask + yt-dlp + SQLite backend, Chrome MV3 browser extension, live web dashboard with SSE.

### User-reported critical issues (verified)

1. **Dashboard renders as a single-column vertical list** instead of the intended 2-column (downloading) / 3-column (queued/recent/failed) grid. The user wants a **fixed, modern grid layout**: 2 cards per row for the DOWNLOADING section, 3 cards per row for QUEUED / RECENT / FAILED sections. Cards wrap to the next row naturally (so 5 downloading jobs = 2+2+1, NOT 2+1-full-width). The layout must NOT collapse to 1 column on normal desktop widths.

2. **"Pause all" button cancels downloads** instead of pausing them — destructive action hidden behind a non-destructive label. The `pause_job` / `resume_job` functions exist in `worker.py` but no Flask route exposes them. The button handler calls `cancelJob()` which SIGTERMs the process.

3. **New commit `9714fc7`** adds `OPENSSL_CONF=/dev/null` to all yt-dlp subprocess environments — a global workaround that needs review.

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
- Python 3.12, Flask (dev server, `threaded=True`), yt-dlp (subprocess), SQLite
- Chrome Extension Manifest V3 (service worker, `chrome.alarms`, `chrome.scripting`)
- SSE for live queue + live logs

### Goal
Apply every fix below. Do not refactor unrelated code. Do not change public API contracts unless a fix explicitly requires it. Preserve all existing behavior that is correct. After fixing, briefly summarize each change with `file:line` references.

---

# 🔴 CRITICAL PRIORITY #1 — Dashboard layout (Bug 43)

## Confirmed root cause (headless-browser verified at commit 9714fc7)

The user wants: **2 cards per row for DOWNLOADING, 3 cards per row for QUEUED/RECENT/FAILED. Cards wrap naturally to the next row. Layout is FIXED and does NOT collapse to 1 column on desktop.**

I verified by rendering the dashboard at 6 viewport widths with Playwright + Chromium at the new HEAD (`9714fc7`):

| Viewport width | grid-2 cols | grid-3 cols | Status |
|---|---|---|---|
| 1440px | 2 ✅ | 3 ✅ | Correct |
| 1280px | 2 ✅ | 3 ✅ | Correct |
| 1024px | 2 ✅ | 3 ✅ | Correct |
| **900px** | **1** ❌ | **1** ❌ | **BUG — collapses to 1 col** |
| **800px** | **1** ❌ | **1** ❌ | **BUG — collapses to 1 col** |
| **640px** | **1** ❌ | **1** ❌ | Expected (phone) |

The CSS at `src/static/style.css:40`:
```css
@media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
```

900px is too aggressive — a maximized browser on a 1366px laptop with a 200px side panel already drops below 900px content width. The user sees a single-column list.

## The fix — `src/static/style.css` (lines 37-40)

### Current (buggy) code
```css
/* ── Grids ── */
.grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
```

### Exact replacement
```css
/* ── Grids ── */
/* FIXED LAYOUT: 2 cols for downloading, 3 cols for queued/recent/failed.
   Cards wrap naturally to the next row (5 downloading = 2+2+1).
   Does NOT collapse to 1 col on desktop — only on phone-width (<640px). */
.grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }

/* Tablet: 3-col grid drops to 2 cols to avoid cramped cards */
@media (max-width: 900px) and (min-width: 641px) {
  .grid-3 { grid-template-columns: repeat(2, 1fr); }
  /* grid-2 stays at 2 cols — downloading cards are wide enough */
}

/* Phone: everything single column */
@media (max-width: 640px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}
```

### Why this is the correct fix
- **Desktop (≥901px):** 2 cols for downloading, 3 cols for rest. 5 downloading jobs = rows of 2+2+1 (the lone card on the last row is at half-width — this is **normal CSS Grid behavior** and what the user wants).
- **Tablet (641-900px):** 3-col grid drops to 2 cols (cards would be too narrow at 3 cols in ~700px). 2-col grid stays at 2 (downloading cards have thumbnails that need width).
- **Phone (≤640px):** everything 1 col.
- **No `:only-child` or `:last-child:nth-child(odd)` hacks** — the user explicitly said the wrapping behavior (2+2+1) is what they want. Don't add full-width-spanning hacks.

### Also fix card heights for consistency

**File:** `src/static/style.css` lines 43 and 66

Change `height:` to `min-height:` so cards can grow to fit long titles but maintain a consistent minimum:

```css
/* Line 43 */
.dl-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius-lg); padding: 16px; min-height: 160px; display: flex; gap: 16px; cursor: default; transition: transform 150ms ease, box-shadow 150ms ease; }

/* Line 66 */
.q-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius-lg); padding: 16px; min-height: 120px; display: flex; gap: 14px; cursor: default; transition: transform 150ms ease, box-shadow 150ms ease; }
```

### Verification checklist
- [ ] Dashboard at 1440px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 1280px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 1024px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 800px → grid-2 shows 2 cols, grid-3 shows 2 cols (was: 1 col)
- [ ] Dashboard at 600px → all grids show 1 col
- [ ] With 5 downloading jobs at 1440px → rows of 2+2+1 (lone card at half-width on last row — this is correct and desired)
- [ ] With 7 queued jobs at 1440px → rows of 3+3+1 (lone card at third-width on last row — correct)
- [ ] Long titles don't cause card overflow (cards grow with `min-height`)

---

# 🔴 CRITICAL PRIORITY #2 — "Pause all" button cancels downloads (Bug 44)

## Confirmed bug

**Files:** `src/templates/dashboard.html:9`, `src/static/dashboard.js:192-200`, `src/worker.py:330-349`, `src/app.py:30`

The footer button is labeled "Pause all":
```html
<span class="footer-action footer-action-danger" id="pause-all">Pause all</span>
```

But the click handler calls `cancelJob()`:
```js
const pauseBtn = document.getElementById("pause-all");
if (pauseBtn) {
  pauseBtn.addEventListener("click", function() {
    const dl = prevJobs.filter(j => j.status === 'downloading');
    if (!dl.length) return;
    dl.forEach(j => cancelJob(j.id));   // CANCELS, not pauses!
    showToast("Cancelled " + dl.length + " active");
  });
}
```

`cancel_job()` (worker.py:303) kills the process group with SIGTERM, sets `status='cancelled'`, and saves. **No resume path exists** — partial downloads are discarded.

The `pause_job` / `resume_job` functions DO exist in `worker.py:330-349` (using SIGSTOP/SIGCONT) and ARE imported in `app.py:30`, but **no Flask route exposes them**. They also have a latent `ProcessLookupError` bug (no try/except around `os.killpg`).

## The fix — 5 parts

### Part A: Add pause/resume Flask routes

**File:** `src/app.py`

After the existing `/api/jobs/<job_id>/cancel` route (around line 200), add these routes:

```python
@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
@require_auth
def api_pause_job(job_id):
    if pause_job(job_id):
        logger.info(f"Job paused: {job_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not active"}), 404

@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
@require_auth
def api_resume_job(job_id):
    if resume_job(job_id):
        logger.info(f"Job resumed: {job_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not paused"}), 404

@app.route("/api/jobs/pause-all", methods=["POST"])
@require_auth
def api_pause_all():
    """Pause every active (downloading) job. Returns count of paused jobs."""
    paused = 0
    with queue_lock:
        for job in list(active_jobs.values()):
            if job.status == "downloading" and pause_job(job.job_id):
                paused += 1
    logger.info(f"Paused {paused} jobs")
    return jsonify({"paused": paused})

@app.route("/api/jobs/resume-all", methods=["POST"])
@require_auth
def api_resume_all():
    """Resume every paused job."""
    resumed = 0
    with queue_lock:
        for job in list(active_jobs.values()):
            if resume_job(job.job_id):
                resumed += 1
    logger.info(f"Resumed {resumed} jobs")
    return jsonify({"resumed": resumed})
```

### Part B: Fix `pause_job` / `resume_job` in worker.py

**File:** `src/worker.py:330-349`

Replace the existing `pause_job` and `resume_job` with:

```python
def pause_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                    job.status = "paused"
                    save_job(job)
                    logger.info(f"Paused job: {job_id}")
                    return True
                except (ProcessLookupError, PermissionError) as e:
                    logger.warning(f"Failed to pause {job_id}: {e}")
                    return False
    return False


def resume_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGCONT)
                    job.status = "downloading"
                    save_job(job)
                    logger.info(f"Resumed job: {job_id}")
                    return True
                except (ProcessLookupError, PermissionError) as e:
                    logger.warning(f"Failed to resume {job_id}: {e}")
                    return False
    return False
```

Key changes:
- Added `try/except` around `os.killpg` (fixes latent `ProcessLookupError` crash)
- Set `job.status = "paused"` / `"downloading"` and call `save_job(job)` so the dashboard reflects the state change
- The `with queue_lock:` block is held during `save_job` — this is intentional to prevent race with `run_download`'s finally block

### Part C: Fix the dashboard "Pause all" button to actually pause

**File:** `src/static/dashboard.js:192-200`

Replace the existing `pauseBtn` click handler with:

```js
const pauseBtn = document.getElementById("pause-all");
if (pauseBtn) {
  pauseBtn.addEventListener("click", function() {
    const dl = prevJobs.filter(j => j.status === 'downloading');
    const paused = prevJobs.filter(j => j.status === 'paused');

    if (dl.length > 0) {
      // Active jobs exist → pause them
      if (!confirm(`Pause ${dl.length} active download(s)?`)) return;
      fetch("/api/jobs/pause-all", {method:"POST"})
        .then(r => r.json())
        .then(d => showToast("Paused " + d.paused + " jobs"))
        .catch(() => showToast("Pause failed", "error"));
    } else if (paused.length > 0) {
      // No active jobs, but some paused → resume them
      if (!confirm(`Resume ${paused.length} paused download(s)?`)) return;
      fetch("/api/jobs/resume-all", {method:"POST"})
        .then(r => r.json())
        .then(d => showToast("Resumed " + d.resumed + " jobs"))
        .catch(() => showToast("Resume failed", "error"));
    }
    // If neither active nor paused jobs exist, do nothing
  });
}
```

### Part D: Add "paused" status to dashboard rendering

**File:** `src/static/dashboard.js`

In `renderDashboard()` (around line 136-154), after the `failed` filter, add:

```js
const paused = jobs.filter(j => j.status === 'paused');
```

And in the rendering section (after the FAILED section block), add:

```js
if (paused.length) {
  html += buildSection('PAUSED', 'grid-2', paused.map(buildPausedCard).join(''));
}
```

Add a new card builder function (place it after `buildFailedCard`):

```js
function buildPausedCard(j) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="dl-thumb">'
    : '<div class="dl-thumb-placeholder">YT</div>';
  const pct = j.progress || 0;
  return '<div class="dl-card" data-id="' + escapeHtml(j.id) + '" style="opacity:0.6">'
    + '<div class="dl-thumb-wrap">' + thumb + '</div>'
    + '<div class="dl-body">'
    + '<div><div class="dl-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div class="dl-meta"><span class="dl-chip">⏸ Paused</span><span class="dl-chip">' + pct.toFixed(1) + '%</span></div></div>'
    + '<div><div class="dl-progress"><div class="dl-progress-bar"><div class="dl-progress-fill" style="width:' + pct + '%"></div></div></div>'
    + '<div class="dl-stats"><span>Paused at ' + pct.toFixed(1) + '%</span><span class="dl-cancel" onclick="resumeJob(\'' + escapeHtml(j.id) + '\')">Resume</span></div></div>'
    + '</div></div>';
}

function resumeJob(id) {
  fetch("/api/jobs/" + id + "/resume", {method:"POST"})
    .then(r => r.json())
    .then(d => { showToast("Resumed"); });
}
```

### Part E: Toggle button label based on state

**File:** `src/static/dashboard.js` — in `updateFooter()`, replace the existing function:

```js
function updateFooter(jobs) {
  const active = jobs.filter(j => j.status === 'downloading').length;
  const queued = jobs.filter(j => j.status === 'queued').length;
  const paused = jobs.filter(j => j.status === 'paused').length;
  const left = document.getElementById("footer-left");
  if (left) left.textContent = '⬇ ' + active + ' active • ' + queued + ' queued' + (paused ? ' • ' + paused + ' paused' : '');

  // Toggle pause/resume label and color based on current state
  const pauseBtn = document.getElementById("pause-all");
  if (pauseBtn) {
    if (active > 0) {
      pauseBtn.textContent = 'Pause all';
      pauseBtn.classList.add('footer-action-danger');
    } else if (paused > 0) {
      pauseBtn.textContent = 'Resume all';
      pauseBtn.classList.remove('footer-action-danger');
    } else {
      pauseBtn.textContent = 'Pause all';
      pauseBtn.classList.add('footer-action-danger');
    }
  }
}
```

### Verification
- [ ] Click "Pause all" with 2 downloading jobs → both become "paused", partial downloads preserved
- [ ] Paused jobs appear in a PAUSED section with a Resume button per card
- [ ] Click "Resume" on a paused job → it resumes downloading
- [ ] When all jobs are paused, footer button label changes to "Resume all"
- [ ] Click "Resume all" → all paused jobs resume
- [ ] No `ProcessLookupError` in daemon log when pausing a job that just finished

---

# 🔴 NEW BUG from commit 9714fc7 — OPENSSL_CONF override (Bug 68)

## Bug 68 — `OPENSSL_CONF=/dev/null` applied globally to all yt-dlp subprocesses

**File:** `src/worker.py:154` (added in commit `9714fc7`)

### Current code
```python
env = {**os.environ, "OPENSSL_CONF": "/dev/null"}
```

This env is passed to BOTH the `--dump-json` info-extraction subprocess (line 158) and the actual download `Popen` (line 215).

### Problems

1. **Global override:** Every single download gets `OPENSSL_CONF=/dev/null`, not just the ones that would crash. This is a system-specific workaround (the author's system had a phantomjs OpenSSL provider crash) applied to all users globally.

2. **Overwrites user's legitimate config:** If a user has a legitimate `OPENSSL_CONF` (e.g., for corporate proxy with custom CA certs, or FIPS compliance), it gets overwritten with `/dev/null`. Their TLS might break.

3. **No comment explaining why:** The commit message says "phantomjs openSSL provider crash" but yt-dlp doesn't use phantomjs directly. Future maintainers won't understand why this is here.

4. **`/dev/null` is Unix-only:** On Windows (if anyone runs this natively), `/dev/null` doesn't exist. The `open()` would fail. (Less of a concern since the project is Linux-focused, but still.)

5. **The real fix should be at the source:** If a specific yt-dlp extractor uses phantomjs and crashes, the fix should target that extractor, not disable OpenSSL config globally.

### Why it matters
Silently overrides a system security setting for every download. Could break TLS for users with custom OpenSSL configs. Makes debugging harder (if TLS breaks, users won't think to check this env var).

### Exact fix

**Option A (recommended — make it conditional + documented):**

```python
# Some systems have a misconfigured OpenSSL provider (e.g., phantomjs
# loaded via a yt-dlp extractor) that crashes yt-dlp. If the user has
# NOT explicitly set OPENSSL_CONF, override it to /dev/null to skip
# the problematic config. If the user HAS set it, respect their value.
env = {**os.environ}
if "OPENSSL_CONF" not in os.environ:
    env["OPENSSL_CONF"] = "/dev/null"
```

This respects an explicit user-set `OPENSSL_CONF` while still applying the workaround by default.

**Option B (make it configurable via settings):**

Add a new config key `openssl_conf_override` (default: `"/dev/null"`) to `DEFAULT_CONFIG` in `models.py`. In `worker.py`:

```python
cfg = load_config()
env = {**os.environ}
override = cfg.get("openssl_conf_override", "")
if override:
    env["OPENSSL_CONF"] = override
```

And add a settings UI field for it (only advanced users would touch it).

Pick **Option A** — it's simpler and respects user intent.

---

# The remaining 65 bugs (Bugs 1–42, 45–67)

These bugs are documented in full detail in the **companion file**: `/home/z/my-project/download/yt-dl_bugfix_prompt.md` (2550 lines). Below is a summary table. DeepSeek should consult the companion file for exact buggy code + exact fix for each.

## Summary table — all 68 bugs

| # | Severity | File | Bug |
|---|---|---|---|
| **43** | 🔴 CRITICAL | `style.css` | Dashboard collapses to 1 col at ≤900px (fixed above) |
| **44** | 🔴 CRITICAL | `dashboard.js`+`worker.py`+`app.py` | "Pause all" cancels downloads (fixed above) |
| **68** | 🔴 NEW | `worker.py:154` | OPENSSL_CONF=/dev/null global override (fixed above) |
| 1 | 🔴 HIGH | `worker.py` | Cancelled download flipped to "failed" by run_download thread |
| 2 | 🔴 HIGH | `worker.py` | `started_at` overwritten on every progress save |
| 3 | 🔴 HIGH | `settings.html` | Saving settings resets theme to "dark" |
| 4 | 🔴 HIGH | `Dockerfile`+`worker.py` | Webhook fails in Docker (no curl) |
| 5 | 🔴 HIGH | `app.py` | Playlist handler hardcodes YouTube URLs for every entry |
| 6 | 🔴 HIGH | `worker.py` | `file_path` stored as basename, not absolute path |
| 7 | 🔴 HIGH | `background.js` | Right-click on `<video>`/`<audio>` doesn't work (no srcUrl) |
| 8 | 🔴 HIGH | `background.js` | SW restarts flood user with toasts (prevJobs not persisted) |
| 9 | 🔴 HIGH | `background.js`+`popup.js` | Extension doesn't send API key → broken when YTDL_API_KEY set |
| 10 | 🔴 HIGH | `background.js` | Chrome alarm `periodInMinutes: 0.1` silently clamped to 0.5 |
| 11 | 🔴 HIGH | `worker.py` | `_worker_loop` lost-wakeup race on Event.clear() |
| 12 | 🔴 HIGH | `worker.py` | Webhook fires for cancelled jobs |
| 13 | 🔴 HIGH | `worker.py` | Fallback file finder doesn't search subdirectories (glob vs rglob) |
| 14 | 🔴 HIGH | `app.py` | `bulk/retry` re-queues completed/cancelled jobs without filter |
| 15 | 🔴 HIGH | `app.py` | Non-YouTube URLs collide on job_id |
| 16 | 🟡 MED | `worker.py`+`models.py` | Timezone inconsistency (UTC vs local) |
| 17 | 🟡 MED | `yt-dl-handler.sh` | Checks wrong health endpoint (`/api/health` vs `/health`) |
| 18 | 🟡 MED | `worker.py` | pause_job/resume_job dead code with latent bugs (now fixed in Bug 44) |
| 19 | 🟡 MED | `app.py` | `stream_queue` SSE leaks DB connection on exception |
| 20 | 🟡 MED | `app.py` | `PUT /api/settings` no validation, accepts arbitrary keys |
| 21 | 🟡 MED | `app.py` | `api_logs` count param not validated |
| 22 | 🟡 MED | `app.py` | Playlist detection blocks Flask thread for 30s |
| 23 | 🟡 MED | `app.py` | API key compared with `!=` (timing attack) |
| 24 | 🟡 MED | `app.py` | `/api/open` allows opening any directory on host |
| 25 | 🟡 MED | `app.py` | `process_queue` wrapped in redundant threading.Thread (3 places) |
| 26 | 🟡 MED | `Dockerfile` | Container runs as root |
| 27 | 🟡 MED | `app.py` | `api_upload_cookies` leaks absolute server path |
| 28 | 🟡 MED | `app.py`+`models.py` | `max_log_lines` setting doesn't resize existing deque |
| 29 | 🟢 LOW | multiple | Version numbers inconsistent (1.1 vs 1.0 vs 1.0.0) |
| 30 | 🟢 LOW | `install.sh` | Hardcodes `/usr/bin/python3` |
| 31 | 🟢 LOW | `install.sh` | Still checks for deleted `python-dbus` |
| 32 | 🟢 LOW | `install.fish` | References deleted `notifications.py` |
| 33 | 🟡 MED | `background.js` | Toast appears on wrong tab (active tab, not source tab) |
| 34 | 🟡 MED | `manifest.json` | `<all_urls>` host permission (overly broad) |
| 35 | 🟢 LOW | `worker.py` | `safe_title` sanitization has dead `replace("/", "⧸")` |
| 36 | 🟢 LOW | `worker.py` | `--format` passed to `--dump-json --no-download` (ignored) |
| 37 | 🟢 LOW | `app.py` | `api_add_job` loads config from disk just for default_quality |
| 38 | 🟡 MED | `app.py` | `api_queue` returns only latest 200 (no pagination) |
| 39 | 🟡 MED | `app.py` | Stats chart pads with empty bars (no labels) |
| 40 | 🟡 MED | `app.py` | Status breakdown "Other" lumps active+queued+cancelled |
| 41 | 🟢 LOW | `README.md` | Cookie export instructions produce empty file |
| 42 | 🟡 MED | `app.py` | SSE `stream_queue` uses Python's randomized `hash()` |
| 45 | 🟡 MED | `dashboard.js` | "Clear completed" silently DELETES downloaded files |
| 46 | 🟡 MED | `dashboard.js` | Completed card has "Delete" but no "Open" button |
| 47 | 🟡 MED | `dashboard.js` | `deleteJob()` confirmation is vague ("Delete?") |
| 48 | 🟢 LOW | `dashboard.js` | Card "mp4" chip shows even for audio downloads |
| 49 | 🟡 MED | `dashboard.js` | Thumbnails load from i.ytimg.com for non-YouTube videos |
| 50 | 🟡 MED | `base.html`+`theme.js` | Theme flash (FOUC) on page load |
| 51 | 🟡 MED | `logs.js` | Logs page shows duplicate entries (SSE + fetch race) |
| 52 | 🟡 MED | `base.html`+`dashboard.js` | Two independent connection monitors can disagree |
| 53 | 🟢 LOW | `style.css`+`stats.html` | `.bar-chart` CSS class is dead code |
| 54 | 🟡 MED | `dashboard.js` | No pagination on dashboard (only latest 200 visible) |
| 55 | 🟡 MED | `stats.html`+`stats.js` | No empty state for stats page |
| 56 | 🟡 MED | `settings.html` | No client-side validation on settings form |
| 57 | 🟡 MED | `settings.html` | "Save Settings" button doesn't disable during save |
| 58 | 🟢 LOW | `logs.html` | "Clear Display" button label is misleading |
| 59 | 🟡 MED | `style.css`+`base.html` | Nav bar not responsive on narrow screens |
| 60 | 🟡 MED | `theme.js` | PUT to `/api/settings` has no error handling |
| 61 | 🟡 MED | `dashboard.js` | Failed card click navigates to /logs (entire card clickable) |
| 62 | 🟡 MED | `app.py` | SSE queue stream polls DB every 1s per client (N+1) |
| 63 | 🟢 LOW | `models.py` | `human_bytes()` type inconsistency on negative input |
| 64 | 🟢 LOW | `style.css` | Card heights fixed (don't adapt to long titles) — fixed in Bug 43 |
| 65 | 🟡 MED | `dashboard.js` | `escapeHtml` doesn't escape single/double quotes |
| 66 | 🟡 MED | `stats.html`+`stats.js` | "Reset Stats" button doesn't confirm file deletion |
| 67 | 🟡 MED | `worker.py` | `%(info.filepath)s` may render as "NA" early in download |

> **Note:** Bug 64 (card heights) is already fixed as part of Bug 43's CSS changes. Bug 18 (pause/resume dead code) is already fixed as part of Bug 44. So effectively 66 unique fixes remain.

---

## Implementation order

Apply in this sequence to avoid merge conflicts:

### Phase 1 — Backend core (`worker.py`)
1. Bug 29 (version constant) — create `src/_version.py`
2. Bug 68 (OPENSSL_CONF conditional) — `worker.py:154`
3. Bug 2 (started_at) — `worker.py` `DownloadJob.__init__` + `save_job`
4. Bug 1, 12, 13 (cancel/fail, webhook on cancel, glob fallback) — `worker.py` `run_download`/`cancel_job`
5. Bug 6 (file_path → filepath) — `worker.py` progress template
6. Bug 67 (filepath NA guard) — `worker.py` parser
7. Bug 35, 36 (safe_title dead replace, --format flag) — `worker.py`
8. Bug 44 Part B (pause_job/resume_job fixes) — `worker.py:330-349`

### Phase 2 — Backend API (`app.py`)
9. Bug 5, 14, 15 (playlist URL, bulk_retry filter, job_id collision) — `app.py` queue routes
10. Bug 22 (playlist DoS) — `app.py` `_detect_playlist_async`
11. Bug 19, 25, 42 (SSE leak, redundant threads, md5 hash) — `app.py` SSE
12. Bug 20, 21, 23, 24, 27, 28, 38 (settings validation, logs count, constant-time auth, open path, cookie leak, max_log_lines, pagination) — `app.py` API
13. Bug 16 (timezone) — `worker.py` + `app.py`
14. Bug 44 Part A (pause/resume routes) — `app.py`

### Phase 3 — Frontend CSS (`style.css`)
15. **Bug 43 (CRITICAL — layout)** — `style.css` grids + breakpoints + card heights
16. Bug 53 (dead .bar-chart) — `style.css` + `stats.html`
17. Bug 59 (responsive nav) — `style.css`

### Phase 4 — Frontend JS (`dashboard.js`, `dashboard.html`)
18. Bug 44 Parts C-E (dashboard pause/resume UI + footer toggle) — `dashboard.js`
19. Bug 45, 46, 47 (destructive button fixes) — `dashboard.js` + `dashboard.html`
20. Bug 48 (mp4/mp3 chip) — `dashboard.js`
21. Bug 49 (non-YouTube thumbnails) — `dashboard.js`
22. Bug 61 (failed card click) — `dashboard.js`
23. Bug 65 (escapeHtml) — `dashboard.js`
24. Bug 54 (pagination) — `dashboard.js` + `dashboard.html`

### Phase 5 — Other frontend (`base.html`, `theme.js`, `logs.*`, `stats.*`, `settings.html`)
25. Bug 50 (theme FOUC) — `base.html` + `theme.js`
26. Bug 60 (theme.js error handling) — `theme.js`
27. Bug 51 (logs SSE race) — `logs.js`
28. Bug 58 (Clear Display label) — `logs.html`
29. Bug 52 (connection monitor) — `base.html` + `dashboard.js`
30. Bug 55, 56, 57, 66 (stats empty state, settings validation, save button, reset confirm) — `stats.html`, `stats.js`, `settings.html`
31. Bug 3 (theme reset) — `settings.html`

### Phase 6 — Extension
32. Bug 7, 8, 9, 10, 33, 34 (srcUrl, prevJobs persist, API key, alarm 0.5, source tab, <all_urls>) — `background.js`, `popup.html`, `popup.js`, `manifest.json`

### Phase 7 — Docker + install + docs
33. Bug 4, 26 (Dockerfile curl + non-root) — `Dockerfile`, `docker-compose.yml`
34. Bug 17, 30, 31, 32 (handler.sh, install.sh, install.fish) — shell scripts
35. Bug 41 (README cookies) — `README.md`

---

## Full test checklist (68 items)

### Layout & UI (Bugs 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 64, 65, 66)
- [ ] Dashboard at 1440px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 1280px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 1024px → grid-2 shows 2 cols, grid-3 shows 3 cols
- [ ] Dashboard at 800px → grid-2 shows 2 cols, grid-3 shows 2 cols (was: 1 col)
- [ ] Dashboard at 600px → all grids show 1 col
- [ ] With 5 downloading jobs at 1440px → rows of 2+2+1 (lone card at half-width — correct, desired)
- [ ] With 7 queued jobs at 1440px → rows of 3+3+1
- [ ] Long titles don't cause card overflow (cards grow with min-height)
- [ ] Click "Pause all" with 2 active jobs → jobs become "paused" (NOT cancelled)
- [ ] Paused jobs appear in a PAUSED section with Resume button
- [ ] Click "Resume" on a paused job → resumes downloading
- [ ] Footer button label changes to "Resume all" when all jobs paused
- [ ] Click "Resume all" → all paused jobs resume
- [ ] No `ProcessLookupError` in daemon log when pausing a job that just finished
- [ ] "Clear completed" shows confirmation mentioning file deletion
- [ ] Completed card has an "Open" button that opens the file's directory
- [ ] `deleteJob()` confirmation mentions file deletion for completed jobs
- [ ] Audio download card shows "mp3" chip, not "mp4"
- [ ] Non-YouTube download shows "YT" placeholder, not broken image
- [ ] No theme flash on page load when localStorage differs from server
- [ ] Logs page shows no duplicates after page load
- [ ] Nav connection indicator matches SSE state within 1s
- [ ] `.bar-chart` CSS class is used (not inline styles)
- [ ] "Load more" button appears with >200 jobs
- [ ] Stats page shows "No downloads yet" on fresh install
- [ ] Settings form rejects `concurrent_limit: 999` client-side
- [ ] "Save Settings" button disables and shows "Saving..." during save
- [ ] "Clear Display" button in logs renamed "Clear View"
- [ ] Nav bar wraps gracefully at 768px
- [ ] Theme toggle reverts on server error
- [ ] Failed card not entirely clickable; "View logs" is separate link
- [ ] Video title with `"` or `'` doesn't break HTML
- [ ] "Reset Stats" renamed "Clear History" with explicit confirmation

### Backend (Bugs 1-42, 68)
- [ ] Cancel active download → status "cancelled", not "failed"; no webhook
- [ ] `started_at` is original start time, not last progress update
- [ ] Toggle theme to light → change unrelated setting → Save → theme stays light
- [ ] Docker + webhook_url set → webhook fires (no "Webhook error" in log)
- [ ] Non-YouTube playlist → entries get correct site-specific URLs
- [ ] After download completes, `file_path` in DB is absolute
- [ ] Right-click `<video>` on youtube.com → download works
- [ ] Restart Chrome → no toast storm
- [ ] Set `YTDL_API_KEY` → set key in popup → all operations work
- [ ] Cancel job → webhook does not fire
- [ ] Output pattern `%(channel)s/%(title)s.%(ext)s` → file_path points to subdir
- [ ] Bulk-retry including completed jobs → completed NOT re-queued
- [ ] Queue 100 non-YouTube URLs rapidly → no IntegrityError
- [ ] "Time ago" labels correct (no 5h offset on IST server)
- [ ] `bash yt-dl-handler.sh <url>` with daemon down → starts daemon, queues
- [ ] PUT `/api/settings` with `concurrent_limit: 9999` → 400
- [ ] PUT `/api/settings` with `evil_key` → ignored
- [ ] GET `/api/logs?count=abc` → 400
- [ ] POST `/api/open` with `{"path":"/etc"}` → 403
- [ ] `docker exec yt-dl id` → uid=1000(ytdl)
- [ ] POST `/api/settings/cookies` → no `path` in response
- [ ] PUT `/api/settings` `max_log_lines:100` → ring buffer resizes
- [ ] `/api/info` returns version "1.1.0" matching `manifest.json`
- [ ] Stats with 2 days data → 5 zero bars with date labels, 2 real bars
- [ ] Stats status breakdown → separate Cancelled and Active/Queued buckets
- [ ] `manifest.json` has no `<all_urls>`
- [ ] Chrome alarm `poll` is 0.5 minutes
- [ ] `install.sh` works with python3 in `/usr/local/bin`
- [ ] `install.sh` doesn't warn about missing `python-dbus`
- [ ] `install.fish` doesn't reference `notifications.py`
- [ ] README cookie instructions produce non-empty `cookies.txt`
- [ ] `human_bytes(-5)` returns `"-5.0 B"`, not crash
- [ ] Download where yt-dlp reports `filepath:"NA"` → doesn't set file_path="NA"
- [ ] Open 5 dashboard tabs → DB query rate stays 1/sec
- [ ] **NEW:** If user has `OPENSSL_CONF` set in env, it's respected (not overwritten)
- [ ] **NEW:** If user has no `OPENSSL_CONF`, yt-dlp subprocess still gets `/dev/null` workaround

---

## Deliverable

After applying all fixes:
1. Run the full test checklist above (68 items).
2. Provide a `CHANGELOG.md` entry summarizing all 68 fixes with `file:line` references.
3. Commit each bug fix as a separate git commit: `fix(#N): short description` where N is the bug number.
4. Open a single PR with all commits, linking back to this prompt.

---

## Reference: confirmed test results from this audit (commit 9714fc7)

```
[Layout test — 15 seeded jobs: 4 downloading, 6 queued, 3 completed, 2 failed]
w=1440px: grid-2 cols=2, grid-3 cols=3 ✅
w=1280px: grid-2 cols=2, grid-3 cols=3 ✅
w=1024px: grid-2 cols=2, grid-3 cols=3 ✅
w=900px:  grid-2 cols=1, grid-3 cols=1 ❌ BUG
w=800px:  grid-2 cols=1, grid-3 cols=1 ❌ BUG
w=640px:  grid-2 cols=1, grid-3 cols=1 (expected phone)

[Pause all button at commit 9714fc7]
- Button HTML: <span id="pause-all">Pause all</span>
- Handler:    dl.forEach(j => cancelJob(j.id))  ← calls CANCEL, not pause
- Worker:     pause_job/resume_job exist (lines 330-349) but NO Flask route
- Worker:     pause_job has latent ProcessLookupError (no try/except)

[New commit 9714fc7 changes]
- Only src/worker.py changed (5 lines)
- Added: env = {**os.environ, "OPENSSL_CONF": "/dev/null"}
- Applied to: info_cmd subprocess (line 158) + download Popen (line 215)
- Impact: overrides user's legitimate OPENSSL_CONF globally → Bug 68
```
