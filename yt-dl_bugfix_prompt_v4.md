# DeepSeek Prompt — Bug-Fix Guide for `CodeAbhi826/yt-dl` AFTER commit 1bc0fb6

> **Audited commit:** `1bc0fb6` ("fix: complete bugfix prompt — all 68 bugs across 7 phases")
> **Previous fixes applied:** Bugs 1–68 from the earlier audit are now in the repo
> **NEW bugs found in this audit:** 4 (2 critical, 2 medium)
> **Total bugs to fix:** 4
> **Repo:** https://github.com/CodeAbhi826/yt-dl

---

## Context

The repo author applied the previous 68-bug fix prompt, but the audit revealed that:
1. The **"Pause all" button still doesn't work** — it deadlocks the Flask thread.
2. The **failed/queued cards still overflow horizontally** — the "3 cards going out of the layout" bug the user reported is REAL and confirmed via pixel measurement.
3. Several button-label/confirmation issues remain.

All findings below were verified by running the daemon at commit `1bc0fb6`, seeding real test data (6 failed + 2 downloading + 3 queued jobs), rendering the dashboard in headless Chromium at 1366px viewport, clicking every button, and measuring `scrollWidth` vs `clientWidth` plus per-card `getBoundingClientRect().width`.

---

# 🔴 BUG 69 (CRITICAL) — `api_pause_all` / `api_resume_all` deadlock

## Confirmed bug

**Files:** `src/app.py:229-249` + `src/worker.py:352-385` + `src/worker.py:50` (queue_lock type)

### The buggy code

**`src/app.py:229-249`:**
```python
@app.route("/api/jobs/pause-all", methods=["POST"])
@require_auth
def api_pause_all():
    paused = 0
    with queue_lock:                                    # ← acquires queue_lock
        for job in list(active_jobs.values()):
            if job.status == "downloading" and pause_job(job.job_id):   # ← calls pause_job
                paused += 1
    logger.info(f"Paused {paused} jobs")
    return jsonify({"paused": paused})

@app.route("/api/jobs/resume-all", methods=["POST"])
@require_auth
def api_resume_all():
    resumed = 0
    with queue_lock:                                    # ← acquires queue_lock
        for job in list(active_jobs.values()):
            if resume_job(job.job_id):                  # ← calls resume_job
                resumed += 1
    logger.info(f"Resumed {resumed} jobs")
    return jsonify({"resumed": resumed})
```

**`src/worker.py:50`:**
```python
queue_lock = threading.Lock()    # ← NON-reentrant lock
```

**`src/worker.py:352-366` (`pause_job`):**
```python
def pause_job(job_id: str) -> bool:
    with queue_lock:                # ← tries to acquire queue_lock AGAIN → DEADLOCK
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                    job.status = "paused"
                    save_job(job)
                    ...
```

### Why it deadlocks

1. `api_pause_all` acquires `queue_lock` (a non-reentrant `threading.Lock`)
2. Inside the lock, it calls `pause_job(job.job_id)`
3. `pause_job` tries to acquire `queue_lock` again
4. Since `threading.Lock()` is NOT reentrant (unlike `threading.RLock()`), the second acquire **blocks forever**
5. The Flask request thread hangs → the browser's `fetch('/api/jobs/pause-all')` never gets a response → the button appears dead

### Verified symptoms

```
POST /api/jobs/pause-all  →  hangs forever (no response)
Button text stays "Pause all" (never changes to "Resume all")
No jobs enter "paused" state
```

### The fix — 2 options

