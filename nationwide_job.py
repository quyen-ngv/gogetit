"""Nationwide food-place discovery and scrape orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import requests

from config import goroute_api_headers
from nationwide_regions import Region, location_queries, region_queries, selected_regions
from place_searcher import search_google_maps
from run_job import scrape_place
from upload_to_api import format_place_for_api

LOG = logging.getLogger(__name__)
FOOD_MARKERS = (
    "restaurant", "food", "eatery", "bistro", "diner", "noodle", "rice", "grill",
    "quán ăn", "nhà hàng", "ẩm thực", "đặc sản", "phở", "bún", "cơm", "bánh",
)


@dataclass
class Counters:
    discovered_count: int = 0
    processed_count: int = 0
    eligible_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    rejected_score_count: int = 0
    insufficient_photo_count: int = 0

    def add(self, other: "Counters") -> None:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))


def _camel_counters(counters: Counters) -> dict[str, int]:
    return {
        "discoveredCount": counters.discovered_count,
        "processedCount": counters.processed_count,
        "eligibleCount": counters.eligible_count,
        "importedCount": counters.imported_count,
        "skippedCount": counters.skipped_count,
        "failedCount": counters.failed_count,
        "rejectedScoreCount": counters.rejected_score_count,
        "insufficientPhotoCount": counters.insufficient_photo_count,
    }


def _post_json(url: str, body: dict[str, Any], token: str, *, attempts: int = 3) -> dict[str, Any]:
    headers = goroute_api_headers({"Content-Type": "application/json"})
    if token:
        headers["X-Internal-Token"] = token
    error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=90)
            if 200 <= response.status_code < 300:
                payload = response.json()
                return payload.get("data", payload) if isinstance(payload, dict) else {}
            error = f"HTTP {response.status_code}: {response.text[:500]}"
        except Exception as exc:
            error = str(exc)
        if attempt < attempts:
            time.sleep(attempt * 1.5)
    raise RuntimeError(error or f"POST failed: {url}")


def _event(
    callback_url: str,
    token: str,
    goroute_job_id: str,
    python_job_id: str,
    event_type: str,
    *,
    region: Region | None = None,
    sequence_no: int = 0,
    region_status: str | None = None,
    counters: Counters | None = None,
    query_count: int = 0,
    error_message: str | None = None,
) -> None:
    body: dict[str, Any] = {
        "jobId": goroute_job_id,
        "pythonJobId": python_job_id,
        "eventType": event_type,
        "errorMessage": error_message,
    }
    if region:
        body.update({
            "regionCode": region.code,
            "regionName": region.name,
            "priority": region.priority,
            "sequenceNo": sequence_no,
            "regionStatus": region_status,
            "queryCount": query_count,
        })
    if counters:
        body.update(_camel_counters(counters))
    try:
        _post_json(callback_url, body, token)
    except Exception as exc:
        # Telemetry, not work: a rejected event must not abort the crawl. GoRoute
        # reconciles a job whose events were lost through its own watchdog.
        LOG.error("Nationwide %s event lost for GoRoute job %s: %s", event_type, goroute_job_id, exc)


def _is_food_place(place: dict[str, Any]) -> bool:
    category = str(place.get("category") or "").lower()
    group = str(place.get("placeGroup") or "").upper()
    return group == "FOOD_AND_DRINK" or any(marker in category for marker in FOOD_MARKERS)


def _language(text: str) -> str:
    return "vi" if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", text.lower()) else "en"


def _review_input(review: dict[str, Any], google_place_id: str) -> dict[str, Any] | None:
    review_id = str(review.get("reviewId") or review.get("review_id") or "").strip()
    author = str(review.get("name") or review.get("author") or "").strip()
    rating = int(review.get("rating") or 0)
    if not review_id or not author or rating < 1 or rating > 5:
        return None
    description = str(review.get("description") or review.get("text") or "").strip()
    review_date = str(review.get("when") or review.get("review_date") or "").strip()
    if not review_date:
        review_date = "1970-01-01"
    images = review.get("images") or review.get("photos") or []
    if not isinstance(images, list):
        images = []
    content = f"{author}|{rating}|{description}|{review_date}".encode("utf-8")
    return {
        "reviewId": review_id,
        "googlePlaceId": google_place_id,
        "authorName": author,
        "profileUrl": str(review.get("profileUrl") or review.get("profile_url") or ""),
        "profilePicture": str(review.get("profilePicture") or review.get("profile_picture") or ""),
        "isLocalGuide": bool(review.get("isLocalGuide") or review.get("reviewer_is_local_guide")),
        "totalReviews": int(review.get("totalReviews") or review.get("reviewer_total_reviews") or 0),
        "totalPhotos": int(review.get("totalPhotos") or review.get("reviewer_total_photos") or 0),
        "rating": rating,
        "reviewText": {_language(description): description} if description else {},
        "reviewDate": review_date,
        "userImages": [str(image) for image in images if str(image).startswith("http")],
        "likes": int(review.get("likes") or 0),
        "contentHash": hashlib.sha256(content).hexdigest(),
        "isDeleted": False,
    }


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("placeId")
        or candidate.get("cid")
        or candidate.get("googleMapsLink")
        or candidate.get("resolvedUrl")
        or ""
    ).strip()


def _distance_km(
    latitude: float,
    longitude: float,
    other_latitude: float,
    other_longitude: float,
) -> float:
    earth_radius_km = 6371.0088
    lat1 = math.radians(latitude)
    lat2 = math.radians(other_latitude)
    delta_lat = math.radians(other_latitude - latitude)
    delta_lng = math.radians(other_longitude - longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _within_radius(
    place: dict[str, Any],
    latitude: float,
    longitude: float,
    radius_km: float,
) -> bool:
    try:
        place_latitude = float(place.get("latitude"))
        place_longitude = float(place.get("longitude"))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(place_latitude) or not math.isfinite(place_longitude):
        return False
    distance = _distance_km(latitude, longitude, place_latitude, place_longitude)
    place["distanceKm"] = round(distance, 3)
    return distance <= radius_km


def _location_region(latitude: float, longitude: float) -> Region:
    return Region(
        "location",
        f"Tọa độ {latitude:.6f}, {longitude:.6f}",
        100,
        (),
    )


def _ensure_place_identity(place: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Fill stable identity fields from discovery when the detail page omits them."""
    if not (place.get("placeId") or place.get("place_id")):
        place["placeId"] = candidate.get("placeId")
    for field in ("cid", "latitude", "longitude"):
        if place.get(field) is None or place.get(field) == "":
            place[field] = candidate.get(field)
    if not place.get("googleMapsLink"):
        place["googleMapsLink"] = candidate.get("googleMapsLink") or candidate.get("resolvedUrl")


