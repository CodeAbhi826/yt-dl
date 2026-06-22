#!/usr/bin/env python3
"""yt-dl daemon - Flask backend for zero-friction YouTube downloading."""

import os
import sys
import json
import time
import sqlite3
import threading
import queue
import logging
import re
import socket
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context

# Import local modules
sys.path.insert(0, str(Path(__file__).parent))
from models import (
    init_db, get_db, load_config, save_config, job_to_dict,
    human_bytes, DEFAULT_CONFIG, QUALITY_MAP, DATA_DIR, DB_PATH, CONFIG_PATH
)
from notifications import NotificationManager, set_action_callbacks
from worker import (
    process_queue, cancel_job, retry_job, active_jobs,
    set_notification_manager
)

# Logging setup
LOG_PATH = DATA_DIR / "daemon.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH)),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("yt-dl")

# Ring buffer log handler for SSE
from collections import deque

class RingBufferLogHandler:
    def __init__(self, max_lines=500):
        self.max_lines = max_lines
        self.buffer = deque(maxlen=max_lines)
        self.lock = threading.Lock()
        self.subscribers = []
        self.sub_lock = threading.Lock()

    def write(self, line):
        if not line.strip():
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"time": ts, "message": line.rstrip(), "level": self._detect_level(line)}
        with self.lock:
            self.buffer.append(entry)
        self._notify(entry)

    def _detect_level(self, line):
        ll = line.lower()
        if "error" in ll or "exception" in ll or "traceback" in ll:
            return "ERROR"
        elif "warn" in ll:
            return "WARN"
        elif "debug" in ll:
            return "DEBUG"
        elif "success" in ll or "completed" in ll:
            return "SUCCESS"
        elif "download" in ll and "%" in ll:
            return "PROGRESS"
        return "INFO"

    def _notify(self, entry):
        with self.sub_lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put(entry, block=False)
                except:
                    dead.append(q)
            for q in dead:
                if q in self.subscribers:
                    self.subscribers.remove(q)

    def get_lines(self, count=None, level_filter=None):
        with self.lock:
            lines = list(self.buffer)
        if level_filter and level_filter != "ALL":
            lines = [l for l in lines if l["level"] == level_filter]
        if count:
            lines = lines[-count:]
        return lines

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self.sub_lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.sub_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

ring_log = RingBufferLogHandler()

class LogRedirector:
    def __init__(self, handler, original):
        self.handler = handler
        self.original = original
    def write(self, s):
        if s.strip():
            self.handler.write(s)
        self.original.write(s)
    def flush(self):
        self.original.flush()

sys.stdout = LogRedirector(ring_log, sys.stdout)
sys.stderr = LogRedirector(ring_log, sys.stderr)

# Initialize notification manager
nm = NotificationManager()
set_notification_manager(nm)
set_action_callbacks(retry_fn=retry_job, cancel_fn=cancel_job)

# ── CSS & Templates ────────────────────────────────────────────────

