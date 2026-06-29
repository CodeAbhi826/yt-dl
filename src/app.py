#!/usr/bin/env python3
"""yt-dl daemon - Flask backend for zero-friction YouTube downloading."""

import os
import sys
import json
import time
import uuid
import hmac
import hashlib
import sqlite3
import threading
import queue
import subprocess
import logging
import functools
from logging.handlers import RotatingFileHandler
from contextlib import closing
import re
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify, render_template, Response, stream_with_context

# Import local modules
sys.path.insert(0, str(Path(__file__).parent))
from models import (
    init_db, get_db, load_config, save_config, job_to_dict,
    human_bytes, DEFAULT_CONFIG, QUALITY_MAP, DATA_DIR, DB_PATH, CONFIG_PATH
)
COOKIES_PATH = DATA_DIR / "cookies.txt"
from worker import (
    process_queue, cancel_job, retry_job, retry_all_failed, active_jobs, pause_job, resume_job,
    queue_lock, save_job, sync_active_downloads_with_toggle
)
from updater import start_auto_updater
from _version import __version__

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

class RingBufferLogHandler(logging.Handler):
    def __init__(self, max_lines=500):
        super().__init__()
        self.max_lines = max_lines
        self.buffer = deque(maxlen=max_lines)
        self.buf_lock = threading.Lock()
        self.subscribers = []
        self.sub_lock = threading.Lock()

    def emit(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "message": record.getMessage(),
            "level": record.levelname,
        }
        with self.buf_lock:
            self.buffer.append(entry)
        self._notify(entry)

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
        with self.buf_lock:
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
ring_log.setLevel(logging.INFO)
logger.addHandler(ring_log)

# Suppress Werkzeug access logs
werkzeug_log = logging.getLogger("werkzeug")
werkzeug_log.setLevel(logging.WARNING)


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

# Auth
API_KEY = os.environ.get("YTDL_API_KEY", "")

def require_auth(f):
    if not API_KEY:
        return f
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {API_KEY}"
        if not hmac.compare_digest(auth, expected):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def normalize_url(url):
    """Normalize URL for dedup. Strips query params (except v=),
    normalizes domain, removes trailing slashes."""
    from urllib.parse import urlparse, parse_qs, urlencode
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    if 'youtube.com' in netloc:
        qs = parse_qs(parsed.query)
        if 'v' in qs:
            query = urlencode({'v': qs['v'][0]})
        else:
            query = ''
    else:
        query = parsed.query
    return f"{parsed.scheme}://{netloc}{parsed.path}" + (f"?{query}" if query else "")


def _fetch_metadata_async(job_id, url):
    """Background fetch of title + thumbnail + video_id via yt-dlp --dump-json."""
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
            video_id = ""
            m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
            if m:
                video_id = m.group(1)
            elif info.get("id"):
                video_id = str(info.get("id"))[:30]

            db = get_db()
            try:
                db.execute(
                    "UPDATE downloads SET title=?, video_id=?, thumbnail=? WHERE job_id=? AND title=''",
                    (title, video_id, thumbnail, job_id)
                )
                db.commit()
            finally:
                db.close()
            logger.info(f"Metadata fetched for {job_id}: {title}")
    except Exception as e:
        logger.debug(f"Metadata fetch failed for {job_id}: {e}")


# ── Flask App ─────────────────────────────────────────────────────

app = Flask(__name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
    static_url_path="/static")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

@app.route("/api/info")
def api_info():
    cfg = load_config()
    return jsonify({
        "dbus_available": False,
        "version": __version__,
        "auth_required": bool(API_KEY),
        "downloads_enabled": cfg.get("downloads_enabled", True),
    })

@app.route("/api/extension/heartbeat", methods=["POST"])
@require_auth
def api_extension_heartbeat():
    return jsonify({"ok": True})

@app.route("/api/extension/register", methods=["POST"])
@require_auth
def api_extension_register():
    logger.info("Extension connected")
    return jsonify({"ok": True})

@app.route("/api/extension/unregister", methods=["POST"])
@require_auth
def api_extension_unregister():
    logger.info("Extension disconnected")
    return jsonify({"ok": True})

