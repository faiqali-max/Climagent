import json
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lib import agents, admin, billing, evaluation, memory, monitoring, observability, optin, plans
from lib import adsense, credits, google_gateway, payoneer, supabase_gateway
from lib import fortyguard as fg
from lib import auth as authlib
from lib import db
from lib.db import init_db
from lib.llm import AgentConfigError

load_dotenv()
BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR / "frontend"
STORAGE_DIR = BASE_DIR / "storage"
ALLOWED_EXT = {"csv", "xlsx", "xls", "json"}
MAX_UPLOAD = 30 * 1024 * 1024

app = FastAPI(title="Climagent", docs_url="/api/docs", openapi_url="/api/openapi.json")

from fastapi.middleware.cors import CORSMiddleware


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Download-Options"] = "noopen"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

_RATE = {}
_RATE_LIMIT = 30
_RATE_WINDOW = 60
_RATE_LAST_PRUNE = [0.0]


def _client_ip(request):
    return request.client.host if request.client else "unknown"


def _rate_ok(client_ip):
    now = time.time()
    # Periodically prune stale per-IP buckets to avoid unbounded memory growth.
    if now - _RATE_LAST_PRUNE[0] > 300:
        _RATE_LAST_PRUNE[0] = now
        cutoff = now - _RATE_WINDOW
        for ip in [k for k, v in _RATE.items() if not v or v[-1] < cutoff]:
            _RATE.pop(ip, None)
    window = [t for t in _RATE.get(client_ip, []) if t > now - _RATE_WINDOW]
    if len(window) >= _RATE_LIMIT:
        _RATE[client_ip] = window
        return False
    window.append(now)
    _RATE[client_ip] = window
    return True


def _bearer(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def _user(request):
    """Resolve the current authenticated user id ('u<id>'). Guests are 'guest'.

    Client-supplied identity headers are NOT trusted (prevents impersonation).
    Authenticated callers are the only ones with a per-user identity."""
    token = _bearer(request)
    if token:
        user = authlib.user_by_token(token)
        if user:
            return f"u{user['id']}"
    return "guest"


def _auth_user(request, admin_only=False):
    token = _bearer(request)
    user = authlib.user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if admin_only and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def _valid_coords(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"lat": lat, "lon": lon}


def _cfg(name):
    from lib.llm import is_configured
    return is_configured(name)


def _credit_view(user):
    uid = f"u{user['id']}"
    return {
        "balance": credits.balance(uid),
        "view_progress": credits.ad_view_summary(uid),
        "ledger": credits.ledger(uid, 20),
    }


@app.on_event("startup")
def _startup():
    init_db()
    authlib.bootstrap_admin()
    monitoring.start_scheduler()


@app.on_event("shutdown")
async def _shutdown():
    await monitoring.stop_scheduler()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "climagent",
        "llm_configured": _cfg("LLM_API_KEY"),
        "fortyguard_configured": _cfg("FORTYGUARD_API_KEY"),
    }


@app.get("/api/config")
def config():
    from lib import tools
    return {
        "app": "climagent",
        "llm_configured": _cfg("LLM_API_KEY"),
        "fortyguard_configured": _cfg("FORTYGUARD_API_KEY"),
        "langsmith_configured": _cfg("LANGCHAIN_API_KEY"),
        "risk_thresholds": tools.effective_thresholds(),
        "hitl_modes": list(monitoring.MODES),
        "plans": {k: v["label"] for k, v in plans.PLANS.items()},
        "gateways": {
            "google_gemini": google_gateway.is_enabled(),
            "adsense": adsense.is_adsense_enabled(),
            "supabase": supabase_gateway.is_enabled(),
            "payoneer": payoneer.is_enabled(),
        },
        "credits": {
            "views_per_credit": credits.VIEWS_PER_CREDIT,
            "costs": {"search": credits.COST_SEARCH, "upload": credits.COST_UPLOAD, "agent_run": credits.COST_AGENT_RUN},
        },
    }


# ---------------------------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class ResetRequest(BaseModel):
    email: str = Field(..., max_length=254)


