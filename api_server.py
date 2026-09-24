#!/usr/bin/env python3
"""
HTTP API for scraping Google Maps places and uploading to goroute.

Scrape jobs run asynchronously in background. Poll GET /api/v1/jobs/{jobId}.

Usage:
  uvicorn api_server:app --host 0.0.0.0 --port 8080
  python api_server.py
"""

import logging
import sys
import time
from typing import Any, Literal
from uuid import UUID

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

from config import (
    GOROUTE_PLACES_URL,
    GOROUTE_REVIEW_REFRESH_URL,
    PLACE_API_HOST,
    PLACE_API_PORT,
    PLACE_REFRESH_API_KEY,
    PLACE_REFRESH_ENABLED,
    PLACE_REFRESH_HOUR,
    PLACE_REFRESH_MAX_PLACES,
    PLACE_REFRESH_MINUTE,
    PLACE_REFRESH_TIMEZONE,
    PLACE_REVIEW_REFRESH_ENABLED,
    PLACE_REVIEW_REFRESH_HOUR,
    PLACE_REVIEW_REFRESH_MAX_PLACES,
    PLACE_REVIEW_REFRESH_MINUTE,
    goroute_api_headers,
)
from job_store import job_store
from place_pipeline import import_scraped_place, scrape_and_import
from place_refresh_job import run_place_detail_refresh
from place_review_refresh_job import is_daily_refresh_enabled, run_place_review_refresh
from place_resolver import resolve_place_url
from place_searcher import search_google_maps
from place_urls import is_google_maps_url, is_google_maps_viewport_url
from run_job import DEFAULT_MAX_REVIEWS, setup_logging
from nationwide_job import run_nationwide_job
from social_location_extractor import extract_social_location, is_social_video_url

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Place Import API",
    description="Scrape Google Maps places and upload to goroute backend (async jobs).",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

refresh_scheduler: BackgroundScheduler | None = None


class ImportConfig(BaseModel):
    enabled: bool = True
    url: str = Field(..., description="GoRoute contribution import URL")
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)


class ContributionPayload(BaseModel):
    gorouteJobId: str
    contributionGroupId: str
    skipPlaceInsertIfExists: bool = True
    contributorUserIds: list[str] = Field(default_factory=list)
    gorouteReviews: list[dict[str, Any]] = Field(default_factory=list)


class ScrapeAndImportRequest(BaseModel):
    url: str = Field(..., description="Google Maps place URL")
    max_reviews: int = Field(
        DEFAULT_MAX_REVIEWS,
        ge=1,
        le=DEFAULT_MAX_REVIEWS,
        description="Maximum reviews to return",
    )
    max_scrolls: int = Field(100, ge=1, le=500, description="Maximum review scrolls")
    headless: bool = Field(True, description="Run Chrome in headless mode")
    visibilityStatus: str | None = Field(None, description="Optional GoRoute place visibility status, e.g. ACTIVE/INACTIVE")
    include_reviews: bool = Field(True, description="Scrape reviews in addition to basic place details")
    importConfig: ImportConfig | None = None
    contribution: ContributionPayload | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not is_google_maps_url(cleaned):
            raise ValueError("Invalid Google Maps URL")
        if is_google_maps_viewport_url(cleaned):
            raise ValueError("Google Maps viewport URL is not a place URL")
        return cleaned

    @model_validator(mode="after")
    def validate_contribution_flow(self) -> "ScrapeAndImportRequest":
        if self.contribution and not self.importConfig:
            raise ValueError("importConfig is required when contribution is provided")
        if self.contribution and self.importConfig and not self.importConfig.enabled:
            raise ValueError("importConfig.enabled must be true for contribution flow")
        return self


class BatchScrapeAndImportRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=20, description="Google Maps URLs")
    max_reviews: int = Field(DEFAULT_MAX_REVIEWS, ge=1, le=DEFAULT_MAX_REVIEWS)
    max_scrolls: int = Field(100, ge=1, le=500)
    headless: bool = True
    visibilityStatus: str | None = Field(None, description="Optional GoRoute place visibility status, e.g. ACTIVE/INACTIVE")
    importConfig: ImportConfig | None = None

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("At least one URL is required")
        for url in cleaned:
            if not is_google_maps_url(url):
                raise ValueError(f"Invalid Google Maps URL: {url}")
            if is_google_maps_viewport_url(url):
                raise ValueError(f"Google Maps viewport URL is not a place URL: {url}")
        return cleaned


class ImportRawRequest(BaseModel):
    place: dict[str, Any] = Field(..., description="Scraped place object (same shape as output.json place entry)")


