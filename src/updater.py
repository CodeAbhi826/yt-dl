#!/usr/bin/env python3
"""Auto-updater for yt-dlp — runs pip install --upgrade in background."""

import sys
import subprocess
import threading
import logging

logger = logging.getLogger("yt-dl")

UPDATE_INTERVAL = 86400  # 24 hours

def _do_update():
    try:
        result = subprocess.run(
            ["yt-dlp", "-U"], capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Updating" in line or "Already up to date" in line:
                    logger.info(f"yt-dlp: {line.strip()}")
                    return
            logger.info("yt-dlp update check completed")
            return
        logger.info(f"yt-dlp self-update not available ({result.stderr.strip()[:50]}), trying pip")
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0 and "externally-managed-environment" in result.stderr:
            cmd.append("--break-system-packages")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "Successfully installed" in line or "Requirement already satisfied" in line:
                    logger.info(f"yt-dlp: {line.strip()}")
                    return
            logger.info("yt-dlp update check completed")
        else:
            logger.warning(f"yt-dlp update failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp update timed out after 120s")
    except Exception as e:
        logger.warning(f"yt-dlp update error: {e}")


def _update_loop():
    _do_update()
    event = threading.Event()
    while not event.wait(UPDATE_INTERVAL):
        _do_update()


def start_auto_updater():
    thread = threading.Thread(target=_update_loop, daemon=True, name="yt-dlp-updater")
    thread.start()
    logger.info("yt-dlp auto-updater started (check every 24h)")
