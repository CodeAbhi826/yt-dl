# DeepSeek Prompt — Add Master Toggle + Bulk URL Features to yt-dl

> **Base commit:** `44302b7` (latest, all 68+6 bug fixes applied)
> **Features to add:** 2
> **Repo:** https://github.com/CodeAbhi826/yt-dl

---

## Context

`yt-dl` is a self-hosted YouTube download daemon. The user wants two new quality-of-life features:

1. **Master Download Toggle** — a switch in the dashboard footer that, when OFF, prevents new downloads from starting (jobs stay in `queued` status). When ON, the worker picks up queued jobs normally. Existing active downloads are NOT affected — only new starts.

2. **Bulk Add URLs** — a button in the dashboard footer that opens a modal where the user can paste multiple URLs (one per line). All valid URLs get queued in one shot, with dedup against history.

Both features build on the existing codebase patterns. No new dependencies needed.

### Repo layout (relevant files only)
```
src/
├── app.py              # Flask server — add 2 new endpoints
├── worker.py           # Download queue — add toggle check
├── models.py           # Config — add 1 new default key
├── templates/
│   └── dashboard.html  # Add toggle + bulk-add button
└── static/
    ├── dashboard.js    # Add toggle + bulk-add modal logic
    └── style.css       # Add toggle + modal styles
```

### Goal
Apply both features below. Preserve all existing behavior. After implementing, briefly summarize each change with `file:line` references.

---

# FEATURE 1 — Master Download Toggle

## Behavior

- A toggle switch appears in the dashboard footer, between the footer-left text and the existing "Delete completed" / "Pause all" buttons.
- **Toggle ON (default):** Worker picks up `queued` jobs normally. This is the current behavior.
- **Toggle OFF:** Worker refuses to start new downloads. Jobs added via `/api/add` still go into `queued` status, but `_process_queue()` returns early without starting them. Active downloads keep running.
- The toggle state persists across daemon restarts (stored in `config.json`).
- The toggle state is exposed via `/api/info` so the extension and other clients can read it.
- A new `PUT /api/toggle` endpoint flips the state.
- The footer button label dynamically reflects the state: "Downloads: ON" (green) or "Downloads: OFF" (gray).
- When OFF, queued cards show a small "⏸ paused by master toggle" hint in their meta line.
- The toggle is INDEPENDENT of the "Pause all" button. Pause all stops active downloads. The toggle prevents new starts. Both can be used together.

## Implementation

### 1a. Add config default — `src/models.py`

In the `DEFAULT_CONFIG` dict (around line 21-34), add one new key:

```python
DEFAULT_CONFIG = {
    "download_dir": "/mnt/storage/YouTube",
    "default_quality": "720p",
    "concurrent_limit": 3,
    "theme": "dark",
    "output_pattern": "%(title)s.%(ext)s",
    "embed_metadata": True,
    "embed_thumbnail": True,
    "embed_chapters": True,
    "embed_subs": True,
    "playlist_limit": 200,
    "max_log_lines": 500,
    "webhook_url": "",
    "downloads_enabled": True,    # ← NEW: master toggle, default ON
}
```

### 1b. Respect the toggle in the worker — `src/worker.py`

In `_process_queue()` (around line 125-147), add a check at the very top, BEFORE the `active_count` check:

```python
def _process_queue():
    cfg = load_config()
    # Master toggle — if OFF, don't start any new downloads.
    # Active downloads keep running; queued jobs stay queued.
    if not cfg.get("downloads_enabled", True):
        return
    concurrent_limit = cfg.get("concurrent_limit", 3)
    download_dir = Path(cfg.get("download_dir", "/mnt/storage/YouTube"))

    with queue_lock:
        # ... rest of existing code unchanged
```

That's the entire backend logic for the toggle. The worker checks `downloads_enabled` on every queue-processing cycle (which happens when jobs are added, when downloads finish, and every 1s via the SSE broadcaster). When the user flips the toggle ON, the next cycle picks up queued jobs automatically.

### 1c. Add toggle API endpoint — `src/app.py`

Add a new endpoint anywhere in the routes section (e.g., after `/api/settings` around line 400). Also expose the toggle state in `/api/info`:

