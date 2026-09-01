-- ============================================================
-- Climagent - Supabase / PostgreSQL schema
-- Paste this entire file into the Supabase SQL editor and Run.
-- Safe to re-run (idempotent): tables/indexes use IF NOT EXISTS.
-- ============================================================

-- Users -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  plan TEXT NOT NULL DEFAULT 'FREE',
  credit_balance BIGINT NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Credits ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credits (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  delta BIGINT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credits_user ON credits(user_id);

-- Ad views ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ad_views (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  ad_id BIGINT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'view',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_adviews_user ON ad_views(user_id);
CREATE INDEX IF NOT EXISTS idx_adviews_user_time ON ad_views(user_id, created_at);

-- Sessions ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  token TEXT NOT NULL UNIQUE,
  created_at DOUBLE PRECISION NOT NULL,
  expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

-- Password resets ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS password_resets (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  token TEXT NOT NULL UNIQUE,
  expires_at DOUBLE PRECISION NOT NULL,
  used BIGINT NOT NULL DEFAULT 0
);

-- Projects -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);

-- Conversations -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id BIGINT,
  title TEXT NOT NULL DEFAULT 'New conversation',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

-- Messages ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

-- Uploaded files --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS uploaded_files (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  size BIGINT NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user ON uploaded_files(user_id);

-- Analysis results -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_results (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  file_name TEXT NOT NULL DEFAULT '',
  explanation TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_results(user_id);

-- Climate plans -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS climate_plans (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'climate',
  name TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_user ON climate_plans(user_id);

-- Construction projects -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS construction_projects (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '{}',
  schedule TEXT NOT NULL DEFAULT '{}',
  plan TEXT NOT NULL DEFAULT '',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cons_user ON construction_projects(user_id);

-- Monitoring results --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monitoring_results (
  id BIGSERIAL PRIMARY KEY,
  monitor_id BIGINT NOT NULL,
  user_id TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mon_results ON monitoring_results(monitor_id);

-- Usage records --------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_records (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id, kind);

-- Subscriptions ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'FREE',
  status TEXT NOT NULL DEFAULT 'active',
  provider TEXT NOT NULL DEFAULT 'manual',
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL
);

-- Ads ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ads (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  image_url TEXT NOT NULL DEFAULT '',
  link_url TEXT NOT NULL DEFAULT '',
  active BIGINT NOT NULL DEFAULT 1,
  created_at DOUBLE PRECISION NOT NULL
);

-- Memories (long-term agent memory) ----------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  meta TEXT DEFAULT '{}',
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id);

-- Activity (observability / agent traces) -----------------------------------------------
CREATE TABLE IF NOT EXISTS activity (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  run_id TEXT,
  kind TEXT NOT NULL,
  agent TEXT,
  tool TEXT,
  input TEXT,
  output TEXT,
  status TEXT,
  error TEXT,
  duration_ms DOUBLE PRECISION,
  tokens BIGINT,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_user ON activity(user_id);
CREATE INDEX IF NOT EXISTS idx_act_run ON activity(run_id);

-- Monitors (background monitoring definitions) ------------------------------------------
CREATE TABLE IF NOT EXISTS monitors (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  query TEXT NOT NULL,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  interval_min DOUBLE PRECISION NOT NULL DEFAULT 60,
  max_runs BIGINT NOT NULL DEFAULT 30,
  run_count BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  last_result TEXT DEFAULT '',
  last_run_at DOUBLE PRECISION,
  next_run_at DOUBLE PRECISION NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);

-- Alerts ----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  monitor_id BIGINT,
  message TEXT NOT NULL,
  level TEXT NOT NULL,
  read BIGINT NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL
);

-- Settings (key/value, incl. admin risk thresholds / hitl mode) ---------------------------
CREATE TABLE IF NOT EXISTS settings (
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);

-- Approvals (human-in-the-loop) ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at DOUBLE PRECISION NOT NULL,
  decided_at DOUBLE PRECISION
);

-- Knowledge (seed knowledge base) ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge (
  id BIGSERIAL PRIMARY KEY,
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL
);

-- Opt-ins (data-sharing form + auto-responder) -----------------------------------------------------
CREATE TABLE IF NOT EXISTS optins (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  consent BIGINT NOT NULL DEFAULT 0,
  areas TEXT NOT NULL DEFAULT '',
  data_types TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  auto_response TEXT NOT NULL DEFAULT '',
  responded_at DOUBLE PRECISION,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_optins_user ON optins(user_id);

-- Notifications (in-app bell + email) ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'info',
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  read BIGINT NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read);

-- ============================================================
-- End of Climagent schema
-- ============================================================
