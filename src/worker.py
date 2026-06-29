#!/usr/bin/env python3
"""Download worker for yt-dl - executes yt-dlp and manages active downloads."""

import os
import sys
import json
import time
import signal
import shutil
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
        self.thumbnail = row["thumbnail"] if "thumbnail" in row.keys() else ""
        self.total_bytes = row["total_bytes"] if "total_bytes" in row.keys() else 0
        self.downloaded_bytes = row["downloaded_bytes"] if "downloaded_bytes" in row.keys() else 0
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
                file_path=?, file_size=?, thumbnail=?, total_bytes=?,
                downloaded_bytes=?, error_message=?,
                started_at=?, completed_at=?
            WHERE job_id=?
        """, (job.title, job.status, job.progress, job.speed, job.eta,
              job.file_path, job.file_size, job.thumbnail,
              job.total_bytes, job.downloaded_bytes,
              job.error_message,
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
    # Master toggle — if OFF, don't start any new downloads.
    if not cfg.get("downloads_enabled", True):
        return
    concurrent_limit = cfg.get("concurrent_limit", 3)
    download_dir = Path(cfg.get("download_dir", "/mnt/storage/YouTube"))

    with queue_lock:
        # Count genuinely active downloads (skip zombies with dead procs)
        active_count = 0
        dead_jobs = []
        for j in list(active_jobs.values()):
            if j.status != "downloading":
                continue
            if j.proc and j.proc.poll() is None:
                active_count += 1
            else:
                dead_jobs.append(j)
        for j in dead_jobs:
            j.status = "failed"
            j.error_message = "Process died unexpectedly"
            del active_jobs[j.job_id]
            save_job(j)
            logger.warning(f"Cleaned up zombie job: {j.job_id}")
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


def parse_bytes(s):
    """Parse byte-size strings like '142.6MiB' → bytes."""
    if not s or s == "NA":
        return 0
    s = s.strip()
    multipliers = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
                   "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    for unit, mult in multipliers.items():
        if s.endswith(unit):
            try:
                return int(float(s[:-len(unit)].strip()) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _unit_multiplier(unit):
    """Convert aria2c unit string (KiB, MiB, GiB, TiB) to byte multiplier."""
    return {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
            "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}.get(unit, 1)


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
            job.thumbnail = info.get("thumbnail") or ""
            save_job(job)
    except Exception as e:
        logger.warning(f"Info extraction failed: {e}")

    download_cmd = [
        "yt-dlp",
        "--format", format_str,
        "--retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "5",
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
        '"filepath":"%(info.filepath)s",'
        '"total_bytes":"%(progress._total_bytes_str)s",'
        '"downloaded_bytes":"%(progress._downloaded_bytes_str)s"}'
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

    # Use aria2c for multi-connection downloads when available (bypasses ISP throttling)
    aria2c_path = shutil.which("aria2c")
    use_aria2c = bool(aria2c_path)
    if use_aria2c:
        download_cmd.extend([
            "--downloader", "aria2c",
            # summary-interval=1 makes aria2c print a progress line every 1 second
            # so we can parse it for real-time progress updates
            "--downloader-args", "aria2c:-x 16 -s 16 -k 1M --file-allocation=none --summary-interval=1",
        ])
        logger.debug(f"Using aria2c downloader ({aria2c_path}) for multi-connection speed")

    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        logger.error(f"Cannot create download directory {download_dir}: {e}")
        job.status = "failed"
        job.error_message = f"Cannot create download directory: {e}"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        save_job(job)
        with queue_lock:
            if job.job_id in active_jobs:
                del active_jobs[job.job_id]
        _fire_webhook(job)
        return
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

                    total_bytes_str = data.get("total_bytes", "")
                    downloaded_bytes_str = data.get("downloaded_bytes", "")
                    # aria2c may report "NA" — don't overwrite valid stored values
                    if total_bytes_str and total_bytes_str not in ("NA", "0 B"):
                        job.total_bytes = parse_bytes(total_bytes_str)
                    if downloaded_bytes_str and downloaded_bytes_str not in ("NA", "0 B"):
                        job.downloaded_bytes = parse_bytes(downloaded_bytes_str)

                    # aria2c doesn't report percentage — always calculate from bytes when available
                    if job.total_bytes > 0 and job.downloaded_bytes > 0:
                        job.progress = round(job.downloaded_bytes / job.total_bytes * 100, 1)

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

            # Parse aria2c's native progress output format:
            # [#abc123 10MiB/100MiB(10%) CN:16 DL:5MiB ETA:18s]
            # or: [#abc123 10MiB(10%) CN:16 DL:5MiB]
            # or: [#abc123 0.5GiB/2GiB(25%) CN:16 DL:10MiB ETA:3m]
            if use_aria2c and line.startswith("[#") and "]" in line:
                try:
                    import re
                    # Extract downloaded/total bytes and percentage
                    m = re.match(r'\[#\w+\s+([\d.]+)(\w+)(?:/([\d.]+)(\w+))?\((\d+)%\)', line)
                    if m:
                        dl_val = float(m.group(1))
                        dl_unit = m.group(2)
                        pct = float(m.group(5))

                        # Parse total if present
                        if m.group(3):
                            total_val = float(m.group(3))
                            total_unit = m.group(4)
                            job.total_bytes = int(total_val * _unit_multiplier(total_unit))

                        job.downloaded_bytes = int(dl_val * _unit_multiplier(dl_unit))
                        job.progress = pct

                        # Extract speed
                        speed_match = re.search(r'DL:([\d.]+)(\w+/s)', line)
                        if speed_match:
                            job.speed = f"{speed_match.group(1)}{speed_match.group(2)}"

                        # Extract ETA
                        eta_match = re.search(r'ETA:(\w+)', line)
                        if eta_match:
                            job.eta = eta_match.group(1)

                        now = time.time()
                        if now - job.last_update_time >= 1.0:
                            save_job(job)
                            job.last_update_time = now
                            job.last_saved_progress = job.progress
                    continue
                except Exception:
                    pass  # Not a parseable aria2c line, fall through

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

        # If aria2c was used and failed, retry once with built-in downloader
        if proc.returncode != 0 and use_aria2c:
            logger.warning(f"aria2c failed (exit {proc.returncode}), retrying with built-in downloader: {job.job_id}")
            # Remove aria2c args
            aria2c_idx = download_cmd.index("--downloader")
            retry_cmd = download_cmd[:aria2c_idx] + download_cmd[aria2c_idx+4:]
            
            try:
                proc = subprocess.Popen(
                    retry_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=str(download_dir),
                    start_new_session=True,
                    env=env,
                )
                job.proc = proc
                job.error_message = None
                job.progress = 0
                job.downloaded_bytes = 0
                save_job(job)
                
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
                            total_bytes_str = data.get("total_bytes", "")
                            downloaded_bytes_str = data.get("downloaded_bytes", "")
                            if total_bytes_str and total_bytes_str not in ("NA", "0 B"):
                                job.total_bytes = parse_bytes(total_bytes_str)
                            if downloaded_bytes_str and downloaded_bytes_str not in ("NA", "0 B"):
                                job.downloaded_bytes = parse_bytes(downloaded_bytes_str)
                            if job.total_bytes > 0 and job.downloaded_bytes > 0:
                                job.progress = round(job.downloaded_bytes / job.total_bytes * 100, 1)
                            filepath = data.get("filepath", "")
                            if filepath and filepath != "NA":
                                ext = os.path.splitext(filepath)[1].lower()
                                if ext in (".mp4", ".mkv", ".mp3", ".m4a"):
                                    job.file_path = filepath
                            now = time.time()
                            if abs(job.progress - job.last_saved_progress) >= 1.0 or now - job.last_update_time >= 1.0:
                                save_job(job)
                                job.last_saved_progress = job.progress
                                job.last_update_time = now
                        except Exception:
                            pass
                    if "ERROR:" in line:
                        job.error_message = line
                        logger.error(f"yt-dlp error: {line}")
                proc.wait()
                logger.info(f"Built-in retry completed with exit code {proc.returncode}: {job.job_id}")
            except Exception as e:
                logger.exception(f"Built-in retry also failed: {e}")

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


def sync_active_downloads_with_toggle():
    """Called when the master toggle changes. Pauses all active downloads
    when toggle goes OFF, resumes them when toggle goes back ON."""
    cfg = load_config()
    enabled = cfg.get("downloads_enabled", True)
    with queue_lock:
        for job in list(active_jobs.values()):
            if not job.proc or job.proc.poll() is not None:
                continue
            try:
                if enabled and job.status == "paused" and getattr(job, '_paused_by_toggle', False):
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGCONT)
                    job.status = "downloading"
                    job._paused_by_toggle = False
                    save_job(job)
                    logger.info(f"Resumed by toggle: {job.job_id}")
                elif not enabled and job.status == "downloading":
                    os.killpg(os.getpgid(job.proc.pid), signal.SIGSTOP)
                    job.status = "paused"
                    job._paused_by_toggle = True
                    save_job(job)
                    logger.info(f"Paused by toggle: {job.job_id}")
            except (ProcessLookupError, PermissionError, AttributeError, OSError) as e:
                logger.warning(f"Toggle sync skipped {job.job_id}: {e}")
                if job.proc and job.proc.poll() is not None:
                    job.status = "failed"
                    job.error_message = f"Process died: {e}"
                    save_job(job)


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


def retry_all_failed():
    """Retry all failed, cancelled, and zombie (stuck downloading) jobs."""
    with queue_lock:
        active_ids = list(active_jobs.keys())
    
    db = get_db()
    try:
        # Retry failed + cancelled
        c = db.execute(
            "UPDATE downloads SET status='queued', progress=0, error_message=NULL, "
            "retry_count=retry_count+1 WHERE status IN ('failed', 'cancelled')"
        )
        # Retry zombies (downloading but no active process)
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            c2 = db.execute(
                "UPDATE downloads SET status='queued', progress=0, error_message=NULL, "
                f"retry_count=retry_count+1 WHERE status='downloading' AND job_id NOT IN ({placeholders})",
                active_ids
            )
        else:
            c2 = db.execute(
                "UPDATE downloads SET status='queued', progress=0, error_message=NULL, "
                "retry_count=retry_count+1 WHERE status='downloading'"
            )
        db.commit()
        total = c.rowcount + c2.rowcount
        if total > 0:
            process_queue()
        return total
    finally:
        db.close()


def cleanup_zombies_on_startup():
    """Run ONCE at daemon startup. Marks any jobs stuck in 'downloading'
    from a previous crash as 'failed'. At startup, active_jobs is empty,
    so any 'downloading' job in the DB is a zombie."""
    db = get_db()
    try:
        c = db.execute(
            "UPDATE downloads SET status='failed', "
            "error_message='Zombie: daemon restarted while downloading' "
            "WHERE status='downloading'"
        )
        db.commit()
        if c.rowcount > 0:
            logger.info(f"Cleaned up {c.rowcount} zombie job(s) from previous run")
    finally:
        db.close()
