import time
import uuid

from lib import db


def _log(user_id, run_id, kind, agent=None, tool=None, input_text=None,
         output=None, status="ok", error=None, duration_ms=None, tokens=None):
    return db.run(
        "INSERT INTO activity (user_id, run_id, kind, agent, tool, input, output, status, error,"
        " duration_ms, tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, run_id, kind, agent, tool, input_text, output, status, error,
         duration_ms, tokens, time.time()),
    )


def start_run(user_id, input_text):
    run_id = uuid.uuid4().hex[:16]
    _log(user_id, run_id, "run_start", input_text=input_text[:300])
    return run_id


def end_run(user_id, run_id, outcome="completed", error=None, duration_ms=None, tokens=None):
    _log(user_id, run_id, "run_end", output=outcome[:200] if outcome else outcome,
         status="error" if error else "ok", error=error, duration_ms=duration_ms, tokens=tokens)


def log_tool(user_id, run_id, tool, args=None, result=None, status="ok", error=None, duration_ms=None):
    _log(user_id, run_id, "tool_call", tool=tool[:80], input_text=str(args)[:200],
         output=str(result)[:300], status=status, error=error, duration_ms=duration_ms)


def log_event(user_id, run_id, kind, message):
    _log(user_id, run_id, kind, output=message[:300])


def timeline(user_id, limit=60):
    return db.run(
        "SELECT * FROM activity WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )


def admin_logs(limit=100, status=None):
    if status:
        return db.run(
            "SELECT * FROM activity WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
        )
    return db.run("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))


def failed_runs(limit=50):
    return db.run(
        "SELECT * FROM activity WHERE status = 'error' ORDER BY id DESC LIMIT ?", (limit,)
    )
