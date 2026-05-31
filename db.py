import sqlite3
from contextlib import contextmanager

DB_PATH = "biblebot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    translation TEXT NOT NULL DEFAULT 'ESV',
    daily_time TEXT,          -- HH:MM in UTC, NULL means opted out
    timezone TEXT NOT NULL DEFAULT 'America/New_York'
);
"""


def init_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_user(user_id: int) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def upsert_user(user_id: int, **kwargs):
    with _conn() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                conn.execute(f"UPDATE users SET {sets} WHERE user_id = ?", (*kwargs.values(), user_id))
        else:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                conn.execute(f"UPDATE users SET {sets} WHERE user_id = ?", (*kwargs.values(), user_id))


def get_daily_subscribers() -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE daily_time IS NOT NULL").fetchall()