class PasswordReset(BaseModel):
    token: str = Field(..., min_length=8, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)


@app.post("/api/auth/signup")
def signup(request: Request, payload: SignupRequest):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    try:
        uid = authlib.create_user(payload.email, payload.password, payload.name)
        token = authlib.create_session(uid)
        user = authlib.public_user(authlib.user_by_token(token))
        return {"token": token, "user": user}
    except authlib.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/auth/login")
def login(request: Request, payload: LoginRequest):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    try:
        user = authlib.authenticate(payload.email, payload.password)
    except authlib.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    token = authlib.create_session(user["id"])
    return {"token": token, "user": authlib.public_user(user)}


@app.post("/api/auth/logout")
def logout(request: Request):
    authlib.logout(_bearer(request))
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = _auth_user(request)
    return {
        "user": authlib.public_user(user),
        "usage": plans.usage_summary(user),
        "credits": _credit_view(user),
    }


@app.post("/api/auth/forgot")
def forgot(request: Request, payload: ResetRequest):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    token = authlib.request_reset(payload.email)
    return {"ok": True, "sent": bool(token)}


@app.post("/api/auth/reset")
def reset_password(request: Request, payload: PasswordReset):
    try:
        authlib.reset_password(payload.token, payload.password)
        return {"ok": True}
    except authlib.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# SUBSCRIPTIONS
# ---------------------------------------------------------------------------

@app.get("/api/subscription")
def get_subscription(request: Request):
    user = _auth_user(request)
    return {
        "plan": plans.usage_summary(user),
        "subscription": plans.get_subscription(str(user["id"])),
    }


@app.get("/api/usage")
def get_usage(request: Request):
    user = _auth_user(request)
    return plans.usage_summary(user)


# ---------------------------------------------------------------------------
# AI WORKSPACE: CHAT + FILE ANALYSIS
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    location: dict = Field(default_factory=dict)
    date: str = Field(default="", max_length=20)
    project_id: int | None = None
    conversation_id: int | None = None


@app.post("/api/chat")
async def chat(request: Request, payload: ChatRequest):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    authed = _auth_user(request)
    user_id = f"u{authed['id']}"
    ok, used, limit = plans.check_ai_limit(authed)
    if not ok:
        raise HTTPException(status_code=403, detail=f"Daily AI request limit reached ({used}/{limit}). Upgrade your plan.")
    if credits.needs_credits(authed):
        spent = credits.spend(user_id, credits.COST_SEARCH, "ai_search", payload.message[:120])
        if spent is None:
            raise HTTPException(
                status_code=402,
                detail=f"No credits. Watch {credits.COST_SEARCH} ad{'s' if credits.COST_SEARCH>1 else ''} to earn 1 credit; each AI search costs {credits.COST_SEARCH} credit(s).",
            )
    plans.record_usage(user_id, "ai_request")
    location = {}
    if payload.location:
        coords = _valid_coords(payload.location.get("lat"), payload.location.get("lon"))
        if not coords:
            raise HTTPException(status_code=422, detail="Invalid location coordinates.")
        location = coords
    try:
        result = await agents.run_chat(payload.message, location, payload.date.strip(), user_id)
    except AgentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {exc}")

    if payload.conversation_id:
        _append_message(payload.conversation_id, user_id, "user", payload.message)
        _append_message(payload.conversation_id, user_id, "assistant", result["reply"])
    elif authed:
        conv = db.run(
            "INSERT INTO conversations (user_id, project_id, title, created_at) VALUES (?, ?, ?, ?)",
            (user_id, payload.project_id, (payload.message[:60] or "New conversation"), time.time()),
        )
        _append_message(conv, user_id, "user", payload.message)
        _append_message(conv, user_id, "assistant", result["reply"])
        result["conversation_id"] = conv
    return result


