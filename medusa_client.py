"""Medusa PVR API v2 client.

Handles communication with a Medusa instance to:
- Test connectivity
- Fetch the current show library  
- Add new shows when suggestions are approved

Medusa API docs: https://github.com/pymedusa/Medusa/wiki/API-v2
"""

import httpx
from database import get_settings


class MedusaClient:
    """Lightweight client for Medusa API v2."""

    async def _get_config(self):
        """Get Medusa URL and API key from settings DB."""
        settings = await get_settings()
        url = settings.get("medusa_url", "").rstrip("/")
        api_key = settings.get("medusa_api_key", "")
        return url, api_key

    async def _request(self, method, path, **kwargs):
        url, api_key = await self._get_config()
        if not url or not api_key:
            return {"error": "Medusa not configured"}

        full_url = url + "/api/v2/" + path.lstrip("/")
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, full_url, headers=headers, **kwargs)
            if resp.status_code in (200, 201):
                return resp.json()
            elif resp.status_code == 204:
                return {"ok": True}
            else:
                return {"error": resp.status_code, "detail": resp.text}

    async def test_connection(self):
        """Check if Medusa is reachable and API key is valid."""
        result = await self._request("GET", "config/main")
        return result is not None and "error" not in result

    async def get_series(self):
        """Get all shows currently in Medusa."""
        result = await self._request("GET", "series?limit=300")
        if isinstance(result, list):
            return result
        return []

    async def add_show(self, tvdb_id=None, tmdb_id=None, quality_preset="default", status="skipped"):
        """
        Add a show to Medusa by TVDB or TMDB ID.

        Args:
            tvdb_id: The TVDB ID of the show (preferred)
            tmdb_id: The TMDB ID of the show (fallback)
            quality_preset: Quality preset to use
            status: Default episode status (skipped, wanted, ignored)
        """
        show_id = {}
        if tvdb_id:
            show_id["tvdb"] = tvdb_id
        if tmdb_id:
            show_id["tmdb"] = tmdb_id

        payload = {
            "id": show_id,
            "showName": "",
            "qualityPreset": quality_preset,
            "defaultStatus": status,
            "defaultStatusAfter": status,
            "seasonFolders": True,
            "subtitles": False,
            "anime": False,
            "scene": False,
        }
        return await self._request("POST", "series", json=payload)


medusa_client = MedusaClient()
