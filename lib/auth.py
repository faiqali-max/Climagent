import hashlib
import hmac
import os
import secrets
import time

from lib import db

SESSION_TTL = 7 * 24 * 3600
RESET_TTL = 3600
_ITERATIONS = 260_000


class AuthError(Exception):
    pass


def _hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"{salt}:{digest.hex()}"


def _verify_password(password, stored):
    try:
        salt, digest_hex = stored.split(":", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _valid_email(email):
    email = (email or "").strip()
    if len(email) > 254 or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local and "." in domain and len(domain) <= 253)


def create_user(email, password, name="", role="user"):
    email = (email or "").strip().lower()
    if not _valid_email(email):
        raise AuthError("Invalid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if db.run("SELECT id FROM users WHERE email = ?", (email,), fetch="one"):
        raise AuthError("An account with this email already exists.")
    uid = db.run(
        "INSERT INTO users (email, password_hash, name, role, status, plan, created_at) "
        "VALUES (?, ?, ?, ?, 'active', 'FREE', ?)",
        (email, _hash_password(password), (name or "")[:120], role, time.time()),
    )
    return uid


def authenticate(email, password):
    row = db.run("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),), fetch="one")
    if not row or not _verify_password(password or "", row["password_hash"]):
        raise AuthError("Invalid email or password.")
    if row["status"] != "active":
        raise AuthError("This account is suspended.")
    return row


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    db.run(
        "INSERT INTO sessions (user_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, token, time.time(), time.time() + SESSION_TTL),
    )
    return token


def user_by_token(token):
    if not token:
        return None
    row = db.run(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ? AND u.status = 'active'",
        (token, time.time()),
        fetch="one",
    )
    return row


def logout(token):
    if token:
        db.run("DELETE FROM sessions WHERE token = ?", (token,))


def request_reset(email):
    row = db.run("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),), fetch="one")
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    db.run(
        "INSERT INTO password_resets (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)",
        (row["id"], token, time.time() + RESET_TTL),
    )
    return token


def reset_password(token, password):
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    row = db.run(
        "SELECT * FROM password_resets WHERE token = ? AND used = 0 AND expires_at > ?",
        (token, time.time()),
        fetch="one",
    )
    if not row:
        raise AuthError("Invalid or expired reset token.")
    db.run(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (_hash_password(password), row["user_id"]),
    )
    db.run("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
    return True


def public_user(user):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "plan": user["plan"],
        "status": user["status"],
    }


def bootstrap_admin():
    email = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not email or not password:
        return
    existing = db.run("SELECT id FROM users WHERE email = ?", (email.lower(),), fetch="one")
    if existing:
        db.run("UPDATE users SET role = 'admin', status = 'active' WHERE id = ?", (existing["id"],))
    else:
        create_user(email, password, name="Administrator", role="admin")
