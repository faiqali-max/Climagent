"""Opt-in form & auto-responder.

Users can opt in to expanded real-time data collection across all FortyGuard
regions. Submissions are stored locally and an automatic acknowledgment reply is
generated on receipt (the "auto-responder"). Admins can review and respond.

No personal data is transmitted off-platform; email is used only to identify the
requester and is stored locally.
"""
import time

from lib import db

COVERAGE_LABEL = "All FortyGuard Regions"
DATACOLLECTION_LABEL = "Real-Time Data Collection"

ALLOWED_DATA_TYPES = [
    "temperature",
    "forecast",
    "heatmap",
    "humidity",
    "air_quality",
    "solar_irradiance",
]


def create(user_id, email, name, consent, areas, data_types, message):
    normalized_types = []
    for dt in data_types or []:
        dt = dt.strip().lower()
        if dt in ALLOWED_DATA_TYPES and dt not in normalized_types:
            normalized_types.append(dt)
    auto = (
        f"Thank you{(' ' + name) if name else ''}, your request has been received. "
        "We will be collecting real-time climate data across all FortyGuard regions "
        "for the selected data types. You will be notified once expanded collection is active."
    )
    return db.run(
        "INSERT INTO optins (user_id, email, name, consent, areas, data_types, message, "
        "status, auto_response, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (
            user_id,
            (email or "").strip()[:254],
            (name or "").strip()[:120],
            1 if consent else 0,
            (areas or COVERAGE_LABEL)[:500],
            ",".join(normalized_types)[:500],
            (message or "").strip()[:2000],
            auto[:2000],
            time.time(),
        ),
    )


def status_for_user(user_id):
    row = db.run(
        "SELECT * FROM optins WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
        fetch="one",
    )
    if not row:
        return {"opted_in": False, "coverage": COVERAGE_LABEL, "collection": DATACOLLECTION_LABEL}
    return {
        "opted_in": True,
        "id": row["id"],
        "consent": bool(row["consent"]),
        "areas": row["areas"],
        "data_types": [d for d in (row["data_types"] or "").split(",") if d],
        "status": row["status"],
        "auto_response": row["auto_response"],
        "created_at": row["created_at"],
        "coverage": COVERAGE_LABEL,
        "collection": DATACOLLECTION_LABEL,
    }


def list_all(limit=200):
    rows = db.run("SELECT * FROM optins ORDER BY id DESC LIMIT ?", (limit,))
    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "email": r["email"],
            "name": r["name"],
            "consent": bool(r["consent"]),
            "areas": r["areas"],
            "data_types": [d for d in (r["data_types"] or "").split(",") if d],
            "message": r["message"],
            "status": r["status"],
            "auto_response": r["auto_response"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def set_status(optin_id, status):
    if status not in ("pending", "approved", "rejected"):
        return False
    db.run(
        "UPDATE optins SET status = ?, responded_at = ? WHERE id = ?",
        (status, time.time(), optin_id),
    )
    return True


def coverage():
    return {"coverage": COVERAGE_LABEL, "collection": DATACOLLECTION_LABEL}
