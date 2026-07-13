"""SQLite database layer for Medusa Suggestions.

Manages all data persistence including:
- Show suggestions (pending/approved/ignored)
- Global admin settings (API keys, filters, layout)
- User accounts and authentication
- Per-user filter preference overrides
- Streaming provider cache (24h TTL)
"""

import aiosqlite
from passlib.hash import bcrypt
from config import DATABASE_PATH, ADMIN_PASSWORD

# ==========================================================================
# DATABASE SCHEMA
# ==========================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER NOT NULL UNIQUE,
    tvdb_id INTEGER,
    title TEXT NOT NULL,
    overview TEXT,
    poster_path TEXT,
    first_air_date TEXT,
    suggested_by TEXT DEFAULT 'anonymous',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tvmaze_cache (
    tmdb_id INTEGER PRIMARY KEY,
    last_aired TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS providers_cache (
    tmdb_id INTEGER PRIMARY KEY,
    providers_json TEXT NOT NULL,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

# ==========================================================================
# DEFAULT SETTINGS
# ==========================================================================

# Default settings (used if not yet saved in DB)
DEFAULT_SETTINGS = {
    "allowed_languages": "en",
    "excluded_countries": "",
    "excluded_genres": "",
    "min_vote_average": "0",
    "medusa_url": "",
    "medusa_api_key": "",
    "tmdb_api_key": "",
    "my_streaming_services": "",
    "card_min_width": "200",
    "grid_gap": "1.5",
    "show_age_threshold": "10",
}

# Keys that users can override with personal preferences
USER_OVERRIDABLE_KEYS = {
    "allowed_languages",
    "excluded_countries",
    "excluded_genres",
    "min_vote_average",
}


# ==========================================================================
# DATABASE CONNECTION
# ==========================================================================

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()

        # Create default admin user if no users exist
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        if row["cnt"] == 0:
            pw_hash = bcrypt.hash(ADMIN_PASSWORD)
            await db.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                ("admin", pw_hash),
            )
            await db.commit()
    finally:
        await db.close()


# ==========================================================================
# USER MANAGEMENT
# ==========================================================================

async def get_user_by_username(username: str) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_user_by_id(user_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def verify_user(username: str, password: str) -> dict | None:
    """Verify credentials and return user dict or None."""
    user = await get_user_by_username(username)
    if user and bcrypt.verify(password, user["password_hash"]):
        return user
    return None


async def create_user(username: str, password: str, is_admin: bool = False) -> dict | None:
    db = await get_db()
    try:
        pw_hash = bcrypt.hash(password)
        await db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, pw_hash, 1 if is_admin else 0),
        )
        await db.commit()
        return await get_user_by_username(username)
    except Exception:
        return None
    finally:
        await db.close()


async def delete_user(user_id: int) -> bool:
    db = await get_db()
    try:
        await db.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
        await db.commit()
        return True
    finally:
        await db.close()


async def get_all_users() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY created_at")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def update_user_password(user_id: int, new_password: str):
    db = await get_db()
    try:
        pw_hash = bcrypt.hash(new_password)
        await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
        await db.commit()
    finally:
        await db.close()


# ==========================================================================
# USER SETTINGS (PERSONAL FILTER OVERRIDES)
# ==========================================================================

async def get_user_settings(user_id: int) -> dict:
    """Get a user's personal filter overrides."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        await db.close()


async def save_user_settings(user_id: int, settings: dict):
    """Save a user's personal filter overrides."""
    db = await get_db()
    try:
        for key, value in settings.items():
            if key in USER_OVERRIDABLE_KEYS:
                await db.execute(
                    "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                    (user_id, key, str(value)),
                )
        await db.commit()
    finally:
        await db.close()


async def get_effective_settings(user_id: int | None = None) -> dict:
    """Get settings with user overrides applied (if logged in)."""
    global_settings = await get_settings()
    if user_id is None:
        return global_settings
    user_overrides = await get_user_settings(user_id)
    # Only override filter keys, not connection/layout keys
    for key in USER_OVERRIDABLE_KEYS:
        if key in user_overrides:
            global_settings[key] = user_overrides[key]
    return global_settings


# ==========================================================================
# SUGGESTIONS
# ==========================================================================

async def get_suggestions(status: str | None = None, max_days: int | None = None) -> list[dict]:
    """Fetch suggestions. If max_days is set, only return items updated within that period."""
    db = await get_db()
    try:
        if status and max_days:
            cursor = await db.execute(
                f"SELECT * FROM suggestions WHERE status = ? AND updated_at > datetime('now', '-{max_days} days') ORDER BY created_at DESC",
                (status,),
            )
        elif status:
            cursor = await db.execute(
                "SELECT * FROM suggestions WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await db.execute("SELECT * FROM suggestions ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def add_suggestion(tmdb_id: int, tvdb_id: int | None, title: str, overview: str, poster_path: str, first_air_date: str, suggested_by: str = "anonymous") -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM suggestions WHERE tmdb_id = ?", (tmdb_id,))
        existing = await cursor.fetchone()
        if existing:
            return None

        await db.execute(
            """INSERT INTO suggestions (tmdb_id, tvdb_id, title, overview, poster_path, first_air_date, suggested_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tmdb_id, tvdb_id, title, overview, poster_path, first_air_date, suggested_by),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM suggestions WHERE tmdb_id = ?", (tmdb_id,))
        row = await cursor.fetchone()
        return dict(row)
    finally:
        await db.close()


async def update_suggestion_status(suggestion_id: int, status: str) -> dict | None:
    db = await get_db()
    try:
        await db.execute(
            "UPDATE suggestions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, suggestion_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ==========================================================================
# GLOBAL SETTINGS
# ==========================================================================

async def get_settings() -> dict:
    """Get all global settings, falling back to defaults."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        saved = {row["key"]: row["value"] for row in rows}
        return {**DEFAULT_SETTINGS, **saved}
    finally:
        await db.close()


async def get_setting(key: str) -> str:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            return row["value"]
        return DEFAULT_SETTINGS.get(key, "")
    finally:
        await db.close()


async def save_settings(settings: dict):
    db = await get_db()
    try:
        for key, value in settings.items():
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        await db.commit()
    finally:
        await db.close()


# ==========================================================================
# PROVIDERS CACHE (24h TTL to avoid excessive TMDb API calls)
# ==========================================================================

async def get_cached_providers(tmdb_id: int) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT providers_json FROM providers_cache WHERE tmdb_id = ? AND cached_at > datetime('now', '-24 hours')",
            (tmdb_id,),
        )
        row = await cursor.fetchone()
        return row["providers_json"] if row else None
    finally:
        await db.close()


async def cache_providers(tmdb_id: int, providers_json: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO providers_cache (tmdb_id, providers_json, cached_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (tmdb_id, providers_json),
        )
        await db.commit()
    finally:
        await db.close()



# ==========================================================================
# TVMAZE CACHE (for last-aired lookups on old shows without providers)
# ==========================================================================

async def get_cached_tvmaze(tmdb_id: int) -> str | None:
    """Get cached TVmaze last_aired date if less than 7 days old."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT last_aired FROM tvmaze_cache WHERE tmdb_id = ? AND cached_at > datetime('now', '-7 days')",
            (tmdb_id,),
        )
        row = await cursor.fetchone()
        return row["last_aired"] if row else None
    finally:
        await db.close()


async def cache_tvmaze(tmdb_id: int, last_aired: str):
    """Cache TVmaze last_aired date for a show."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO tvmaze_cache (tmdb_id, last_aired, cached_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (tmdb_id, last_aired),
        )
        await db.commit()
    finally:
        await db.close()
