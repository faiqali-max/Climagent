# Climagent Vercel + Supabase Migration Plan

## Goal
Make Climagent fully **hostable on Vercel (Python serverless)** by moving all
state to **Supabase** (Postgres DB + Storage), so there is **no local disk / no
long-lived process** dependency. The app keeps working on local SQLite for development.

## Why this is needed
Vercel runs each request in a short-lived serverless function with no persistent
disk and no long-running background process. Today Climagent relies on:

1. **Local SQLite** (`climagent.db`) for all state.
2. **Local files** (`storage/`) for uploads.
3. An **in-process background scheduler** (`monitoring.scheduler_loop`) for monitors.
4. In-memory LangGraph checkpointer for chat continuity.

For Vercel these must move to Supabase / become stateless.

## Architecture: a transparent storage adapter (lowest risk)

Keep the app's ~128 `db.run(...)` call sites **unchanged** by introducing a
storage adapter behind `lib/db.py` with the same `run()` / fetch interface.

- `lib/db.py` becomes a **dispatcher**: if `SUPABASE_URL` (Postgres) is configured ->
  route to Postgres backend; else -> current SQLite backend.
- Two backend drivers, both implementing `run(sql, params, fetch)` + DDL init:
  - `lib/storage_sqlite.py` (existing behavior)
  - `lib/storage_pg.py` (new, Postgres via `psycopg`)

Because call sites keep using `db.run(...)` with `?` placeholders, the Postgres
backend must translate:
- `?` placeholders -> `%s`
- `INSERT`/`UPDATE`/`DELETE` -> return inserted row id (via `RETURNING id`)
- SQLite functions like `strftime` -> Postgres equivalents where used
- `ON CONFLICT ... DO UPDATE` already supported by Postgres
- Row access stays by column name (`row["col"]`)

## Files to add/change

### New modules
- `lib/storage_sqlite.py` — wraps current SQLite connection/run logic.
- `lib/storage_pg.py` — Postgres driver + Postgres DDL for all tables
  (identical schema as SQLite, using `BIGSERIAL`/`SERIAL` ids, `text`, `double precision`,
  `timestamptz`, `jsonb` where needed) + upserts + RETURNING id.
- `lib/storage.py` — dispatcher that picks backend from env.
- `scripts/migrate_sqlite_to_pg.py` — one-time copy of current `climagent.db` rows
  into Supabase Postgres (read SQLite, insert into PG).

### Changed
- `lib/db.py` — route through `lib/storage`.
- `backend.py` —
  - add uploads path: when Supabase is enabled, store files in **Supabase Storage**
    bucket `climagent-files` instead of `storage/`; return a public/expiring URL.
  - `startup()`: only start in-process scheduler in non-Serverless mode; add a
    `/api/monitors/run-due` endpoint that can be called by an external **cron**.
  - get `os`/uploads to respect storage abstraction.
- `lib/agents.py` — persist the LangGraph checkpointer to Postgres when Supabase is
  enabled (or keep stateless + rely on `memories` table) so chat continuity survives
  serverless restarts.
- `lib/notify.py` — unchanged (DB-backed already).
- `requirements.txt` — add `psycopg[binary]`.
- `.env.example` — document `DATABASE_URL` / Supabase Postgres URL + storage bucket.
- `vercel.json` — configure Python runtime/build (entrypoint already declared in
  `pyproject.toml`: `backend:app`).

## Serverless-safe scheduler
- In-process `scheduler_loop()` is **disabled** on Vercel (no long-lived process).
- Add `POST /api/monitors/run-due` (admin/authed) that runs due monitors.
- Set up a **Supabase cron** (or Vercel Cron `/api/cron` -> calls run-due) that fires
  every N minutes. This is the recommended scheduler on a serverless host.

## Agent memory on serverless
- Long-term agent memory is **already DB-persisted** (`memories` + `activity` tables) —
  these move to Postgres automatically and survive restarts.
- Only the short-lived conversation state (LangGraph checkpointer) is RAM. Plan:
  persist it to Postgres when Supabase is enabled; otherwise it resets per request
  (acceptable for stateless serverless).

## File uploads on serverless
- Replace local `storage/` writes with **Supabase Storage** when enabled.
- `analyze` reads the file bytes into memory (already does), stores to the bucket,
  processes, then records the row in Postgres. No local disk needed.

## Data migration (one-time)
- `scripts/migrate_sqlite_to_pg.py` reads every table from `climagent.db` and upserts
  into Supabase Postgres, preserving ids where possible. Run once after enabling Supabase.

## Testing plan
1. Local default → still SQLite: run app, confirm no regression (auth, chat, opt-in,
   notifications, admin, climate endpoints).
2. Enable Supabase in a local `.env` (SUPABASE_URL + service role + DB URL) → confirm the
   same flows run on Postgres; run the migration script; verify data counts match.
3. On Vercel: confirm build succeeds, `/api/docs` loads, endpoints return data from
   Supabase, `/api/monitors/run-due` works via cron, uploads store to Supabase Storage.

## Risks / trade-offs (please confirm you accept)
- **In-process monitor scheduler is removed on Vercel**; monitoring requires the
  external cron (free on Supabase/Vercel Cron free plan).
- **Chat conversation continuity** across requests requires the Postgres checkpointer;
  without it, each request is stateless (still fine functionally via memories).
- **Agent runs are long** (up to ~seconds-minutes). Vercel Hobby has a function timeout
  (~10s default, up to 60s configurable); long agent/heat-intelligence calls may time
  out on the free tier. This is a real limit — plan recommends keeping heavy jobs
  short or moving to a worker later.
- This is a **significant rewrite** of the data layer with real regression risk; SQLite
  mode is preserved so local testing stays safe.

## Requested confirmation
Before I write code, please confirm you accept the three trade-offs above
(scheduler->cron, per-request stateless chat, Vercel function timeout on long agent
runs). If yes, I will implement in this order:
1. storage adapters + db dispatch (SQLite-first)
2. Postgres schema + migration script
3. uploads to Supabase Storage
4. serverless-safe scheduler + cron endpoint
5. agent checkpointer to Postgres
6. Vercel config + docs
7. local tests in both SQLite and Supabase modes