@app.route("/api/queue")
def api_queue():
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid pagination params"}), 400
    db = get_db()
    rows = db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    db.close()
    return jsonify([job_to_dict(r) for r in rows])

@app.route("/api/jobs/<job_id>/retry", methods=["POST"])
@require_auth
def api_retry_job(job_id):
    if retry_job(job_id):
        logger.info(f"Job retried: {job_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404

@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@require_auth
def api_cancel_job(job_id):
    if cancel_job(job_id):
        logger.info(f"Job cancelled: {job_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found"}), 404

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
    """Pause all active downloads AND queued jobs. Queued jobs are marked
    as 'paused' in the DB so the worker won't pick them up."""
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
            except (ProcessLookupError, PermissionError, AttributeError, OSError) as e:
                logger.warning(f"Failed to pause {job.job_id}: {e}")
    db = get_db()
    try:
        c = db.execute("UPDATE downloads SET status='paused' WHERE status='queued'")
        db.commit()
        queued_paused = c.rowcount
        paused += queued_paused
        if queued_paused:
            logger.info(f"Marked {queued_paused} queued jobs as paused")
    finally:
        db.close()
    logger.info(f"Paused {paused} jobs total")
    return jsonify({"paused": paused})

@app.route("/api/jobs/resume-all", methods=["POST"])
@require_auth
def api_resume_all():
    """Resume every paused job (active + DB-marked). Inline logic to avoid deadlock."""
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
            except (ProcessLookupError, PermissionError, AttributeError, OSError) as e:
                logger.warning(f"Failed to resume {job.job_id}: {e}")
    db = get_db()
    try:
        c = db.execute("UPDATE downloads SET status='queued' WHERE status='paused'")
        db.commit()
        db_resumed = c.rowcount
        resumed += db_resumed
        if db_resumed:
            logger.info(f"Marked {db_resumed} DB-paused jobs as queued")
    finally:
        db.close()
    logger.info(f"Resumed {resumed} jobs total")
    if resumed > 0:
        process_queue()
    return jsonify({"resumed": resumed})

@app.route("/api/jobs/retry-all", methods=["POST"])
@require_auth
def api_retry_all():
    total = retry_all_failed()
    logger.info(f"Retried {total} failed/zombie jobs")
    return jsonify({"retried": total})

@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@require_auth
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
@require_auth
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

@app.route("/api/bulk/retry", methods=["POST"])
@require_auth
def api_bulk_retry():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"retried": 0})
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    c = db.execute(
        f"UPDATE downloads SET status='queued', progress=0, error_message=NULL, retry_count=retry_count+1 "
        f"WHERE job_id IN ({placeholders}) AND status IN ('failed', 'cancelled')",
        tuple(ids)
    )
    db.commit()
    db.close()
    if c.rowcount > 0:
        process_queue()
    return jsonify({"retried": c.rowcount})

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_config())

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
    "downloads_enabled": bool,
    "duplicate_detection": str,
}

VALID_QUALITIES = {"144p","240p","360p","480p","720p","1080p","1440p","2160p","best","audio"}

@app.route("/api/settings", methods=["PUT"])
@require_auth
def api_update_settings():
    cfg = load_config()
    updates = request.get_json() or {}
    for key, value in updates.items():
        if key not in ALLOWED_SETTINGS:
            continue
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
    if "max_log_lines" in updates and isinstance(cfg["max_log_lines"], int) and cfg["max_log_lines"] > 0:
        with ring_log.buf_lock:
            new_buffer = deque(maxlen=cfg["max_log_lines"])
            new_buffer.extend(ring_log.buffer)
            ring_log.buffer = new_buffer
        ring_log.max_lines = cfg["max_log_lines"]
    return jsonify(cfg)

