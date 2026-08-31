"""Ad-credit economy for Climagent.

Free-tier (ads-enabled) users earn credits by watching ads and spend them on actions.
Core rule (per product spec):

  - watching 1 ad  -> +1 credit
  - 1 AI search     -> 1 credit (1 ad)
  - 1 file upload   -> 2 credits (2 ads)
  - 1 agent run     -> 1 credit (1 ad)   [automated monitoring runs]

Every earned/spent credit is recorded in the credits ledger so balances can be
audited and re-computed at any time.
"""
import os
import time

from lib import db, plans

# Configurable via env with defaults matching the product spec.
VIEWS_PER_CREDIT = max(1, int(os.getenv("VIEWS_PER_CREDIT", "1")))
COST_SEARCH = max(1, int(os.getenv("COST_SEARCH", "1")))          # per AI search
COST_UPLOAD = max(1, int(os.getenv("COST_UPLOAD", "2")))          # per file upload
COST_AGENT_RUN = max(1, int(os.getenv("COST_AGENT_RUN", "1")))    # per monitoring agent run

# A user needs at least this many seconds between distinct ad views of the same ad
# so that "views" represent genuine attention, not rapid misclicks.
MIN_VIEW_INTERVAL_S = 30


def balance(user_id):
    """Current credit balance for a user id (u<id> or guest)."""
    if user_id.startswith("u"):
        row = db.run("SELECT credit_balance FROM users WHERE id = ?", (user_id[1:],), fetch="one")
        return row["credit_balance"] if row else 0
    return 0


def mint_credits(user_id, delta, reason, detail=""):
    """A positive adjustment. Returns the new balance or None if the user is unknown."""
    if not user_id.startswith("u"):
        return None
    row = db.run("SELECT id FROM users WHERE id = ?", (user_id[1:],), fetch="one")
    if not row:
        return None
    db.run("UPDATE users SET credit_balance = credit_balance + ? WHERE id = ?", (int(delta), user_id[1:]))
    db.run(
        "INSERT INTO credits (user_id, delta, reason, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, int(delta), reason, detail[:200], time.time()),
    )
    return balance(user_id)


def spend(user_id, amount, reason, detail="", required=True):
    """Spend `amount` credits on an action.

    If required=True and the user lacks enough credits, returns False without debiting
    (caller should reject the action). If required=False it debits what's available.
    Returns the remaining balance on success, else None (account missing or insufficient).
    """
    amount = max(1, int(amount))
    if not user_id.startswith("u"):
        return None
    row = db.run("SELECT credit_balance FROM users WHERE id = ?", (user_id[1:],), fetch="one")
    if not row:
        return None
    if row["credit_balance"] < amount:
        if required:
            return None
        amount = row["credit_balance"]
    db.run("UPDATE users SET credit_balance = credit_balance - ? WHERE id = ?", (amount, user_id[1:]))
    db.run(
        "INSERT INTO credits (user_id, delta, reason, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, -amount, reason, detail[:200], time.time()),
    )
    return balance(user_id)


def can_view_ad(user_id, ad_id):
    """Prevent degenerate rapid re-views of the same ad for credit farming."""
    if not user_id.startswith("u"):
        return False
    row = db.run(
        "SELECT created_at FROM ad_views WHERE user_id = ? AND ad_id = ? ORDER BY id DESC LIMIT 1",
        (user_id, ad_id),
        fetch="one",
    )
    if row and (time.time() - row["created_at"]) < MIN_VIEW_INTERVAL_S:
        return False
    return True


def record_ad_view(user_id, ad_id):
    """Record one ad view and award VIEWS_PER_CREDIT credits (1 ad -> 1 credit by default)."""
    if not user_id.startswith("u"):
        return None
    if not can_view_ad(user_id, ad_id):
        return None
    db.run(
        "INSERT INTO ad_views (user_id, ad_id, kind, created_at) VALUES (?, ?, 'view', ?)",
        (user_id, ad_id, time.time()),
    )
    return mint_credits(user_id, VIEWS_PER_CREDIT, "ad_credit", f"watched ad #{ad_id}")


def ad_view_summary(user_id):
    """Return view counts and action cost summary."""
    views_today = db.run(
        "SELECT COUNT(*) AS c FROM ad_views WHERE user_id = ? AND kind = 'view' AND created_at > ?",
        (user_id, time.time() - 24 * 3600),
        fetch="one",
    )["c"]
    return {
        "views_today": views_today,
        "views_per_credit": VIEWS_PER_CREDIT,
        "costs": {
            "search": COST_SEARCH,
            "upload": COST_UPLOAD,
            "agent_run": COST_AGENT_RUN,
        },
    }


def ledger(user_id, limit=100):
    return db.run(
        "SELECT * FROM credits WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )


def all_views(limit=500):
    return db.run("SELECT * FROM ad_views ORDER BY id DESC LIMIT ?", (limit,))


def all_ledger(limit=500):
    return db.run("SELECT * FROM credits ORDER BY id DESC LIMIT ?", (limit,))


def needs_credits(user):
    """True when the user is on the FREE (ads-enabled) tier and should pay credits for actions."""
    return bool(plans.limits(user)["ads"])


def needs_credits_by_id(user_id):
    """True when the user (u<id>) is on the FREE (ads-enabled) tier."""
    if not user_id.startswith("u"):
        return False
    try:
        row = db.run("SELECT plan FROM users WHERE id = ?", (user_id[1:],), fetch="one")
    except Exception:
        return False
    return bool(row and plans.limits({"plan": row["plan"]})["ads"])


