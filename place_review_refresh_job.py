"""Sequential refresh of image-backed Google Maps reviews for ACTIVE places."""

from __future__ import annotations

import hashlib
import gc
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import (
    GOROUTE_API_HEADERS,
    GOROUTE_PLACES_URL,
    GOROUTE_REVIEW_REFRESH_URL,
    PLACE_REVIEW_REFRESH_DELAY_SECONDS,
    PLACE_REVIEW_REFRESH_MAX_AGE_HOURS,
    PLACE_REVIEW_BROWSER_CRASH_RETRIES,
    PLACE_REVIEW_MAX_SCROLLS,
    goroute_api_headers,
)
from place_refresh_job import _unwrap_places
from place_urls import is_google_maps_url, is_google_maps_viewport_url
from run_job import (
    MAX_REVIEW_COLLECTION_LIMIT,
    close_driver,
    scrape_place_reviews_only,
    setup_driver,
)

logger = logging.getLogger(__name__)

# Recycle the shared Chrome after this many places: releases the single
# browser slot so other jobs (e.g. the detail refresh) can interleave, and
# drops renderer memory accumulated across places.
BROWSER_RECYCLE_EVERY_PLACES = 10

ProgressCallback = Callable[[dict[str, Any]], None]
CancelRequested = Callable[[], bool]
_review_refresh_lock = threading.Lock()


class _ReviewBrowserSession:
    """Reuse one Chrome process across places and replace it after a crash."""

    def __init__(self, headless: bool):
        self.headless = headless
        self.driver = None

    def __enter__(self) -> "_ReviewBrowserSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def get(self):
        if self.driver is None:
            self.driver = setup_driver(self.headless)
        return self.driver

    def reset(self) -> None:
        self.close()
        gc.collect()

    def close(self) -> None:
        if self.driver is None:
            return
        driver, self.driver = self.driver, None
        try:
            close_driver(driver)
        except Exception as exc:
            logger.debug("Could not close review browser cleanly: %s", exc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_place_review_candidates(
    *,
    places_url: str = GOROUTE_PLACES_URL,
    headers: dict[str, str] | None = None,
    place_id: str | None = None,
    place_ids: list[str] | None = None,
    max_places: int | None = None,
    max_age_hours: int = PLACE_REVIEW_REFRESH_MAX_AGE_HOURS,
    include_recent: bool = False,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
) -> tuple[list[dict[str, Any]], int]:
    """Return backend-ranked review refresh candidates.

    The backend aggregates review/image state in one query. A legacy place-list
    response is still accepted during rolling deployments.
    """
    request_url = f"{review_refresh_url.rstrip('/')}/refresh-candidates"
    response = requests.get(
        request_url,
        headers=goroute_api_headers(headers or GOROUTE_API_HEADERS),
        params={
            "placeId": place_id,
            "maxAgeHours": max(1, max_age_hours),
            "includeRecent": str(include_recent).lower(),
        },
        timeout=60,
    )
    if response.status_code == 404:
        request_url = f"{places_url.rstrip('/')}/{place_id}" if place_id else places_url
        response = requests.get(
            request_url,
            headers=goroute_api_headers(headers or GOROUTE_API_HEADERS),
            timeout=60,
        )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    ranked_response = isinstance(data, dict) and isinstance(data.get("items"), list)
    if ranked_response:
        all_places = data.get("items") or []
        database_count = int(data.get("databaseCount") or len(all_places))
    elif place_id:
        all_places = [data] if isinstance(data, dict) else []
        database_count = len(all_places)
    else:
        all_places = _unwrap_places(payload)
        database_count = len(all_places)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, max_age_hours))
    requested_ids = {str(value) for value in (place_ids or []) if value}
    candidates: list[dict[str, Any]] = []
    for place in all_places:
        database_place_id = str(place.get("id") or "")
        if requested_ids and database_place_id not in requested_ids:
            continue
        if str(place.get("visibilityStatus") or "").upper() != "ACTIVE":
            continue
        url = str(place.get("googleMapsLink") or "").strip()
        if not url or not is_google_maps_url(url) or is_google_maps_viewport_url(url):
            continue
        last_scraped_at = _parse_timestamp(place.get("lastScrapedAt"))
        if (not ranked_response and not place_id and not requested_ids and not include_recent
                and last_scraped_at is not None and last_scraped_at > cutoff):
            continue
        candidates.append(place)

    if not ranked_response and not requested_ids:
        candidates.sort(key=lambda place: _parse_timestamp(place.get("lastScrapedAt")) or datetime.min.replace(tzinfo=timezone.utc))
    if max_places is not None and max_places > 0:
        candidates = candidates[:max_places]
    return candidates, database_count


