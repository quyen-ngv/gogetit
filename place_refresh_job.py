"""Daily refresh of Google Maps enrichment fields for places already in GoRoute."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from config import (
    GOROUTE_API_HEADERS,
    GOROUTE_PLACES_URL,
    PLACE_REFRESH_DELAY_SECONDS,
    goroute_api_headers,
)
from place_pipeline import scrape_and_import
from place_urls import is_google_maps_url, is_google_maps_viewport_url

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
CancelRequested = Callable[[], bool]


def _unwrap_places(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("GoRoute places response must be a JSON object or array")

    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "content", "places"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    raise ValueError("GoRoute places response does not contain a place list")


def fetch_place_refresh_candidates(
    *,
    places_url: str = GOROUTE_PLACES_URL,
    headers: dict[str, str] | None = None,
    place_id: str | None = None,
    max_places: int | None = None,
    include_inactive: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Load every place GoRoute considers refreshable, not one page of the catalogue.

    The backend owns the visibility filter: ACTIVE only unless include_inactive is set.
    A legacy paginated place-list response is still accepted during rolling deployments,
    which is why the fallback below exists.
    """
    request_headers = goroute_api_headers(headers or GOROUTE_API_HEADERS)
    base_url = places_url.rstrip("/")
    response = requests.get(
        f"{base_url}/detail-refresh-candidates",
        headers=request_headers,
        params={
            "placeId": place_id,
            "includeInactive": str(include_inactive).lower(),
            "maxPlaces": max_places if max_places and max_places > 0 else None,
        },
        timeout=120,
    )
    if response.status_code == 404:
        logger.warning("Backend has no detail-refresh-candidates endpoint; falling back to the place list")
        request_url = f"{base_url}/{place_id}" if place_id else base_url
        response = requests.get(request_url, headers=request_headers, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if place_id:
            place = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(place, dict):
                raise ValueError("GoRoute place response does not contain a place object")
            all_places = [place]
        else:
            all_places = _unwrap_places(payload)
    else:
        response.raise_for_status()
        all_places = _unwrap_places(response.json())

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for place in all_places:
        url = str(place.get("googleMapsLink") or "").strip()
        if not url or url in seen_urls:
            continue
        if not is_google_maps_url(url) or is_google_maps_viewport_url(url):
            logger.warning("Skipping invalid Google Maps URL for place %s: %s", place.get("id"), url)
            continue
        seen_urls.add(url)
        candidates.append(place)
        if max_places is not None and max_places > 0 and len(candidates) >= max_places:
            break
    return candidates, len(all_places)


def _place_overrides(candidate: dict[str, Any]) -> dict[str, Any]:
    """Keep stable DB identity and curated grouping while refreshing Google data."""
    return {
        "placeId": candidate.get("placeId"),
        "placeGroup": candidate.get("placeGroup"),
        "status": candidate.get("status"),
        "visibilityStatus": candidate.get("visibilityStatus"),
    }


def run_place_detail_refresh(
    *,
    place_id: str | None = None,
    max_places: int | None = None,
    headless: bool = True,
    continue_on_error: bool = True,
    include_inactive: bool = False,
    delay_seconds: float = PLACE_REFRESH_DELAY_SECONDS,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, Any]:
    """Refresh all eligible places sequentially and return an aggregate report."""
    candidates, database_count = fetch_place_refresh_candidates(
        place_id=place_id,
        max_places=max_places,
        include_inactive=include_inactive,
    )
    total = len(candidates)
    results: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    def report(current: int, current_place: dict[str, Any] | None = None) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "databaseCount": database_count,
                "eligibleCount": total,
                "processedCount": current,
                "successCount": success_count,
                "failedCount": failed_count,
                "currentPlaceId": (current_place or {}).get("id"),
                "currentTitle": (current_place or {}).get("title"),
            }
        )

    report(0)
    for index, candidate in enumerate(candidates, start=1):
        if cancel_requested and cancel_requested():
            return {
                "success": False,
                "cancelled": True,
                "databaseCount": database_count,
                "eligibleCount": total,
                "processedCount": len(results),
                "successCount": success_count,
                "failedCount": failed_count,
                "results": results,
            }
        url = str(candidate.get("googleMapsLink") or "").strip()
        logger.info(
            "Refreshing place details %d/%d: id=%s title=%r",
            index,
            total,
            candidate.get("id"),
            candidate.get("title"),
        )
        result = None
        try:
            result = scrape_and_import(
                url,
                headless=headless,
                max_reviews=1,
                max_scrolls=1,
                include_reviews=False,
                place_overrides=_place_overrides(candidate),
            )
            item = {
                "placeId": candidate.get("id"),
                "googlePlaceId": candidate.get("placeId"),
                "title": candidate.get("title"),
                "url": url,
                **result.to_dict(),
            }
            if result.success:
                success_count += 1
            else:
                failed_count += 1
        except Exception as exc:
            logger.exception("Place detail refresh failed for %s", candidate.get("id"))
            failed_count += 1
            item = {
                "placeId": candidate.get("id"),
                "googlePlaceId": candidate.get("placeId"),
                "title": candidate.get("title"),
                "url": url,
                "success": False,
                "error": str(exc),
            }
            if not continue_on_error:
                results.append(item)
                report(index, candidate)
                raise

        results.append(item)
        report(index, candidate)
        if result is not None and result.blocked_by_google:
            # Google's IP-level block applies to every remaining candidate too; burning
            # through the rest of the batch right now only prolongs the block instead of
            # letting it clear.
            logger.error(
                "Google blocked this IP (bot-check page); stopping the batch at %d/%d "
                "instead of continuing to hammer it while blocked.",
                index,
                total,
            )
            return {
                "success": False,
                "blockedByGoogle": True,
                "databaseCount": database_count,
                "eligibleCount": total,
                "processedCount": len(results),
                "successCount": success_count,
                "failedCount": failed_count,
                "results": results,
            }
        if delay_seconds > 0 and index < total:
            time.sleep(delay_seconds)

    return {
        "success": True,
        "allSucceeded": failed_count == 0,
        "databaseCount": database_count,
        "eligibleCount": total,
        "processedCount": len(results),
        "successCount": success_count,
        "failedCount": failed_count,
        "results": results,
    }
