import logging
import os
from datetime import datetime, date, timezone, timedelta
from tinydb import TinyDB, Query

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "brother-john.json")
RATE_LIMIT = 20          # max study generations
RATE_WINDOW = 3600       # per hour (seconds)
LOOKUP_RATE_LIMIT = 60   # max passage lookups (cheaper — no Claude call)
LOOKUP_RATE_WINDOW = 3600

_db: TinyDB | None = None


def _get_db() -> TinyDB:
    global _db
    if _db is None:
        _db = TinyDB(DB_PATH)
    return _db


def _users(db: TinyDB):
    return db.table("users")


def _study_cache(db: TinyDB):
    return db.table("study_cache")


def init_db():
    """No-op for TinyDB — file is created on first write."""
    _get_db()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_user(user_id: int) -> dict | None:
    User = Query()
    result = _users(_get_db()).search(User.user_id == user_id)
    return result[0] if result else None


def upsert_user(user_id: int, **kwargs):
    User = Query()
    table = _users(_get_db())
    existing = table.search(User.user_id == user_id)
    if existing:
        if kwargs:
            table.update(kwargs, User.user_id == user_id)
    else:
        doc = {
            "user_id": user_id,
            "chat_id": user_id,
            "translation": "KJV",
            "timezone": "America/New_York",
            "daily_time": None,
            "requests": [],
            "cache_used_date": None,
        }
        doc.update(kwargs)
        table.insert(doc)


def get_daily_subscribers() -> list[dict]:
    User = Query()
    return _users(_get_db()).search(User.daily_time.test(lambda v: v is not None))


def get_all_users() -> list[dict]:
    return _users(_get_db()).all()


def delete_user(user_id: int):
    User = Query()
    _users(_get_db()).remove(User.user_id == user_id)
    logger.info(f"Deleted user {user_id} from DB")


def get_active_translations() -> list[str]:
    """Return distinct translations currently in use across all users."""
    all_users = _users(_get_db()).all()
    return list({u.get("translation", "KJV") for u in all_users})


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(
    user_id: int,
    limit: int = RATE_LIMIT,
    window: int = RATE_WINDOW,
    field: str = "requests",
) -> tuple[bool, int]:
    """Check if user is within the given rate limit bucket.
    Returns (allowed: bool, remaining: int).
    Separate buckets (e.g. study vs lookup) are tracked via distinct `field`s.
    """
    upsert_user(user_id)
    user = get_user(user_id)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=window)).isoformat()

    recent = [r for r in user.get(field, []) if r >= cutoff]
    remaining = max(0, limit - len(recent))
    allowed = len(recent) < limit

    if allowed:
        recent.append(now.isoformat())

    User = Query()
    _users(_get_db()).update({field: recent}, User.user_id == user_id)

    return allowed, remaining


# ---------------------------------------------------------------------------
# Daily study cache
# ---------------------------------------------------------------------------

def get_cached_study(translation: str) -> dict | None:
    """Return today's pre-generated study for a translation, or None."""
    Cache = Query()
    today = date.today().isoformat()
    result = _study_cache(_get_db()).search(
        (Cache.translation == translation.upper()) & (Cache.cached_date == today)
    )
    return result[0] if result else None


def set_cached_study(translation: str, reference: str, study_text: str):
    """Store a pre-generated study for today."""
    Cache = Query()
    today = date.today().isoformat()
    table = _study_cache(_get_db())
    table.remove((Cache.translation == translation.upper()))
    table.insert({
        "translation": translation.upper(),
        "reference": reference,
        "study_text": study_text,
        "cached_date": today,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    })


def clear_study_cache():
    """Clear all cached studies (called at midnight before pre-fetch)."""
    _study_cache(_get_db()).truncate()


def has_used_cache_today(user_id: int) -> bool:
    """Return True if this user already consumed the cached study today."""
    user = get_user(user_id)
    if not user:
        return False
    return user.get("cache_used_date") == date.today().isoformat()


def mark_cache_used(user_id: int):
    """Record that this user has consumed today's cached study."""
    User = Query()
    _users(_get_db()).update(
        {"cache_used_date": date.today().isoformat()},
        User.user_id == user_id,
    )
