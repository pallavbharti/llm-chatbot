import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DATABASE_URL


def database_path():
    path = DATABASE_URL
    if path.startswith("sqlite:///"):
        path = path[len("sqlite:///"):]
    return path


@contextmanager
def get_connection():
    connection = sqlite3.connect(database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    folder = os.path.dirname(os.path.abspath(database_path()))
    os.makedirs(folder, exist_ok=True)
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
            """
        )


def is_database_ok():
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1 FROM messages LIMIT 1")
        return True
    except sqlite3.Error:
        return False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_session(session_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT session_id, system_prompt FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def get_history(session_id, limit=None):
    query = "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id"
    with get_connection() as connection:
        rows = connection.execute(query, (session_id,)).fetchall()
    if limit:
        rows = rows[-limit:]
    history = []
    for row in rows:
        item = {"role": row["role"], "content": row["content"]}
        if row["metadata"]:
            item["metadata"] = json.loads(row["metadata"])
        history.append(item)
    return history


def save_turn(session_id, system_prompt, user_message, reply, metadata):
    created_at = now_iso()
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?)",
            (session_id, system_prompt, created_at),
        )
        if system_prompt is not None:
            connection.execute(
                "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                (system_prompt, session_id),
            )
        connection.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
            (session_id, user_message, created_at),
        )
        connection.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, reply, json.dumps(metadata), now_iso()),
        )
