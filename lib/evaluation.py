from lib import db


def summary():
    total = db.run("SELECT COUNT(*) AS c FROM activity WHERE kind IN ('run_start','run_end')", fetch="one") or {}
    errors = db.run("SELECT COUNT(*) AS c FROM activity WHERE status = 'error'", fetch="one") or {}
    completed = db.run("SELECT COUNT(*) AS c FROM activity WHERE kind = 'run_end' AND status = 'ok'", fetch="one") or {}
    tools = db.run(
        "SELECT tool, COUNT(*) AS c FROM activity WHERE kind = 'tool_call' GROUP BY tool ORDER BY c DESC LIMIT 10"
    )
    agents = db.run(
        "SELECT agent, COUNT(*) AS c FROM activity WHERE agent IS NOT NULL GROUP BY agent ORDER BY c DESC LIMIT 10"
    )
    avg_dur = db.run(
        "SELECT AVG(duration_ms) AS d FROM activity WHERE kind = 'run_end'", fetch="one"
    ) or {}
    last_24h = db.run(
        "SELECT COUNT(*) AS c FROM activity WHERE created_at > (strftime('%s','now') - 86400)",
        fetch="one",
    ) or {}
    tasks = max(1, (total.get("c") or 0))
    return {
        "runs": total.get("c") or 0,
        "completed_ok": completed.get("c") or 0,
        "completion_rate": round(100 * (completed.get("c") or 0) / tasks, 1),
        "errors": errors.get("c") or 0,
        "error_rate": round(100 * (errors.get("c") or 0) / tasks, 1),
        "avg_duration_ms": round(avg_dur.get("d") or 0, 1),
        "activity_last_24h": last_24h.get("c") or 0,
        "tool_usage": tools,
        "agent_usage": agents,
    }


def failed_runs(limit=50):
    return db.run(
        "SELECT * FROM activity WHERE status = 'error' ORDER BY id DESC LIMIT ?", (limit,)
    )
