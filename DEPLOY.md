# Deploy Climagent to Fly.io (free tier, persistent data)

Climagent runs SQLite + local file storage + a background scheduler, so it needs a
**persistent runtime** (not stateless serverless). Fly.io provides a free tier with a
persistent volume, long-running services, and background processes.

## Prerequisites

- Install the Fly CLI: `curl -L https://fly.io/install.sh | sh`
- Log in: `fly auth login`
- Create a free account at fly.io if you don't have one.

## Steps

### 1. Set your secrets (keys). Never commit .env.

```bash
fly secrets set FORTYGUARD_API_KEY=your-fortyguard-key
fly secrets set GOOGLE_API_KEY=your-google-api-key
fly secrets set LLM_API_KEY=your-google-api-key
fly secrets set GOOGLE_MODEL=gemini-2.5-flash
```

Optional gateways (only if you use them):
```bash
fly secrets set ADSENSE_CLIENT_ID=... ADSENSE_SLOT_ID=...
fly secrets set PAYONEER_CLIENT_ID=... PAYONEER_CLIENT_SECRET=... PAYONEER_BASE_URL=...
fly secrets set LANGCHAIN_API_KEY=... LANGCHAIN_TRACING_V2=true
```

### 2. Launch the app (first time)

```bash
fly launch --no-deploy --name climagent
```

This reads `fly.toml`. It will create the app and a free volume automatically. The volume
mounts at `/data`, and the app stores its DB (`/data/climagent.db`) and uploads
(`/data/storage`) there (set via `CLIMAGENT_DB` / `CLIMAGENT_STORAGE`).

### 3. Deploy

```bash
fly deploy
```

### 4. Open it

```bash
fly open
```

The FastAPI app + docs are served:
- App home: `https://climagent.fly.dev/`
- API docs (Swagger): `https://climagent.fly.dev/api/docs`

## Scaling / notes

- **Scheduler**: the app starts a monitor scheduler in-process on startup, so it runs
  while the single instance is up. Keep the app on one instance.
- **Scale down**: on the free tier the app may idle-sleep. Upgrade or use `fly scale count 1` to keep a single instance warm for continuous monitoring.
- **Backups**: your data lives in the `data` volume. Fly volumes are replicated within a
  region; export `climagent.db` regularly if you need off-site backups.

## Updating

```bash
git pull && fly deploy
```
