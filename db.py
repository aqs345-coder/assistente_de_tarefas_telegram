import sqlite3
from datetime import date
from contextlib import contextmanager

from config import DATABASE_PATH

@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                time TEXT NOT NULL,
                is_urgent INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )

def add_reminder(user_id, chat_id, text, start_date, end_date, time, is_urgent):
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO reminders (user_id, chat_id, text, start_date, end_date, time, is_urgent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, text, start_date, end_date, time, is_urgent, date.today().isoformat()),
        )
        return cur.lastrowid

def list_active_reminders(user_id):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND active = 1 ORDER BY time",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]

def list_all_active_reminders():
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM reminders WHERE active = 1")
        return [dict(r) for r in cur.fetchall()]

def get_reminder(reminder_id):
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def deactivate_reminder(reminder_id):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE reminders SET active = 0 WHERE id = ? AND active = 1", (reminder_id,)
        )
        return cur.rowcount > 0