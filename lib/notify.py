"""In-app and email notifications for Climagent.

Email is OPTIONAL and degrades gracefully: if SMTP is not configured, only the
in-app notification is stored. No exception ever bubbles up to callers, so a
misconfigured or unreachable mail server can never break a login/broadcast.

Keys are read strictly from the environment (.env); never hardcoded.
"""
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from lib import db
from lib.llm import is_configured, PLACEHOLDERS


def _cfg(name):
    return os.getenv(name, "").strip()


def email_enabled():
    """True only when a real (non-placeholder) SMTP host is configured."""
    host = _cfg("SMTP_HOST")
    return bool(host) and host.lower() not in PLACEHOLDERS


def _default_from():
    return _cfg("SMTP_FROM") or _cfg("SMTP_USERNAME") or "no-reply@climagent.local"


def send_email(to_email, subject, html):
    """Best-effort email send. Returns True on success, False otherwise (never raises)."""
    if not to_email or not email_enabled():
        return False
    try:
        host = _cfg("SMTP_HOST")
        port = int(_cfg("SMTP_PORT") or 587)
        username = _cfg("SMTP_USERNAME")
        password = _cfg("SMTP_PASSWORD")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _default_from()
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            if _cfg("SMTP_TLS", "1").lower() in ("1", "true", "yes"):
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.sendmail(_default_from(), [to_email], msg.as_string())
        return True
    except Exception:
        return False


def _user_email(user):
    return (user or {}).get("email") or ""


def notify(user_id, kind, title, body, email_to="", html=""):
    """Create an in-app notification; optionally email it. Never raises."""
    try:
        db.run(
            "INSERT INTO notifications (user_id, kind, title, body, read, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, kind[:40], title[:255], body[:2000], time.time()),
        )
        if email_to and html:
            subject = f"[Climagent] {title}"
            send_email(email_to, subject, html)
    except Exception:
        pass


def create_broadcast(title, body, html=""):
    """Notify every active user (in-app) and email them. Returns the number notified."""
    users = db.run("SELECT id, email FROM users WHERE status = 'active'")
    count = 0
    for u in users:
        uid = f"u{u['id']}"
        notify(uid, "update", title, body, email_to=u.get("email") or "", html=html)
        count += 1
    return count


def list_for_user(user_id, limit=30):
    rows = db.run(
        "SELECT id, kind, title, body, read, created_at FROM notifications "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, int(limit)),
    )
    unread = db.run(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read = 0",
        (user_id,),
        fetch="one",
    )
    return {"notifications": rows, "unread": (unread or {}).get("c", 0)}


def mark_read(user_id, notification_id=None):
    if notification_id:
        db.run(
            "UPDATE notifications SET read = 1 WHERE user_id = ? AND id = ?",
            (user_id, int(notification_id)),
        )
    else:
        db.run("UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0", (user_id,))


def on_login(user):
    """Called after a successful login: record a 'signed in' notification + optional email."""
    uid = f"u{user['id']}"
    email = _user_email(user)
    body = f"Signed in to Climagent on {time.strftime('%Y-%m-%d %H:%M:%S')}."
    html = f"<p>You just signed in to <b>Climagent</b>.</p><p>{body}</p>"
    notify(uid, "info", "Signed in", body, email_to=email, html=html)
