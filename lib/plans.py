import time

from lib import db

PLANS = {
    "FREE": {
        "label": "Free",
        "ai_requests_per_day": 10,
        "max_projects": 3,
        "max_monitors": 1,
        "max_uploads": 5,
        "max_upload_mb": 10,
        "ads": True,
    },
    "PREMIUM": {
        "label": "Premium",
        "ai_requests_per_day": 100,
        "max_projects": 20,
        "max_monitors": 10,
        "max_uploads": 50,
        "max_upload_mb": 50,
        "ads": False,
    },
    "PROFESSIONAL": {
        "label": "Professional",
        "ai_requests_per_day": 1000,
        "max_projects": 100,
        "max_monitors": 50,
        "max_uploads": 500,
        "max_upload_mb": 100,
        "ads": False,
    },
}

DEFAULT_PLAN = "FREE"


def _plan_of(user):
    plan = (user.get("plan") or DEFAULT_PLAN).upper()
    return plan if plan in PLANS else DEFAULT_PLAN


def limits(user):
    return PLANS[_plan_of(user)]


def used_today(user_id, kind):
    start = time.time() - 24 * 3600
    row = db.run(
        "SELECT COUNT(*) AS c FROM usage_records WHERE user_id = ? AND kind = ? AND created_at > ?",
        (user_id, kind, start),
        fetch="one",
    )
    return row["c"] if row else 0


def record_usage(user_id, kind):
    db.run(
        "INSERT INTO usage_records (user_id, kind, created_at) VALUES (?, ?, ?)",
        (user_id, kind, time.time()),
    )


def check_ai_limit(user):
    """Return (ok, used, limit) for AI requests today."""
    limits_ = limits(user)
    used = used_today(str(user["id"]), "ai_request")
    return used < limits_["ai_requests_per_day"], used, limits_["ai_requests_per_day"]


def count_rows(user_id, table):
    row = db.run(f"SELECT COUNT(*) AS c FROM {table} WHERE user_id = ?", (user_id,), fetch="one")
    return row["c"] if row else 0


def usage_summary(user):
    limits_ = limits(user)
    uid = str(user["id"])
    return {
        "plan": _plan_of(user),
        "plan_label": limits_["label"],
        "limits": limits_,
        "usage": {
            "ai_requests": used_today(uid, "ai_request"),
            "projects": count_rows(uid, "projects"),
            "monitors": count_rows(uid, "monitors"),
            "uploads": count_rows(uid, "uploaded_files"),
        },
    }


def get_subscription(user_id):
    return db.run(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,), fetch="one"
    )


def set_subscription(user_id, plan, provider="manual"):
    db.run(
        "INSERT INTO subscriptions (user_id, plan, status, provider, created_at, updated_at) "
        "VALUES (?, ?, 'active', ?, ?, ?)",
        (user_id, plan, provider, time.time(), time.time()),
    )
    db.run("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