```python
@app.route("/api/toggle", methods=["PUT"])
@require_auth
def api_toggle_downloads():
    data = request.get_json() or {}
    if "enabled" not in data:
        return jsonify({"error": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    cfg = load_config()
    cfg["downloads_enabled"] = enabled
    save_config(cfg)
    logger.info(f"Master download toggle: {'ON' if enabled else 'OFF'}")
    # If toggled ON, wake up the worker so it picks up queued jobs immediately.
    if enabled:
        process_queue()
    return jsonify({"downloads_enabled": enabled})
```

Update the existing `/api/info` endpoint (around line 165-170) to include the toggle state:

```python
@app.route("/api/info")
def api_info():
    cfg = load_config()
    return jsonify({
        "dbus_available": False,
        "version": __version__,
        "auth_required": bool(API_KEY),
        "downloads_enabled": cfg.get("downloads_enabled", True),   # ← NEW
    })
```

Also update the settings validation whitelist (the `ALLOWED_SETTINGS` dict, around line 380-395) to include the new key:

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
    "downloads_enabled": bool,   # ← NEW
}
```

### 1d. Add toggle UI to dashboard footer — `src/templates/dashboard.html`

Replace the existing footer (lines 8-14) with:

```html
<footer class="page-footer" id="page-footer">
  <span id="footer-left"></span>
  <div class="footer-actions">
    <span class="footer-action" id="bulk-add">+ Bulk add</span>
    <span class="footer-action footer-action-danger" id="clear-completed">Delete completed</span>
    <span class="footer-action footer-action-danger" id="pause-all">Pause all</span>
    <span class="toggle-row" id="master-toggle-row" title="When OFF, new downloads won't start. Active downloads keep running.">
      <span class="toggle" id="master-toggle"><span class="knob"></span></span>
      <span class="toggle-label" id="master-toggle-label">Downloads: ON</span>
    </span>
  </div>
</footer>
```

Note: the `+ Bulk add` button is for Feature 2 (below).

### 1e. Add toggle CSS — `src/static/style.css`

Append these rules at the end of the file:

```css
/* ── Master Toggle ── */
.toggle-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}
.toggle-row:hover { background: rgba(255,255,255,.04); }
.toggle {
  position: relative;
  width: 36px;
  height: 20px;
  background: #333;
  border-radius: 999px;
  transition: background 0.2s ease;
  flex-shrink: 0;
}
.toggle.on { background: var(--green); }
.toggle .knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  background: white;
  border-radius: 50%;
  transition: transform 0.2s ease;
}
.toggle.on .knob { transform: translateX(16px); }
.toggle-label { font-size: 12px; font-weight: 500; color: var(--text-muted); }
.toggle-label.on { color: var(--green); }
.toggle-label.off { color: var(--text-muted); }
```

### 1f. Add toggle JS logic — `src/static/dashboard.js`

Add these functions and wire them up. Place near the other footer-button handlers (around line 240-270, after the `pauseBtn` handler):

```js
// ─── Master Toggle ───
let downloadsEnabled = true;

async function loadToggleState() {
  try {
    const r = await fetch('/api/info');
    const d = await r.json();
    downloadsEnabled = d.downloads_enabled !== false;
    updateToggleUI();
  } catch (e) {
    // If /api/info fails, assume ON
    downloadsEnabled = true;
    updateToggleUI();
  }
}

function updateToggleUI() {
  const toggle = document.getElementById('master-toggle');
  const label = document.getElementById('master-toggle-label');
  if (!toggle || !label) return;
  if (downloadsEnabled) {
    toggle.classList.add('on');
    label.classList.add('on');
    label.classList.remove('off');
    label.textContent = 'Downloads: ON';
  } else {
    toggle.classList.remove('on');
    label.classList.add('off');
    label.classList.remove('on');
    label.textContent = 'Downloads: OFF';
  }
}