**Option A (recommended — inline the pause logic, don't call pause_job):**

Replace `api_pause_all` and `api_resume_all` in `src/app.py`:

```python
@app.route("/api/jobs/pause-all", methods=["POST"])
@require_auth
def api_pause_all():
    """Pause every active (downloading) job. Inline the logic to avoid
    re-acquiring queue_lock (which would deadlock since pause_job also
    tries to acquire it)."""
    paused = 0
    with queue_lock:
        for job in list(active_jobs.values()):
            if job.status != "downloading":
                continue
            if not job.proc or job.proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                job.status = "paused"
                save_job(job)
                paused += 1
                logger.info(f"Paused job: {job.job_id}")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Failed to pause {job.job_id}: {e}")
    logger.info(f"Paused {paused} jobs total")
    return jsonify({"paused": paused})

@app.route("/api/jobs/resume-all", methods=["POST"])
@require_auth
def api_resume_all():
    """Resume every paused job. Inline the logic to avoid deadlock."""
    resumed = 0
    with queue_lock:
        for job in list(active_jobs.values()):
            if job.status != "paused":
                continue
            if not job.proc or job.proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(job.proc.pid), signal.SIGCONT)
                job.status = "downloading"
                save_job(job)
                resumed += 1
                logger.info(f"Resumed job: {job.job_id}")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Failed to resume {job.job_id}: {e}")
    logger.info(f"Resumed {resumed} jobs total")
    return jsonify({"resumed": resumed})
```

You also need to add `import os` and `import signal` at the top of `app.py` if not already present (check — `signal` is likely already imported for the shutdown handler).

**Option B (use RLock instead of Lock):**

In `src/worker.py:50`, change:
```python
queue_lock = threading.Lock()
```
to:
```python
queue_lock = threading.RLock()
```

`RLock` (reentrant lock) allows the same thread to acquire the lock multiple times. This is a one-line fix but changes the lock semantics globally — make sure no other code relies on `queue_lock` being non-reentrant. **Option A is safer** because it doesn't change global behavior.

### Verification

- [ ] Click "Pause all" with 2 downloading jobs → request completes in <1s
- [ ] Both jobs enter "paused" state (check via `GET /api/queue`)
- [ ] PAUSED section appears in dashboard with Resume buttons
- [ ] Footer button label changes to "Resume all"
- [ ] Click "Resume all" → all paused jobs resume downloading
- [ ] No hung Flask threads in daemon log
- [ ] `curl -X POST http://localhost:5000/api/jobs/pause-all` returns immediately with `{"paused": N}`

---

# 🔴 BUG 70 (CRITICAL) — Failed/queued cards overflow horizontally

## Confirmed bug

**Files:** `src/static/style.css` (missing `min-width: 0` on flex children) + `src/static/dashboard.js` (long error_message in `.q-meta`)

### The bug

The `.q-card` uses `display: flex`, and `.q-body` is a flex child with `flex: 1; min-width: 0`. But `.q-body`'s OWN children (the wrapper `<div>` containing title+status, the `.q-meta`, the `.q-bottom`) have the default `min-width: auto`.

When `.q-meta` contains long text like `"720p • mp4 • ERROR: Video unavailable • just now"` and has `white-space: nowrap`, the flex algorithm sees the children's `min-width: auto` and refuses to shrink them below their content's intrinsic width. This forces `.q-body` to grow, which forces `.q-card` to grow beyond its grid track.

### Verified measurements

```
Viewport: 1366px
Expected card width (3-col grid): 429px
Actual failed card width: 523px  ← 94px too wide!
Page scrollWidth: 1625px
Page clientWidth: 1366px
Horizontal overflow: 259px  ← causes horizontal scrollbar + "cards going out of layout"
```

The "before" Playwright screenshot is literally 518px wider than the "after" screenshot (at 2x device pixel ratio), confirming the overflow is visible.

### The fix — `src/static/style.css`

Find the Grids section (around line 37-50) and add the `min-width: 0` rules. The fix must apply to ALL card types (downloading, queued, completed, failed, paused):

```css
/* ── Grids ── */
.grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }

@media (max-width: 900px) and (min-width: 641px) {
  .grid-3 { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 640px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}

/* BUG 70 FIX — Prevent flex children from forcing cards wider than their grid track.
   Without this, long .q-meta text (e.g. error messages) with white-space:nowrap
   makes .q-body grow, which makes .q-card overflow its grid column. */
.grid-2 > *,
.grid-3 > * { min-width: 0; }

.dl-card, .q-card { min-width: 0; }
.dl-body, .q-body { min-width: 0; }
.dl-body > *, .q-body > * { min-width: 0; }
```

### Why this works

`min-width: 0` overrides the default `min-width: auto` on flex items, allowing them to shrink below their content's intrinsic width. The `overflow: hidden; text-overflow: ellipsis` on `.q-meta` and `.q-title` then properly truncates the text instead of forcing the card to grow.

### Verification

- [ ] Dashboard at 1366px with 6 failed jobs → all failed cards are 429px wide (not 523px)
- [ ] `document.documentElement.scrollWidth` equals `document.body.clientWidth` (no horizontal scroll)
- [ ] Long error messages in `.q-meta` show "…" truncation instead of overflowing
- [ ] No horizontal scrollbar appears at the bottom of the page
- [ ] Test with error_message = "ERROR: This is a very long error message that would normally cause the card to overflow horribly and break the entire layout"
- [ ] Test the same on queued cards with long titles
- [ ] Test the same on completed cards with long file paths

---

# 🟡 BUG 71 (MEDIUM) — "Clear completed" deletes files without confirmation

## Confirmed bug

**File:** `src/static/dashboard.js:240-246`

### The buggy code

```js
const clearBtn = document.getElementById("clear-completed");
if (clearBtn) {
  clearBtn.addEventListener("click", function() {
    const completed = prevJobs.filter(j => j.status === 'completed');
    if (!completed.length) return;
    fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
      .then(r => r.json()).then(d => { showToast("Cleared " + d.deleted + " completed"); });
  });
}
```

`/api/bulk/delete` runs `os.remove(row["file_path"])` for each job — it deletes the actual downloaded video files from disk. The button label "Clear completed" sounds like it just hides the records, but it actually deletes the user's downloaded files. No confirmation dialog.

### The fix — `src/static/dashboard.js:240-246`

```js
const clearBtn = document.getElementById("clear-completed");
if (clearBtn) {
  clearBtn.addEventListener("click", function() {
    const completed = prevJobs.filter(j => j.status === 'completed');
    if (!completed.length) return;
    if (!confirm(`Delete ${completed.length} completed download(s) AND their files from disk?`)) return;
    fetch("/api/bulk/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ids: completed.map(j => j.id)})})
      .then(r => r.json()).then(d => { showToast("Deleted " + d.deleted + " files"); });
  });
}
```

Also rename the button label in `src/templates/dashboard.html`:
```html
<!-- Change from: -->
<span class="footer-action" id="clear-completed">Clear completed</span>
<!-- To: -->
<span class="footer-action footer-action-danger" id="clear-completed">Delete completed</span>
```

### Verification

- [ ] Click "Delete completed" → confirmation dialog appears mentioning file deletion
- [ ] Cancel the dialog → nothing happens
- [ ] Confirm → files are deleted, toast says "Deleted N files"

---

# 🟡 BUG 72 (MEDIUM) — Failed card entire-body click navigates to /logs unexpectedly

## Confirmed bug

**File:** `src/static/dashboard.js:110` (the `buildFailedCard` function)

### The buggy code

```js
return '<div class="q-card failed" data-id="' + escapeHtml(j.id) + '" style="cursor:pointer" onclick="window.location.href=\'/logs\'">'
```

The entire failed card is clickable and navigates to `/logs`. The user might click the card to select it or read the title, and suddenly they're on the logs page. The "Retry" button has `event.stopPropagation()` so it's fine, but clicking anywhere else on the card (title, status, meta text) jumps to /logs.

### The fix — `src/static/dashboard.js:110, 115`

Remove the card-level onclick and the cursor:pointer. Replace the "View logs →" link to be a real clickable element:

```js
function buildFailedCard(j) {
  const thumb = j.video_id
    ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="q-thumb">'
    : '<div class="q-thumb-placeholder">YT</div>';
  const errMsg = j.error_message || 'Unknown error';
  const meta = [j.quality, 'mp4', errMsg, timeAgo(j.created_at)].filter(Boolean).join(' • ');
  return '<div class="q-card failed" data-id="' + escapeHtml(j.id) + '">'
    + '<div class="q-thumb-wrap">' + thumb + '</div>'
    + '<div class="q-body">'
    + '<div><div class="q-title" title="' + escapeHtml(j.title || '') + '">' + escapeHtml(j.title || j.video_id || 'Unknown') + '</div>'
    + '<div style="margin-top:2px;"><span class="q-status">✕ FAILED</span></div></div>'
    + '<div class="q-meta">' + escapeHtml(meta) + '</div>'
    + '<div class="q-bottom" style="justify-content:space-between;">'
    + '<span class="q-retry" onclick="retryJob(\'' + escapeHtml(j.id) + '\')">Retry</span>'
    + '<span class="q-cancel" style="cursor:pointer;" onclick="window.location.href=\'/logs\'">View logs →</span>'
    + '</div></div></div>';
}
```

Key changes:
- Removed `style="cursor:pointer"` and `onclick="window.location.href='/logs'"` from the card `<div>`
- The "View logs →" span already has the onclick — keep it, but add `cursor:pointer` to make it look clickable
- Removed the `event.stopPropagation()` from the Retry button since the card no longer navigates

### Verification

- [ ] Click the failed card's title → nothing happens (stays on dashboard)
- [ ] Click the failed card's status text → nothing happens
- [ ] Click "Retry" → retries the job
- [ ] Click "View logs →" → navigates to /logs
- [ ] Cursor only shows pointer over Retry and View logs, not the whole card

---

# 🟢 BONUS FIXES (minor, found during audit)

## Bug 73 — Thumbnails 404 for non-11-char video IDs

**File:** `src/static/dashboard.js` (all card builders)

The thumbnail URL `https://i.ytimg.com/vi/{video_id}/mqdefault.jpg` only works for real YouTube 11-char video IDs. For non-YouTube downloads (TikTok, Twitter) or test data, these 404 and show broken image icons. Console fills with 404 errors.

### Fix
Add an `onerror` handler to the thumbnail `<img>` that swaps to the placeholder:
```js
const thumb = (j.video_id && j.url && /youtube\.com|youtu\.be/.test(j.url))
  ? '<img src="https://i.ytimg.com/vi/' + escapeHtml(j.video_id) + '/mqdefault.jpg" class="q-thumb" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="q-thumb-placeholder" style="display:none;">YT</div>'
  : '<div class="q-thumb-placeholder">YT</div>';
```
Apply to all 4 card builders (`buildDownloadingCard`, `buildQueueCard`, `buildRecentCard`, `buildFailedCard`, `buildPausedCard`).

## Bug 74 — `deleteJob` confirmation doesn't mention file deletion

**File:** `src/static/dashboard.js:232-235`

```js
function deleteJob(id) {
  if (!confirm("Delete?")) return;   // ← too vague
  ...
}
```

### Fix
```js
function deleteJob(id) {
  const job = prevJobs.find(j => j.id === id);
  const hasFile = job && job.file_path && job.status === 'completed';
  const msg = hasFile
    ? "Delete this download AND remove the file from disk?"
    : "Delete this download record?";
  if (!confirm(msg)) return;
  fetch("/api/jobs/" + id, {method:"DELETE"}).then(r => r.json()).then(d => { showToast("Deleted"); });
}
```

---

# Implementation order

1. **Bug 69** (deadlock fix) — `src/app.py` — inline pause/resume logic in `api_pause_all`/`api_resume_all`
2. **Bug 70** (overflow fix) — `src/static/style.css` — add `min-width: 0` rules
3. **Bug 71** (Clear completed confirmation) — `src/static/dashboard.js` + `src/templates/dashboard.html`
4. **Bug 72** (failed card click) — `src/static/dashboard.js`
5. **Bug 73** (thumbnail 404) — `src/static/dashboard.js`
6. **Bug 74** (deleteJob confirmation) — `src/static/dashboard.js`

# Full test checklist

- [ ] `curl -X POST http://localhost:5000/api/jobs/pause-all` returns immediately with `{"paused": N}` (was: hangs forever)
- [ ] Click "Pause all" button → jobs enter "paused" state within 1s
- [ ] PAUSED section appears in dashboard with Resume buttons
- [ ] Click "Resume all" → all paused jobs resume
- [ ] Footer button label toggles between "Pause all" (red) and "Resume all" (white)
- [ ] Dashboard at 1366px with 6 failed jobs → no horizontal scrollbar
- [ ] `document.documentElement.scrollWidth === document.body.clientWidth` (no overflow)
- [ ] Failed cards are 429px wide at 1366px viewport (not 523px)
- [ ] Long error messages truncate with "…" instead of overflowing
- [ ] Click "Delete completed" → confirmation dialog mentions file deletion
- [ ] Click failed card title → stays on dashboard (no navigation)
- [ ] Click "View logs →" on failed card → navigates to /logs
- [ ] Non-YouTube downloads show "YT" placeholder instead of broken image
- [ ] `deleteJob` on completed job → confirmation mentions file deletion

# Deliverable

After applying all fixes:
1. Run the full test checklist above (14 items).
2. Commit each bug fix as a separate git commit: `fix(#69): pause-all deadlock`, etc.
3. Open a single PR linking back to this prompt.

---

## Reference: confirmed test results from this audit (commit 1bc0fb6)

```
[Button click tests — 6 failed + 2 downloading + 3 queued seeded]
POST /api/jobs/pause-all  →  HUNG (deadlock confirmed)
Button "Pause all" text: stayed "Pause all" (never changed)
Job statuses after click: {queued: 1, downloading: 5, failed: 5} — no 'paused' state

[Layout measurement at 1366px viewport]
DOWNLOADING (2): grid-2, 2 cols, card width=651px ✓
QUEUED (3): grid-3, 3 cols, card width=429px ✓
FAILED (6): grid-3, 3 cols, card width=523px ✗ (should be 429px — 94px too wide!)
Page scrollWidth: 1625px
Page clientWidth: 1366px
Horizontal overflow: 259px

[After injecting CSS fix: .q-card, .q-body, .q-body > * { min-width: 0; }]
FAILED card width: 429px ✓
Page scrollWidth: 1366px = clientWidth ✓
Overflow fixed: True

[Screenshot dimensions]
BEFORE fix screenshot: 3250px wide (captures the overflow)
AFTER fix screenshot:  2732px wide
Difference: 518px (at 2x device scale = 259px actual overflow)

[Deadlock code path]
app.py:233  with queue_lock:              ← acquires lock (1st time)
app.py:235  pause_job(job.job_id)         ← calls function
worker.py:353  with queue_lock:           ← tries to acquire AGAIN → HANG
              (queue_lock is threading.Lock, NOT threading.RLock)
```