class PlaceDetailRefreshRequest(BaseModel):
    gorouteJobId: str | None = Field(None, min_length=1)
    callbackUrl: str | None = None
    callbackToken: str = ""
    placeId: UUID | None = Field(
        None,
        description="Optional GoRoute DB place ID; when set, refresh only this place",
    )
    maxPlaces: int | None = Field(
        None,
        ge=1,
        le=10000,
        description="Optional cap for manual/test runs; omit to refresh every eligible DB place",
    )
    headless: bool = Field(True, description="Run Chrome in headless mode")
    continueOnError: bool = Field(True, description="Continue refreshing remaining places after one failure")
    includeInactive: bool = Field(False, description="Also refresh INACTIVE places; ACTIVE only by default")

    @field_validator("callbackUrl")
    @classmethod
    def validate_callback_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("callbackUrl must use HTTP or HTTPS")
        return cleaned

    @model_validator(mode="after")
    def validate_backend_ownership(self) -> "PlaceDetailRefreshRequest":
        if bool(self.gorouteJobId) != bool(self.callbackUrl):
            raise ValueError("gorouteJobId and callbackUrl must be provided together")
        return self


class PlaceReviewRefreshRequest(BaseModel):
    placeId: UUID | None = Field(
        None,
        description="Optional GoRoute DB place ID; omit to scan every due ACTIVE place",
    )
    maxPlaces: int | None = Field(None, ge=1, le=10000)
    placesUrl: str | None = Field(None, min_length=8, max_length=500)
    reviewRefreshUrl: str | None = Field(None, min_length=8, max_length=500)
    headless: bool = True
    continueOnError: bool = True


class PlaceReviewRerunRequest(BaseModel):
    mode: Literal["ALL", "FAILED", "NOT_EXECUTED", "FAILED_AND_NOT_EXECUTED"]


class PlaceSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300, description="Place search text")
    latitude: float | None = Field(None, ge=-90, le=90, description="Optional search bias latitude")
    longitude: float | None = Field(None, ge=-180, le=180, description="Optional search bias longitude")
    zoom: int = Field(14, ge=3, le=21, description="Google Maps zoom used when latitude/longitude are provided")
    limit: int = Field(5, ge=1, le=10, description="Maximum candidates to return")
    headless: bool = Field(True, description="Run Chrome in headless mode")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("query is required")
        return cleaned

    @model_validator(mode="after")
    def validate_location_bias(self) -> "PlaceSearchRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class NationwideJobRequest(BaseModel):
    gorouteJobId: str = Field(..., min_length=1)
    callbackUrl: str
    importUrl: str
    callbackToken: str = ""
    maxReviews: int = Field(200, ge=20, le=200)
    selectedReviews: int = Field(20, ge=1, le=50)
    lowStarQuota: int = Field(4, ge=0, le=20)
    minReviewCount: int = Field(101, ge=1)
    minGoogleRating: float = Field(4.0, ge=0, le=5)
    minAdjustedRating: float = Field(3.0, ge=0, le=5)
    searchLimitPerQuery: int = Field(40, ge=1, le=100)
    maxQueriesPerRegion: int = Field(20, ge=1, le=50)
    headless: bool = True
    regionCodes: list[str] | None = None
    queryMode: Literal["APPEND", "REPLACE"] = "APPEND"
    customQueries: list[str] = Field(default_factory=list, max_length=50)
    includeRegionalSpecialties: bool = True
    includeTouristAreas: bool = True
    latitude: float | None = Field(None, ge=-90, le=90, description="Optional search center latitude")
    longitude: float | None = Field(None, ge=-180, le=180, description="Optional search center longitude")
    radiusKm: float = Field(10, gt=0, le=100, description="Search radius around the center in kilometres")
    searchZoom: int = Field(14, ge=3, le=21, description="Google Maps zoom used for coordinate searches")
    duplicateCheckUrl: str

    @field_validator("callbackUrl", "importUrl", "duplicateCheckUrl")
    @classmethod
    def validate_internal_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Internal URL must use HTTP or HTTPS")
        return cleaned

    @model_validator(mode="after")
    def validate_search_configuration(self) -> "NationwideJobRequest":
        if self.queryMode == "REPLACE" and not any(query.strip() for query in self.customQueries):
            raise ValueError("customQueries is required when queryMode is REPLACE")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class SocialLocationExtractRequest(BaseModel):
    url: str = Field(..., description="TikTok or Instagram Reel/Post URL")
    language: str = Field("vi", min_length=2, max_length=20, description="Output language for descriptive fields")
    dry_run: bool = Field(False, description="Stop before Claude/map search and save transcript/images for inspection")
    debug_output_dir: str | None = Field(None, description="Optional folder for dry-run artifacts")
    max_audio_seconds: int | None = Field(None, ge=10, le=600, description="Maximum audio seconds to transcribe (None = no limit)")
    max_duration_seconds: int = Field(180, ge=10, le=600, description="Reject videos longer than this duration")
    max_frames: int = Field(50, ge=1, le=100, description="Maximum frames/slides to extract and send to the AI provider")
    frame_interval_seconds: int = Field(3, ge=1, le=30, description="Seconds between extracted frames")
    image_max_width: int = Field(384, ge=256, le=1280, description="Resize extracted images to this max width")
    image_jpeg_quality: int = Field(10, ge=2, le=31, description="FFmpeg JPEG q:v, lower is better quality/larger file")
    ai_provider: str | None = Field(None, description="ANTHROPIC, DEEPSEEK, OPENAI, or OPENAI_COMPATIBLE")
    ai_model: str | None = Field(None, max_length=200, description="Provider model override")
    ai_base_url: str | None = Field(None, max_length=500, description="Provider API base URL override")
    include_map_search: bool = Field(True, description="Run Google Maps scrape search for each candidate")
    map_search_limit: int = Field(1, ge=1, le=10, description="Max Google Maps candidates per extracted place")
    headless: bool = Field(True, description="Run Chrome headless for map search")
    keep_temp: bool = Field(False, description="Keep temp files for debugging")

    @field_validator("url")
    @classmethod
    def validate_social_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not is_social_video_url(cleaned):
            raise ValueError("URL must be a TikTok or Instagram URL")
        return cleaned

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("language is required")
        return cleaned

    @field_validator("ai_provider")
    @classmethod
    def validate_ai_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if cleaned not in {"ANTHROPIC", "CLAUDE", "DEEPSEEK", "OPENAI", "OPENAI_COMPATIBLE"}:
            raise ValueError("Unsupported AI provider")
        return cleaned

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        if not cleaned.startswith("https://"):
            raise ValueError("ai_base_url must use HTTPS")
        return cleaned.rstrip("/")