def _initial_filter_reason(
    place: dict[str, Any],
    min_review_count: int,
    min_google_rating: float,
) -> str | None:
    if not _is_food_place(place):
        return "NON_FOOD"
    review_count = int(place.get("reviewCount") or place.get("review_count") or 0)
    if review_count < min_review_count:
        return "REVIEW_COUNT"
    rating = float(place.get("reviewRating") or place.get("review_rating") or 0)
    if rating <= min_google_rating:
        return "GOOGLE_RATING"
    return None


def _place_payload(place: dict[str, Any], *, inactive: bool) -> dict[str, Any]:
    prepared = dict(place)
    if inactive:
        images = prepared.get("images") or []
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except (TypeError, ValueError):
                images = []
        prepared["images"] = images[:1] if isinstance(images, list) else []
        prepared["reviews"] = []
        prepared["userReviews"] = []
    payload = format_place_for_api(prepared)
    payload["userReviews"] = None
    if inactive:
        payload["visibilityStatus"] = "INACTIVE"
    return payload


def _discover_region(
    region: Region,
    max_queries: int,
    search_limit: int,
    headless: bool,
    *,
    query_mode: str,
    custom_queries: list[str],
    include_regional_specialties: bool,
    include_tourist_areas: bool,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float | None = None,
    search_zoom: int = 14,
) -> tuple[list[dict[str, Any]], int]:
    candidates: dict[str, dict[str, Any]] = {}
    location_mode = latitude is not None and longitude is not None
    queries = (
        location_queries(
            max_queries,
            custom_queries=custom_queries,
            query_mode=query_mode,
        )
        if location_mode
        else region_queries(
            region,
            max_queries,
            custom_queries=custom_queries,
            query_mode=query_mode,
            include_regional_specialties=include_regional_specialties,
            include_tourist_areas=include_tourist_areas,
        )
    )
    for query in queries:
        search_kwargs: dict[str, Any] = {
            "limit": search_limit,
            "max_scrolls": 25,
            "headless": headless,
        }
        if location_mode:
            search_kwargs.update(
                latitude=latitude,
                longitude=longitude,
                zoom=search_zoom,
            )
        result = search_google_maps(query, **search_kwargs)
        for candidate in result.get("candidates") or []:
            if location_mode and radius_km is not None and not _within_radius(
                candidate, latitude, longitude, radius_km
            ):
                continue
            key = _candidate_key(candidate)
            if key:
                candidate["searchQuery"] = query
                candidates.setdefault(key, candidate)
    return list(candidates.values()), len(queries)


