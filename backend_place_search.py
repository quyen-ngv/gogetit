"""Google Maps search through the GoRoute backend, which calls Google Places API with its key.

The backend answers in the same shape as ``place_searcher.search_google_maps``, so the social
extractor's matching code cannot tell the two apart. Any failure falls back to the browser
search, so switching the backend on can never leave a job with fewer lookups than before.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import requests

from place_searcher import search_google_maps

LOG = logging.getLogger(__name__)

MAP_SEARCH_PROVIDER_SCRAPE = "SCRAPE"
MAP_SEARCH_PROVIDER_GOOGLE = "GOOGLE"
BACKEND_SEARCH_TIMEOUT_SECONDS = 20

MapSearcher = Callable[[str, int], dict[str, Any]]


def search_places_via_backend(
    query: str,
    *,
    limit: int,
    url: str,
    token: str,
    timeout: float = BACKEND_SEARCH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST the query to the backend and return its ``data`` payload."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    response = requests.post(
        url,
        json={"query": query, "limit": limit},
        headers=headers,
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Backend place search answered HTTP {response.status_code}")
    data = (response.json() or {}).get("data")
    if not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError("Backend place search returned no usable result")
    return data


def build_map_searcher(
    *,
    provider: str | None,
    url: str | None,
    token: str,
    headless: bool,
) -> MapSearcher | None:
    """Return a searcher for GOOGLE, or None so the caller keeps its browser search."""
    if (provider or "").strip().upper() != MAP_SEARCH_PROVIDER_GOOGLE or not (url or "").strip():
        return None
    backend_url = url.strip()

    def search(query: str, limit: int) -> dict[str, Any]:
        try:
            return search_places_via_backend(query, limit=limit, url=backend_url, token=token)
        except Exception as exc:
            LOG.warning("Backend place search failed, falling back to browser search: query=%r error=%s", query, exc)
            return search_google_maps(query, limit=limit, headless=headless)

    return search