@app.route("/api/settings/reset", methods=["POST"])
@require_auth
def api_reset_settings():
    save_config(DEFAULT_CONFIG.copy())
    return jsonify(DEFAULT_CONFIG.copy())

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

    if was_enabled != enabled:
        sync_active_downloads_with_toggle()

    if enabled:
        process_queue()
    return jsonify({"downloads_enabled": enabled})

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
            tuple(params) + (limit, offset)
        ).fetchall()
    finally:
        db.close()

    return jsonify({
        "jobs": [job_to_dict(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit
    })


@app.route("/api/settings/cookies", methods=["GET"])
def api_cookies_status():
    return jsonify({"exists": COOKIES_PATH.exists(), "size": COOKIES_PATH.stat().st_size if COOKIES_PATH.exists() else 0})

@app.route("/api/settings/cookies", methods=["POST"])
@require_auth
def api_upload_cookies():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f.save(str(COOKIES_PATH))
    logger.info("Cookies file uploaded")
    return jsonify({"ok": True})

@app.route("/api/settings/cookies", methods=["DELETE"])
@require_auth
def api_delete_cookies():
    if COOKIES_PATH.exists():
        COOKIES_PATH.unlink()
        logger.info("Cookies file deleted")
    return jsonify({"ok": True})

@app.route("/api/stats")
def api_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM downloads").fetchone()["c"]
    success = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='completed'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='failed'").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='downloading'").fetchone()["c"]
    daily = db.execute("SELECT date(created_at) as day, COUNT(*) as cnt FROM downloads WHERE created_at >= date('now', '-7 days') GROUP BY day ORDER BY day").fetchall()
    total_bytes = db.execute("SELECT COALESCE(SUM(file_size), 0) as s FROM downloads WHERE status='completed'").fetchone()["s"]
    cancelled = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='cancelled'").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) as c FROM downloads WHERE status IN ('queued','downloading')").fetchone()["c"]
    db.close()

    today = datetime.now(timezone.utc).date()
    day_map = {r["day"]: r["cnt"] for r in daily}
    daily_bars = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_str = day.isoformat()
        cnt = day_map.get(day_str, 0)
        daily_bars.append({
            "label": day_str[5:],
            "pct": int(cnt / max(max(day_map.values(), default=1), 1) * 100),
            "count": cnt
        })

    success_rate = round(success / total * 100, 1) if total > 0 else 0
    other = total - success - failed - cancelled - active
    status_breakdown = [
        {"label": "Completed", "count": success, "color": "#22c55e", "pct": round(success/total*100,1) if total else 0},
        {"label": "Failed", "count": failed, "color": "#dc2626", "pct": round(failed/total*100,1) if total else 0},
        {"label": "Cancelled", "count": cancelled, "color": "#f59e0b", "pct": round(cancelled/total*100,1) if total else 0},
        {"label": "Active/Queued", "count": active, "color": "#3ea6ff", "pct": round(active/total*100,1) if total else 0},
        {"label": "Other", "count": other, "color": "#666666", "pct": round(other/total*100,1) if total else 0},
    ]

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
@require_auth
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
    try:
        count = int(request.args.get("count", 100))
        count = max(1, min(count, 1000))
    except (ValueError, TypeError):
        return jsonify({"error": "count must be an integer"}), 400
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
@require_auth
def api_add_job():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    cfg = load_config()
    quality = data.get("quality", cfg.get("default_quality", "720p"))
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not re.match(r"https?://", url):
        return jsonify({"error": "Invalid URL"}), 400

    video_id = ""
    m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})", url)
    if m:
        video_id = m.group(1)

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]

    # Dedup check (Phase 7a)
    cfg_dedup = cfg.get("duplicate_detection", "strict")
    if cfg_dedup != "off":
        nurl = normalize_url(url)
        db = get_db()
        try:
            if cfg_dedup == "lenient":
                existing = db.execute("SELECT job_id, status, title FROM downloads WHERE (url=? OR url=?) AND status='completed' ORDER BY created_at DESC LIMIT 1", (url, nurl)).fetchone()
            else:
                existing = db.execute("SELECT job_id, status, title FROM downloads WHERE (url=? OR url=?) ORDER BY created_at DESC LIMIT 1", (url, nurl)).fetchone()
            if existing:
                return jsonify({
                    "duplicate": True,
                    "existing_job_id": existing["job_id"],
                    "existing_status": existing["status"],
                    "message": f"Already exists as {existing['status']}"
                }), 409
        finally:
            db.close()

    # Check if this is a playlist
    is_playlist = "list=" in url or "/playlist/" in url
    if is_playlist:
        try:
            result = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--dump-json", "--no-download",
                 "--playlist-end", "50", url],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                entries = [json.loads(line) for line in result.stdout.strip().split("\n") if line.strip()]
                playlist_title = entries[0].get("playlist_title", "Playlist") if entries else "Playlist"
                logger.info(f"Playlist detected: \"{playlist_title}\" ({len(entries)} videos)")

                db = get_db()
                existing_urls = {r["url"] for r in db.execute("SELECT DISTINCT url FROM downloads WHERE status='completed'").fetchall()}

                count = 0
                for entry in entries[:cfg.get("playlist_limit", 200)]:
                    eid = entry.get("id")
                    if not eid:
                        continue
                    eurl = entry.get("url") or entry.get("webpage_url") or entry.get("original_url")
                    if not eurl:
                        if entry.get("ie_key") == "Youtube" or "youtube" in (entry.get("extractor_key", "") or "").lower():
                            eurl = f"https://www.youtube.com/watch?v={eid}"
                        else:
                            logger.warning(f"Skipping playlist entry with no URL: {entry}")
                            continue
                    if eurl in existing_urls:
                        continue
                    etitle = (entry.get("title") or "Unknown")[:80]
                    ejob_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                    db.execute(
                        "INSERT INTO downloads (job_id, video_id, url, quality, status, title) VALUES (?, ?, ?, ?, 'queued', ?)",
                        (ejob_id, eid, eurl, quality, etitle)
                    )
                    count += 1

                db.commit()
                db.close()
                logger.info(f"Playlist queued: {count} new videos (skipped {len(entries) - count} existing)")
                process_queue()
                return jsonify({"status": "playlist", "title": playlist_title, "total": len(entries), "added": count, "skipped": len(entries) - count})

        except subprocess.TimeoutExpired:
            logger.warning("Playlist detection timed out, treating as single video")
        except Exception as e:
            logger.warning(f"Playlist detection failed: {e}")

    job_id = f"job_{int(time.time() * 1000)}_{url_hash}"
    title = data.get("title", "")
    db = get_db()
    db.execute("INSERT INTO downloads (job_id, video_id, url, quality, status, title) VALUES (?, ?, ?, ?, 'queued', ?)",
               (job_id, video_id, url, quality, title))
    db.commit()
    db.close()

    logger.info(f"Download added [{quality}] {title or video_id or url}")

    threading.Thread(
        target=_fetch_metadata_async,
        args=(job_id, url),
        daemon=True,
        name=f"meta-{job_id[:8]}"
    ).start()

    process_queue()

    return jsonify({"job_id": job_id, "status": "queued"})