def _existing_candidate_keys(
    duplicate_check_url: str,
    callback_token: str,
    goroute_job_id: str,
    python_job_id: str,
    candidates: list[dict[str, Any]],
) -> set[str]:
    existing: set[str] = set()
    for offset in range(0, len(candidates), 500):
        chunk = candidates[offset:offset + 500]
        body = {
            "jobId": goroute_job_id,
            "pythonJobId": python_job_id,
            "candidates": [
                {
                    "candidateKey": _candidate_key(candidate),
                    "googlePlaceId": candidate.get("placeId"),
                    "cid": candidate.get("cid"),
                    "latitude": candidate.get("latitude"),
                    "longitude": candidate.get("longitude"),
                }
                for candidate in chunk
            ],
        }
        try:
            response = _post_json(duplicate_check_url, body, callback_token)
            existing.update(str(key) for key in response.get("existingCandidateKeys") or [])
        except Exception as exc:
            # Import still performs the authoritative duplicate check.
            LOG.warning("Duplicate pre-check failed; continuing with final import guard: %s", exc)
            return set()
    return existing


def run_nationwide_job(
    *,
    goroute_job_id: str,
    python_job_id: str,
    callback_url: str,
    import_url: str,
    callback_token: str,
    max_reviews: int,
    selected_reviews: int,
    min_review_count: int,
    min_google_rating: float,
    search_limit_per_query: int,
    max_queries_per_region: int,
    headless: bool,
    region_codes: list[str] | None,
    query_mode: str,
    custom_queries: list[str],
    include_regional_specialties: bool,
    include_tourist_areas: bool,
    duplicate_check_url: str,
    cancel_requested: Callable[[], bool],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_km: float = 10.0,
    search_zoom: int = 14,
) -> dict[str, Any]:
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    location_mode = latitude is not None and longitude is not None
    regions = [_location_region(latitude, longitude)] if location_mode else selected_regions(region_codes)
    global_counts = Counters()
    global_seen: set[str] = set()

    try:
        _event(callback_url, callback_token, goroute_job_id, python_job_id, "JOB_STARTED", counters=global_counts)
        for sequence_no, region in enumerate(regions, start=1):
            if cancel_requested():
                _event(callback_url, callback_token, goroute_job_id, python_job_id, "JOB_CANCELLED", counters=global_counts)
                return {"success": False, "cancelled": True, **asdict(global_counts)}

            region_counts = Counters()
            query_count = 0
            _event(
                callback_url, callback_token, goroute_job_id, python_job_id, "REGION_STARTED",
                region=region, sequence_no=sequence_no, region_status="PROCESSING", counters=region_counts,
            )
            try:
                candidates, query_count = _discover_region(
                    region,
                    max_queries_per_region,
                    search_limit_per_query,
                    headless,
                    query_mode=query_mode,
                    custom_queries=custom_queries,
                    include_regional_specialties=include_regional_specialties,
                    include_tourist_areas=include_tourist_areas,
                    latitude=latitude,
                    longitude=longitude,
                    radius_km=radius_km if location_mode else None,
                    search_zoom=search_zoom,
                )
                unique_candidates: list[dict[str, Any]] = []
                for candidate in candidates:
                    key = _candidate_key(candidate)
                    if key and key not in global_seen:
                        global_seen.add(key)
                        unique_candidates.append(candidate)
                region_counts.discovered_count = len(unique_candidates)
                existing_keys = _existing_candidate_keys(
                    duplicate_check_url,
                    callback_token,
                    goroute_job_id,
                    python_job_id,
                    unique_candidates,
                )
                if existing_keys:
                    unique_candidates = [
                        candidate for candidate in unique_candidates
                        if _candidate_key(candidate) not in existing_keys
                    ]
                    region_counts.processed_count += len(existing_keys)
                    region_counts.skipped_count += len(existing_keys)

                for candidate in unique_candidates:
                    if cancel_requested():
                        global_counts.add(region_counts)
                        _event(callback_url, callback_token, goroute_job_id, python_job_id, "JOB_CANCELLED", counters=global_counts)
                        return {"success": False, "cancelled": True, **asdict(global_counts)}
                    url = str(candidate.get("googleMapsLink") or candidate.get("resolvedUrl") or "")
                    if not url:
                        region_counts.failed_count += 1
                        continue
                    region_counts.processed_count += 1
                    metadata_place = scrape_place(
                        url,
                        headless=headless,
                        max_reviews=max_reviews,
                        max_scrolls=1,
                        include_reviews=False,
                        reviews_with_images_only=False,
                    )
                    if metadata_place.get("status") == "failed":
                        region_counts.failed_count += 1
                        continue
                    _ensure_place_identity(metadata_place, candidate)
                    if location_mode and not _within_radius(
                        metadata_place, latitude, longitude, radius_km
                    ):
                        region_counts.skipped_count += 1
                        continue
                    filter_reason = _initial_filter_reason(
                        metadata_place, min_review_count, min_google_rating
                    )
                    place = metadata_place
                    review_inputs: list[dict[str, Any]] = []

                    if filter_reason is None:
                        region_counts.eligible_count += 1
                        reviewed_place = scrape_place(
                            url,
                            headless=headless,
                            max_reviews=max_reviews,
                            max_scrolls=400,
                            include_reviews=True,
                            reviews_with_images_only=False,
                            require_newest_sort=True,
                        )
                        if reviewed_place.get("status") == "failed":
                            filter_reason = "REVIEW_SCRAPE_FAILED"
                        else:
                            _ensure_place_identity(reviewed_place, candidate)
                            place = reviewed_place

                    google_place_id = str(place.get("placeId") or place.get("place_id") or "").strip()
                    if not google_place_id:
                        region_counts.failed_count += 1
                        continue

                    if filter_reason is None:
                        review_inputs = [
                            normalized
                            for review in (place.get("userReviews") or place.get("reviews") or [])
                            if (normalized := _review_input(review, google_place_id)) is not None
                        ]
                        if not review_inputs:
                            filter_reason = "NO_SCORING_REVIEWS"

                    inactive = filter_reason is not None
                    place_payload = _place_payload(place, inactive=inactive)
                    response = _post_json(import_url, {
                        "jobId": goroute_job_id,
                        "pythonJobId": python_job_id,
                        "regionCode": region.code,
                        "regionName": region.name,
                        "searchQuery": candidate.get("searchQuery"),
                        "filterReason": filter_reason,
                        "place": place_payload,
                        "reviews": review_inputs[:max_reviews],
                    }, callback_token)
                    outcome = str(response.get("outcome") or "")
                    if response.get("imported"):
                        region_counts.imported_count += 1
                        if outcome == "IMPORTED" and int(response.get("selectedReviewCount") or 0) < selected_reviews:
                            region_counts.insufficient_photo_count += 1
                    if outcome.startswith("SAVED_INACTIVE_"):
                        region_counts.skipped_count += 1
                        if outcome == "SAVED_INACTIVE_ADJUSTED_RATING":
                            region_counts.rejected_score_count += 1
                    elif not response.get("imported"):
                        region_counts.skipped_count += 1

                    if progress_callback:
                        progress_callback({
                            "regionCode": region.code,
                            "regionName": region.name,
                            **_camel_counters(region_counts),
                        })

                global_counts.add(region_counts)
                _event(
                    callback_url, callback_token, goroute_job_id, python_job_id, "REGION_COMPLETED",
                    region=region, sequence_no=sequence_no, region_status="COMPLETED",
                    counters=region_counts, query_count=query_count,
                )
                _event(
                    callback_url, callback_token, goroute_job_id, python_job_id, "JOB_PROGRESS",
                    counters=global_counts,
                )
            except Exception as exc:
                LOG.exception("Nationwide region failed: %s", region.name)
                region_counts.failed_count += 1
                global_counts.add(region_counts)
                _event(
                    callback_url, callback_token, goroute_job_id, python_job_id, "REGION_FAILED",
                    region=region, sequence_no=sequence_no, region_status="FAILED",
                    counters=region_counts, query_count=query_count, error_message=str(exc),
                )
                _event(callback_url, callback_token, goroute_job_id, python_job_id, "JOB_PROGRESS", counters=global_counts)

        _event(callback_url, callback_token, goroute_job_id, python_job_id, "JOB_COMPLETED", counters=global_counts)
        return {"success": True, "regions": len(regions), **asdict(global_counts)}
    except Exception as exc:
        LOG.exception("Nationwide job failed")
        _event(
            callback_url, callback_token, goroute_job_id, python_job_id, "JOB_FAILED",
            counters=global_counts, error_message=str(exc),
        )
        raise