class SocialLocationJobRequest(SocialLocationExtractRequest):
    callback_url: str = Field(..., description="GoRoute backend URL for incremental progress and final callbacks")
    callback_token: str = Field("", max_length=500, description="Shared token sent with social-location callbacks")
    goroute_job_id: str | None = Field(None, description="GoRoute-owned job ID for callback correlation")

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("callback_url must be an HTTP URL")
        return cleaned


class HealthResponse(BaseModel):
    status: str
    service: str


class JobCreatedResponse(BaseModel):
    jobId: str
    status: str
    message: str
    pollUrl: str


def _job_created_response(job_id: str) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "status": "pending",
        "message": "Job queued. Poll pollUrl until status is completed or failed.",
        "pollUrl": f"/api/v1/jobs/{job_id}",
    }


def _verify_refresh_api_key(api_key: str | None) -> None:
    if PLACE_REFRESH_API_KEY and api_key != PLACE_REFRESH_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid X-API-Key")


def _queue_place_detail_refresh(
    *,
    source: str,
    place_id: str | None = None,
    max_places: int | None = None,
    headless: bool = True,
    continue_on_error: bool = True,
    include_inactive: bool = False,
    goroute_job_id: str | None = None,
    callback_url: str | None = None,
    callback_token: str = "",
) -> tuple[Any, bool]:
    request_payload = {
        "source": source,
        "place_id": place_id,
        "max_places": max_places,
        "headless": headless,
        "continue_on_error": continue_on_error,
        "include_inactive": include_inactive,
        "goroute_job_id": goroute_job_id,
    }
    job, created = job_store.create_unique("place-detail-refresh", request_payload)
    if not created:
        return job, False

    def notify(event_type: str, progress: dict[str, Any] | None = None, error: str | None = None) -> None:
        """Report progress to GoRoute. Never raises: telemetry must not abort the scrape.

        GoRoute reconciles a job whose callbacks were lost through its own watchdog, so
        losing an event costs visibility, not work. Raising here used to kill the run on
        the very first JOB_STARTED.
        """
        if not goroute_job_id or not callback_url:
            return
        payload = {
            "jobId": goroute_job_id,
            "pythonJobId": job.id,
            "eventType": event_type,
            "errorMessage": error,
            **(progress or {}),
        }
        headers = goroute_api_headers({"Content-Type": "application/json"})
        if callback_token:
            headers["X-Internal-Token"] = callback_token
        last_error = ""
        for attempt in range(1, 4):
            try:
                response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
                if 200 <= response.status_code < 300:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            except Exception as exc:
                last_error = str(exc)
            logger.warning("Refresh callback %s attempt %d failed: %s", event_type, attempt, last_error)
            if attempt < 3:
                time.sleep(attempt * 1.5)
        logger.error(
            "Refresh callback %s gave up for GoRoute job %s: %s", event_type, goroute_job_id, last_error
        )

    def report(progress: dict[str, Any]) -> None:
        job_store.update_progress(job.id, progress)
        notify("JOB_PROGRESS", progress)

    def worker() -> dict[str, Any]:
        try:
            notify("JOB_STARTED")
            result = run_place_detail_refresh(
                place_id=place_id,
                max_places=max_places,
                headless=headless,
                continue_on_error=continue_on_error,
                include_inactive=include_inactive,
                progress_callback=report,
                cancel_requested=lambda: job_store.is_cancel_requested(job.id),
            )
            final_progress = {
                key: result.get(key)
                for key in (
                    "databaseCount", "eligibleCount", "processedCount", "successCount", "failedCount"
                )
            }
            if result.get("blockedByGoogle"):
                notify(
                    "JOB_FAILED",
                    final_progress,
                    error="Google blocked this IP mid-run (bot-check page); stopped early instead of "
                    "burning through the rest of the batch while blocked. Retry later once traffic "
                    "from this IP has cooled off.",
                )
            else:
                notify("JOB_CANCELLED" if result.get("cancelled") else "JOB_COMPLETED", final_progress)
            return result
        except Exception as exc:
            notify("JOB_FAILED", error=str(exc))
            raise

    job_store.submit(job, worker)
    logger.info("Queued place detail refresh: job_id=%s source=%s max_places=%s", job.id, source, max_places)
    return job, True


