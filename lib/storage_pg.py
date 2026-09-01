"""PostgreSQL / Supabase storage backend.

Implements the same run() interface as lib.storage_sqlite, so the rest of the app
is unchanged. When enabled, every db.run(...) executes here against Supabase
Postgres instead of the local SQLite file.

Dialect translation handled here:
  - '?' placeholders -> psycopg '%s'
  - strftime('%s','now') -> EXTRACT(EPOCH FROM now())  (epoch seconds, matching SQLite)
  - INSERT/UPDATE/DELETE return the affected/inserted row id (via RETURNING id)
  - rows are returned as dicts keyed by column name
  - ON CONFLICT ... DO UPDATE is already valid Postgres

Connection is read strictly from the environment; never hardcoded.
"""
import os
import re
from functools import lru_cache

from lib.llm import is_configured, PLACEHOLDERS

_pg_lock = __import__("threading").Lock()


def _db_url():
    return os.getenv("DATABASE_URL", "").strip()


def enabled():
    """True only when a real Postgres DATABASE_URL is configured."""
    url = _db_url()
    return bool(url) and url.lower() not in PLACEHOLDERS


def _translate(sql):
    """Convert the app's SQLite-flavored SQL into Postgres-compatible SQL."""
    out = sql
    # epoch seconds: SQLite strftime('%s','now') -> Postgres EXTRACT(EPOCH FROM now())
    out = re.sub(
        r"strftime\('%s'\s*,\s*'now'\)",
        "EXTRACT(EPOCH FROM now())",
        out,
        flags=re.IGNORECASE,
    )
    # SQLite '?' placeholders -> psycopg '%s'
    out = re.sub(r"\?", "%s", out)
    return out


def _connect():
    import psycopg

    return psycopg.connect(_db_url(), connect_timeout=15)


def run(sql, params=(), fetch="all"):
    if not enabled():
        raise RuntimeError("Supabase/Postgres storage is not configured (DATABASE_URL missing).")
    psql = _translate(sql)
    params = tuple(params) if params else ()
    with _pg_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(psql, params)
                head = psql.lstrip().upper()
                if head.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
                    # INSERT: return the new/affected row id. UPDATE/DELETE: best-effort id.
                    if head.startswith("REPLACE"):
                        conn.rollback()
                        raise RuntimeError("REPLACE INTO is not supported on Postgres storage.")
                    if head.startswith("INSERT"):
                        # Re-run with RETURNING id for a true id (safe: non-empty insert).
                        try:
                            cur.execute(psql + " RETURNING id")
                            row = cur.fetchone()
                            conn.commit()
                            return int(row[0]) if row else 0
                        except Exception:
                            conn.rollback()
                            # fall back to commit-only
                            conn = _connect()
                            with conn.cursor() as c2:
                                c2.execute(psql, params)
                            conn.commit()
                            return 0
                    conn.commit()
                    # UPDATE/DELETE: return rowcount-ish (id unknown). Callers mostly ignore.
                    conn = _connect()
                    with conn.cursor() as c2:
                        c2.execute(psql, params)
                        affected = c2.rowcount
                    conn.commit()
                    return affected
                rows = cur.fetchall()
                conn.commit()
                cols = [d.name for d in cur.description] if cur.description else []
                if fetch == "all":
                    return [dict(zip(cols, r)) for r in rows]
                return dict(zip(cols, rows[0])) if rows else None
        finally:
            conn.close()


def execute_script(sql):
    """Run a multi-statement DDL script (schema bootstrap)."""
    if not enabled():
        return
    conn = _connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in sql.split(";"):
                if stmt.strip():
                    cur.execute(stmt)
    finally:
        conn.close()
