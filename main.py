"""Medusa Suggestions — main application entrypoint.

Starts the FastAPI server and initializes the database on startup.
Run with: python main.py (or: uvicorn main:app --host 0.0.0.0 --port 8555)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import init_db
from routes import user_router, admin_router, auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    yield


app = FastAPI(title="Medusa Suggestions", lifespan=lifespan)

# Register route groups:
# - auth_router: login, logout, user preferences
# - user_router: browsing, searching, suggesting, API endpoints
# - admin_router: dashboard, settings, user management
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8555, reload=True)
