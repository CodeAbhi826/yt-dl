# yt-dl — Bugfix & Refactor Summary

## PhantomJS OpenSSL Fix (Jun 26)
- **Root cause**: OpenSSL 3.x provider module crash (`libproviders.so` not found) in PhantomJS
- **Fix**: `OPENSSL_CONF=/dev/null` env var in worker.py subprocess calls
- **Removed**: `src/phantom_patch.py` (Playwright approach didn't work — worker uses subprocess, not Python library)
- **77 PornHub URLs** extracted from daemon log, saved to `~/.local/share/yt-dl/hub_urls.txt`
- **73 active/queued jobs** cancelled

## Bugfix Prompt — All 68 Bugs Applied (Jun 26)

### Phase 1 — worker.py (~10 bugs)
- `_fire_webhook()` via `urllib.request` (no more `curl` subprocess)
- `rglob` fallback for filename detection
- UTC timezone on all timestamps
- `started_at`/`completed_at` preserved from DB row (not overwritten)
- Cancelled-job guard after `proc.wait()` — skip post-processing
- Info command has no `--format` flag
- `filepath` (not `filename`) in progress template with NA guard
- Removed dead `.replace("/", "⧸")` code

### Phase 2 — app.py (~15 bugs)
- Constant-time auth via `hmac.compare_digest`
- Version from `_version.py` (single source of truth)
- Pagination (`limit`/`offset`) for `/api/queue`
- Settings validation (type checks, range checks, allowed keys)
- `max_log_lines` deque resize on config update
- Open-path restriction to download directory
- `bulk_retry` only targets `failed`/`cancelled` jobs
- Cookie path leak fixed (no path in response)
- Stats: daily bars with zero-fill, separate status buckets (completed/failed/cancelled/active/other)
- Logs count clamped to 1-1000
- Playlist: URL fallback chain (`url` → `webpage_url` → construct), job_id via `uuid.uuid4()`
- Playlist DoS guard: `--playlist-end 50`, 10s timeout
- SSE DB connection leak: `with closing(get_db())`
- SSE hash: `hashlib.md5` over Python's built-in `hash()`
- SSE N+1: single background poll thread → broadcast to subscribers

### Phase 3 — style.css (1 bug)
- Responsive nav collapse at 768px (flex-wrap, hidden conn-text)

### Phase 4 — dashboard.js (2 bugs)
- `loadMore()` pagination button
- Failed card: "View logs →" link instead of entire card being clickable

### Phase 5 — templates + theme.js (~10 bugs)
- **FOUC fix**: inline `<script>` in `<head>` before CSS loads
- **Theme toggle**: reverts on fetch error
- **Stats empty state**: "No downloads yet" message
- **"Clear Display"** renamed to **"Clear View"** in logs
- **Settings**: client-side validation (concurrent 1-20, playlist 1-1000), button disables during save with "Saving..."
- **"Reset Stats"** renamed to **"Clear History"** with explicit confirmation message

### Phase 6 — Extension (6 bugs)
- **Bug 7**: `info.linkUrl || info.srcUrl || info.pageUrl` for video/audio context menu
- **Bug 8**: `chrome.storage.local` persistence for `prevJobs` + `__initialized` flag to prevent notification storm on SW restart
- **Bug 9**: `apiFetch()` helper with auth headers from storage; API key input in popup
- **Bug 10**: alarm `periodInMinutes` 0.5 (Chrome minimum)
- **Bug 33**: `sourceTabs` tracking for correct toast routing
- **Bug 34**: Removed `<all_urls>` and YouTube host_permissions (privacy)

### Phase 7 — Docker/Install/README (7 bugs)
- **Dockerfile**: non-root `ytdl` user (uid 1000), `COPY --chown`, `USER`, `HOME` env
- **docker-compose.yml**: volume path → `/home/ytdl/.local/share/yt-dl`
- **handler.sh**: `/health` (not `/api/health`), `curl -sf` flag
- **install.sh**: `command -v python3` (not hardcoded `/usr/bin/python3`), removed stale `dbus` import check
- **install.fish**: `notifications.py` ref → "handled by browser extension"
- **README.md**: fixed `--cookies-from-browser` example with `chrome:Default`

## Git History (newest first)
```
84e17f2 fix(#44): pause-all actually pauses, add pause/resume routes + dashboard UI
94b4a59 fix(#68): make OPENSSL_CONF override conditional, respect user env
a4bb96b fix(#43): dashboard grid collapse at 900px, card heights to min-height
9714fc7 fix: phantomjs openSSL provider crash via OPENSSL_CONF env
```