CSS = """
:root { --bg: #0a0a0a; --card: #141414; --hover: #1a1a1a; --border: #2a2a2a; --text: #e5e5e5; --text-secondary: #888888; --accent: #ff2d20; --accent-hover: #e0261a; --green: #22c55e; --orange: #f39c12; --gray: #666666; --radius-card: 16px; --radius-btn: 10px; --font: 'Inter', sans-serif; }
[data-theme="light"] { --bg: #f5f5f5; --card: #ffffff; --hover: #f0f0f0; --border: #e0e0e0; --text: #1a1a1a; --text-secondary: #666666; --accent: #ff2d20; --accent-hover: #e0261a; --green: #16a34a; --orange: #d97706; --gray: #9ca3af; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 24px; }
.nav { display: flex; align-items: center; gap: 8px; padding: 16px 24px; background: var(--card); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 100; }
.nav-brand { font-size: 20px; font-weight: 700; color: var(--accent); text-decoration: none; }
.nav-links { display: flex; gap: 4px; margin-left: 32px; flex: 1; }
.nav-link { padding: 8px 16px; border-radius: var(--radius-btn); text-decoration: none; color: var(--text-secondary); font-size: 13px; font-weight: 500; }
.nav-link:hover, .nav-link.active { color: var(--text); background: var(--hover); }
.btn { padding: 8px 16px; border-radius: var(--radius-btn); border: none; cursor: pointer; font-family: var(--font); font-size: 13px; font-weight: 500; }
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: var(--hover); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { background: var(--border); }
.btn-danger { background: #dc2626; color: white; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.card { background: var(--card); border-radius: var(--radius-card); border: 1px solid var(--border); padding: 24px; }
.card-title { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); margin-bottom: 16px; font-weight: 600; }
.grid { display: grid; gap: 20px; }
.grid-2 { grid-template-columns: repeat(2, 1fr); }
.grid-4 { grid-template-columns: repeat(4, 1fr); }
@media (max-width: 1024px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .grid-2, .grid-4 { grid-template-columns: 1fr; } }
.stat-value { font-size: 32px; font-weight: 700; color: var(--text); }
.stat-label { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.progress-bar { height: 6px; background: var(--hover); border-radius: 3px; overflow: hidden; margin-top: 12px; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s ease; }
.form-group { margin-bottom: 20px; }
.form-label { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); margin-bottom: 8px; font-weight: 600; }
.form-input, .form-select { width: 100%; padding: 12px 16px; border-radius: var(--radius-btn); border: 1px solid var(--border); background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; }
.form-input:focus, .form-select:focus { outline: none; border-color: var(--accent); }
.checkbox-group { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.checkbox-group input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--accent); }
.tag { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
.tag-queued { background: rgba(102,102,102,0.2); color: var(--gray); }
.tag-downloading { background: rgba(255,45,32,0.15); color: var(--accent); }
.tag-completed { background: rgba(34,197,94,0.15); color: var(--green); }
.tag-failed { background: rgba(220,38,38,0.15); color: #dc2626; }
.tag-cancelled { background: rgba(243,156,18,0.15); color: var(--orange); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 12px 16px; font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--text-secondary); font-weight: 600; border-bottom: 1px solid var(--border); }
td { padding: 14px 16px; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--hover); }
.log-line { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; padding: 6px 12px; border-radius: 6px; margin-bottom: 4px; display: flex; gap: 12px; }
.log-time { color: var(--text-secondary); flex-shrink: 0; min-width: 140px; }
.log-msg { word-break: break-word; }
.log-ERROR { background: rgba(220,38,38,0.1); color: #ef4444; }
.log-WARN { background: rgba(243,156,18,0.1); color: var(--orange); }
.log-SUCCESS { background: rgba(34,197,94,0.1); color: var(--green); }
.log-PROGRESS { background: rgba(255,45,32,0.05); color: var(--accent); }
.log-DEBUG { background: rgba(100,100,100,0.1); color: var(--text-secondary); }
.log-INFO { background: transparent; }
.bulk-bar { display: flex; gap: 8px; align-items: center; padding: 12px 16px; background: var(--hover); border-radius: var(--radius-btn); margin-bottom: 16px; border: 1px solid var(--border); }
.bulk-bar.hidden { display: none; }
.theme-toggle { background: var(--hover); border: 1px solid var(--border); color: var(--text); cursor: pointer; padding: 8px; border-radius: 10px; }
.theme-toggle:hover { background: var(--border); }
.toast { position: fixed; bottom: 24px; right: 24px; padding: 14px 20px; border-radius: var(--radius-btn); background: var(--card); border: 1px solid var(--border); color: var(--text); font-size: 13px; font-weight: 500; box-shadow: 0 8px 32px rgba(0,0,0,0.3); z-index: 1000; transform: translateY(100px); opacity: 0; transition: all 0.3s ease; }
.toast.show { transform: translateY(0); opacity: 1; }
.toast.success { border-left: 3px solid var(--green); }
.toast.error { border-left: 3px solid var(--accent); }
.bar-chart { display: flex; align-items: flex-end; gap: 8px; height: 180px; padding: 0 8px; }
.bar { flex: 1; background: var(--accent); border-radius: 4px 4px 0 0; min-height: 4px; transition: height 0.5s ease; position: relative; }
.bar-label { position: absolute; bottom: -20px; left: 50%; transform: translateX(-50%); font-size: 10px; color: var(--text-secondary); white-space: nowrap; }
.badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); animation: pulse 2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.spinner { width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
"""

NAV = """
<nav class="nav">
<a href="/" class="nav-brand">yt-dl</a>
<div class="nav-links">
<a href="/" class="nav-link {{ 'active' if active == 'dashboard' else '' }}">Queue</a>
<a href="/stats" class="nav-link {{ 'active' if active == 'stats' else '' }}">Stats</a>
<a href="/logs" class="nav-link {{ 'active' if active == 'logs' else '' }}">Logs</a>
<a href="/search" class="nav-link {{ 'active' if active == 'search' else '' }}">Search</a>
<a href="/settings" class="nav-link {{ 'active' if active == 'settings' else '' }}">Settings</a>
</div>
<div class="nav-actions">
<button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
</button>
</div>
</nav>
"""

THEME_JS = """
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  localStorage.setItem("yt-dl-theme", next);
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({theme:next})});
}
const savedTheme = localStorage.getItem("yt-dl-theme");
if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);
"""

