"""Configuration for Medusa Suggestions app.

All sensitive values are loaded from environment variables with safe defaults.
These are typically set in docker-compose.yml.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Database
DATABASE_PATH = os.getenv("DB_PATH", str(BASE_DIR / "suggestions.db"))

# TMDb image base URL (constant, not configurable)
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w300"

# Admin password (used to create initial admin user on first run)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# Secret key for signing session cookies
SECRET_KEY = os.getenv("SECRET_KEY", "medusa-suggestions-secret-change-me")