async function toggleDownloads() {
  const newState = !downloadsEnabled;
  try {
    const r = await fetch('/api/toggle', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: newState })
    });
    if (!r.ok) throw new Error('Toggle failed');
    downloadsEnabled = newState;
    updateToggleUI();
    showToast(newState ? 'Downloads enabled' : 'Downloads paused — new jobs will wait in queue');
  } catch (e) {
    showToast('Toggle failed: ' + e.message, 'error');
  }
}

// Wire up the toggle click
document.addEventListener('DOMContentLoaded', function() {
  loadToggleState();
  const toggleRow = document.getElementById('master-toggle-row');
  if (toggleRow) {
    toggleRow.addEventListener('click', toggleDownloads);
  }
});
```

Also, in the `buildQueueCard` function (around line 68-85), add a hint when the toggle is OFF. Find the line that builds the meta string:

```js
const meta = [j.quality, 'mp4', sizeInfo].filter(Boolean).join(' • ');
```

Change it to:

```js
const meta = [j.quality, 'mp4', sizeInfo, downloadsEnabled ? '' : '⏸ waiting for toggle'].filter(Boolean).join(' • ');
```

This makes queued cards show "⏸ waiting for toggle" in their meta line when the toggle is OFF, so the user understands why nothing is downloading.

### 1g. Poll for toggle state changes (optional but nice)

If multiple clients are open (e.g., dashboard + extension), one client flipping the toggle should update the other. The simplest way is to re-check `/api/info` periodically. Add to the existing `connectSSE` function or create a lightweight poller:

```js
// Re-check toggle state every 10s (in case another client changed it)
setInterval(loadToggleState, 10000);
```

Place this inside the `DOMContentLoaded` handler, after `loadToggleState()`.

## Verification — Feature 1

- [ ] Dashboard footer shows a green toggle labeled "Downloads: ON" by default
- [ ] Click the toggle → it turns gray, label changes to "Downloads: OFF"
- [ ] With toggle OFF: queue a new job via extension/curl → it appears in QUEUED section with "⏸ waiting for toggle" hint, does NOT start downloading
- [ ] With toggle OFF: active downloads continue running (not paused)
- [ ] Click toggle again → it turns green, label changes to "Downloads: ON", queued jobs immediately start
- [ ] Restart the daemon → toggle state persists (load from config.json)
- [ ] `GET /api/info` returns `"downloads_enabled": true/false`
- [ ] `PUT /api/toggle {"enabled": false}` → returns `{"downloads_enabled": false}`
- [ ] Toggle works independently from "Pause all" button

---

# FEATURE 2 — Bulk Add URLs

## Behavior

- A "+ Bulk add" button appears in the dashboard footer, to the LEFT of "Delete completed".
- Clicking it opens a modal overlay with a `<textarea>` where the user pastes multiple URLs (one per line).
- The modal shows a live count: "5 URLs detected · 2 duplicates skipped · quality: 720p (default)".
- A quality dropdown lets the user pick the quality for ALL URLs in this batch.
- Clicking "Queue all N" sends the URLs to a new `/api/bulk/add` endpoint.
- The endpoint validates each URL, deduplicates against completed jobs (by URL hash), and inserts valid ones as `queued`.
- The modal closes and shows a toast: "Queued 5 new downloads (skipped 2 duplicates)".
- Invalid URLs (not starting with `http://` or `https://`) are skipped with a count in the response.
- Playlists are NOT expanded in bulk mode (to keep it fast) — they're inserted as a single job and the existing playlist-detection logic handles them on the next queue cycle. Document this in the modal.

## Implementation

### 2a. Add bulk add API endpoint — `src/app.py`

Add this new endpoint after the existing `/api/add` route (around line 640):