@app.route("/api/bulk/add", methods=["POST"])
@require_auth
def api_bulk_add():
    """Add multiple URLs at once. Each URL is validated and deduplicated
    against completed jobs. Playlists are NOT expanded here — they're
    inserted as a single job and handled by the normal queue process."""
    data = request.get_json() or {}
    urls = data.get("urls", [])
    quality = data.get("quality")
    cfg = load_config()
    if not quality:
        quality = cfg.get("default_quality", "720p")

    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "No URLs provided"}), 400

    if len(urls) > 100:
        return jsonify({"error": "Maximum 100 URLs per request"}), 400

    added = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    results = []

    dedup_mode = cfg.get("duplicate_detection", "strict")
    added_job_ids = []

    db = get_db()
    try:
        for url in urls:
            url = (url or "").strip()
            if not url:
                continue
            if not re.match(r"https?://", url):
                skipped_invalid += 1
                results.append({"url": url, "status": "invalid"})
                continue

            # Dedup check
            if dedup_mode != "off":
                nurl = normalize_url(url)
                if dedup_mode == "lenient":
                    existing = db.execute("SELECT 1 FROM downloads WHERE (url=? OR url=?) AND status='completed' LIMIT 1", (url, nurl)).fetchone()
                else:
                    existing = db.execute("SELECT 1 FROM downloads WHERE (url=? OR url=?) LIMIT 1", (url, nurl)).fetchone()
                if existing:
                    skipped_duplicate += 1
                    results.append({"url": url, "status": "duplicate"})
                    continue

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
            added += 1
            added_job_ids.append((job_id, url))
            results.append({"url": url, "status": "added", "job_id": job_id})

        db.commit()
    finally:
        db.close()

    logger.info(f"Bulk add: {added} added, {skipped_duplicate} duplicates, {skipped_invalid} invalid")
    if added > 0:
        for jid, u in added_job_ids:
            threading.Thread(
                target=_fetch_metadata_async,
                args=(jid, u),
                daemon=True,
                name=f"meta-{jid[:8]}"
            ).start()
        process_queue()
    return jsonify({
        "added": added,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "total": len(urls),
        "results": results
    })

