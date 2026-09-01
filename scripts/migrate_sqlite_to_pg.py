"""One-time migration: copy data from the local SQLite DB into Supabase/Postgres.

Usage (after enabling Supabase in .env):
    python3 scripts/migrate_sqlite_to_pg.py

This reads every table from your local climagent.db and upserts the rows into the
matching Supabase Postgres table, preserving ids where possible. Idempotent and safe
to re-run (ON CONFLICT DO NOTHING). Requires:
  - a local climagent.db (SQLite) with data,
  - DATABASE_URL set in .env pointing at Supabase Postgres,
  - the Supabase schema already created (paste supabase/schema.sql in the SQL editor).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from lib import storage_pg, storage_sqlite  # noqa: E402

# All tables to migrate, in dependency-safe order (parents before children).
TABLES = [
    "users", "credits", "ad_views", "sessions", "password_resets", "projects",
    "conversations", "messages", "uploaded_files", "analysis_results", "climate_plans",
    "construction_projects", "monitoring_results", "usage_records", "subscriptions",
    "ads", "memories", "activity", "monitors", "alerts", "settings", "approvals",
    "knowledge", "optins", "notifications",
]


def _sqlite_rows(table):
    conn = storage_sqlite._connect()
    try:
        return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"').fetchall()]
    finally:
        conn.close()


def _insert_pg(table, rows):
    if not rows:
        return 0, 0
    cols = list(rows[0].keys())
    colsql = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = (
        f'INSERT INTO "{table}" ({colsql}) VALUES ({placeholders}) '
        f"ON CONFLICT DO NOTHING"
    )
    conn = storage_pg._connect()
    try:
        inserted = 0
        with conn.cursor() as cur:
            for r in rows:
                vals = tuple(r[c] for c in cols)
                cur.execute(insert_sql, vals)
                if cur.rowcount > 0:
                    inserted += 1
        conn.commit()
        return len(rows), inserted
    finally:
        conn.close()


def main():
    if not storage_pg.enabled():
        print("ERROR: DATABASE_URL not configured. Add it to .env first.")
        sys.exit(1)
    print(f"Migrating SQLite -> Supabase/Postgres ({storage_pg._db_url().split('@')[-1]})")
    total_read = total_ins = 0
    for table in TABLES:
        rows = _sqlite_rows(table)
        read, ins = _insert_pg(table, rows)
        total_read += read
        total_ins += ins
        print(f"  {table:24s} read={read:6d} inserted={ins:6d}")
    print(f"Done: {total_read} rows read, {total_ins} newly inserted.")


if __name__ == "__main__":
    main()