```python
@app.route("/api/bulk/add", methods=["POST"])
@require_auth
def api_bulk_add():
    """Add multiple URLs at once. Each URL is validated and deduplicated
    against completed jobs. Playlists are NOT expanded here — they're
    inserted as a single job and handled by the normal queue process.
    Returns counts of added/skipped/invalid URLs."""
    data = request.get_json() or {}
    urls = data.get("urls", [])
    quality = data.get("quality")
    cfg = load_config()
    if not quality:
        quality = cfg.get("default_quality", "720p")

    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "No URLs provided"}), 400

    # Cap at 100 URLs per request to prevent abuse
    if len(urls) > 100:
        return jsonify({"error": "Maximum 100 URLs per request"}), 400

    added = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    results = []

    db = get_db()
    try:
        # Get all completed URLs for dedup
        existing_urls = {r["url"] for r in db.execute(
            "SELECT DISTINCT url FROM downloads WHERE status='completed'"
        ).fetchall()}

        for url in urls:
            url = (url or "").strip()
            if not url:
                continue
            if not re.match(r"https?://", url):
                skipped_invalid += 1
                results.append({"url": url, "status": "invalid"})
                continue
            if url in existing_urls:
                skipped_duplicate += 1
                results.append({"url": url, "status": "duplicate"})
                continue

            # Extract video_id (YouTube-specific, but harmless for other sites)
            video_id = ""
            m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
            if m:
                video_id = m.group(1)

            url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
            job_id = f"job_{int(time.time() * 1000)}_{url_hash}_{added}"
            db.execute(
                "INSERT INTO downloads (job_id, video_id, url, quality, status, title) VALUES (?, ?, ?, ?, 'queued', ?)",
                (job_id, video_id, url, quality, "")
            )
            existing_urls.add(url)  # prevent dup within same batch
            added += 1
            results.append({"url": url, "status": "added", "job_id": job_id})

        db.commit()
    finally:
        db.close()

    logger.info(f"Bulk add: {added} added, {skipped_duplicate} duplicates, {skipped_invalid} invalid")
    if added > 0:
        process_queue()
    return jsonify({
        "added": added,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "total": len(urls),
        "results": results
    })
```

### 2b. Add bulk-add modal HTML — `src/templates/dashboard.html`

Add this modal div right before the closing `{% endblock %}` of the content block (after the footer, before `{% block scripts %}`):

```html
<!-- Bulk Add Modal -->
<div id="bulk-modal" class="modal-backdrop" style="display:none;">
  <div class="modal">
    <h3>Bulk add downloads</h3>
    <p style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">
      One URL per line. Duplicates of completed downloads will be skipped.
      Playlists are added as a single job and expanded by the queue worker.
    </p>
    <textarea id="bulk-urls" placeholder="https://www.youtube.com/watch?v=...&#10;https://youtu.be/...&#10;https://www.tiktok.com/@user/video/..." rows="8"></textarea>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px; gap:12px;">
      <div>
        <span id="bulk-count" style="font-size:11px; color:var(--text-muted);">0 URLs detected</span>
      </div>
      <div style="display:flex; gap:8px; align-items:center;">
        <label style="font-size:11px; color:var(--text-muted);">Quality:</label>
        <select id="bulk-quality" class="form-input" style="width:auto; padding:4px 8px; font-size:12px;">
          <option value="144p">144p</option>
          <option value="240p">240p</option>
          <option value="360p">360p</option>
          <option value="480p">480p</option>
          <option value="720p" selected>720p</option>
          <option value="1080p">1080p</option>
          <option value="1440p">1440p</option>
          <option value="2160p">2160p</option>
          <option value="best">Best</option>
          <option value="audio">Audio (MP3)</option>
        </select>
        <button class="btn btn-secondary" onclick="closeBulkModal()">Cancel</button>
        <button class="btn btn-primary" id="bulk-submit" onclick="submitBulkAdd()">Queue all</button>
      </div>
    </div>
  </div>
</div>
```

### 2c. Add bulk-add modal CSS — `src/static/style.css`

Append these rules:

```css
/* ── Bulk Add Modal ── */
.modal-backdrop {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.7);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: var(--radius-lg);
  padding: 24px;
  width: 90%;
  max-width: 560px;
  box-shadow: 0 24px 64px rgba(0,0,0,0.5);
}
.modal h3 { font-size: 18px; margin-bottom: 12px; }
.modal textarea {
  width: 100%;
  background: #1c1c1c;
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 12px;
  font-size: 13px;
  color: var(--text);
  font-family: 'SF Mono', Monaco, monospace;
  resize: vertical;
  line-height: 1.5;
}
.modal textarea:focus { outline: none; border-color: var(--accent); }
```

### 2d. Add bulk-add JS logic — `src/static/dashboard.js`