@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    authed = _auth_user(request)
    user_id = f"u{authed['id']}"
    if plans.count_rows(user_id, "uploaded_files") >= plans.limits(authed)["max_uploads"]:
        raise HTTPException(status_code=403, detail="Upload limit reached for your plan.")
    if credits.needs_credits(authed):
        spent = credits.spend(user_id, credits.COST_UPLOAD, "file_upload")
        if spent is None:
            raise HTTPException(
                status_code=402,
                detail=f"No credits. Watching {credits.COST_UPLOAD} ads earns enough credits for one file upload.",
            )
    plans.record_usage(user_id, "upload")
    name = file.filename or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=415, detail="Unsupported file type. Upload CSV, Excel, or JSON.")
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File too large (max 30 MB).")
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = STORAGE_DIR / stored
    try:
        path.write_bytes(content)
        result = await agents.analyze_file(str(path), name, user_id)
        db.run(
            "INSERT INTO uploaded_files (user_id, original_name, stored_name, size, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name[:255], stored, len(content), time.time()),
        )
        db.run(
            "INSERT INTO analysis_results (user_id, file_name, explanation, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name[:255], result["explanation"][:4000], json.dumps(result["data"], default=str)[:4000], time.time()),
        )
        return result
    except AgentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")
    finally:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def _append_message(conversation_id, user_id, role, content):
    row = db.run("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.run(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content[:4000], time.time()),
    )


# ---------------------------------------------------------------------------
# PROJECTS
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    location: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    location: dict = Field(default_factory=dict)


@app.get("/api/projects")
def list_projects(request: Request):
    user = _auth_user(request)
    return {"projects": db.run("SELECT * FROM projects WHERE user_id = ? ORDER BY id DESC", (f"u{user['id']}",)) or []}


@app.post("/api/projects")
def create_project(request: Request, payload: ProjectCreate):
    user = _auth_user(request)
    uid = f"u{user['id']}"
    if plans.count_rows(uid, "projects") >= plans.limits(user)["max_projects"]:
        raise HTTPException(status_code=403, detail="Project limit reached for your plan.")
    loc = _valid_coords(payload.location.get("lat"), payload.location.get("lon")) if payload.location else {}
    pid = db.run(
        "INSERT INTO projects (user_id, name, description, location, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, payload.name[:200], payload.description[:2000], json.dumps(loc), time.time()),
    )
    return {"id": pid}