def _run_scheduled_place_detail_refresh() -> None:
    max_places = PLACE_REFRESH_MAX_PLACES if PLACE_REFRESH_MAX_PLACES > 0 else None
    job, created = _queue_place_detail_refresh(
        source="schedule",
        place_id=None,
        max_places=max_places,
    )
    if not created:
        logger.warning("Scheduled place refresh skipped; job %s is still active", job.id)


# COMMENTED OUT DUE TO SCRAPE REVIEW ERRORS
def _queue_place_review_refresh(
    *,
    source: str,
    place_id: str | None,
    place_ids: list[str] | None = None,
    max_places: int | None = None,
    places_url: str = GOROUTE_PLACES_URL,
    review_refresh_url: str = GOROUTE_REVIEW_REFRESH_URL,
    headless: bool = True,
    continue_on_error: bool = True,
    include_recent: bool = False,
) -> tuple[Any, bool]:
    """DISABLED: Review refresh is currently disabled due to scrape errors."""
    raise RuntimeError("Review refresh is currently disabled")


def _run_scheduled_place_review_refresh() -> None:
    """DISABLED: Review refresh is currently disabled due to scrape errors."""
    logger.warning("Scheduled review refresh is disabled")
    return



@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="place-import-api")


@app.get("/api/v1/places/resolve", tags=["places"])
def resolve_place(
    url: str = Query(..., description="Google Maps URL to resolve"),
    headless: bool = Query(True, description="Run Chrome headless"),
) -> dict[str, Any]:
    """Resolve short Google Maps URL to place identifiers before triggering scrape."""
    if not is_google_maps_url(url.strip()) or is_google_maps_viewport_url(url.strip()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid Google Maps URL")

    try:
        return resolve_place_url(url.strip(), headless=headless)
    except Exception as exc:
        logger.exception("Resolve failed for %s", url)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "RESOLVE_FAILED", "message": str(exc)},
        ) from exc


@app.get("/api/v1/places/search", tags=["places"])
def search_places(
    query: str = Query(..., min_length=1, max_length=300, description="Place search text"),
    latitude: float | None = Query(None, ge=-90, le=90, description="Optional search bias latitude"),
    longitude: float | None = Query(None, ge=-180, le=180, description="Optional search bias longitude"),
    zoom: int = Query(14, ge=3, le=21, description="Google Maps zoom used with latitude/longitude"),
    limit: int = Query(5, ge=1, le=10, description="Maximum candidates to return"),
    headless: bool = Query(True, description="Run Chrome headless"),
) -> dict[str, Any]:
    """Search Google Maps by text and return lightweight place candidate URLs."""
    body = PlaceSearchRequest(
        query=query,
        latitude=latitude,
        longitude=longitude,
        zoom=zoom,
        limit=limit,
        headless=headless,
    )
    return search_places_post(body)


@app.post("/api/v1/places/search", tags=["places"])
def search_places_post(body: PlaceSearchRequest) -> dict[str, Any]:
    """Search Google Maps by text and return lightweight place candidate URLs."""
    try:
        return search_google_maps(
            body.query,
            latitude=body.latitude,
            longitude=body.longitude,
            zoom=body.zoom,
            limit=body.limit,
            headless=body.headless,
        )
    except Exception as exc:
        logger.exception("Search failed for %s", body.query)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "SEARCH_FAILED", "message": str(exc)},
        ) from exc


def _social_platform(url: str) -> str:
    cleaned = url.lower()
    if "tiktok.com" in cleaned:
        return "tiktok"
    if "instagram.com" in cleaned or "instagr.am" in cleaned:
        return "instagram"
    return "unknown"


def _post_social_location_callback(
    callback_url: str,
    payload: dict[str, Any],
    callback_token: str = "",
) -> dict[str, Any]:
    last_error = ""
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            headers = goroute_api_headers({"Content-Type": "application/json"})
            if callback_token:
                headers["X-Internal-Token"] = callback_token
            response = requests.post(
                callback_url,
                json=payload,
                headers=headers,
                timeout=20,
            )
            if 200 <= response.status_code < 300:
                return {"success": True, "statusCode": response.status_code}
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
        except Exception as exc:
            last_error = str(exc)
        logger.warning(
            "Social location callback failed: url=%s attempt=%s error=%s",
            callback_url,
            attempt,
            last_error,
        )
        if attempt < max_attempts:
            time.sleep(min(30, 2 ** (attempt - 1)))
    return {"success": False, "error": last_error}