Add these functions:

```js
// ─── Bulk Add Modal ───
function openBulkModal() {
  document.getElementById('bulk-modal').style.display = 'flex';
  document.getElementById('bulk-urls').focus();
  updateBulkCount();
}

function closeBulkModal() {
  document.getElementById('bulk-modal').style.display = 'none';
}

function updateBulkCount() {
  const text = document.getElementById('bulk-urls').value;
  const urls = text.split('\n').map(s => s.trim()).filter(s => s.length > 0);
  const count = urls.length;
  const valid = urls.filter(u => /^https?:\/\//.test(u)).length;
  const invalid = count - valid;
  const el = document.getElementById('bulk-count');
  if (count === 0) {
    el.textContent = '0 URLs detected';
    el.style.color = 'var(--text-muted)';
  } else {
    el.textContent = `${count} URL${count !== 1 ? 's' : ''} detected` +
      (invalid > 0 ? ` · ${invalid} invalid` : '') +
      ` · duplicates auto-skipped on submit`;
    el.style.color = invalid > 0 ? 'var(--orange)' : 'var(--text-secondary)';
  }
}

async function submitBulkAdd() {
  const text = document.getElementById('bulk-urls').value;
  const urls = text.split('\n').map(s => s.trim()).filter(s => s.length > 0);
  const quality = document.getElementById('bulk-quality').value;
  
  if (urls.length === 0) {
    showToast('No URLs to add', 'error');
    return;
  }

  const btn = document.getElementById('bulk-submit');
  btn.disabled = true;
  btn.textContent = 'Queuing...';

  try {
    const r = await fetch('/api/bulk/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls, quality })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Bulk add failed');
    
    let msg = `Queued ${d.added} download${d.added !== 1 ? 's' : ''}`;
    if (d.skipped_duplicate > 0) msg += ` · ${d.skipped_duplicate} duplicate${d.skipped_duplicate !== 1 ? 's' : ''} skipped`;
    if (d.skipped_invalid > 0) msg += ` · ${d.skipped_invalid} invalid`;
    showToast(msg);
    
    if (d.added > 0) {
      closeBulkModal();
      document.getElementById('bulk-urls').value = '';
    }
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Queue all';
  }
}

// Wire up the bulk-add button and modal events
document.addEventListener('DOMContentLoaded', function() {
  const bulkBtn = document.getElementById('bulk-add');
  if (bulkBtn) {
    bulkBtn.addEventListener('click', openBulkModal);
  }
  // Update count as user types
  const bulkTextarea = document.getElementById('bulk-urls');
  if (bulkTextarea) {
    bulkTextarea.addEventListener('input', updateBulkCount);
  }
  // Close modal on backdrop click
  const modal = document.getElementById('bulk-modal');
  if (modal) {
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeBulkModal();
    });
  }
  // Close modal on Escape
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && modal && modal.style.display !== 'none') {
      closeBulkModal();
    }
  });
});
```

### 2e. Update the "Queue all" button label dynamically

In `updateBulkCount()`, also update the submit button text to show the count:

```js
// Add to updateBulkCount(), after setting el.textContent:
const submitBtn = document.getElementById('bulk-submit');
if (submitBtn) {
  submitBtn.textContent = count > 0 ? `Queue all ${count}` : 'Queue all';
}
```

## Verification — Feature 2

