import asyncio
import json
import os
import time

from langchain_core.messages import HumanMessage

from lib import db, observability

MODES = ("suggestion", "approval", "automated")
DEFAULT_MODE = os.getenv("HITL_MODE_DEFAULT", "suggestion")


def get_mode(user_id):
    row = db.run("SELECT value FROM settings WHERE user_id = ? AND key = 'hitl_mode'", (user_id,), fetch="one")
    return row["value"] if row and row["value"] in MODES else DEFAULT_MODE


def set_mode(user_id, mode):
    if mode not in MODES:
        return False
    db.run(
        "INSERT INTO settings (user_id, key, value) VALUES (?, 'hitl_mode', ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
        (user_id, mode),
    )
    return True


def create_approval(user_id, action, payload):
    return db.run(
        "INSERT INTO approvals (user_id, action, payload, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
        (user_id, action, json.dumps(payload, ensure_ascii=False, default=str), time.time()),
    )


def list_approvals(user_id):
    return db.run(
        "SELECT * FROM approvals WHERE user_id = ? AND status = 'pending' ORDER BY id DESC", (user_id,)
    )


def decide_approval(user_id, approval_id, approve):
    row = db.run(
        "SELECT * FROM approvals WHERE id = ? AND user_id = ?", (approval_id, user_id), fetch="one"
    )
    if not row:
        return False
    db.run(
        "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
        ("approved" if approve else "rejected", time.time(), approval_id),
    )
    return True


def create_monitor(user_id, query, lat, lon, interval_min, max_runs):
    interval = max(1.0, float(interval_min))
    max_r = max(1, int(max_runs))
    now = time.time()
    return db.run(
        "INSERT INTO monitors (user_id, query, lat, lon, interval_min, max_runs, status,"
        " next_run_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (user_id, str(query)[:300], lat, lon, interval, max_r, now + interval, now),
    )


def list_monitors(user_id):
    return db.run("SELECT * FROM monitors WHERE user_id = ? ORDER BY id DESC", (user_id,))


def stop_monitor(user_id, monitor_id):
    return db.run(
        "UPDATE monitors SET status = 'stopped' WHERE id = ? AND user_id = ?", (monitor_id, user_id)
    )


def delete_monitor(user_id, monitor_id):
    db.run("DELETE FROM monitors WHERE id = ? AND user_id = ?", (monitor_id, user_id))
    db.run("DELETE FROM alerts WHERE monitor_id = ? AND user_id = ?", (monitor_id, user_id))
    return True


def get_alerts(user_id, limit=50):
    return db.run("SELECT * FROM alerts WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))


def mark_alerts_read(user_id):
    db.run("UPDATE alerts SET read = 1 WHERE user_id = ? AND read = 0", (user_id,))


def _insert_alert(user_id, monitor_id, message, level):
    db.run(
        "INSERT INTO alerts (user_id, monitor_id, message, level, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, monitor_id, message, level, time.time()),
    )


def _extract_json(text):
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def _handle_alert(monitor, risk, change, summary):
    user_id = monitor["user_id"]
    message = f"Monitor {monitor['id']}: risk level '{risk}' for '{monitor['query'][:60]}'"
    if change:
        message += "; significant change detected vs previous run"
    mode = get_mode(user_id)
    if mode == "automated":
        _insert_alert(user_id, monitor["id"], message, "auto")
        observability.log_event(user_id, None, "alert_auto", message)
    elif mode == "approval":
        create_approval(user_id, "send_external_alert", {"monitor_id": monitor["id"], "message": message})
        _insert_alert(user_id, monitor["id"], message + " (awaiting approval)", "pending-approval")
    else:
        _insert_alert(user_id, monitor["id"], message + " (advisory - human approval recommended)", "advisory")


async def run_monitor(monitor):
    from lib import agents, credits
    # Free-tier users pay 1 credit per monitoring agent run. If they cannot pay,
    # pause the monitor and notify them rather than silently charging nothing.
    if credits.needs_credits_by_id(monitor["user_id"]):
        remaining = credits.spend(monitor["user_id"], credits.COST_AGENT_RUN, "agent_run",
                                  f"monitor {monitor['id']} run")
        if remaining is None:
            _insert_alert(monitor["user_id"], monitor["id"],
                          "Paused: no credits left for this monitoring run. Watch an ad to earn credits.",
                          "no-credit")
            db.run("UPDATE monitors SET status = 'paused' WHERE id = ?", (monitor["id"],))
            return None
    agent = agents.build_monitor_agent()
    prev = monitor["last_result"] or "No previous reading."
    prompt = (
        f"Monitoring task: {monitor['query']}\nLocation: lat={monitor['lat']}, lon={monitor['lon']}\n"
        f"Previous result: {prev}\n"
        "Fetch live data with tools, then reply with ONLY a JSON object exactly like: "
        '{"current_temp_c": <number>, "risk_level": "<low|moderate|high|extreme>", '
        '"significant_change": <true|false>, "recommendation": "<short sentence>"}'
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]}, config={"recursion_limit": 20}
    )
    reply = result["messages"][-1].content or ""
    verdict = _extract_json(reply) or {}
    risk = verdict.get("risk_level", "unknown")
    change = bool(verdict.get("significant_change", False))
    summary = {
        "temp_c": verdict.get("current_temp_c"),
        "risk_level": risk,
        "significant_change": change,
        "recommendation": str(verdict.get("recommendation", reply))[:500],
    }
    db.run(
        "INSERT INTO monitoring_results (monitor_id, user_id, result, created_at) VALUES (?, ?, ?, ?)",
        (monitor["id"], monitor["user_id"], json.dumps(summary, ensure_ascii=False), time.time()),
    )
    if risk in ("high", "extreme") or change:
        _handle_alert(monitor, risk, change, summary)
    db.run(
        "UPDATE monitors SET run_count = run_count + 1, last_result = ?, last_run_at = ?,"
        " next_run_at = ? WHERE id = ?",
        (json.dumps(summary, ensure_ascii=False), time.time(),
         time.time() + float(monitor["interval_min"] * 60), monitor["id"]),
    )
    current = db.run("SELECT * FROM monitors WHERE id = ?", (monitor["id"],), fetch="one")
    if current and current["run_count"] >= current["max_runs"]:
        db.run("UPDATE monitors SET status = 'completed' WHERE id = ?", (monitor["id"],))
    observability.log_event(monitor["user_id"], None, "monitor_run", f"monitor {monitor['id']} risk={risk}")
    return summary


async def tick():
    due = db.run("SELECT * FROM monitors WHERE status = 'active' AND next_run_at <= ?", (time.time(),))
    for monitor in due:
        try:
            await run_monitor(monitor)
        except Exception as exc:
            observability.log_event(monitor["user_id"], None, "monitor_error", str(exc)[:300])


_stop = None
_task = None


async def scheduler_loop():
    while not _stop.is_set():
        try:
            await tick()
        except Exception:
            pass
        await asyncio.sleep(30)


def start_scheduler():
    global _stop, _task
    if _task and not _task.done():
        return _task
    _stop = asyncio.Event()
    _task = asyncio.create_task(scheduler_loop())
    return _task


async def stop_scheduler():
    global _stop, _task
    if _task and not _task.done():
        _stop.set()
        try:
            await asyncio.wait_for(_task, timeout=10)
        except Exception:
            _task.cancel()
        _task = None
