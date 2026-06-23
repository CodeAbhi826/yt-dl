#!/usr/bin/env python3
"""Database and configuration models for yt-dl."""

import os
import sys
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("yt-dl")

# Paths - all runtime data goes to ~/.local/share/yt-dl/
DATA_DIR = Path.home() / ".local/share/yt-dl"
DB_PATH = DATA_DIR / "yt-dl.db"
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "daemon.log"

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
}

QUALITY_MAP = {
    "144p": "bestvideo[vcodec^=vp9][height<=144]+bestaudio/best[height<=144]",
    "240p": "bestvideo[vcodec^=vp9][height<=240]+bestaudio/best[height<=240]",
    "360p": "bestvideo[vcodec^=vp9][height<=360]+bestaudio/best[height<=360]",
    "480p": "bestvideo[vcodec^=vp9][height<=480]+bestaudio/best[height<=480]",
    "720p": "bestvideo[vcodec^=vp9][height<=720]+bestaudio/best[height<=720]",
    "1080p": "bestvideo[vcodec^=vp9][height<=1080]+bestaudio/best[height<=1080]",
    "1440p": "bestvideo[vcodec^=vp9][height<=1440]+bestaudio/best[height<=1440]",
    "2160p": "bestvideo[vcodec^=vp9][height<=2160]+bestaudio/best[height<=2160]",
    "best": "bestvideo[vcodec^=vp9]+bestaudio/best",
    "audio": "bestaudio/best[audioonly]",
}


def init_db():
    """Initialize SQLite database with all required columns."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            video_id TEXT,
            title TEXT,
            url TEXT NOT NULL,
            quality TEXT DEFAULT "720p",
            status TEXT DEFAULT "queued",
            progress REAL DEFAULT 0,
            speed TEXT,
            eta TEXT,
            file_path TEXT,
            file_size INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            retry_count INTEGER DEFAULT 0
        )
    """)

    # Migrate: add missing columns if table exists from old schema
    c.execute("PRAGMA table_info(downloads)")
    existing_cols = [row[1] for row in c.fetchall()]

    migrations = {
        'video_id': 'TEXT',
        'title': 'TEXT',
        'file_size': 'INTEGER DEFAULT 0',
        'retry_count': 'INTEGER DEFAULT 0',
        'error_message': 'TEXT',
        'started_at': 'TIMESTAMP',
        'completed_at': 'TIMESTAMP',
    }
    for col, dtype in migrations.items():
        if col not in existing_cols:
            c.execute(f"ALTER TABLE downloads ADD COLUMN {col} {dtype}")
            logger.info(f"Migrated DB: added column {col}")

    c.execute("CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_downloads_created_at ON downloads(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_downloads_video_id ON downloads(video_id)")

    conn.commit()
    conn.close()


def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_config() -> dict:
    """Load config from JSON, merge with defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                return cfg
        except Exception:
            pass
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    """Save config to JSON."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def job_to_dict(row) -> dict:
    """Convert sqlite3.Row to dict for API responses."""
    return {
        "id": row["job_id"],
        "video_id": row["video_id"],
        "title": row["title"] or "",
        "url": row["url"],
        "quality": row["quality"],
        "status": row["status"],
        "progress": row["progress"] or 0,
        "speed": row["speed"],
        "eta": row["eta"],
        "file_path": row["file_path"],
        "file_size": row["file_size"] or 0,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "retry_count": row["retry_count"] or 0,
        "error_message": row["error_message"],
    }


def human_bytes(b: int) -> str:
    """Convert bytes to human readable string."""
    if b == 0:
        return "0.0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
