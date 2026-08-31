import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.getenv("CLIMAGENT_DB", str(Path(__file__).resolve().parent.parent / "climagent.db")))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  meta TEXT DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  run_id TEXT,
  kind TEXT NOT NULL,
  agent TEXT,
  tool TEXT,
  input TEXT,
  output TEXT,
  status TEXT,
  error TEXT,
  duration_ms REAL,
  tokens INTEGER,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_user ON activity(user_id);
CREATE INDEX IF NOT EXISTS idx_act_run ON activity(run_id);

CREATE TABLE IF NOT EXISTS monitors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  query TEXT NOT NULL,
  lat REAL,
  lon REAL,
  interval_min REAL NOT NULL DEFAULT 60,
  max_runs INTEGER NOT NULL DEFAULT 30,
  run_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  last_result TEXT DEFAULT '',
  last_run_at REAL,
  next_run_at REAL NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  monitor_id INTEGER,
  message TEXT NOT NULL,
  level TEXT NOT NULL,
  read INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  decided_at REAL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  plan TEXT NOT NULL DEFAULT 'FREE',
  credit_balance INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS credits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credits_user ON credits(user_id);

CREATE TABLE IF NOT EXISTS ad_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  ad_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'view',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_adviews_user ON ad_views(user_id);
CREATE INDEX IF NOT EXISTS idx_adviews_user_time ON ad_views(user_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

CREATE TABLE IF NOT EXISTS password_resets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  expires_at REAL NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  project_id INTEGER,
  title TEXT NOT NULL DEFAULT 'New conversation',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS uploaded_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user ON uploaded_files(user_id);

CREATE TABLE IF NOT EXISTS analysis_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  file_name TEXT NOT NULL DEFAULT '',
  explanation TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_results(user_id);

CREATE TABLE IF NOT EXISTS climate_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'climate',
  name TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_user ON climate_plans(user_id);

CREATE TABLE IF NOT EXISTS construction_projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  schedule TEXT NOT NULL DEFAULT '{}',
  plan TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cons_user ON construction_projects(user_id);

CREATE TABLE IF NOT EXISTS monitoring_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  monitor_id INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mon_results ON monitoring_results(monitor_id);

CREATE TABLE IF NOT EXISTS usage_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id, kind);

CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'FREE',
  status TEXT NOT NULL DEFAULT 'active',
  provider TEXT NOT NULL DEFAULT 'manual',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  image_url TEXT NOT NULL DEFAULT '',
  link_url TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS optins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  consent INTEGER NOT NULL DEFAULT 0,
  areas TEXT NOT NULL DEFAULT '',
  data_types TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  auto_response TEXT NOT NULL DEFAULT '',
  responded_at REAL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_optins_user ON optins(user_id);
"""

SEED_KNOWLEDGE = [
    ("heat stress thresholds",
     "OSHA/NIOSH advise rest cycles for outdoor workers based on heat index and wet-bulb globe temperature (WBGT). WBGT around 30 C is generally considered high risk for heavy physical work.",
     "OSHA / NIOSH"),
    ("wet bulb globe temperature",
     "WBGT is a leading metric for outdoor heat stress. Rough guidance for acclimatized workers: below 27 C moderate, 27-30 C high, above 30 C very high risk.",
     "NIOSH"),
    ("urban heat island",
     "Cities can be roughly 1.7-4.4 C warmer than surrounding rural areas due to heat-absorbing surfaces. Trees and shade can reduce local surface temperatures substantially.",
     "US EPA"),
    ("cool roofs",
     "Highly reflective cool roofs can cut peak roof surface temperature by up to ~28 C and reduce cooling energy use by roughly 10-30%.",
     "US DOE"),
    ("tree canopy",
     "Increasing urban tree canopy is a widely used heat mitigation measure; canopy and shade infrastructure reduce local air and surface temperatures.",
     "US EPA / research review"),
    ("green roofs",
     "Green roofs can lower rooftop surface temperatures by 15-24 C in summer and reduce stormwater runoff while adding insulation.",
     "US EPA"),
    ("heat index",
     "Heat index combines temperature and humidity. NWS heat index values around 41 C (105 F) are dangerous; around 54 C (130 F) extremely dangerous.",
     "NOAA NWS"),
    ("worker acclimatization",
     "Acclimatization over roughly 5-7 days reduces heat illness risk; ACGIH recommends progressively increasing work/rest schedules as WBGT rises.",
     "ACGIH"),
]


SEED_ADS = [
    ("ClimaPro Certification", "Become a certified extreme-heat safety professional.", "", ""),
    ("CoolRoof Systems", "Reflective roofing that lowers peak roof temperatures.", "", ""),
]


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            count = conn.execute("SELECT COUNT(*) AS c FROM knowledge").fetchone()["c"]
            if count == 0:
                conn.executemany(
                    "INSERT INTO knowledge (topic, content, source) VALUES (?, ?, ?)", SEED_KNOWLEDGE
                )
            ad_count = conn.execute("SELECT COUNT(*) AS c FROM ads").fetchone()["c"]
            if ad_count == 0:
                conn.executemany(
                    "INSERT INTO ads (title, body, image_url, link_url, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                    [(t, b, i, l, time.time()) for (t, b, i, l) in SEED_ADS],
                )
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()


def _migrate(conn):
    """Additive schema migrations for databases created before new columns/tables existed."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "credit_balance" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN credit_balance INTEGER NOT NULL DEFAULT 0")


def get_setting(key, default=None):
    row = run("SELECT value FROM settings WHERE key = ?", (key,), fetch="one")
    return row["value"] if row else default


def set_setting(key, value):
    run(
        "INSERT INTO settings (user_id, key, value) VALUES ('system', ?, ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


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