@app.post("/api/v1/social-location/jobs", tags=["social-location"])
def create_social_location_job(body: SocialLocationJobRequest) -> dict[str, Any]:
    """Queue social-location extraction and callback GoRoute when finished."""
    platform = _social_platform(body.url)
    request_payload = body.model_dump()
    request_payload["platform"] = platform
    existing = job_store.find_by_request_value(
        "social-location-extract", "goroute_job_id", body.goroute_job_id
    )
    if existing is not None:
        logger.info(
            "Returning idempotent social location job: job_id=%s goroute_job_id=%s status=%s",
            existing.id,
            body.goroute_job_id,
            existing.status,
        )
        response = _job_created_response(existing.id)
        response["status"] = existing.status
        response["gorouteJobId"] = body.goroute_job_id
        response["platform"] = platform
        return response
    job = job_store.create("social-location-extract", request_payload)
    logger.info(
        "Queued social location job: job_id=%s goroute_job_id=%s url=%s platform=%s",
        job.id,
        body.goroute_job_id,
        body.url,
        platform,
    )

    def worker() -> dict[str, Any]:
        result = None
        success = False
        error_message = None

        def publish_progress(partial_result: dict[str, Any]) -> None:
            callback_payload = {
                "gorouteJobId": body.goroute_job_id,
                "pythonJobId": job.id,
                "status": "PROCESSING",
                "sourceUrl": body.url,
                "platform": platform,
                "result": partial_result,
                "error": None,
            }
            callback_result = _post_social_location_callback(
                body.callback_url, callback_payload, body.callback_token
            )
            if not callback_result.get("success"):
                logger.warning(
                    "Could not publish social extraction progress: job_id=%s progress=%s error=%s",
                    job.id,
                    partial_result.get("progress"),
                    callback_result.get("error"),
                )
        
        try:
            result = extract_social_location(
                body.url,
                language=body.language,
                dry_run=body.dry_run,
                debug_output_dir=body.debug_output_dir,
                max_audio_seconds=body.max_audio_seconds,
                max_duration_seconds=body.max_duration_seconds,
                max_frames=body.max_frames,
                frame_interval_seconds=body.frame_interval_seconds,
                image_max_width=body.image_max_width,
                image_jpeg_quality=body.image_jpeg_quality,
                include_map_search=body.include_map_search,
                map_search_limit=body.map_search_limit,
                headless=body.headless,
                keep_temp=body.keep_temp,
                ai_provider=body.ai_provider,
                ai_model=body.ai_model,
                ai_base_url=body.ai_base_url,
                progress_callback=publish_progress,
            )
            success = bool(result.get("success"))
            raw_error = result.get("error")
            error_message = None if success else (
                raw_error.get("message") if isinstance(raw_error, dict) else str(raw_error or "Extraction failed")
            )
        except Exception as exc:
            logger.exception("Social location extraction failed with exception: url=%s goroute_job_id=%s", body.url, body.goroute_job_id)
            success = False
            error_message = f"Extraction exception: {type(exc).__name__}: {str(exc)}"
            result = {"success": False, "error": error_message}
        
        # Luôn gọi callback dù có lỗi hay không
        callback_error = result.get("error") if isinstance(result, dict) else None
        if not isinstance(callback_error, dict) and error_message:
            callback_error = {"code": "EXTRACTION_FAILED", "message": error_message}
        callback_payload = {
            "gorouteJobId": body.goroute_job_id,
            "pythonJobId": job.id,
            "status": "COMPLETED" if success else str(result.get("rejectedStatus") or "FAILED"),
            "sourceUrl": body.url,
            "platform": platform,
            "result": result,
            "error": callback_error,
        }
        callback_result = _post_social_location_callback(
            body.callback_url, callback_payload, body.callback_token
        )
        
        return {
            "success": success and bool(callback_result.get("success")),
            "extractionSucceeded": success,
            "sourceUrl": body.url,
            "platform": platform,
            "gorouteJobId": body.goroute_job_id,
            "pythonJobId": job.id,
            "callback": callback_result,
            "extraction": result,
            "error": error_message,
        }

    job_store.submit(job, worker)
    response = _job_created_response(job.id)
    response["gorouteJobId"] = body.goroute_job_id
    response["platform"] = platform
    return response