TOAST_JS = """
function showToast(message, type="success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = "toast show " + type;
  setTimeout(() => toast.classList.remove("show"), 3000);
}
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Queue - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + CSS + """
</style>
</head>
<body>
""" + NAV + """
<main class="container">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px;">
<h1 style="font-size:24px; font-weight:700;">Download Queue</h1>
<div style="display:flex; gap:8px;">
<button class="btn btn-secondary btn-sm" onclick="refreshQueue()">Refresh</button>
</div>
</div>
<div id="bulk-bar" class="bulk-bar hidden">
<span id="bulk-count" style="font-size:13px; font-weight:600;">0 selected</span>
<div style="flex:1"></div>
<button class="btn btn-secondary btn-sm" onclick="bulkRetry()">Retry</button>
<button class="btn btn-danger btn-sm" onclick="bulkDelete()">Delete</button>
<button class="btn btn-secondary btn-sm" onclick="clearSelection()">Cancel</button>
</div>
<div class="card">
<div style="overflow-x:auto;">
<table>
<thead><tr>
<th style="width:32px"><input type="checkbox" id="select-all" onchange="toggleSelectAll()"></th>
<th>Video</th><th>Quality</th><th>Status</th><th>Progress</th><th>Added</th><th style="width:120px">Actions</th>
</tr></thead>
<tbody id="queue-body">
<tr><td colspan="7" style="text-align:center; padding:40px;"><div class="spinner" style="margin:0 auto;"></div></td></tr>
</tbody>
</table>
</div>
</div>
</main>
<div id="toast" class="toast"></div>
<script>
""" + THEME_JS + TOAST_JS + """
let selectedIds = new Set();
function refreshQueue() {
  fetch("/api/queue").then(r=>r.json()).then(data=>renderQueue(data));
}
function renderQueue(jobs) {
  const tbody = document.getElementById("queue-body");
  if (!jobs.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:40px;"><p style="font-size:16px;font-weight:600;">No downloads yet</p><p style="color:var(--text-secondary);">Use the Brave extension to add videos.</p></td></tr>';
    return;
  }
  tbody.innerHTML = jobs.map(j => `<tr data-id="${j.id}"><td><input type="checkbox" class="row-check" value="${j.id}" onchange="toggleRow('${j.id}')"></td><td><div style="display:flex;align-items:center;gap:12px;"><div style="width:48px;height:36px;background:var(--hover);border-radius:8px;overflow:hidden;">${j.video_id ? `<img src="https://i.ytimg.com/vi/${j.video_id}/mqdefault.jpg" style="width:100%;height:100%;object-fit:cover;">` : 'YT'}</div><div><div style="font-weight:600;font-size:13px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${j.title || j.video_id || 'Unknown'}</div><div style="font-size:11px;color:var(--text-secondary);">${j.url}</div></div></div></td><td><span class="tag">${j.quality}</span></td><td><span class="tag tag-${j.status}">${j.status}</span></td><td style="width:180px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;"><span>${j.progress || 0}%</span><span style="color:var(--text-secondary);">${j.speed || ''}</span></div><div class="progress-bar"><div class="progress-fill" style="width:${j.progress || 0}%"></div></div></td><td style="font-size:12px;color:var(--text-secondary);">${j.created_at || ''}</td><td><div style="display:flex;gap:4px;">${j.status === 'failed' ? `<button class="btn btn-secondary btn-sm" onclick="retryJob('${j.id}')">Retry</button>` : ''}${j.status !== 'downloading' ? `<button class="btn btn-danger btn-sm" onclick="deleteJob('${j.id}')">Delete</button>` : `<button class="btn btn-secondary btn-sm" onclick="cancelJob('${j.id}')">Cancel</button>`}</div></td></tr>`).join('');
  updateBulkBar();
}
function toggleRow(id) { if(selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id); updateBulkBar(); }
function toggleSelectAll() { const all=document.getElementById("select-all").checked; document.querySelectorAll(".row-check").forEach(cb=>{cb.checked=all; if(all) selectedIds.add(cb.value); else selectedIds.delete(cb.value);}); updateBulkBar(); }
function updateBulkBar() { const bar=document.getElementById("bulk-bar"), count=document.getElementById("bulk-count"); if(selectedIds.size>0){bar.classList.remove("hidden"); count.textContent=selectedIds.size+" selected";}else{bar.classList.add("hidden");} }
function clearSelection() { selectedIds.clear(); document.querySelectorAll(".row-check,#select-all").forEach(cb=>cb.checked=false); updateBulkBar(); }
function bulkDelete() { if(!confirm("Delete "+selectedIds.size+" items?"))return; fetch("/api/bulk/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids:Array.from(selectedIds)})}).then(r=>r.json()).then(d=>{showToast("Deleted "+d.deleted+" items"); clearSelection(); refreshQueue();}); }
function bulkRetry() { fetch("/api/bulk/retry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids:Array.from(selectedIds)})}).then(r=>r.json()).then(d=>{showToast("Retried "+d.retried+" items"); clearSelection(); refreshQueue();}); }
function retryJob(id) { fetch("/api/jobs/"+id+"/retry",{method:"POST"}).then(r=>r.json()).then(d=>{showToast("Job retried"); refreshQueue();}); }
function deleteJob(id) { if(!confirm("Delete this item?"))return; fetch("/api/jobs/"+id,{method:"DELETE"}).then(r=>r.json()).then(d=>{showToast("Deleted"); refreshQueue();}); }
function cancelJob(id) { fetch("/api/jobs/"+id+"/cancel",{method:"POST"}).then(r=>r.json()).then(d=>{showToast("Cancelled"); refreshQueue();}); }
refreshQueue(); setInterval(refreshQueue, 3000);
</script>
</body>
</html>
"""

# ── Flask App ─────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/api/queue")
def api_queue():
    db = get_db()
    rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
    db.close()
    return jsonify([job_to_dict(r) for r in rows])

@app.route("/api/jobs/<job_id>/retry", methods=["POST"])
def api_retry_job(job_id):
    if retry_job(job_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404

@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id):
    if cancel_job(job_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404

@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_delete_job(job_id):
    db = get_db()
    row = db.execute("SELECT file_path FROM downloads WHERE job_id=?", (job_id,)).fetchone()
    if row and row["file_path"] and os.path.exists(row["file_path"]):
        try:
            os.remove(row["file_path"])
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")
    db.execute("DELETE FROM downloads WHERE job_id=?", (job_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/bulk/delete", methods=["POST"])
def api_bulk_delete():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"deleted": 0})
    db = get_db()
    deleted = 0
    for jid in ids:
        row = db.execute("SELECT file_path FROM downloads WHERE job_id=?", (jid,)).fetchone()
        if row and row["file_path"] and os.path.exists(row["file_path"]):
            try:
                os.remove(row["file_path"])
            except:
                pass
        c = db.execute("DELETE FROM downloads WHERE job_id=?", (jid,))
        deleted += c.rowcount
    db.commit()
    db.close()
    return jsonify({"deleted": deleted})

@app.route("/api/bulk/retry", methods=["POST"])
def api_bulk_retry():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"retried": 0})
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    db.execute(f"UPDATE downloads SET status='queued', progress=0, error_message=NULL, retry_count=retry_count+1 WHERE job_id IN ({placeholders})", tuple(ids))
    db.commit()
    db.close()
    threading.Thread(target=process_queue, daemon=True).start()
    return jsonify({"retried": len(ids)})

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_config())

@app.route("/api/settings", methods=["PUT"])
def api_update_settings():
    cfg = load_config()
    updates = request.get_json() or {}
    cfg.update(updates)
    save_config(cfg)
    if "max_log_lines" in updates:
        ring_log.max_lines = cfg["max_log_lines"]
    return jsonify(cfg)

@app.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    save_config(DEFAULT_CONFIG.copy())
    return jsonify(DEFAULT_CONFIG.copy())

@app.route("/api/stats")
def api_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM downloads").fetchone()["c"]
    success = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='completed'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='failed'").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='downloading'").fetchone()["c"]
    daily = db.execute("SELECT date(created_at) as day, COUNT(*) as cnt FROM downloads WHERE created_at >= date('now', '-7 days') GROUP BY day ORDER BY day").fetchall()
    db.close()

    max_cnt = max([r["cnt"] for r in daily] + [1])
    daily_bars = [{"label": r["day"][5:], "pct": int(r["cnt"] / max_cnt * 100), "count": r["cnt"]} for r in daily]
    while len(daily_bars) < 7:
        daily_bars.insert(0, {"label": "", "pct": 0, "count": 0})

    success_rate = round(success / total * 100, 1) if total > 0 else 0
    status_breakdown = [
        {"label": "Completed", "count": success, "color": "#22c55e", "pct": round(success/total*100,1) if total else 0},
        {"label": "Failed", "count": failed, "color": "#dc2626", "pct": round(failed/total*100,1) if total else 0},
        {"label": "Other", "count": total - success - failed, "color": "#666666", "pct": round((total-success-failed)/total*100,1) if total else 0},
    ]

    return jsonify({
        "total_downloaded": total,
        "total_success": success,
        "total_failed": failed,
        "success_rate": success_rate,
        "total_bytes": 0,
        "total_bytes_human": "0.0 B",
        "active_now": active,
        "daily_bars": daily_bars,
        "status_breakdown": status_breakdown,
    })

@app.route("/api/stats/reset", methods=["POST"])
def api_reset_stats():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("DELETE FROM downloads")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({"reset": True, "deleted_records": deleted})

@app.route("/api/logs")
def api_logs():
    level = request.args.get("level", "ALL")
    count = int(request.args.get("count", 100))
    return jsonify(ring_log.get_lines(count=count, level_filter=level))

@app.route("/api/logs/stream")
def stream_logs():
    def event_stream():
        q = ring_log.subscribe()
        try:
            for entry in ring_log.get_lines(count=50):
                yield "data: " + json.dumps(entry) + "\n\n"
            while True:
                entry = q.get(timeout=30)
                yield "data: " + json.dumps(entry) + "\n\n"
        except:
            pass
        finally:
            ring_log.unsubscribe(q)
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    quality = request.args.get("quality", "")
    date_range = request.args.get("date", "")
    db = get_db()
    conditions = ["1=1"]
    params = []
    if q:
        conditions.append("(title LIKE ? OR video_id LIKE ? OR url LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if status:
        conditions.append("status = ?")
        params.append(status)
    if quality:
        conditions.append("quality = ?")
        params.append(quality)
    if date_range == "today":
        conditions.append("date(created_at) = date('now')")
    elif date_range == "week":
        conditions.append("created_at >= date('now', '-7 days')")
    elif date_range == "month":
        conditions.append("created_at >= date('now', '-30 days')")
    where = " AND ".join(conditions)
    rows = db.execute(f"SELECT * FROM downloads WHERE {where} ORDER BY created_at DESC LIMIT 100", tuple(params)).fetchall()
    db.close()
    return jsonify([job_to_dict(r) for r in rows])

@app.route("/api/add", methods=["POST"])
def api_add_job():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    quality = data.get("quality", load_config().get("default_quality", "720p"))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    video_id = ""
    m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
    if m:
        video_id = m.group(1)

    job_id = f"job_{int(time.time() * 1000)}_{video_id or 'unknown'}"
    db = get_db()
    db.execute("INSERT INTO downloads (job_id, video_id, url, quality, status, title) VALUES (?, ?, ?, ?, 'queued', ?)",
               (job_id, video_id, url, quality, data.get("title", "")))
    db.commit()
    db.close()

    # Start worker
    threading.Thread(target=process_queue, daemon=True).start()

    return jsonify({"job_id": job_id, "status": "queued"})

# ── Page Routes ───────────────────────────────────────────────────

@app.route("/")
def dashboard():
    cfg = load_config()
    return render_template_string(DASHBOARD_HTML, active="dashboard", theme=cfg.get("theme", "dark"))

@app.route("/settings")
def settings_page():
    cfg = load_config()
    return render_template_string(SETTINGS_HTML, active="settings", config=cfg, qualities=list(QUALITY_MAP.keys()),
                                  download_dir=cfg.get("download_dir", "/mnt/storage/YouTube"),
                                  default_quality=cfg.get("default_quality", "720p"),
                                  concurrent_limit=cfg.get("concurrent_limit", 3),
                                  embed_metadata=cfg.get("embed_metadata", True),
                                  embed_thumbnail=cfg.get("embed_thumbnail", True),
                                  embed_chapters=cfg.get("embed_chapters", True),
                                  embed_subs=cfg.get("embed_subs", True),
                                  theme=cfg.get("theme", "dark"))

@app.route("/stats")
def stats_page():
    cfg = load_config()
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM downloads").fetchone()["c"]
    success = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='completed'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='failed'").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='downloading'").fetchone()["c"]
    daily = db.execute("SELECT date(created_at) as day, COUNT(*) as cnt FROM downloads WHERE created_at >= date('now', '-7 days') GROUP BY day ORDER BY day").fetchall()
    db.close()
    max_cnt = max([r["cnt"] for r in daily] + [1])
    daily_bars = [{"label": r["day"][5:], "pct": int(r["cnt"] / max_cnt * 100), "count": r["cnt"]} for r in daily]
    while len(daily_bars) < 7:
        daily_bars.insert(0, {"label": "", "pct": 0, "count": 0})
    success_rate = round(success / total * 100, 1) if total > 0 else 0
    status_breakdown = [
        {"label": "Completed", "count": success, "color": "#22c55e", "pct": round(success/total*100,1) if total else 0},
        {"label": "Failed", "count": failed, "color": "#dc2626", "pct": round(failed/total*100,1) if total else 0},
        {"label": "Other", "count": total - success - failed, "color": "#666666", "pct": round((total-success-failed)/total*100,1) if total else 0},
    ]
    return render_template_string(STATS_HTML, active="stats",
                                  total_downloaded=total, total_success=success, total_failed=failed,
                                  success_rate=success_rate, total_bytes_human="0.0 B", active_now=active,
                                  daily_bars=daily_bars, status_breakdown=status_breakdown,
                                  theme=cfg.get("theme", "dark"))

@app.route("/logs")
def logs_page():
    cfg = load_config()
    return render_template_string(LOGS_HTML, active="logs", theme=cfg.get("theme", "dark"))

@app.route("/search")
def search_page():
    cfg = load_config()
    return render_template_string(SEARCH_HTML, active="search", qualities=list(QUALITY_MAP.keys()), theme=cfg.get("theme", "dark"))

# ── Additional HTML Templates ─────────────────────────────────────

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Settings - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + CSS + """
</style>
</head>
<body>
""" + NAV + """
<main class="container">
<h1 style="font-size:24px; font-weight:700; margin-bottom:24px;">Settings</h1>
<div class="grid grid-2">
<div class="card">
<div class="card-title">Download</div>
<div class="form-group">
<label class="form-label">Download Directory</label>
<input type="text" class="form-input" id="download_dir" value="{{ download_dir }}">
</div>
<div class="form-group">
<label class="form-label">Default Quality</label>
<select class="form-select" id="default_quality">
{% for q in qualities %}<option value="{{ q }}" {% if q == default_quality %}selected{% endif %}>{{ q }}</option>{% endfor %}
</select>
</div>
<div class="form-group">
<label class="form-label">Concurrent Downloads</label>
<input type="number" class="form-input" id="concurrent_limit" value="{{ concurrent_limit }}" min="1" max="10">
</div>
</div>
<div class="card">
<div class="card-title">Embed Options</div>
<div class="form-group">
<label class="checkbox-group"><input type="checkbox" id="embed_metadata" {% if embed_metadata %}checked{% endif %}><span>Embed metadata</span></label>
</div>
<div class="form-group">
<label class="checkbox-group"><input type="checkbox" id="embed_thumbnail" {% if embed_thumbnail %}checked{% endif %}><span>Embed thumbnail</span></label>
</div>
<div class="form-group">
<label class="checkbox-group"><input type="checkbox" id="embed_chapters" {% if embed_chapters %}checked{% endif %}><span>Embed chapters</span></label>
</div>
<div class="form-group">
<label class="checkbox-group"><input type="checkbox" id="embed_subs" {% if embed_subs %}checked{% endif %}><span>Embed subtitles</span></label>
</div>
</div>
</div>
<div style="margin-top:24px; display:flex; gap:12px;">
<button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
</div>
<div style="margin-top:24px; padding:20px; background:#1a1a1a; border-radius:12px; border:1px solid #333;">
<h3 style="font-size:14px; font-weight:600; color:#ff2d20; margin:0 0 16px 0; text-transform:uppercase; letter-spacing:1px;">Danger Zone</h3>
<button onclick="if(confirm('Clear all download history? This cannot be undone.')){fetch('/api/stats/reset',{method:'POST'}).then(r=>r.json()).then(d=>alert('Cleared '+d.deleted_records+' records'))}" style="background:#dc2626;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:12px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Clear Download History</button>
<p style="font-size:11px; color:#666; margin:12px 0 0 0;">Removes all records from the database.</p>
</div>
</main>
<div id="toast" class="toast"></div>
<script>
""" + THEME_JS + TOAST_JS + """
function saveSettings() {
  const cfg = {
    download_dir: document.getElementById("download_dir").value,
    default_quality: document.getElementById("default_quality").value,
    concurrent_limit: parseInt(document.getElementById("concurrent_limit").value),
    embed_metadata: document.getElementById("embed_metadata").checked,
    embed_thumbnail: document.getElementById("embed_thumbnail").checked,
    embed_chapters: document.getElementById("embed_chapters").checked,
    embed_subs: document.getElementById("embed_subs").checked,
  };
  fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(cfg)})
    .then(r=>r.json()).then(d=> showToast("Settings saved"));
}
</script>
</body>
</html>
"""

STATS_HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stats - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + CSS + """
</style>
</head>
<body>
""" + NAV + """
<main class="container">
<div style="display:flex;align-items:center;gap:16px;margin-bottom:24px;">
<h1 style="font-size:24px; font-weight:700;">Statistics</h1>
<button id="reset-stats-btn" style="background:#dc2626;color:#fff;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Reset Stats</button>
</div>
<div class="grid grid-4" style="margin-bottom:24px;">
<div class="card"><div class="card-title">Total Downloads</div><div class="stat-value" id="stat-total">{{ total_downloaded }}</div><div class="stat-label">All time</div></div>
<div class="card"><div class="card-title">Success Rate</div><div class="stat-value" id="stat-rate">{{ success_rate }}%</div><div class="stat-label">{{ total_success }} succeeded / {{ total_failed }} failed</div><div class="progress-bar"><div class="progress-fill" id="stat-rate-bar" style="width:{{ success_rate }}%"></div></div></div>
<div class="card"><div class="card-title">Total Data</div><div class="stat-value" id="stat-bytes">{{ total_bytes_human }}</div><div class="stat-label">Downloaded</div></div>
<div class="card"><div class="card-title">Active Now</div><div class="stat-value" id="stat-active">{{ active_now }}</div><div class="stat-label">In progress</div></div>
</div>
<div class="grid grid-2">
<div class="card"><div class="card-title">Downloads Over Time</div><div style="height:200px; display:flex; align-items:flex-end; gap:8px; padding:20px 0;">{% for bar in daily_bars %}<div class="bar" style="height:{{ bar.pct }}%"><div class="bar-label">{{ bar.label }}</div></div>{% endfor %}</div></div>
<div class="card"><div class="card-title">Status Breakdown</div><div style="margin-top:16px;">{% for item in status_breakdown %}<div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;"><div style="width:12px; height:12px; border-radius:50%; background:{{ item.color }};"></div><div style="flex:1; font-size:13px;">{{ item.label }}</div><div style="font-weight:700;">{{ item.count }}</div><div style="font-size:12px; color:var(--text-secondary); width:50px; text-align:right;">{{ item.pct }}%</div></div><div class="progress-bar" style="height:4px; margin-bottom:12px;"><div class="progress-fill" style="width:{{ item.pct }}%; background:{{ item.color }};"></div></div>{% endfor %}</div></div>
</div>
</main>
<script>
""" + THEME_JS + """
function resetStats() {
  const btn = document.getElementById("reset-stats-btn");
  btn.textContent = "Clearing..."; btn.disabled = true;
  fetch("/api/stats/reset", {method: "POST"})
    .then(r => r.json())
    .then(d => {
      if (d.deleted_records === 0) {
        showToast("No records to clear", "error");
        btn.textContent = "Reset Stats"; btn.disabled = false;
      } else {
        showToast("Cleared " + d.deleted_records + " records", "success");
        setTimeout(()=>location.reload(), 1200);
      }
    })
    .catch(e => { showToast("Error: " + e, "error"); btn.textContent = "Reset Stats"; btn.disabled = false; });
}
document.getElementById("reset-stats-btn").addEventListener("click", resetStats);
function showToast(message, type="success") {
  const toast = document.createElement("div");
  toast.style.cssText = "position:fixed;bottom:24px;right:24px;padding:14px 20px;border-radius:10px;background:var(--card);border:1px solid var(--border);color:var(--text);font-size:13px;font-weight:500;box-shadow:0 8px 32px rgba(0,0,0,0.3);z-index:1000;transition:all 0.3s ease;border-left:3px solid " + (type==="success"?"#22c55e":"#ff2d20") + ";";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
function refreshStats() {
  fetch("/api/stats").then(r=>r.json()).then(d=>{
    document.getElementById("stat-total").textContent = d.total_downloaded;
    document.getElementById("stat-rate").textContent = d.success_rate+"%";
    document.getElementById("stat-rate-bar").style.width = d.success_rate+"%";
    document.getElementById("stat-bytes").textContent = d.total_bytes_human;
    document.getElementById("stat-active").textContent = d.active_now;
  });
}
setInterval(refreshStats, 5000);
</script>
</body>
</html>
"""

LOGS_HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live Logs - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + CSS + """
</style>
</head>
<body>
""" + NAV + """
<main class="container">
<h1 style="font-size:24px; font-weight:700; margin-bottom:24px;">Live Logs</h1>
<div style="display:flex; gap:12px; margin-bottom:24px; flex-wrap: wrap;">
<select style="width:140px; padding:8px; border-radius:10px; border:1px solid var(--border); background:var(--bg); color:var(--text);" id="log-level" onchange="filterLogs()">
<option value="ALL">All Levels</option><option value="ERROR">Error</option><option value="WARN">Warn</option><option value="INFO">Info</option><option value="SUCCESS">Success</option><option value="PROGRESS">Progress</option><option value="DEBUG">Debug</option>
</select>
<button class="btn btn-secondary" onclick="clearLogs()">Clear Display</button>
<button class="btn btn-secondary" onclick="toggleAutoScroll()" id="autoscroll-btn">Auto-scroll: ON</button>
<div style="flex:1"></div>
<span class="badge"><span class="pulse"></span>Live</span>
</div>
<div class="card" style="padding:12px; max-height:70vh; overflow-y:auto;" id="log-container">
<div id="log-lines"></div>
</div>
</main>
<script>
""" + THEME_JS + """
let autoScroll = true;
let currentLevel = "ALL";
const container = document.getElementById("log-container");
const linesDiv = document.getElementById("log-lines");
function appendLog(entry) {
  if (currentLevel !== "ALL" && entry.level !== currentLevel) return;
  const div = document.createElement("div");
  div.className = "log-line log-" + entry.level;
  div.innerHTML = "<span class="log-time">" + entry.time + "</span><span class="log-msg">" + escapeHtml(entry.message) + "</span>";
  linesDiv.appendChild(div);
  while (linesDiv.children.length > 500) linesDiv.removeChild(linesDiv.firstChild);
  if (autoScroll) container.scrollTop = container.scrollHeight;
}
function escapeHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
function filterLogs() { currentLevel = document.getElementById("log-level").value; linesDiv.innerHTML = ""; fetch("/api/logs?level=" + currentLevel + "&count=100").then(r=>r.json()).then(data=> data.forEach(appendLog)); }
function clearLogs() { linesDiv.innerHTML = ""; }
function toggleAutoScroll() { autoScroll = !autoScroll; document.getElementById("autoscroll-btn").textContent = "Auto-scroll: " + (autoScroll?"ON":"OFF"); }
const evtSource = new EventSource("/api/logs/stream");
evtSource.onmessage = function(e) { appendLog(JSON.parse(e.data)); };
filterLogs();
</script>
</body>
</html>
"""

SEARCH_HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Search - yt-dl</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + CSS + """
</style>
</head>
<body>
""" + NAV + """
<main class="container">
<h1 style="font-size:24px; font-weight:700; margin-bottom:24px;">Search Downloads</h1>
<div style="display:flex; gap:12px; margin-bottom:24px; flex-wrap: wrap;">
<input type="text" style="flex:1; min-width:250px; padding:12px 16px; border-radius:10px; border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:var(--font); font-size:14px;" id="search-input" placeholder="Search by title, video ID, or URL..." onkeyup="if(event.key==='Enter')doSearch()">
<select style="width:140px; padding:8px; border-radius:10px; border:1px solid var(--border); background:var(--bg); color:var(--text);" id="status-filter"><option value="">All Status</option><option value="queued">Queued</option><option value="downloading">Downloading</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select>
<select style="width:140px; padding:8px; border-radius:10px; border:1px solid var(--border); background:var(--bg); color:var(--text);" id="quality-filter"><option value="">All Quality</option>{% for q in qualities %}<option value="{{ q }}">{{ q }}</option>{% endfor %}</select>
<select style="width:140px; padding:8px; border-radius:10px; border:1px solid var(--border); background:var(--bg); color:var(--text);" id="date-filter"><option value="">All Time</option><option value="today">Today</option><option value="week">This Week</option><option value="month">This Month</option></select>
<button class="btn btn-primary" onclick="doSearch()">Search</button>
</div>
<div class="card">
<div style="overflow-x:auto;">
<table>
<thead><tr><th>Video</th><th>Quality</th><th>Status</th><th>Progress</th><th>Date</th><th>Actions</th></tr></thead>
<tbody id="search-results"><tr><td colspan="6" style="text-align:center; padding:40px; color:var(--text-secondary);">Enter a search term to find downloads</td></tr></tbody>
</table>
</div>
</div>
</main>
<script>
""" + THEME_JS + """
function doSearch() {
  const params = new URLSearchParams();
  const q = document.getElementById("search-input").value.trim();
  if(q) params.append("q", q);
  const status = document.getElementById("status-filter").value;
  if(status) params.append("status", status);
  const quality = document.getElementById("quality-filter").value;
  if(quality) params.append("quality", quality);
  const date = document.getElementById("date-filter").value;
  if(date) params.append("date", date);
  fetch("/api/search?" + params.toString()).then(r=>r.json()).then(data=>renderResults(data));
}
function renderResults(jobs) {
  const tbody = document.getElementById("search-results");
  if (!jobs.length) { tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--text-secondary);">No results found</td></tr>'; return; }
  tbody.innerHTML = jobs.map(j => `<tr><td><div style="display:flex;align-items:center;gap:12px;"><div style="width:48px;height:36px;background:var(--hover);border-radius:8px;overflow:hidden;">${j.video_id ? `<img src="https://i.ytimg.com/vi/${j.video_id}/mqdefault.jpg" style="width:100%;height:100%;object-fit:cover;">` : ''}</div><div><div style="font-weight:600;font-size:13px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${j.title || j.video_id || 'Unknown'}</div><div style="font-size:11px;color:var(--text-secondary);">${j.url}</div></div></div></td><td><span class="tag">${j.quality}</span></td><td><span class="tag tag-${j.status}">${j.status}</span></td><td style="width:180px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;"><span>${j.progress || 0}%</span></div><div class="progress-bar"><div class="progress-fill" style="width:${j.progress || 0}%"></div></div></td><td style="font-size:12px;color:var(--text-secondary);">${j.created_at || ''}</td><td><div style="display:flex;gap:4px;">${j.status === 'failed' ? `<button class="btn btn-secondary btn-sm" onclick="retryJob('${j.id}')">Retry</button>` : ''}<button class="btn btn-danger btn-sm" onclick="deleteJob('${j.id}')">Delete</button></div></td></tr>`).join('');
}
function retryJob(id) { fetch("/api/jobs/"+id+"/retry",{method:"POST"}).then(r=>r.json()).then(d=>{doSearch();}); }
function deleteJob(id) { if(!confirm("Delete?"))return; fetch("/api/jobs/"+id,{method:"DELETE"}).then(r=>r.json()).then(d=>{doSearch();}); }
</script>
</body>
</html>
"""

# ── Main ──────────────────────────────────────────────────────────

def find_free_port(start=5000):
    for port in range(start, start + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start

if __name__ == "__main__":
    init_db()
    cfg = load_config()
    ring_log.max_lines = cfg.get("max_log_lines", 500)
    port = find_free_port(5000)
    logger.info(f"yt-dl daemon starting on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
