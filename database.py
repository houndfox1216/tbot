import aiosqlite
from datetime import datetime, timezone

DB_PATH = "tbot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                chat_id    INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                full_name  TEXT,
                last_seen  TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id       INTEGER PRIMARY KEY,
                inactive_days INTEGER NOT NULL DEFAULT 30
            )
        """)
        await db.commit()


async def update_activity(chat_id: int, user_id: int, username: str | None, full_name: str):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_activity (chat_id, user_id, username, full_name, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name,
                last_seen = excluded.last_seen
        """, (chat_id, user_id, username, full_name, now))
        await db.commit()


async def get_inactive_users(chat_id: int, days: int) -> list[dict]:
    """Return users in chat_id who haven't been seen in `days` days."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT user_id, username, full_name, last_seen,
                   CAST((julianday('now') - julianday(last_seen)) AS INTEGER) AS days_inactive
            FROM user_activity
            WHERE chat_id = ?
              AND last_seen < datetime('now', ? || ' days')
            ORDER BY last_seen ASC
        """, (chat_id, f"-{days}")) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def remove_user(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM user_activity WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await db.commit()


async def get_inactive_days(chat_id: int, default: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT inactive_days FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else default


async def set_inactive_days(chat_id: int, days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chat_settings (chat_id, inactive_days)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET inactive_days = excluded.inactive_days
        """, (chat_id, days))
        await db.commit()


async def get_all_chat_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT chat_id FROM user_activity"
        ) as cursor:
            rows = await cursor.fetchall()
    return [r[0] for r in rows]
