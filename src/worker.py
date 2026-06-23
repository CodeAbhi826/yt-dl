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
from datetime import datetime

from models import get_db, job_to_dict, load_config, QUALITY_MAP
from notifications import NotificationManager

logger = logging.getLogger("yt-dl")

# Global state
active_jobs = {}
job_queue = []
queue_lock = threading.Lock()
notification_manager = None
_queue_event = threading.Event()
_worker_thread = None


def set_notification_manager(nm):
    global notification_manager
    notification_manager = nm


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
              datetime.now().isoformat() if job.status == "downloading" else None,
              datetime.now().isoformat() if job.status in ("completed", "failed", "cancelled") else None,
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

            if notification_manager:
                notification_manager.show_queued(job.job_id, job.title, job.quality)


def run_download(job, download_dir):
    cfg = load_config()
    format_str = QUALITY_MAP.get(job.quality, QUALITY_MAP["720p"])

    try:
        info_cmd = ["yt-dlp", "--format", format_str, "--dump-json", "--no-download", job.url]
        result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.strip().split("\n")[0])
            job.title = info.get("title", "Unknown")[:80]
            save_job(job)
    except Exception as e:
        logger.warning(f"Info extraction failed: {e}")

    download_cmd = [
        "yt-dlp",
        "--format", format_str,
        "--merge-output-format", "mp4",
    ]

    if job.quality == "audio":
        download_cmd.extend(["--extract-audio", "--audio-format", "mp3"])

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
        '"filename":"%(info.filename)s"}'
    )

    download_cmd.extend([
        "--newline", "--progress",
        "--progress-template", progress_template,
        "-P", str(download_dir),
        "-o", "%(title)s.%(ext)s",
        job.url,
    ])

    download_dir.mkdir(parents=True, exist_ok=True)
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

                    filename = data.get("filename", "")
                    if filename:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in (".mp4", ".mkv", ".mp3", ".m4a"):
                            job.file_path = filename
                        elif not job.file_path and ext in (".webm", ".vtt"):
                            job.file_path = filename

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

            if "ERROR:" in line:
                job.error_message = line
                logger.error(f"yt-dlp error: {line}")

        proc.wait()

        if not job.file_path or not os.path.exists(job.file_path) or os.path.getsize(job.file_path) == 0:
            video_files = []
            safe_title = "".join(c for c in (job.title or "") if c.isalnum() or c in " _-")[:60]
            for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
                video_files.extend(download_dir.glob(f"*{safe_title}*{ext}"))
            if not video_files:
                for ext in [".mp4", ".mkv", ".webm", ".mp3", ".m4a"]:
                    video_files.extend(download_dir.glob(f"*{job.video_id}*{ext}"))
            if video_files:
                job.file_path = str(video_files[0])

        if proc.returncode == 0 and job.file_path:
            job.status = "completed"
            job.progress = 100.0
            if os.path.exists(job.file_path):
                job.file_size = os.path.getsize(job.file_path)
            logger.info(f"Completed: {job.file_path}")
        else:
            job.status = "failed"
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

        if notification_manager:
            if job.status == "completed":
                notification_manager.show_done(job.job_id, job.title, job.quality, job.file_path)
            elif job.status == "failed":
                notification_manager.show_failed(job.job_id, job.title, job.quality, job.error_message)

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
            del active_jobs[job_id]
            save_job(job)
            if notification_manager:
                notification_manager.show_cancelled(job.job_id, job.title, job.quality)
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
                os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                logger.info(f"Paused job: {job_id}")
                return True
    return False


def resume_job(job_id: str) -> bool:
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                os.killpg(os.getpgid(job.proc.pid), signal.SIGCONT)
                logger.info(f"Resumed job: {job_id}")
                return True
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