def is_daily_refresh_enabled(
    *,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
    headers: dict[str, str] | None = None,
) -> bool:
    """Read the PLACE_REVIEW/DAILY_REFRESH_ENABLED switch from the GoRoute config table.

    Fails closed: the scheduled run needs the same backend to list candidates and store
    results, so an unreachable backend means skipping the run rather than burning an
    hour of browser time on work that cannot be persisted.
    """
    request_url = f"{review_refresh_url.rstrip('/')}/refresh-settings"
    try:
        response = requests.get(
            request_url,
            headers=goroute_api_headers(headers or GOROUTE_API_HEADERS),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        settings = data if isinstance(data, dict) else payload
        if not isinstance(settings, dict) or "dailyRefreshEnabled" not in settings:
            logger.warning("Review refresh settings response has no dailyRefreshEnabled field; skipping run")
            return False
        return bool(settings.get("dailyRefreshEnabled"))
    except Exception as exc:
        logger.warning("Could not read daily review refresh switch (%s); skipping scheduled run", exc)
        return False


def _is_retryable_browser_error(error: Any) -> bool:
    message = str(error or "").casefold()
    return any(signal in message for signal in (
        "tab crashed",
        "browser became unresponsive",
        "read timed out",
        "chrome not reachable",
        "not connected to devtools",
        "invalid session id",
        "session deleted because of page crash",
        "limited view",
    ))


def _iso_review_date(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        return fallback


def _review_input(review: dict[str, Any], google_place_id: str, scraped_at: str) -> dict[str, Any] | None:
    review_id = str(review.get("reviewId") or "").strip()
    author_name = str(review.get("name") or "").strip()
    images = [str(url).strip() for url in (review.get("images") or []) if str(url).strip()]
    try:
        rating = int(review.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if not review_id or not author_name or not images or rating < 1 or rating > 5:
        return None

    description = str(review.get("description") or "").strip()
    review_date = _iso_review_date(review.get("when"), scraped_at)
    content_hash = hashlib.sha256(
        f"{review_id}|{rating}|{description}|{review_date}".encode("utf-8")
    ).hexdigest()
    return {
        "reviewId": review_id,
        "googlePlaceId": google_place_id,
        "authorName": author_name,
        "profileUrl": str(review.get("profileUrl") or "").strip() or None,
        "profilePicture": str(review.get("profilePicture") or "").strip() or None,
        "isLocalGuide": bool(review.get("isLocalGuide")),
        "totalReviews": int(review.get("totalReviews") or 0),
        "totalPhotos": int(review.get("totalPhotos") or 0),
        "rating": rating,
        "reviewText": {"other": description} if description else {},
        "reviewDate": review_date,
        "userImages": images,
        "likes": int(review.get("likes") or 0),
        "contentHash": content_hash,
        "isDeleted": False,
    }


def _post_backend(
    path: str,
    *,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.post(
        f"{review_refresh_url.rstrip('/')}{path}",
        json=json,
        headers=goroute_api_headers({"Content-Type": "application/json"}),
        timeout=(30, 900),
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload


# COMMENTED OUT DUE TO SCRAPE REVIEW ERRORS
# The entire review refresh functionality has been disabled
def _refresh_one(
    candidate: dict[str, Any],
    *,
    headless: bool,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
    cancel_requested: CancelRequested | None = None,
    browser_session: _ReviewBrowserSession | None = None,
) -> dict[str, Any]:
    """DISABLED: Review refresh is currently disabled due to scrape errors."""
    raise RuntimeError("Review refresh is currently disabled")


def run_place_review_refresh(
    *,
    place_id: str | None = None,
    place_ids: list[str] | None = None,
    max_places: int | None = None,
    headless: bool = True,
    continue_on_error: bool = True,
    places_url: str = GOROUTE_PLACES_URL,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
    include_recent: bool = False,
    delay_seconds: float = PLACE_REVIEW_REFRESH_DELAY_SECONDS,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, Any]:
    """DISABLED: Review refresh is currently disabled due to scrape errors."""
    return {
        "success": False,
        "error": "Review refresh is currently disabled",
        "databaseCount": 0,
        "eligibleCount": 0,
        "processedCount": 0,
        "successCount": 0,
        "failedCount": 0,
        "results": [],
        "places": [],
    }