@app.post("/api/v1/social-location/extract", tags=["social-location"])
def extract_social_location_endpoint(body: SocialLocationExtractRequest) -> dict[str, Any]:
    """Extract location candidates from TikTok/Instagram using audio + frames."""
    logger.info(
        "Social location API request: url=%s language=%s dry_run=%s max_audio_seconds=%s max_frames=%s include_map_search=%s",
        body.url,
        body.language,
        body.dry_run,
        body.max_audio_seconds,
        body.max_frames,
        body.include_map_search,
    )
    result = extract_social_location(
        body.url,
        language=body.language,
        dry_run=body.dry_run,
        debug_output_dir=body.debug_output_dir,
        max_audio_seconds=body.max_audio_seconds,
        max_duration_seconds=body.max_duration_seconds,
        max_frames=body.max_frames,
        frame_interval_seconds=body.frame_interval_seconds,
        image_max_width=body.image_max_width,
        image_jpeg_quality=body.image_jpeg_quality,
        include_map_search=body.include_map_search,
        map_search_limit=body.map_search_limit,
        headless=body.headless,
        keep_temp=body.keep_temp,
        ai_provider=body.ai_provider,
        ai_model=body.ai_model,
        ai_base_url=body.ai_base_url,
    )
    if not result.get("success"):
        logger.warning("Social location API failed: url=%s error=%s", body.url, result.get("error"))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.get("error") or result)
    extraction = result.get("extraction") or {}
    logger.info(
        "Social location API completed: url=%s found=%s candidates=%s",
        body.url,
        extraction.get("found"),
        len(extraction.get("candidates") or []),
    )
    return result


@app.get("/api/v1/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str) -> dict[str, Any]:
    """Poll job status and result."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job.to_dict()


@app.post("/api/v1/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job(job_id: str) -> dict[str, Any]:
    """Request cooperative cancellation; long-running workers stop between places."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not job_store.request_cancel(job_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOB_NOT_CANCELLABLE", "status": job.status},
        )
    return {"jobId": job_id, "status": "cancelling"}


@app.post("/api/v1/nationwide/jobs", response_model=JobCreatedResponse, tags=["nationwide"])
def create_nationwide_job(body: NationwideJobRequest) -> dict[str, Any]:
    """Queue the legacy-64-region food place crawl owned by a GoRoute job."""
    request_payload = body.model_dump()
    job, created = job_store.create_unique("nationwide-place-import", request_payload)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NATIONWIDE_JOB_ALREADY_RUNNING",
                "jobId": job.id,
                "pollUrl": f"/api/v1/jobs/{job.id}",
            },
        )

    def worker() -> dict[str, Any]:
        return run_nationwide_job(
            goroute_job_id=body.gorouteJobId,
            python_job_id=job.id,
            callback_url=body.callbackUrl,
            import_url=body.importUrl,
            callback_token=body.callbackToken,
            max_reviews=body.maxReviews,
            selected_reviews=body.selectedReviews,
            min_review_count=body.minReviewCount,
            min_google_rating=body.minGoogleRating,
            search_limit_per_query=body.searchLimitPerQuery,
            max_queries_per_region=body.maxQueriesPerRegion,
            headless=body.headless,
            region_codes=body.regionCodes,
            query_mode=body.queryMode,
            custom_queries=body.customQueries,
            include_regional_specialties=body.includeRegionalSpecialties,
            include_tourist_areas=body.includeTouristAreas,
            latitude=body.latitude,
            longitude=body.longitude,
            radius_km=body.radiusKm,
            search_zoom=body.searchZoom,
            duplicate_check_url=body.duplicateCheckUrl,
            cancel_requested=lambda: job_store.is_cancel_requested(job.id),
            progress_callback=lambda progress: job_store.update_progress(job.id, progress),
        )

    job_store.submit(job, worker)
    return _job_created_response(job.id)


@app.post(
    "/api/v1/maintenance/places/refresh-reviews/{job_id}/rerun",
    response_model=JobCreatedResponse,
    tags=["maintenance"],
)
def rerun_place_review_refresh(
    job_id: str,
    body: PlaceReviewRerunRequest,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Create a new review job from all, failed, or unexecuted places of a terminal job."""
    _verify_refresh_api_key(x_api_key)
    original = job_store.get(job_id)
    if original is None or original.job_type != "place-review-refresh":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review refresh job not found")
    if original.status in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stop the active job before rerunning")

    result = original.result or {}
    snapshot = result.get("progress") if isinstance(result.get("progress"), dict) else result
    place_rows = snapshot.get("places") or []
    result_rows = snapshot.get("results") or result.get("results") or []
    if not place_rows:
        place_rows = [
            {
                "placeId": item.get("placeId"),
                "status": "SUCCESS" if item.get("success") else "FAILED",
            }
            for item in result_rows
            if item.get("placeId")
        ]

    all_ids = [str(item.get("placeId")) for item in place_rows if item.get("placeId")]
    failed_ids = [str(item.get("placeId")) for item in place_rows if item.get("placeId") and item.get("status") == "FAILED"]
    unexecuted_ids = [
        str(item.get("placeId"))
        for item in place_rows
        if item.get("placeId") and item.get("status") in {"PENDING", "RUNNING"}
    ]
    selected = {
        "ALL": all_ids,
        "FAILED": failed_ids,
        "NOT_EXECUTED": unexecuted_ids,
        "FAILED_AND_NOT_EXECUTED": failed_ids + unexecuted_ids,
    }[body.mode]
    selected = list(dict.fromkeys(selected))
    if not selected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No places match the rerun mode")

    request = original.request or {}
    job, _ = _queue_place_review_refresh(
        source=f"rerun:{body.mode.lower()}",
        place_id=None,
        place_ids=selected,
        max_places=None,
        places_url=str(request.get("places_url") or GOROUTE_PLACES_URL),
        review_refresh_url=str(request.get("review_refresh_url") or GOROUTE_REVIEW_REFRESH_URL),
        headless=bool(request.get("headless", True)),
        continue_on_error=True,
        include_recent=True,
    )
    return _job_created_response(job.id)


@app.post(
    "/api/v1/maintenance/places/refresh-details",
    response_model=JobCreatedResponse,
    tags=["maintenance"],
)
def trigger_place_detail_refresh(
    body: PlaceDetailRefreshRequest | None = None,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Queue a sequential refresh of Google Maps detail fields for DB places."""
    _verify_refresh_api_key(x_api_key)
    request = body or PlaceDetailRefreshRequest()
    job, created = _queue_place_detail_refresh(
        source="api",
        place_id=str(request.placeId) if request.placeId else None,
        max_places=request.maxPlaces,
        headless=request.headless,
        continue_on_error=request.continueOnError,
        include_inactive=request.includeInactive,
        goroute_job_id=request.gorouteJobId,
        callback_url=request.callbackUrl,
        callback_token=request.callbackToken,
    )
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PLACE_REFRESH_ALREADY_RUNNING",
                "jobId": job.id,
                "pollUrl": f"/api/v1/jobs/{job.id}",
            },
        )
    return _job_created_response(job.id)


