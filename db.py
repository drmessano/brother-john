import os
from datetime import datetime, timezone, timedelta
from tinydb import TinyDB, Query

DB_PATH = os.getenv("DB_PATH", "brother-john.json")
RATE_LIMIT = 20          # max study/verse requests
RATE_WINDOW = 3600       # per hour (seconds)

_db: TinyDB | None = None


def _get_db() -> TinyDB:
    global _db
    if _db is None:
        _db = TinyDB(DB_PATH)
    return _db


def init_db():
    """No-op for TinyDB — file is created on first write."""
    _get_db()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_user(user_id: int) -> dict | None:
    User = Query()
    result = _get_db().search(User.user_id == user_id)
    return result[0] if result else None


def upsert_user(user_id: int, **kwargs):
    User = Query()
    db = _get_db()
    existing = db.search(User.user_id == user_id)
    if existing:
        if kwargs:
            db.update(kwargs, User.user_id == user_id)
    else:
        doc = {
            "user_id": user_id,
            "chat_id": user_id,  # default to same as user_id, updated on /daily
            "translation": "KJV",
            "timezone": "America/New_York",
            "daily_time": None,
            "requests": [],
        }
        doc.update(kwargs)
        db.insert(doc)


def get_daily_subscribers() -> list[dict]:
    User = Query()
    return _get_db().search(User.daily_time.test(lambda v: v is not None))


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Check if user is within rate limit.
    Returns (allowed: bool, remaining: int).
    """
    upsert_user(user_id)  # ensure user exists
    user = get_user(user_id)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=RATE_WINDOW)).isoformat()

    # Prune old requests
    recent = [r for r in user.get("requests", []) if r >= cutoff]
    remaining = max(0, RATE_LIMIT - len(recent))
    allowed = len(recent) < RATE_LIMIT

    if allowed:
        recent.append(now.isoformat())

    User = Query()
    _get_db().update({"requests": recent}, User.user_id == user_id)

    return allowed, remaining