- [ ] Dashboard footer shows "+ Bulk add" button
- [ ] Click "+ Bulk add" → modal opens with empty textarea
- [ ] Type/paste 5 URLs (one per line) → count updates to "5 URLs detected", button says "Queue all 5"
- [ ] Paste a non-URL line like "hello world" → count shows "6 URLs detected · 1 invalid" in orange
- [ ] Click "Queue all 5" → toast says "Queued 5 downloads", modal closes, 5 jobs appear in QUEUED section
- [ ] Try adding the same 5 URLs again → toast says "Queued 0 downloads · 5 duplicates skipped" (assuming they completed; if still queued, they'll be added again — dedup is only against `completed` status)
- [ ] Change quality dropdown to "audio" → all 5 queued jobs have quality="audio"
- [ ] Click modal backdrop → modal closes
- [ ] Press Escape → modal closes
- [ ] Submit empty textarea → toast says "No URLs to add"
- [ ] `curl -X POST -H "Content-Type: application/json" -d '{"urls":["https://youtube.com/watch?v=a","https://youtube.com/watch?v=b","not-a-url"]}' http://localhost:5000/api/bulk/add` → returns `{"added":2, "skipped_invalid":1, ...}`

---

# Implementation order

1. **Feature 1a:** Add `downloads_enabled` to `DEFAULT_CONFIG` in `models.py`
2. **Feature 1b:** Add toggle check in `_process_queue()` in `worker.py`
3. **Feature 1c:** Add `/api/toggle` endpoint + update `/api/info` + `ALLOWED_SETTINGS` in `app.py`
4. **Feature 1d:** Add toggle HTML in `dashboard.html`
5. **Feature 1e:** Add toggle CSS in `style.css`
6. **Feature 1f:** Add toggle JS in `dashboard.js`
7. **Feature 2a:** Add `/api/bulk/add` endpoint in `app.py`
8. **Feature 2b:** Add bulk-add modal HTML in `dashboard.html`
9. **Feature 2c:** Add modal CSS in `style.css`
10. **Feature 2d:** Add bulk-add JS in `dashboard.js`

# Full test checklist (15 items)

## Master Toggle
- [ ] Footer shows green "Downloads: ON" toggle by default
- [ ] Click toggle → gray "Downloads: OFF", queued jobs show "⏸ waiting for toggle" hint
- [ ] With toggle OFF, queue a job → stays in queued, doesn't download
- [ ] With toggle OFF, active downloads keep running
- [ ] Click toggle ON → queued jobs immediately start
- [ ] Toggle state persists across daemon restart
- [ ] `GET /api/info` returns `downloads_enabled` field
- [ ] Toggle works independently from "Pause all"

## Bulk Add
- [ ] Footer shows "+ Bulk add" button
- [ ] Click → modal opens, textarea focused
- [ ] Live count updates as you type
- [ ] Submit 5 URLs → 5 jobs queued, toast confirms
- [ ] Submit duplicates → skipped with count in toast
- [ ] Quality dropdown applies to all URLs in batch
- [ ] Escape key + backdrop click closes modal

# Deliverable

After implementing both features:
1. Run the full test checklist above (15 items).
2. Commit each feature as a separate git commit:
   - `feat: master download toggle (footer switch, /api/toggle, config-persisted)`
   - `feat: bulk add URLs modal (/api/bulk/add, footer button, dedup against history)`
3. Open a single PR linking back to this prompt.

---

## Reference: design decisions

### Why the toggle is stored in config.json (not DB)
The toggle is a runtime setting, not a per-job attribute. Storing it in `config.json` alongside `concurrent_limit` and `theme` keeps it consistent with other daemon-level settings. The existing `load_config()` / `save_config()` functions handle persistence automatically.

### Why bulk add dedups only against `completed` (not `queued`)
If a user pastes the same URL twice in one batch, the in-batch dedup (via `existing_urls.add(url)`) catches it. But if they paste a URL that's currently `queued`, we want to allow it — they might be re-queuing after a failure, or want to download it twice (e.g., different quality). Deduping against `completed` only prevents re-downloading videos the user already has.

### Why playlists aren't expanded in bulk add
The existing `/api/add` route runs `yt-dlp --flat-playlist` synchronously (or in a background thread for playlists), which takes 5-30 seconds per playlist. If a user pastes 5 playlist URLs, that's 25-150 seconds of blocking. Instead, bulk add inserts each playlist URL as a single `queued` job. The existing playlist-detection logic in `_process_queue` / `run_download` handles expansion when the job starts. This keeps bulk add fast and responsive.

### Why the toggle check is in `_process_queue` (not `process_queue`)
`process_queue()` just sets an event and starts the worker thread. The actual logic is in `_process_queue()`. Putting the check there means the toggle is evaluated on every queue-processing cycle, including when a download finishes and the worker picks up the next job. If the user flips the toggle OFF mid-download, the next job won't start.
