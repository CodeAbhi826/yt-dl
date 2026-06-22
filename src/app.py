#!/usr/bin/env python3
"""yt-dl daemon - Flask backend for zero-friction YouTube downloading."""

import os
import sys
import json
import time
import sqlite3
import threading
import queue
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import re
import signal
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template, Response, stream_with_context

# Import local modules
sys.path.insert(0, str(Path(__file__).parent))
from models import (
    init_db, get_db, load_config, save_config, job_to_dict,
    human_bytes, DEFAULT_CONFIG, QUALITY_MAP, DATA_DIR, DB_PATH, CONFIG_PATH
)
from notifications import NotificationManager, set_action_callbacks
from worker import (
    process_queue, cancel_job, retry_job, active_jobs, pause_job, resume_job,
    queue_lock, set_notification_manager
)

# Logging setup
LOG_PATH = DATA_DIR / "daemon.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(str(LOG_PATH), maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, stream_handler])
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


def shutdown_handler(signum, frame):
    logger.info(f"Signal {signum} received, shutting down...")
    with queue_lock:
        for job in active_jobs.values():
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
                except (ProcessLookupError, AttributeError):
                    try:
                        job.proc.terminate()
                    except Exception:
                        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# ── Flask App ─────────────────────────────────────────────────────

app = Flask(__name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
    static_url_path="/static")

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

@app.route("/api/jobs/<job_id>")
def api_get_job(job_id):
    db = get_db()
    row = db.execute("SELECT * FROM downloads WHERE job_id=?", (job_id,)).fetchone()
    db.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job_to_dict(row))

@app.route("/api/open", methods=["POST"])
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

    total_bytes = db.execute("SELECT COALESCE(SUM(file_size), 0) as s FROM downloads WHERE status='completed'").fetchone()["s"]

    return jsonify({
        "total_downloaded": total,
        "total_success": success,
        "total_failed": failed,
        "success_rate": success_rate,
        "total_bytes": total_bytes,
        "total_bytes_human": human_bytes(total_bytes),
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
    return render_template("dashboard.html", active="dashboard", theme=cfg.get("theme", "dark"))

@app.route("/settings")
def settings_page():
    cfg = load_config()
    return render_template("settings.html", active="settings", qualities=list(QUALITY_MAP.keys()),
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
    return render_template("stats.html", active="stats", theme=cfg.get("theme", "dark"))

@app.route("/logs")
def logs_page():
    cfg = load_config()
    return render_template("logs.html", active="logs", theme=cfg.get("theme", "dark"))

@app.route("/search")
def search_page():
    cfg = load_config()
    return render_template("search.html", active="search", qualities=list(QUALITY_MAP.keys()), theme=cfg.get("theme", "dark"))

# ── SSE: Queue Stream ─────────────────────────────────────────────

@app.route("/api/queue/stream")
def stream_queue():
    def event_stream():
        last_hash = ""
        while True:
            try:
                db = get_db()
                rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT 200").fetchall()
                db.close()
                data = json.dumps([job_to_dict(r) for r in rows])
                current_hash = str(hash(data))
                if current_hash != last_hash:
                    yield "data: " + data + "\n\n"
                    last_hash = current_hash
                else:
                    yield ": unchanged\n\n"
            except Exception as e:
                logger.error(f"SSE error: {e}")
                yield ": error\n\n"
            time.sleep(1)
    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--init-only" in sys.argv:
        init_db()
        load_config()
        logger.info("Database initialized. Exiting (--init-only).")
        sys.exit(0)

    init_db()
    cfg = load_config()
    ring_log.max_lines = cfg.get("max_log_lines", 500)
    port = 5000
    logger.info(f"yt-dl daemon starting on http://127.0.0.1:{port}")
    try:
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
    except OSError as e:
        logger.error(f"Failed to bind to port {port}: {e}")
        logger.error("Is another instance already running?")
        sys.exit(1)
