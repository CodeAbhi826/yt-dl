#!/usr/bin/env python3
"""Download worker for yt-dl - executes yt-dlp and manages active downloads."""

import os
import sys
import json
import time
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


def set_notification_manager(nm):
    """Set the notification manager instance."""
    global notification_manager
    notification_manager = nm


class DownloadJob:
    """Represents an active download."""
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
        self.error_message = row["error_message"]
        self.proc = None
        self.position = 0


def save_job(job):
    """Save job state to database."""
    db = get_db()
    db.execute("""
        UPDATE downloads SET
            title=?, status=?, progress=?, speed=?, eta=?,
            file_path=?, error_message=?, started_at=?, completed_at=?
        WHERE job_id=?
    """, (job.title, job.status, job.progress, job.speed, job.eta,
          job.file_path, job.error_message,
          datetime.now().isoformat() if job.status == "downloading" else None,
          datetime.now().isoformat() if job.status in ("done", "failed", "cancelled") else None,
          job.job_id))
    db.commit()
    db.close()


def process_queue():
    """Process the download queue."""
    cfg = load_config()
    concurrent_limit = cfg.get("concurrent_limit", 3)
    download_dir = Path(cfg.get("download_dir", "/mnt/storage/YouTube"))

    with queue_lock:
        active_count = sum(1 for j in active_jobs.values() if j.status == "downloading")
        if active_count >= concurrent_limit:
            return

        # Get queued jobs from DB
        db = get_db()
        rows = db.execute(
            "SELECT * FROM downloads WHERE status='queued' ORDER BY created_at LIMIT ?",
            (concurrent_limit - active_count,)
        ).fetchall()
        db.close()

        for row in rows:
            job = DownloadJob(row)
            job.status = "downloading"
            active_jobs[job.job_id] = job
            save_job(job)
            threading.Thread(target=run_download, args=(job, download_dir), daemon=True).start()

            if notification_manager:
                notification_manager.show_queued(job.job_id, job.title, job.quality)


def run_download(job, download_dir):
    """Execute yt-dlp for a single job."""
    cfg = load_config()
    format_str = QUALITY_MAP.get(job.quality, QUALITY_MAP["720p"])

    # Extract video info first
    try:
        info_cmd = ["yt-dlp", "--format", format_str, "--dump-json", "--no-download", job.url]
        result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.strip().split("\n")[0])
            job.title = info.get("title", "Unknown")[:80]
            save_job(job)
    except Exception as e:
        logger.warning(f"Info extraction failed: {e}")

    # Build download command
    download_cmd = [
        "yt-dlp",
        "--format", format_str,
        "--merge-output-format", "mp4",
    ]

    if cfg.get("embed_thumbnail"):
        download_cmd.append("--embed-thumbnail")
    if cfg.get("embed_metadata"):
        download_cmd.append("--embed-metadata")
    if cfg.get("embed_chapters"):
        download_cmd.append("--embed-chapters")
    if cfg.get("embed_subs"):
        download_cmd.extend(["--embed-subs", "--sub-langs", "en", "--convert-subs", "srt"])

    download_cmd.extend([
        "--downloader", "aria2c",
        "--downloader-args", "aria2c:-x 16 -s 16 -k 1M",
        "--newline", "--progress", "--no-simulate",
        "-P", str(download_dir),
        "-o", "%(title)s [%(id)s].%(ext)s",
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
        )
        job.proc = proc

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            if "[download]" in line and "%" in line:
                try:
                    pct_str = line.split("%")[0].split()[-1]
                    job.progress = float(pct_str)
                    if "at " in line and "/s" in line:
                        job.speed = line.split("at ")[1].split(" ETA")[0].strip()
                    if "ETA " in line:
                        job.eta = line.split("ETA ")[1].strip()
                    save_job(job)
                    if notification_manager:
                        notification_manager.update_downloading(
                            job.job_id, job.title, job.quality, job.progress, job.speed, job.eta
                        )
                except Exception as e:
                    logger.debug(f"Parse error: {e}")

            if "ERROR:" in line:
                job.error_message = line
                logger.error(f"yt-dlp error: {line}")

        proc.wait()

        # Find downloaded file
        video_files = []
        for ext in [".mp4", ".mkv", ".webm"]:
            video_files.extend(download_dir.glob(f"*{job.video_id}*{ext}"))
        if not video_files:
            safe_title = "".join(c for c in (job.title or "") if c.isalnum() or c in " _-")[:30]
            for ext in [".mp4", ".mkv", ".webm"]:
                video_files.extend(download_dir.glob(f"*{safe_title}*{ext}"))

        if proc.returncode == 0 and video_files:
            job.status = "done"
            job.progress = 100.0
            job.file_path = str(video_files[0])
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
            if job.status == "done":
                notification_manager.show_done(job.job_id, job.title, job.quality, job.file_path)
            elif job.status == "failed":
                notification_manager.show_failed(job.job_id, job.title, job.quality, job.error_message)

        # Start next job
        threading.Thread(target=process_queue, daemon=True).start()


def cancel_job(job_id: str) -> bool:
    """Cancel a queued or active job."""
    with queue_lock:
        if job_id in active_jobs:
            job = active_jobs[job_id]
            if job.proc and job.proc.poll() is None:
                job.proc.terminate()
                try:
                    job.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    job.proc.kill()
            job.status = "cancelled"
            del active_jobs[job_id]
            save_job(job)
            if notification_manager:
                notification_manager.show_cancelled(job.job_id, job.title, job.quality)
            return True

    # Cancel queued job in DB
    db = get_db()
    c = db.execute("UPDATE downloads SET status='cancelled' WHERE job_id=? AND status='queued'", (job_id,))
    db.commit()
    db.close()
    return c.rowcount > 0


def retry_job(job_id: str) -> bool:
    """Retry a failed job."""
    db = get_db()
    c = db.execute("""
        UPDATE downloads SET status='queued', progress=0, error_message=NULL,
        retry_count=retry_count+1 WHERE job_id=?
    """, (job_id,))
    db.commit()
    db.close()
    if c.rowcount > 0:
        threading.Thread(target=process_queue, daemon=True).start()
        return True
    return False
