"""SQLite-backed persistence for warnings, joke channels, and agent memory notes.

Replaces the old in-memory dicts so state survives a bot restart.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

_DB_PATH = config.DB_PATH


def init_db(path: str | None = None) -> None:
    global _DB_PATH
    if path:
        _DB_PATH = path
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                issued_by TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS joke_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


@contextmanager
def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_warning(guild_id: int, user_id: int, reason: str, issued_by: str = "AI Agent") -> int:
    """Log a warning and return the user's new total warning count."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO warnings (guild_id, user_id, reason, issued_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, reason, issued_by, datetime.now(timezone.utc).isoformat()),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()["c"]
    return count


def get_warnings(guild_id: int, user_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT reason, issued_by, created_at FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id",
            (guild_id, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_warnings(guild_id: int, user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))


def set_joke_channel(guild_id: int, channel_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO joke_channels (guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (guild_id, channel_id),
        )


def get_joke_channel(guild_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT channel_id FROM joke_channels WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["channel_id"] if row else None


def get_all_joke_channels() -> dict[int, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT guild_id, channel_id FROM joke_channels").fetchall()
    return {row["guild_id"]: row["channel_id"] for row in rows}


def add_note(guild_id: int, user_id: int, content: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO notes (guild_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, content, datetime.now(timezone.utc).isoformat()),
        )


def get_notes(guild_id: int, user_id: int, limit: int = 20) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT content FROM notes WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, user_id, limit),
        ).fetchall()
    return [row["content"] for row in rows]
