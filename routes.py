"""FastAPI routes for Medusa Suggestions app."""

import time
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer

from database import (
    get_suggestions, add_suggestion, update_suggestion_status,
    get_settings, save_settings, get_effective_settings,
    get_user_settings, save_user_settings,
    verify_user, create_user, delete_user, get_all_users,
    get_user_by_id, update_user_password,
)
from tmdb_client import tmdb_client
from medusa_client import medusa_client
from config import SECRET_KEY

templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(SECRET_KEY)

# ==========================================================================
# MEDUSA LIBRARY CACHE
# Caches the list of TMDb IDs already in Medusa to avoid repeated API calls.
# Refreshes every 5 minutes.
# ==========================================================================

# Simple in-memory cache for Medusa show list (refreshes every 5 minutes)
_medusa_cache = {"tmdb_ids": [], "timestamp": 0}
MEDUSA_CACHE_TTL = 300


async def get_medusa_tmdb_ids() -> list[int]:
    """Get list of TMDb IDs from Medusa, cached for 5 minutes."""
    now = time.time()
    if now - _medusa_cache["timestamp"] < MEDUSA_CACHE_TTL and _medusa_cache["tmdb_ids"]:
        return _medusa_cache["tmdb_ids"]

    series = await medusa_client.get_series()
    tmdb_ids = []
    for show in series:
        tmdb_id = show.get("externals", {}).get("tmdb")
        if tmdb_id:
            tmdb_ids.append(tmdb_id)
    _medusa_cache["tmdb_ids"] = tmdb_ids
    _medusa_cache["timestamp"] = now
    return tmdb_ids


# ==========================================================================
# SESSION / AUTH HELPERS
# These functions handle reading the signed session cookie to identify
# the current user, and enforcing login/admin requirements on routes.
# ==========================================================================

def get_current_user(request: Request) -> dict | None:
    """Get current user from session cookie, or None if not logged in."""
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        data = serializer.loads(token)
        return data
    except Exception:
        return None


def get_current_user_id(request: Request) -> int | None:
    """Get current user ID or None."""
    user = get_current_user(request)
    return user.get("id") if user else None


def require_admin(request: Request):
    """Require admin user."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def require_login(request: Request):
    """Require any logged-in user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ==========================================================================
# AUTHENTICATION ROUTES
# Handles login, logout, and user preference pages.
# Login is optional - anonymous users can still browse and suggest.
# ==========================================================================

auth_router = APIRouter()


@auth_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@auth_router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handle login."""
    user = await verify_user(username, password)
    if user:
        token = serializer.dumps({"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])})
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("session", token, httponly=True, max_age=86400 * 7)
        return response
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid username or password",
    })


@auth_router.get("/logout")
async def logout():
    """Logout."""
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response


# --- User preferences ---
@auth_router.get("/preferences", response_class=HTMLResponse)
async def preferences_page(request: Request, _=Depends(require_login)):
    """User preferences page for personal filter overrides."""
    user = get_current_user(request)
    user_settings = await get_user_settings(user["id"])
    global_settings = await get_settings()
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("preferences.html", {
        "request": request,
        "user": user,
        "user_settings": user_settings,
        "global_settings": global_settings,
        "msg": msg,
    })


@auth_router.post("/preferences")
async def save_preferences(
    request: Request,
    _=Depends(require_login),
    allowed_languages: str = Form(""),
    excluded_countries: str = Form(""),
    excluded_genres: str = Form(""),
    min_vote_average: str = Form(""),
):
    """Save user's personal filter preferences."""
    user = get_current_user(request)
    await save_user_settings(user["id"], {
        "allowed_languages": allowed_languages.strip(),
        "excluded_countries": excluded_countries.strip(),
        "excluded_genres": excluded_genres.strip(),
        "min_vote_average": min_vote_average.strip(),
    })
    return RedirectResponse("/preferences?msg=saved", status_code=303)


# ==========================================================================
# USER-FACING ROUTES
# Main browsing pages (trending, popular, airing, upcoming, search)
# and the suggestion form. These work for both anonymous and logged-in users.
# ==========================================================================

user_router = APIRouter()


@user_router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    trending = await tmdb_client.trending(user_id=get_current_user_id(request))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": trending,
        "tab": "trending",
        "user": get_current_user(request),
    })


@user_router.get("/popular", response_class=HTMLResponse)
async def popular(request: Request, sort: str = "popularity"):
    shows = await tmdb_client.popular(user_id=get_current_user_id(request), sort=sort)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "tab": "popular",
        "sort": sort,
        "user": get_current_user(request),
    })


@user_router.get("/airing", response_class=HTMLResponse)
async def airing(request: Request):
    shows = await tmdb_client.airing_today(user_id=get_current_user_id(request))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "tab": "airing",
        "user": get_current_user(request),
    })