@app.get("/api/v1/maintenance/places/refresh-details/schedule", tags=["maintenance"])
def get_place_detail_refresh_schedule(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Return daily refresh schedule configuration and next execution time."""
    _verify_refresh_api_key(x_api_key)
    scheduled_job = refresh_scheduler.get_job("daily-place-detail-refresh") if refresh_scheduler else None
    return {
        "enabled": PLACE_REFRESH_ENABLED,
        "timezone": PLACE_REFRESH_TIMEZONE,
        "hour": PLACE_REFRESH_HOUR,
        "minute": PLACE_REFRESH_MINUTE,
        "maxPlaces": PLACE_REFRESH_MAX_PLACES or None,
        "nextRunAt": scheduled_job.next_run_time.isoformat() if scheduled_job and scheduled_job.next_run_time else None,
    }


# COMMENTED OUT DUE TO SCRAPE REVIEW ERRORS
# @app.post(
#     "/api/v1/maintenance/places/refresh-reviews",
#     response_model=JobCreatedResponse,
#     tags=["maintenance"],
# )
# def trigger_place_review_refresh(
#     body: PlaceReviewRefreshRequest | None = None,
#     x_api_key: str | None = Header(None, alias="X-API-Key"),
# ) -> dict[str, Any]:
#     """Queue sequential review refreshes; every worker handles one ACTIVE place at a time."""
#     _verify_refresh_api_key(x_api_key)
#     request = body or PlaceReviewRefreshRequest()
#     job, created = _queue_place_review_refresh(
#         source="api",
#         place_id=str(request.placeId) if request.placeId else None,
#         max_places=request.maxPlaces,
#         places_url=request.placesUrl or GOROUTE_PLACES_URL,
#         review_refresh_url=request.reviewRefreshUrl or GOROUTE_REVIEW_REFRESH_URL,
#         headless=request.headless,
#         continue_on_error=request.continueOnError,
#     )
#     if not created:
#         raise HTTPException(
#             status_code=status.HTTP_409_CONFLICT,
#             detail={
#                 "code": "PLACE_REVIEW_REFRESH_ALREADY_RUNNING",
#                 "jobId": job.id,
#                 "pollUrl": f"/api/v1/jobs/{job.id}",
#             },
#         )
#     return _job_created_response(job.id)


# @app.get("/api/v1/maintenance/places/refresh-reviews/schedule", tags=["maintenance"])
# def get_place_review_refresh_schedule(
#     x_api_key: str | None = Header(None, alias="X-API-Key"),
# ) -> dict[str, Any]:
#     _verify_refresh_api_key(x_api_key)
#     scheduled_job = refresh_scheduler.get_job("daily-place-review-refresh") if refresh_scheduler else None
#     return {
#         "enabled": PLACE_REVIEW_REFRESH_ENABLED and is_daily_refresh_enabled(),
#         "schedulerEnabled": PLACE_REVIEW_REFRESH_ENABLED,
#         "configEnabled": is_daily_refresh_enabled(),
#         "timezone": PLACE_REFRESH_TIMEZONE,
#         "hour": PLACE_REVIEW_REFRESH_HOUR,
#         "minute": PLACE_REVIEW_REFRESH_MINUTE,
#         "maxPlaces": PLACE_REVIEW_REFRESH_MAX_PLACES or None,
#         "nextRunAt": scheduled_job.next_run_time.isoformat() if scheduled_job and scheduled_job.next_run_time else None,
#     }



@app.post("/api/v1/places/scrape-and-import", response_model=JobCreatedResponse, tags=["places"])
def scrape_and_import_endpoint(body: ScrapeAndImportRequest) -> dict[str, Any]:
    """Queue scrape + import job. Returns immediately with jobId."""
    logger.info("Queued scrape-and-import job: %s", body.url)
    request_payload = body.model_dump()
    job = job_store.create("scrape-and-import", request_payload)

    import_config = body.importConfig.model_dump() if body.importConfig else None
    contribution = body.contribution.model_dump() if body.contribution else None

    def worker() -> dict[str, Any]:
        result = scrape_and_import(
            body.url,
            headless=body.headless,
            max_reviews=body.max_reviews,
            max_scrolls=body.max_scrolls,
            include_reviews=body.include_reviews,
            job_id=job.id,
            import_config=import_config,
            contribution=contribution,
            place_overrides={"visibilityStatus": body.visibilityStatus},
        )
        return result.to_dict()

    job_store.submit(job, worker)
    return _job_created_response(job.id)


@app.post("/api/v1/places/scrape-and-import/batch", tags=["places"])
def scrape_and_import_batch_endpoint(body: BatchScrapeAndImportRequest) -> dict[str, Any]:
    """Queue batch scrape + import job (legacy flow only). Returns immediately with jobId."""
    logger.info("Queued batch scrape-and-import job: %d urls", len(body.urls))
    job = job_store.create("scrape-and-import-batch", body.model_dump())

    def worker() -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        success_count = 0
        import_config = body.importConfig.model_dump() if body.importConfig else None

        for url in body.urls:
            result = scrape_and_import(
                url,
                headless=body.headless,
                max_reviews=body.max_reviews,
                max_scrolls=body.max_scrolls,
                import_config=import_config,
                place_overrides={"visibilityStatus": body.visibilityStatus},
            )
            item = {"url": url, **result.to_dict()}
            results.append(item)
            if result.success:
                success_count += 1

        return {
            "success": success_count == len(body.urls),
            "total": len(body.urls),
            "successCount": success_count,
            "failedCount": len(body.urls) - success_count,
            "results": results,
        }

    job_store.submit(job, worker)
    response = _job_created_response(job.id)
    response["total"] = len(body.urls)
    return response


@app.post("/api/v1/places/import", tags=["places"])
def import_raw_endpoint(body: ImportRawRequest) -> dict[str, Any]:
    """Upload already-scraped place data via legacy /places/import (sync, no scrape)."""
    result = import_scraped_place(body.place)
    response = result.to_dict()
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=response,
        )
    return response


@app.on_event("startup")
def on_startup() -> None:
    global refresh_scheduler
    setup_logging("WARNING", "api_scraper.log")
    if (PLACE_REFRESH_ENABLED or PLACE_REVIEW_REFRESH_ENABLED) and refresh_scheduler is None:
        refresh_scheduler = BackgroundScheduler(timezone=PLACE_REFRESH_TIMEZONE)
    if PLACE_REFRESH_ENABLED and refresh_scheduler is not None:
        refresh_scheduler.add_job(
            _run_scheduled_place_detail_refresh,
            trigger=CronTrigger(
                hour=PLACE_REFRESH_HOUR,
                minute=PLACE_REFRESH_MINUTE,
                timezone=PLACE_REFRESH_TIMEZONE,
            ),
            id="daily-place-detail-refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Daily place detail refresh scheduled at %02d:%02d %s",
            PLACE_REFRESH_HOUR,
            PLACE_REFRESH_MINUTE,
            PLACE_REFRESH_TIMEZONE,
        )
    # COMMENTED OUT DUE TO SCRAPE REVIEW ERRORS
    # if PLACE_REVIEW_REFRESH_ENABLED and refresh_scheduler is not None:
    #     refresh_scheduler.add_job(
    #         _run_scheduled_place_review_refresh,
    #         trigger=CronTrigger(
    #             hour=PLACE_REVIEW_REFRESH_HOUR,
    #             minute=PLACE_REVIEW_REFRESH_MINUTE,
    #             timezone=PLACE_REFRESH_TIMEZONE,
    #         ),
    #         id="daily-place-review-refresh",
    #         replace_existing=True,
    #         max_instances=1,
    #         coalesce=True,
    #     )
    #     logger.info(
    #         "Daily ACTIVE-place review refresh scheduled at %02d:%02d %s",
    #         PLACE_REVIEW_REFRESH_HOUR,
    #         PLACE_REVIEW_REFRESH_MINUTE,
    #         PLACE_REFRESH_TIMEZONE,
    #     )
    if refresh_scheduler is not None:
        refresh_scheduler.start()
    logger.info("Place Import API started on %s:%s", PLACE_API_HOST, PLACE_API_PORT)


@app.on_event("shutdown")
def on_shutdown() -> None:
    global refresh_scheduler
    if refresh_scheduler is not None:
        refresh_scheduler.shutdown(wait=False)
        refresh_scheduler = None


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn not installed. Install with: pip install uvicorn")
        return 1

    uvicorn.run(
        "api_server:app",
        host=PLACE_API_HOST,
        port=PLACE_API_PORT,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
