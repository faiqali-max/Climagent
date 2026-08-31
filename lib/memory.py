import re
import time

from lib import db

KINDS = ("preference", "project", "location", "plan", "decision", "context", "analysis")


def _score(query, text):
    q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not q_words:
        return 0.0
    t_words = re.findall(r"[a-z0-9]+", text.lower())
    if not t_words:
        return 0.0
    return sum(1 for w in q_words if w in t_words) / len(q_words)


def add(user_id, kind, content, meta=None):
    kind = kind if kind in KINDS else "context"
    return db.run(
        "INSERT INTO memories (user_id, kind, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, content, _json(meta), time.time()),
    )


def _json(value):
    import json
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def search(user_id, query, limit=6):
    rows = db.run(
        "SELECT * FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT 200", (user_id,)
    )
    scored = []
    for row in rows:
        score = _score(query, row["content"])
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: (x[0], x[1]["created_at"]), reverse=True)
    return [row for _, row in scored[:limit]]


def recall_context(user_id, query, limit=5):
    rows = search(user_id, query, limit)
    if not rows:
        return ""
    return "\n".join(f"- [{r['kind']}] {r['content']}" for r in rows)


def list_all(user_id, limit=100):
    return db.run(
        "SELECT * FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
    )


def delete(user_id, memory_id):
    db.run("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))
    return True


def clear(user_id):
    db.run("DELETE FROM memories WHERE user_id = ?", (user_id,))
    return True