@user_router.get("/upcoming", response_class=HTMLResponse)
async def upcoming(request: Request, new_only: str = "1"):
    shows = await tmdb_client.on_the_air(user_id=get_current_user_id(request), new_only=(new_only == "1"))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "tab": "upcoming",
        "new_only": new_only,
        "user": get_current_user(request),
    })


@user_router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    shows = []
    if q:
        shows = await tmdb_client.search(q, user_id=get_current_user_id(request))
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "tab": "search",
        "query": q,
        "user": get_current_user(request),
    })


@user_router.post("/suggest")
async def suggest_show(
    request: Request,
    tmdb_id: int = Form(...),
    title: str = Form(...),
    overview: str = Form(""),
    poster_path: str = Form(""),
    first_air_date: str = Form(""),
):
    user = get_current_user(request)
    suggested_by = user["username"] if user else "anonymous"

    external_ids = await tmdb_client.get_external_ids(tmdb_id)
    tvdb_id = external_ids.get("tvdb_id")

    result = await add_suggestion(
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        title=title,
        overview=overview,
        poster_path=poster_path,
        first_air_date=first_air_date,
        suggested_by=suggested_by,
    )

    if result is None:
        return RedirectResponse("/?msg=already_suggested", status_code=303)
    return RedirectResponse("/?msg=suggested", status_code=303)


# --- API endpoints (called by frontend JavaScript) ---

@user_router.get("/api/providers/{tmdb_id}")
async def get_providers(tmdb_id: int):
    providers = await tmdb_client.get_watch_providers(tmdb_id)
    return JSONResponse(content=providers)


@user_router.get("/api/shows")
async def get_more_shows(request: Request, tab: str = "trending", start_page: int = 4, q: str = ""):
    user_id = get_current_user_id(request)
    if tab == "trending":
        shows = await tmdb_client.trending(start_page=start_page, user_id=user_id)
    elif tab == "popular":
        shows = await tmdb_client.popular(start_page=start_page, user_id=user_id)
    elif tab == "airing":
        shows = await tmdb_client.airing_today(start_page=start_page, user_id=user_id)
    elif tab == "upcoming":
        new_only = request.query_params.get("new_only", "1") == "1"
        shows = await tmdb_client.on_the_air(start_page=start_page, user_id=user_id, new_only=new_only)
    elif tab == "search" and q:
        shows = await tmdb_client.search(q, start_page=start_page, user_id=user_id)
    else:
        shows = []
    return JSONResponse(content=shows)


@user_router.get("/api/layout")
async def get_layout():
    settings = await get_settings()
    return JSONResponse(content={
        "card_min_width": settings.get("card_min_width", "200"),
        "grid_gap": settings.get("grid_gap", "1.5"),
        "show_age_threshold": settings.get("show_age_threshold", "10"),
    })


@user_router.get("/api/trailer/{tmdb_id}")
async def get_trailer(tmdb_id: int):
    url = await tmdb_client.get_trailer(tmdb_id)
    return JSONResponse(content={"url": url})


@user_router.get("/api/my-services")
async def get_my_services(request: Request):
    user_id = get_current_user_id(request)
    settings = await get_effective_settings(user_id)
    services = settings.get("my_streaming_services", "")
    if services:
        return JSONResponse(content=[s.strip() for s in services.split(",") if s.strip()])
    return JSONResponse(content=[])




@user_router.get("/api/tvmaze-check/{tmdb_id}")
async def tvmaze_check(tmdb_id: int):
    """Check TVmaze for a show's last aired date (used for old shows without providers).
    Looks up show by TVDB ID via TMDb external IDs, then checks TVmaze.
    Returns the last aired year or null."""
    import httpx
    from database import get_cached_tvmaze, cache_tvmaze

    # Check cache first
    cached = await get_cached_tvmaze(tmdb_id)
    if cached is not None:
        return JSONResponse(content={"last_aired": cached})

    # Get TVDB ID from TMDb
    external_ids = await tmdb_client.get_external_ids(tmdb_id)
    tvdb_id = external_ids.get("tvdb_id")
    if not tvdb_id:
        await cache_tvmaze(tmdb_id, "")
        return JSONResponse(content={"last_aired": ""})

    # Look up on TVmaze via TVDB ID
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}")
            if resp.status_code == 200:
                data = resp.json()
                # Get ended date or last episode air date
                ended = data.get("ended", "")
                # If show hasn't ended, it's still active
                status = data.get("status", "")
                if status in ("Running", "To Be Determined"):
                    last_aired = str(data.get("premiered", "")[:4]) if data.get("premiered") else ""
                    # Actually it's active, use current year
                    from datetime import date
                    last_aired = str(date.today().year)
                elif ended:
                    last_aired = ended[:4]
                else:
                    last_aired = ""
                await cache_tvmaze(tmdb_id, last_aired)
                return JSONResponse(content={"last_aired": last_aired})
            else:
                await cache_tvmaze(tmdb_id, "")
                return JSONResponse(content={"last_aired": ""})
    except Exception:
        return JSONResponse(content={"last_aired": ""})
