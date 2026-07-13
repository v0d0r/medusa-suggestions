"""TMDb API client for searching and browsing TV shows.

Handles all communication with The Movie Database (TMDb) API v3:
- Discover/browse shows (trending, popular, airing today, upcoming)
- Search for shows by name
- Fetch streaming provider info (with 24h caching)
- Fetch YouTube trailers
- Get external IDs (TVDB) for Medusa integration

Supports per-user filter overrides when a user_id is provided.
TMDb API docs: https://developer.themoviedb.org/reference
"""

import json
import httpx
from config import TMDB_BASE_URL, TMDB_IMAGE_BASE
from database import get_settings, get_effective_settings, get_cached_providers, cache_providers


class TMDbClient:
    """Client for The Movie Database (TMDb) API v3 with language/genre filtering."""

    def __init__(self):
        self.base_url = TMDB_BASE_URL

    async def _get_filters(self, user_id: int | None = None) -> dict:
        """Load current filter settings from database."""
        settings = await get_effective_settings(user_id)
        return settings

    def _format_show(self, show: dict) -> dict:
        """Normalize a TMDb show result into our standard format."""
        return {
            "tmdb_id": show.get("id"),
            "title": show.get("name", "Unknown"),
            "overview": show.get("overview", ""),
            "poster_path": f"{TMDB_IMAGE_BASE}{show['poster_path']}" if show.get("poster_path") else None,
            "first_air_date": show.get("first_air_date", ""),
            "vote_average": show.get("vote_average", 0),
            "popularity": show.get("popularity", 0),
            "original_language": show.get("original_language", ""),
            "origin_country": show.get("origin_country", []),
        }

    def _post_filter(self, shows: list[dict], settings: dict) -> list[dict]:
        """Post-filter and deduplicate results."""
        # Deduplicate by tmdb_id
        seen = set()
        unique = []
        for s in shows:
            if s.get("tmdb_id") not in seen:
                seen.add(s.get("tmdb_id"))
                unique.append(s)
        shows = unique

        filtered = shows

        # Filter by allowed languages
        allowed_langs = settings.get("allowed_languages", "").strip()
        if allowed_langs:
            allowed = {l.strip() for l in allowed_langs.split(",") if l.strip()}
            filtered = [s for s in filtered if s.get("original_language") in allowed]

        # Filter by excluded countries
        excluded_countries = settings.get("excluded_countries", "").strip()
        if excluded_countries:
            excluded = {c.strip().upper() for c in excluded_countries.split(",") if c.strip()}
            filtered = [
                s for s in filtered
                if not any(c in excluded for c in s.get("origin_country", []))
            ]

        return filtered

    async def _discover(self, pages: int = 3, start_page: int = 1, user_id: int | None = None, **extra_params) -> list[dict]:
        """Use the discover/tv endpoint with all configured filters, fetching multiple pages."""
        settings = await self._get_filters(user_id)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return []

        base_params = {
            "api_key": api_key,
            "language": "en-US",
            "sort_by": "popularity.desc",
            **extra_params,
        }

        allowed_langs = settings.get("allowed_languages", "").strip()
        if allowed_langs and "," not in allowed_langs:
            base_params["with_original_language"] = allowed_langs

        excluded_genres = settings.get("excluded_genres", "").strip()
        if excluded_genres:
            base_params["without_genres"] = excluded_genres

        min_vote = float(settings.get("min_vote_average", "0") or "0")
        if min_vote > 0:
            base_params["vote_average.gte"] = str(min_vote)

        all_results = []
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(start_page, start_page + pages):
                params = {**base_params, "page": page}
                resp = await client.get(f"{self.base_url}/discover/tv", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    all_results.extend([self._format_show(s) for s in results])

        return self._post_filter(all_results, settings)

    async def _get_dedicated(self, path: str, pages: int = 3, start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Fetch multiple pages from a dedicated TMDb endpoint with post-filtering."""
        settings = await self._get_filters(user_id)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return []

        all_results = []
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(start_page, start_page + pages):
                resp = await client.get(
                    f"{self.base_url}/{path}",
                    params={"api_key": api_key, "language": "en-US", "page": page},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    all_results.extend([self._format_show(s) for s in results])

        return self._post_filter(all_results, settings)

    async def search(self, query: str, pages: int = 3, start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Search for TV shows by name, fetching multiple pages (post-filtered)."""
        settings = await self._get_filters(user_id)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return []

        all_results = []
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(start_page, start_page + pages):
                resp = await client.get(
                    f"{self.base_url}/search/tv",
                    params={"api_key": api_key, "query": query, "page": page},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    if not results:
                        break
                    all_results.extend([self._format_show(s) for s in results])

        return self._post_filter(all_results, settings)

    async def trending(self, time_window: str = "week", start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Get trending TV shows (post-filtered, 3 pages)."""
        return await self._get_dedicated(f"trending/tv/{time_window}", start_page=start_page, user_id=user_id)

    async def popular(self, start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Get popular TV shows using discover endpoint (supports all filters, 3 pages)."""
        return await self._discover(start_page=start_page, user_id=user_id)

    async def airing_today(self, start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Get shows airing today - uses dedicated TMDb endpoint."""
        return await self._get_dedicated("tv/airing_today", start_page=start_page, user_id=user_id)

    async def on_the_air(self, start_page: int = 1, user_id: int | None = None) -> list[dict]:
        """Get shows on the air - uses dedicated TMDb endpoint."""
        return await self._get_dedicated("tv/on_the_air", start_page=start_page, user_id=user_id)

    async def get_external_ids(self, tmdb_id: int) -> dict:
        """Get external IDs (TVDB, IMDB, etc.) for a show."""
        settings = await self._get_filters(None)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return {}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/tv/{tmdb_id}/external_ids",
                params={"api_key": api_key},
            )
            if resp.status_code == 200:
                return resp.json()
            return {}

    async def get_watch_providers(self, tmdb_id: int) -> list[dict]:
        """Get streaming providers for a show, with 24h caching."""
        # Check cache first
        cached = await get_cached_providers(tmdb_id)
        if cached is not None:
            return json.loads(cached)

        # Fetch from TMDb
        settings = await self._get_filters(None)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return []

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/tv/{tmdb_id}/watch/providers",
                params={"api_key": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", {})

                # Try ZA (South Africa) first, then US, then GB, then first available
                region_data = results.get("ZA") or results.get("US") or results.get("GB")
                if not region_data:
                    for region in results.values():
                        if isinstance(region, dict):
                            region_data = region
                            break

                if not region_data:
                    await cache_providers(tmdb_id, "[]")
                    return []

                # Get flatrate (streaming) providers, fall back to any type
                providers = region_data.get("flatrate", [])
                if not providers:
                    providers = region_data.get("ads", [])
                if not providers:
                    providers = region_data.get("buy", [])

                # Format: just name and logo
                formatted = [
                    {
                        "name": p.get("provider_name", ""),
                        "logo": f"https://image.tmdb.org/t/p/w45{p['logo_path']}" if p.get("logo_path") else None,
                    }
                    for p in providers[:3]  # Max 3 providers per show
                ]

                await cache_providers(tmdb_id, json.dumps(formatted))
                return formatted

            await cache_providers(tmdb_id, "[]")
            return []

    async def get_trailer(self, tmdb_id: int) -> str | None:
        """Get YouTube trailer URL for a show."""
        settings = await self._get_filters(None)
        api_key = settings.get("tmdb_api_key", "")
        if not api_key:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base_url}/tv/{tmdb_id}/videos",
                params={"api_key": api_key, "language": "en-US"},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                for vid_type in ["Trailer", "Teaser"]:
                    for vid in results:
                        if vid.get("type") == vid_type and vid.get("site") == "YouTube":
                            return f"https://www.youtube.com/watch?v={vid['key']}"
                for vid in results:
                    if vid.get("site") == "YouTube":
                        return f"https://www.youtube.com/watch?v={vid['key']}"
            return None


tmdb_client = TMDbClient()