# ── Page Routes ───────────────────────────────────────────────────

@app.route("/")
def dashboard():
    cfg = load_config()
    return render_template("dashboard.html", active="dashboard", theme=cfg.get("theme", "dark"))

@app.route("/downloads")
def downloads_page():
    cfg = load_config()
    return render_template("downloads.html", active="downloads", theme=cfg.get("theme", "dark"))


@app.route("/settings")
def settings_page():
    cfg = load_config()
    return render_template("settings.html", active="settings",
                           download_dir=cfg.get("download_dir", "/mnt/storage/YouTube"),
                           concurrent_limit=cfg.get("concurrent_limit", 3),
                           output_pattern=cfg.get("output_pattern", "%(title)s.%(ext)s"),
                           default_quality=cfg.get("default_quality", "720p"),
                           playlist_limit=cfg.get("playlist_limit", 200),
                           webhook_url=cfg.get("webhook_url", ""),
                           embed_metadata=cfg.get("embed_metadata", True),
                           embed_thumbnail=cfg.get("embed_thumbnail", True),
                           embed_chapters=cfg.get("embed_chapters", True),
                           embed_subs=cfg.get("embed_subs", True),
                           theme=cfg.get("theme", "dark"),
                           duplicate_detection=cfg.get("duplicate_detection", "strict"))

@app.route("/stats")
def stats_page():
    cfg = load_config()
    return render_template("stats.html", active="stats", theme=cfg.get("theme", "dark"))

@app.route("/logs")
def logs_page():
    cfg = load_config()
    return render_template("logs.html", active="logs", theme=cfg.get("theme", "dark"))

# ── SSE: Queue Stream (single-poller broadcaster) ────────────────

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

@app.route("/api/queue/stream")
def stream_queue():
    def event_stream():
        q = queue_broadcaster.subscribe()
        try:
            yield "data: " + queue_broadcaster._last_data + "\n\n"
            while True:
                data = q.get(timeout=30)
                yield "data: " + data + "\n\n"
        except:
            pass
        finally:
            queue_broadcaster.unsubscribe(q)
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
        # Migrate old "done" status to "completed"
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE downloads SET status='completed' WHERE status='done'")
        conn.commit()
        conn.close()
        logger.info("Database initialized. Exiting (--init-only).")
        sys.exit(0)

    init_db()
    # Migrate old "done" status to "completed"
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE downloads SET status='completed', progress=100.0 WHERE status='done'")
    conn.commit()
    conn.close()
    cfg = load_config()
    ring_log.max_lines = cfg.get("max_log_lines", 500)
    start_auto_updater()
    queue_broadcaster.start()
    # Kick the queue worker on startup to process any queued jobs
    process_queue()
    host = os.environ.get("YTDL_BIND", "127.0.0.1")
    port = int(os.environ.get("YTDL_PORT", 5000))
    logger.info(f"yt-dl v{__version__} started — http://{host}:{port}")
    logger.info(f"Downloads directory: {cfg.get('download_dir', '/mnt/storage/YouTube')}")
    logger.info(f"Concurrent downloads: {cfg.get('concurrent_limit', 3)}")
    logger.info("Notifications handled by browser extension")
    try:
        app.run(host=host, port=port, threaded=True, debug=False)
    except OSError as e:
        logger.error(f"Failed to bind to port {port}: {e}")
        logger.error("Is another instance already running?")
        sys.exit(1)
