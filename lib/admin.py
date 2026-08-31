import json
import time

from lib import db, plans


def list_users(limit=200):
    rows = db.run("SELECT * FROM users ORDER BY id ASC LIMIT ?", (limit,))
    return [auth_public(u) for u in rows]


def auth_public(user):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "plan": user["plan"],
        "status": user["status"],
        "credit_balance": user.get("credit_balance", 0),
        "created_at": user["created_at"],
    }


def set_user_role(user_id, role):
    if role not in ("user", "premium", "admin"):
        return False
    db.run("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return True


def set_user_plan(user_id, plan):
    if plan not in plans.PLANS:
        return False
    db.run("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
    return True


def set_user_status(user_id, status):
    if status not in ("active", "suspended"):
        return False
    db.run("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    return True


def get_risk_thresholds():
    raw = db.get_setting("risk_thresholds")
    if raw:
        try:
            data = json.loads(raw)
            return {k: float(data[k]) for k in ("low", "moderate", "high", "extreme") if k in data}
        except (ValueError, TypeError):
            pass
    return None


def set_risk_thresholds(values):
    cleaned = {}
    for key, default in (("low", 27.0), ("moderate", 31.0), ("high", 35.0), ("extreme", 40.0)):
        try:
            val = float(values.get(key, default))
        except (TypeError, ValueError):
            val = default
        cleaned[key] = val
    if not (cleaned["low"] < cleaned["moderate"] < cleaned["high"] < cleaned["extreme"]):
        raise ValueError("Thresholds must be strictly increasing: low < moderate < high < extreme.")
    db.set_setting("risk_thresholds", json.dumps(cleaned))
    return cleaned


def list_ads(active_only=False):
    sql = "SELECT * FROM ads"
    if active_only:
        sql += " WHERE active = 1"
    return db.run(sql + " ORDER BY id ASC")


def create_ad(title, body, image_url, link_url):
    return db.run(
        "INSERT INTO ads (title, body, image_url, link_url, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (title, body, image_url, link_url, time.time()),
    )


def update_ad(ad_id, title, body, image_url, link_url, active):
    db.run(
        "UPDATE ads SET title = ?, body = ?, image_url = ?, link_url = ?, active = ? WHERE id = ?",
        (title, body, image_url, link_url, 1 if active else 0, ad_id),
    )


def delete_ad(ad_id):
    db.run("DELETE FROM ads WHERE id = ?", (ad_id,))


def system_monitors(limit=100):
    return db.run("SELECT * FROM monitors ORDER BY id DESC LIMIT ?", (limit,))


def system_alerts(limit=100):
    return db.run("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))


def user_activity(limit=50):
    """All user activity across the platform, newest first."""
    rows = db.run(
        "SELECT 'project' AS kind, id, user_id, name AS detail, created_at "
        "FROM projects "
        "UNION ALL "
        "SELECT 'conversation', id, user_id, title, created_at "
        "FROM conversations "
        "UNION ALL "
        "SELECT 'upload', id, user_id, original_name, created_at "
        "FROM uploaded_files "
        "UNION ALL "
        "SELECT 'climate_plan', id, user_id, name, created_at "
        "FROM climate_plans "
        "UNION ALL "
        "SELECT 'construction', id, user_id, name, created_at "
        "FROM construction_projects "
        "UNION ALL "
        "SELECT 'monitor', id, user_id, query, created_at "
        "FROM monitors "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return rows or []


def user_queries(limit=100):
    """What users are asking: all user messages across conversations."""
    rows = db.run(
        "SELECT m.id AS msg_id, m.conversation_id, c.user_id, c.title, "
        "SUBSTR(m.content, 1, 300) AS content_preview, m.created_at "
        "FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.role = 'user' "
        "ORDER BY m.created_at DESC LIMIT ?",
        (limit,),
    )
    return rows or []