@app.patch("/api/projects/{pid}")
def update_project(request: Request, pid: int, payload: ProjectUpdate):
    user = _auth_user(request)
    uid = f"u{user['id']}"
    row = db.run("SELECT * FROM projects WHERE id = ? AND user_id = ?", (pid, uid), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Project not found.")
    loc = _valid_coords(payload.location.get("lat"), payload.location.get("lon")) if payload.location else json.loads(row["location"] or "{}")
    db.run(
        "UPDATE projects SET name = ?, description = ?, location = ? WHERE id = ?",
        (payload.name or row["name"], payload.description or row["description"], json.dumps(loc), pid),
    )
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(request: Request, pid: int):
    user = _auth_user(request)
    db.run("DELETE FROM projects WHERE id = ? AND user_id = ?", (pid, f"u{user['id']}"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# CONVERSATIONS
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
def list_conversations(request: Request):
    user = _auth_user(request)
    return {"conversations": db.run("SELECT * FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT 100", (f"u{user['id']}",)) or []}


@app.post("/api/conversations")
def create_conversation(request: Request, title: str = "New conversation"):
    user = _auth_user(request)
    cid = db.run(
        "INSERT INTO conversations (user_id, title, created_at) VALUES (?, ?, ?)",
        (f"u{user['id']}", title[:120], time.time()),
    )
    return {"id": cid}


@app.get("/api/conversations/{cid}/messages")
def conversation_messages(request: Request, cid: int):
    user = _auth_user(request)
    row = db.run("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (cid, f"u{user['id']}"), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"conversation": row, "messages": db.run("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (cid,)) or []}


# ---------------------------------------------------------------------------
# DATA / FILES / ANALYSIS
# ---------------------------------------------------------------------------

@app.get("/api/files")
def list_files(request: Request):
    user = _auth_user(request)
    return {"files": db.run("SELECT * FROM uploaded_files WHERE user_id = ? ORDER BY id DESC", (f"u{user['id']}",)) or []}


@app.get("/api/analyses")
def list_analyses(request: Request):
    user = _auth_user(request)
    return {"analyses": db.run("SELECT * FROM analysis_results WHERE user_id = ? ORDER BY id DESC", (f"u{user['id']}",)) or []}


@app.delete("/api/analyses/{aid}")
def delete_analysis(request: Request, aid: int):
    user = _auth_user(request)
    db.run("DELETE FROM analysis_results WHERE id = ? AND user_id = ?", (aid, f"u{user['id']}"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# PLANS (climate / cooling / construction)
# ---------------------------------------------------------------------------

class PlanCreate(BaseModel):
    kind: str = Field(default="climate", max_length=30)
    name: str = Field(..., min_length=1, max_length=200)
    content: str = Field(default="", max_length=20000)
    location: dict = Field(default_factory=dict)


@app.get("/api/plans")
def list_plans(request: Request, kind: str = ""):
    user = _auth_user(request)
    if kind:
        return {"plans": db.run("SELECT * FROM climate_plans WHERE user_id = ? AND kind = ? ORDER BY id DESC", (f"u{user['id']}", kind)) or []}
    return {"plans": db.run("SELECT * FROM climate_plans WHERE user_id = ? ORDER BY id DESC", (f"u{user['id']}",)) or []}


@app.post("/api/plans")
def create_plan(request: Request, payload: PlanCreate):
    user = _auth_user(request)
    loc = json.dumps(_valid_coords(payload.location.get("lat"), payload.location.get("lon"))) if payload.location else "{}"
    pid = db.run(
        "INSERT INTO climate_plans (user_id, kind, name, content, location, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (f"u{user['id']}", payload.kind[:30], payload.name[:200], payload.content[:20000], loc, time.time()),
    )
    return {"id": pid}


@app.delete("/api/plans/{pid}")
def delete_plan(request: Request, pid: int):
    user = _auth_user(request)
    db.run("DELETE FROM climate_plans WHERE id = ? AND user_id = ?", (pid, f"u{user['id']}"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# CONSTRUCTION PROJECTS
# ---------------------------------------------------------------------------

class ConstructionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    location: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    plan: str = Field(default="", max_length=20000)


class ConstructionUpdate(BaseModel):
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    location: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    plan: str = Field(default="", max_length=20000)


@app.get("/api/construction")
def list_construction(request: Request):
    user = _auth_user(request)
    return {"projects": db.run("SELECT * FROM construction_projects WHERE user_id = ? ORDER BY id DESC", (f"u{user['id']}",)) or []}


@app.post("/api/construction")
def create_construction(request: Request, payload: ConstructionCreate):
    user = _auth_user(request)
    loc = json.dumps(_valid_coords(payload.location.get("lat"), payload.location.get("lon"))) if payload.location else "{}"
    pid = db.run(
        "INSERT INTO construction_projects (user_id, name, description, location, schedule, plan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"u{user['id']}", payload.name[:200], payload.description[:2000], loc,
         json.dumps(payload.schedule)[:4000], payload.plan[:20000], time.time()),
    )
    return {"id": pid}


@app.patch("/api/construction/{pid}")
def update_construction(request: Request, pid: int, payload: ConstructionUpdate):
    user = _auth_user(request)
    uid = f"u{user['id']}"
    row = db.run("SELECT * FROM construction_projects WHERE id = ? AND user_id = ?", (pid, uid), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Construction project not found.")
    loc = _valid_coords(payload.location.get("lat"), payload.location.get("lon")) if payload.location else json.loads(row["location"] or "{}")
    db.run(
        "UPDATE construction_projects SET name = ?, description = ?, location = ?, schedule = ?, plan = ? WHERE id = ?",
        (payload.name or row["name"], payload.description or row["description"], json.dumps(loc),
         json.dumps(payload.schedule)[:4000] if payload.schedule else row["schedule"],
         payload.plan or row["plan"], pid),
    )
    return {"ok": True}


@app.delete("/api/construction/{pid}")
def delete_construction(request: Request, pid: int):
    user = _auth_user(request)
    db.run("DELETE FROM construction_projects WHERE id = ? AND user_id = ?", (pid, f"u{user['id']}"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# MONITORING
# ---------------------------------------------------------------------------

class MonitorCreate(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    lat: float
    lon: float
    interval_min: float = Field(default=60, ge=1, le=10080)
    max_runs: int = Field(default=30, ge=1, le=100000)


@app.get("/api/monitors")
def get_monitors(request: Request):
    user = _auth_user(request)
    return {"monitors": monitoring.list_monitors(f"u{user['id']}")}


@app.post("/api/monitors")
def create_monitor(request: Request, payload: MonitorCreate):
    user = _auth_user(request)
    uid = f"u{user['id']}"
    if plans.count_rows(uid, "monitors") >= plans.limits(user)["max_monitors"]:
        raise HTTPException(status_code=403, detail="Monitor limit reached for your plan.")
    coords = _valid_coords(payload.lat, payload.lon)
    if not coords:
        raise HTTPException(status_code=422, detail="Invalid location coordinates.")
    mid = monitoring.create_monitor(uid, payload.query, coords["lat"], coords["lon"], payload.interval_min, payload.max_runs)
    return {"id": mid}


@app.post("/api/monitors/{mid}/stop")
def stop_monitor(request: Request, mid: int):
    user = _auth_user(request)
    monitoring.stop_monitor(f"u{user['id']}", mid)
    return {"ok": True}


@app.delete("/api/monitors/{mid}")
def delete_monitor(request: Request, mid: int):
    user = _auth_user(request)
    monitoring.delete_monitor(f"u{user['id']}", mid)
    return {"ok": True}


@app.get("/api/monitoring/results")
def monitoring_results(request: Request, monitor_id: int | None = None):
    user = _auth_user(request)
    uid = f"u{user['id']}"
    if monitor_id:
        rows = db.run(
            "SELECT * FROM monitoring_results WHERE monitor_id = ? AND user_id = ? ORDER BY id DESC LIMIT 50",
            (monitor_id, uid),
        )
    else:
        rows = db.run("SELECT * FROM monitoring_results WHERE user_id = ? ORDER BY id DESC LIMIT 100", (uid,))
    return {"results": rows or []}


@app.get("/api/alerts")
def get_alerts(request: Request):
    user = _auth_user(request)
    return {"alerts": monitoring.get_alerts(f"u{user['id']}")}


@app.post("/api/alerts/read")
def read_alerts(request: Request):
    user = _auth_user(request)
    monitoring.mark_alerts_read(f"u{user['id']}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# MEMORY & ACTIVITY
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    kind: str = Field(default="context", max_length=30)


@app.get("/api/memory")
def get_memory(request: Request):
    user = _auth_user(request)
    return {"memories": memory.list_all(f"u{user['id']}")}


@app.post("/api/memory")
def add_memory(request: Request, payload: MemoryCreate):
    user = _auth_user(request)
    mem_id = memory.add(f"u{user['id']}", payload.kind, payload.content)
    return {"id": mem_id}


@app.delete("/api/memory/{mem_id}")
def delete_memory(request: Request, mem_id: int):
    user = _auth_user(request)
    memory.delete(f"u{user['id']}", mem_id)
    return {"ok": True}


@app.delete("/api/memory")
def clear_memory(request: Request):
    user = _auth_user(request)
    memory.clear(f"u{user['id']}")
    return {"ok": True}


@app.get("/api/activity")
def get_activity(request: Request, limit: int = 60):
    if limit > 500:
        limit = 500
    user = _auth_user(request)
    return {"timeline": observability.timeline(f"u{user['id']}", limit)}


# ---------------------------------------------------------------------------
# HITL MODE
# ---------------------------------------------------------------------------

class ModeRequest(BaseModel):
    mode: str = Field(..., min_length=1, max_length=20)


@app.get("/api/settings/hitl-mode")
def get_hitl_mode(request: Request):
    user = _auth_user(request)
    return {"mode": monitoring.get_mode(f"u{user['id']}")}


@app.put("/api/settings/hitl-mode")
def set_hitl_mode(request: Request, payload: ModeRequest):
    user = _auth_user(request)
    if not monitoring.set_mode(f"u{user['id']}", payload.mode):
        raise HTTPException(status_code=422, detail="Invalid HITL mode. Use suggestion, approval or automated.")
    return {"mode": payload.mode}


@app.get("/api/approvals")
def get_approvals(request: Request):
    user = _auth_user(request)
    return {"approvals": monitoring.list_approvals(f"u{user['id']}")}


class ApprovalDecide(BaseModel):
    approve: bool = True


@app.post("/api/approvals/{aid}/decide")
def decide_approval(request: Request, aid: int, payload: ApprovalDecide):
    user = _auth_user(request)
    if not monitoring.decide_approval(f"u{user['id']}", aid, payload.approve):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# ADS + AD-CREDIT ECONOMY + GATEWAYS
# ---------------------------------------------------------------------------

class AdView(BaseModel):
    ad_id: int


@app.get("/api/ads")
def get_ads(request: Request):
    config = adsense.config()
    authed = _auth_user(request) if _bearer(request) else None
    active = []
    if authed and not plans.limits(authed)["ads"]:
        # Paid tiers still receive the provider config so the embed target is known.
        return {"ads": [], "config": config, "show_ads": False}
    return {"ads": admin.list_ads(active_only=True), "config": config, "show_ads": True}


@app.post("/api/ads/view")
def ad_view(request: Request, payload: AdView):
    user_id = _user(request)
    if not user_id.startswith("u"):
        raise HTTPException(status_code=401, detail="Sign in to earn credits by watching ads.")
    ad = db.run("SELECT * FROM ads WHERE id = ? AND active = 1", (payload.ad_id,), fetch="one")
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found.")
    balance = credits.record_ad_view(user_id, payload.ad_id)
    return {"ok": True, "credit_balance": balance, "progress": credits.ad_view_summary(user_id)}


@app.get("/api/credits")
def credit_status(request: Request):
    user = _auth_user(request)
    return _credit_view(user)


@app.get("/api/gateways")
def gateway_status():
    return {
        "supabase": supabase_gateway.status(),
        "google_gemini": {"enabled": google_gateway.is_enabled(), "model": google_gateway.model_name()},
        "payoneer": payoneer.status(),
        "adsense": adsense.status(),
    }


class InterpretRequest(BaseModel):
    question: str = Field(default="", max_length=2000)
    lat: float | None = None
    lon: float | None = None


@app.post("/api/gg/interpret")
def gg_interpret(request: Request, payload: InterpretRequest):
    """FortyGuard -> Google Gemini: fetch live FortyGuard data for a location, then have
    Gemini interpret it into a plain-language assessment."""
    _auth_user(request)
    if not google_gateway.is_enabled():
        raise HTTPException(status_code=503, detail="GOOGLE_API_KEY is not configured.")
    if payload.lat is None or payload.lon is None:
        raise HTTPException(status_code=422, detail="lat and lon are required.")
    coords = _valid_coords(payload.lat, payload.lon)
    if not coords:
        raise HTTPException(status_code=422, detail="Invalid coordinates.")
    try:
        fg_payload = fg.current_temperature(coords["lat"], coords["lon"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FortyGuard fetch failed: {exc}")
    try:
        text = google_gateway.interpret_fortyguard(fg_payload, payload.question, coords["lat"], coords["lon"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini interpretation failed: {exc}")
    return {"source": "fortyguard->google_gemini", "fortyguard": fg_payload, "interpretation": text}


# ---------------------------------------------------------------------------
# OPT-IN FORM & AUTO-RESPONDER
# ---------------------------------------------------------------------------

class OptInRequest(BaseModel):
    email: str = Field(default="", max_length=254)
    name: str = Field(default="", max_length=120)
    consent: bool = False
    areas: str = Field(default="", max_length=500)
    data_types: list = Field(default_factory=list)
    message: str = Field(default="", max_length=2000)


@app.get("/api/opt-in")
def optin_coverage():
    return optin.coverage()


@app.get("/api/opt-in/status")
def optin_status(request: Request):
    user = _auth_user(request)
    return {"status": optin.status_for_user(f"u{user['id']}")}


@app.post("/api/opt-in")
def optin_submit(request: Request, payload: OptInRequest):
    user = _auth_user(request)
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Consent is required to opt in.")
    if not payload.data_types:
        raise HTTPException(status_code=400, detail="Select at least one data type.")
    oid = optin.create(
        f"u{user['id']}",
        payload.email or user.get("email", ""),
        payload.name or user.get("name", ""),
        payload.consent,
        payload.areas,
        payload.data_types,
        payload.message,
    )
    return {"ok": True, "id": oid, "status": optin.status_for_user(f"u{user['id']}")}


class OptInStatus(BaseModel):
    status: str = Field(..., max_length=20)


@app.get("/api/admin/opt-ins")
def admin_optins(request: Request, limit: int = 200):
    _auth_user(request, admin_only=True)
    if limit > 500:
        limit = 500
    return {"optins": optin.list_all(limit)}


@app.post("/api/admin/opt-ins/{oid}/status")
def admin_optin_status(request: Request, oid: int, payload: OptInStatus):
    _auth_user(request, admin_only=True)
    if not optin.set_status(oid, payload.status):
        raise HTTPException(status_code=400, detail="Invalid status.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# ADMIN
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
def admin_users(request: Request):
    _auth_user(request, admin_only=True)
    return {"users": admin.list_users()}


class UserRole(BaseModel):
    role: str = Field(..., max_length=20)


@app.post("/api/admin/users/{uid}/role")
def admin_user_role(request: Request, uid: int, payload: UserRole):
    _auth_user(request, admin_only=True)
    if not admin.set_user_role(uid, payload.role):
        raise HTTPException(status_code=400, detail="Invalid role.")
    return {"ok": True}


class UserPlan(BaseModel):
    plan: str = Field(..., max_length=20)


@app.post("/api/admin/users/{uid}/plan")
def admin_user_plan(request: Request, uid: int, payload: UserPlan):
    _auth_user(request, admin_only=True)
    if not admin.set_user_plan(uid, payload.plan.upper()):
        raise HTTPException(status_code=400, detail="Invalid plan.")
    return {"ok": True}


class UserStatus(BaseModel):
    status: str = Field(..., max_length=20)


@app.post("/api/admin/users/{uid}/status")
def admin_user_status(request: Request, uid: int, payload: UserStatus):
    _auth_user(request, admin_only=True)
    if not admin.set_user_status(uid, payload.status):
        raise HTTPException(status_code=400, detail="Invalid status.")
    return {"ok": True}


@app.get("/api/admin/subscriptions")
def admin_subscriptions(request: Request):
    _auth_user(request, admin_only=True)
    return {"subscriptions": billing.list_subscriptions()}


@app.get("/api/admin/usage")
def admin_usage(request: Request):
    _auth_user(request, admin_only=True)
    total = db.run("SELECT COUNT(*) AS c FROM usage_records", fetch="one") or {}
    by_kind = db.run("SELECT kind, COUNT(*) AS c FROM usage_records GROUP BY kind ORDER BY c DESC")
    today = db.run("SELECT COUNT(*) AS c FROM usage_records WHERE created_at > ?", (time.time() - 86400,), fetch="one") or {}
    by_user = db.run(
        "SELECT user_id, COUNT(*) AS c FROM usage_records GROUP BY user_id ORDER BY c DESC LIMIT 20"
    )
    return {
        "total_records": total.get("c") or 0,
        "today": today.get("c") or 0,
        "by_kind": by_kind or [],
        "by_user": by_user or [],
    }


@app.get("/api/admin/observability")
def admin_observability(request: Request):
    _auth_user(request, admin_only=True)
    return {
        "evaluation": evaluation.summary(),
        "failed_runs": evaluation.failed_runs(50),
        "recent_logs": observability.admin_logs(50),
    }


@app.get("/api/admin/monitors")
def admin_monitors(request: Request):
    _auth_user(request, admin_only=True)
    return {"monitors": admin.system_monitors()}


@app.get("/api/admin/alerts")
def admin_alerts(request: Request):
    _auth_user(request, admin_only=True)
    return {"alerts": admin.system_alerts()}


@app.get("/api/admin/thresholds")
def admin_get_thresholds(request: Request):
    _auth_user(request, admin_only=True)
    from lib import tools
    return {"thresholds": tools.effective_thresholds()}


class ThresholdUpdate(BaseModel):
    low: float | None = None
    moderate: float | None = None
    high: float | None = None
    extreme: float | None = None


@app.put("/api/admin/thresholds")
def admin_set_thresholds(request: Request, payload: ThresholdUpdate):
    _auth_user(request, admin_only=True)
    current = admin.get_risk_thresholds() or {}
    merged = {k: payload.__dict__.get(k, current.get(k)) for k in ("low", "moderate", "high", "extreme")}
    try:
        updated = admin.set_risk_thresholds(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"thresholds": updated}


class AdCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(default="", max_length=2000)
    image_url: str = Field(default="", max_length=500)
    link_url: str = Field(default="", max_length=500)


class AdUpdate(AdCreate):
    active: bool = True


@app.get("/api/admin/ads")
def admin_list_ads(request: Request):
    _auth_user(request, admin_only=True)
    return {"ads": admin.list_ads()}


@app.post("/api/admin/ads")
def admin_create_ad(request: Request, payload: AdCreate):
    _auth_user(request, admin_only=True)
    aid = admin.create_ad(payload.title, payload.body, payload.image_url, payload.link_url)
    return {"id": aid}


@app.put("/api/admin/ads/{aid}")
def admin_update_ad(request: Request, aid: int, payload: AdUpdate):
    _auth_user(request, admin_only=True)
    admin.update_ad(aid, payload.title, payload.body, payload.image_url, payload.link_url, payload.active)
    return {"ok": True}


@app.delete("/api/admin/ads/{aid}")
def admin_delete_ad(request: Request, aid: int):
    _auth_user(request, admin_only=True)
    admin.delete_ad(aid)
    return {"ok": True}


@app.get("/api/admin/credit-ledger")
def admin_credit_ledger(request: Request, limit: int = 200):
    _auth_user(request, admin_only=True)
    if limit > 500:
        limit = 500
    return {"ledger": credits.all_ledger(limit)}


@app.get("/api/admin/ad-views")
def admin_ad_views(request: Request, limit: int = 200):
    _auth_user(request, admin_only=True)
    if limit > 500:
        limit = 500
    return {"views": credits.all_views(limit)}


@app.get("/api/admin/evaluation")
def admin_evaluation(request: Request):
    _auth_user(request, admin_only=True)
    return evaluation.summary()


@app.get("/api/admin/runs")
def admin_runs(request: Request, limit: int = 50):
    _auth_user(request, admin_only=True)
    if limit > 500:
        limit = 500
    return {"failed_runs": evaluation.failed_runs(limit)}


@app.get("/api/admin/logs")
def admin_logs(request: Request, limit: int = 100):
    _auth_user(request, admin_only=True)
    if limit > 500:
        limit = 500
    return {"logs": observability.admin_logs(limit)}


@app.get("/api/admin/activity")
def admin_activity(request: Request, limit: int = 50):
    _auth_user(request, admin_only=True)
    if limit > 200:
        limit = 200
    return {"activity": admin.user_activity(limit)}


@app.get("/api/admin/queries")
def admin_queries(request: Request, limit: int = 100):
    _auth_user(request, admin_only=True)
    if limit > 200:
        limit = 200
    return {"queries": admin.user_queries(limit)}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
