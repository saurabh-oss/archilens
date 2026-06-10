"""
SQLite-backed snapshot cache for the ArchiLens MCP server.

Keyed on (repo_path_hash, git_ref) — if the HEAD SHA matches what was
previously analysed, re-analysis is skipped and the cached snapshot is
returned instead.  Cache lives at ~/.archilens/snapshots.db.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from pathlib import Path

from archilens.models import ArchSnapshot

logger = logging.getLogger(__name__)

_CACHE_DB = Path.home() / ".archilens" / "snapshots.db"


def _connection() -> sqlite3.Connection:
    _CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_CACHE_DB))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            repo_key TEXT NOT NULL,
            git_ref  TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            saved_at REAL NOT NULL,
            PRIMARY KEY (repo_key, git_ref)
        )
        """
    )
    conn.commit()
    return conn


def _repo_key(repo_path: Path) -> str:
    return hashlib.sha1(str(repo_path.resolve()).encode()).hexdigest()[:16]


def load_snapshot(repo_path: Path, git_ref: str) -> ArchSnapshot | None:
    """Return cached snapshot for (repo, ref) or None on cache miss."""
    try:
        conn = _connection()
        row = conn.execute(
            "SELECT snapshot FROM snapshots WHERE repo_key=? AND git_ref=?",
            (_repo_key(repo_path), git_ref),
        ).fetchone()
        conn.close()
        if row:
            logger.debug("Cache hit: %s @ %s", repo_path.name, git_ref)
            return ArchSnapshot.model_validate_json(row[0])
    except Exception as exc:
        logger.debug("Cache miss (%s)", exc)
    return None


def save_snapshot(repo_path: Path, git_ref: str, snapshot: ArchSnapshot) -> None:
    """Persist snapshot to the SQLite cache."""
    try:
        conn = _connection()
        conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
            (_repo_key(repo_path), git_ref, snapshot.model_dump_json(), time.time()),
        )
        conn.commit()
        conn.close()
        logger.debug("Snapshot cached: %s @ %s", repo_path.name, git_ref)
    except Exception as exc:
        logger.warning("Could not save snapshot to cache: %s", exc)


def invalidate_cache(repo_path: Path) -> None:
    """Remove all cached snapshots for a repository."""
    try:
        conn = _connection()
        conn.execute("DELETE FROM snapshots WHERE repo_key=?", (_repo_key(repo_path),))
        conn.commit()
        conn.close()
        logger.info("Cache invalidated for %s", repo_path.name)
    except Exception as exc:
        logger.warning("Could not invalidate cache: %s", exc)