@user_router.get("/api/medusa-shows")
async def get_medusa_shows():
    tmdb_ids = await get_medusa_tmdb_ids()
    return JSONResponse(content=tmdb_ids)


# ==========================================================================
# ADMIN ROUTES
# Dashboard for approving/ignoring suggestions, global settings,
# and user management. Requires admin login.
# ==========================================================================

admin_router = APIRouter(prefix="/admin")


@admin_router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _=Depends(require_admin)):
    pending = await get_suggestions("pending")
    approved = await get_suggestions("approved", max_days=30)
    ignored = await get_suggestions("ignored", max_days=30)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
        "ignored": ignored,
        "user": get_current_user(request),
    })


@admin_router.post("/approve/{suggestion_id}")
async def approve_suggestion(suggestion_id: int, request: Request, _=Depends(require_admin)):
    suggestion = await update_suggestion_status(suggestion_id, "approved")
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    tvdb_id = suggestion.get("tvdb_id")
    tmdb_id = suggestion.get("tmdb_id")
    if tvdb_id or tmdb_id:
        await medusa_client.add_show(tvdb_id=tvdb_id, tmdb_id=tmdb_id)
    return RedirectResponse("/admin", status_code=303)


@admin_router.post("/ignore/{suggestion_id}")
async def ignore_suggestion(suggestion_id: int, request: Request, _=Depends(require_admin)):
    suggestion = await update_suggestion_status(suggestion_id, "ignored")
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return RedirectResponse("/admin", status_code=303)


@admin_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _=Depends(require_admin)):
    settings = await get_settings()
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "msg": msg,
        "user": get_current_user(request),
    })


@admin_router.post("/settings")
async def save_settings_form(
    request: Request,
    _=Depends(require_admin),
    medusa_url: str = Form(""),
    medusa_api_key: str = Form(""),
    tmdb_api_key: str = Form(""),
    allowed_languages: str = Form(""),
    excluded_countries: str = Form(""),
    excluded_genres: str = Form(""),
    min_vote_average: str = Form("0"),
    my_streaming_services: str = Form(""),
    card_min_width: str = Form("200"),
    grid_gap: str = Form("1.5"),
    show_age_threshold: str = Form("10"),
):
    await save_settings({
        "medusa_url": medusa_url.strip(),
        "medusa_api_key": medusa_api_key.strip(),
        "tmdb_api_key": tmdb_api_key.strip(),
        "allowed_languages": allowed_languages.strip(),
        "excluded_countries": excluded_countries.strip(),
        "excluded_genres": excluded_genres.strip(),
        "min_vote_average": min_vote_average.strip(),
        "my_streaming_services": my_streaming_services.strip(),
        "card_min_width": card_min_width.strip(),
        "grid_gap": grid_gap.strip(),
        "show_age_threshold": show_age_threshold.strip(),
    })
    return RedirectResponse("/admin/settings?msg=saved", status_code=303)


@admin_router.post("/test-medusa")
async def test_medusa_connection(request: Request, _=Depends(require_admin)):
    ok = await medusa_client.test_connection()
    if ok:
        return RedirectResponse("/admin/settings?msg=medusa_ok", status_code=303)
    return RedirectResponse("/admin/settings?msg=medusa_fail", status_code=303)


# --- User management ---
# --- User management (admin only) ---

@admin_router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, _=Depends(require_admin)):
    users = await get_all_users()
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "msg": msg,
        "user": get_current_user(request),
    })


@admin_router.post("/users/add")
async def add_user(request: Request, _=Depends(require_admin), username: str = Form(...), password: str = Form(...)):
    result = await create_user(username, password)
    if result:
        return RedirectResponse("/admin/users?msg=user_added", status_code=303)
    return RedirectResponse("/admin/users?msg=user_exists", status_code=303)


@admin_router.post("/users/delete/{user_id}")
async def remove_user(user_id: int, request: Request, _=Depends(require_admin)):
    await delete_user(user_id)
    return RedirectResponse("/admin/users?msg=user_deleted", status_code=303)


@admin_router.post("/users/reset-password/{user_id}")
async def reset_password(user_id: int, request: Request, _=Depends(require_admin), new_password: str = Form(...)):
    await update_user_password(user_id, new_password)
    return RedirectResponse("/admin/users?msg=password_reset", status_code=303)
