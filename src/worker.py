#!/usr/bin/env python3
"""Download worker for yt-dl - executes yt-dlp and manages active downloads."""

import os
import sys
import json
import time
import signal
import sqlite3
import subprocess
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone

from models import get_db, job_to_dict, load_config, QUALITY_MAP, DATA_DIR

COOKIES_PATH = DATA_DIR / "cookies.txt"

logger = logging.getLogger("yt-dl")


import urllib.request
import urllib.error

def _fire_webhook(job):
    if job.status not in ("completed", "failed"):
        return
    try:
        cfg = load_config()
        url = cfg.get("webhook_url", "").strip()
        if not url:
            return
        payload = json.dumps({
            "event": job.status,
            "job_id": job.job_id,
            "title": job.title,
            "quality": job.quality,
            "file_path": job.file_path,
            "file_size": job.file_size,
            "error": job.error_message,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        logger.warning(f"Webhook error: {e}")

# Global state
active_jobs = {}
job_queue = []
queue_lock = threading.Lock()
_queue_event = threading.Event()
_worker_thread = None


class DownloadJob:
    def __init__(self, row):
        self.job_id = row["job_id"]
        self.video_id = row["video_id"]
        self.title = row["title"] or ""
        self.url = row["url"]
        self.quality = row["quality"]
        self.status = row["status"]
        self.progress = row["progress"] or 0
        self.speed = row["speed"]
        self.eta = row["eta"]
        self.file_path = row["file_path"]
        self.file_size = row["file_size"] or 0
        self.error_message = row["error_message"]
        self.started_at = row["started_at"]
        self.completed_at = row["completed_at"]
        self.proc = None
        self.position = 0
        self.last_saved_progress = 0.0
        self.last_update_time = time.time()


def save_job(job):
    db = None
    try:
        db = get_db()
        db.execute("""
            UPDATE downloads SET
                title=?, status=?, progress=?, speed=?, eta=?,
                file_path=?, file_size=?, error_message=?,
                started_at=?, completed_at=?
            WHERE job_id=?
        """, (job.title, job.status, job.progress, job.speed, job.eta,
              job.file_path, job.file_size, job.error_message,
              job.started_at, job.completed_at,
              job.job_id))
        db.commit()
    except Exception as e:
        logger.error(f"Failed to save job {job.job_id}: {e}")
    finally:
        if db:
            db.close()


def _start_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, name="process-queue", daemon=True)
        _worker_thread.start()


def _worker_loop():
    while True:
        _queue_event.wait()
        _queue_event.clear()
        try:
            _process_queue()
        except Exception as e:
            logger.exception(f"Queue processing error: {e}")


def process_queue():
    _queue_event.set()
    _start_worker()


def _process_queue():
    cfg = load_config()
    concurrent_limit = cfg.get("concurrent_limit", 3)
    download_dir = Path(cfg.get("download_dir", "/mnt/storage/YouTube"))

    with queue_lock:
        active_count = sum(1 for j in active_jobs.values() if j.status == "downloading")
        if active_count >= concurrent_limit:
            return

        db = get_db()
        try:
            rows = db.execute(
                "SELECT * FROM downloads WHERE status='queued' ORDER BY created_at LIMIT ?",
                (concurrent_limit - active_count,)
            ).fetchall()
        finally:
            db.close()

        for row in rows:
            job = DownloadJob(row)
            job.status = "downloading"
            active_jobs[job.job_id] = job
            save_job(job)
            threading.Thread(
                target=run_download, args=(job, download_dir),
                name=f"download-{job.job_id[:8]}", daemon=True
            ).start()


def run_download(job, download_dir):
    cfg = load_config()
    format_str = QUALITY_MAP.get(job.quality, QUALITY_MAP["720p"])

    # Some yt-dlp extractors load phantomjs which crashes with OpenSSL 3.x
    # provider errors. Only apply the workaround if user hasn't set it.
    env = {**os.environ}
    if "OPENSSL_CONF" not in os.environ:
        env["OPENSSL_CONF"] = "/dev/null"

    try:
        info_cmd = ["yt-dlp", "--dump-json", "--no-download", job.url]
        result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.strip().split("\n")[0])
            job.title = info.get("title", "Unknown")[:80]
            save_job(job)
    except Exception as e:
        logger.warning(f"Info extraction failed: {e}")

    download_cmd = [
        "yt-dlp",
        "--format", format_str,
    ]

    if job.quality == "audio":
        download_cmd.extend(["--extract-audio", "--audio-format", "mp3"])
    else:
        download_cmd.extend(["--merge-output-format", "mp4"])

    if cfg.get("embed_thumbnail"):
        download_cmd.append("--embed-thumbnail")
    if cfg.get("embed_metadata"):
        download_cmd.append("--embed-metadata")
    if cfg.get("embed_chapters"):
        download_cmd.append("--embed-chapters")
    if cfg.get("embed_subs"):
        download_cmd.extend(["--embed-subs", "--sub-langs", "en", "--convert-subs", "srt"])

    progress_template = (
        '{"percent":"%(progress._percent_str)s",'
        '"speed":"%(progress._speed_str)s",'
        '"eta":"%(progress._eta_str)s",'
        '"filepath":"%(info.filepath)s"}'
    )

    download_cmd.extend([
        "--newline", "--progress",
        "--progress-template", progress_template,
        "-P", str(download_dir),
        "-o", cfg.get("output_pattern", "%(title)s.%(ext)s"),
        job.url,
    ])

    if COOKIES_PATH.exists():
        download_cmd.extend(["--cookies", str(COOKIES_PATH)])

    download_dir.mkdir(parents=True, exist_ok=True)
    if not job.started_at:
        job.started_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"Starting download: {job.job_id} -> {job.title}")

    try:
        proc = subprocess.Popen(
            download_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(download_dir),
            start_new_session=True,
            env=env,
        )
        job.proc = proc

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    percent_str = data.get("percent", "0%")
                    job.progress = float(percent_str.rstrip("%"))
                    job.speed = data.get("speed", "")
                    job.eta = data.get("eta", "")

                    filepath = data.get("filepath", "")
                    if filepath and filepath != "NA":
                        ext = os.path.splitext(filepath)[1].lower()
                        if ext in (".mp4", ".mkv", ".mp3", ".m4a"):
                            job.file_path = filepath
                        elif not job.file_path and ext in (".webm", ".vtt"):
                            job.file_path = filepath

                    now = time.time()
                    progress_changed = abs(job.progress - job.last_saved_progress) >= 1.0
                    time_elapsed = now - job.last_update_time >= 1.0

                    if progress_changed or time_elapsed:
                        save_job(job)
                        job.last_saved_progress = job.progress
                        job.last_update_time = now
                except Exception as e:
                    logger.debug(f"Progress parse error: {e}")
                continue

            if line.startswith("[download] Destination: "):
                dest = line.split("[download] Destination: ")[-1].strip()
                job.file_path = dest
                continue

            if "[download]" in line and "has already been downloaded" in line:
                dest = line.split("[download] ")[-1].split(" has already")[0].strip()
                job.file_path = dest
                continue

            if "ERROR:" in line:
                job.error_message = line
                logger.error(f"yt-dlp error: {line}")

        proc.wait()

        with queue_lock:
            already_cancelled = job.job_id not in active_jobs or job.status == "cancelled"
        if already_cancelled:
            return

        if not job.file_path or job.file_path == "NA" or not os.path.exists(job.file_path) or os.path.getsize(job.file_path) == 0:
            video_files = []
            safe_title = "".join(c for c in (job.title or "") if c.isalnum() or c in " _-.")[:60]
            for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
                video_files.extend(download_dir.rglob(f"*{safe_title}*{ext}"))
            if not video_files:
                for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
                    video_files.extend(download_dir.rglob(f"*{job.video_id}*{ext}"))
            if video_files:
                job.file_path = str(video_files[0])

        if proc.returncode == 0 and job.file_path:
            job.status = "completed"
            job.progress = 100.0
            job.completed_at = datetime.now(timezone.utc).isoformat()
            if os.path.exists(job.file_path):
                job.file_size = os.path.getsize(job.file_path)
            logger.info(f"Completed: {job.file_path}")
        else:
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            if not job.error_message:
                job.error_message = f"yt-dlp exited {proc.returncode}"
            logger.error(f"Failed: {job.error_message}")

    except Exception as e:
        logger.exception(f"Download crashed: {job.job_id}")
        job.status = "failed"
        job.error_message = str(e)

    finally:
        with queue_lock:
            if job.job_id in active_jobs:
                del active_jobs[job.job_id]
        save_job(job)

        _fire_webhook(job)
        process_queue()


def cancel_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
                    try:
                        job.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(job.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            del active_jobs[job_id]
            save_job(job)
            return True

    db = get_db()
    try:
        c = db.execute("UPDATE downloads SET status='cancelled' WHERE job_id=? AND status='queued'", (job_id,))
        db.commit()
        return c.rowcount > 0
    finally:
        db.close()


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


def retry_job(job_id: str) -> bool:
    db = get_db()
    try:
        c = db.execute("""
            UPDATE downloads SET status='queued', progress=0, error_message=NULL,
            retry_count=retry_count+1 WHERE job_id=?
        """, (job_id,))
        db.commit()
        if c.rowcount > 0:
            process_queue()
            return True
        return False
    finally:
        db.close()
