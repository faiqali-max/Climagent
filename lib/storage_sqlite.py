"""SQLite storage backend.

Implements the same run() interface the rest of the app expects. This is the
default/local backend. It preserves the original behavior exactly.
"""
import sqlite3
import threading
from pathlib import Path

import os

DB_PATH = Path(os.getenv("CLIMAGENT_DB", str(Path(__file__).resolve().parent.parent / "climagent.db")))
_lock = threading.Lock()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def run(sql, params=(), fetch="all"):
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(sql, params)
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
                conn.commit()
                return cur.lastrowid
            rows = cur.fetchall()
            if fetch == "all":
                return [dict(r) for r in rows]
            return dict(rows[0]) if rows else None
        finally:
            conn.close()


def execute_script(sql):
    with _lock:
        conn = _connect()
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()
