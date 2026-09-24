"""Shared scrape-and-import pipeline used by Telegram bot and HTTP API."""

from dataclasses import dataclass, field
from typing import Any

from config import GOROUTE_API_HEADERS, GOROUTE_API_URL
from contribution_import import build_contribution_import_body, import_contribution
from run_job import DEFAULT_MAX_REVIEWS, scrape_place
from upload_to_api import format_place_for_api, upload_place_detailed
from place_urls import is_google_maps_viewport_url


GENERIC_MAP_TITLES = {"this place", "dropped pin", "this location"}


@dataclass
class PlaceSummary:
    title: str
    place_id: str
    review_rating: float
    review_count: int
    reviews_scraped: int
    google_maps_link: str = ""

    @classmethod
    def from_place_data(cls, place_data: dict[str, Any]) -> "PlaceSummary":
        return cls(
            title=place_data.get("title") or place_data.get("name") or "Unknown",
            place_id=place_data.get("placeId") or place_data.get("place_id") or "",
            review_rating=float(place_data.get("reviewRating") or 0),
            review_count=int(place_data.get("reviewCount") or place_data.get("review_count") or 0),
            reviews_scraped=int(place_data.get("reviews_count_output") or 0),
            google_maps_link=place_data.get("googleMapsLink") or place_data.get("input_url") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "placeId": self.place_id,
            "reviewRating": self.review_rating,
            "reviewCount": self.review_count,
            "reviewsScraped": self.reviews_scraped,
            "googleMapsLink": self.google_maps_link,
        }


@dataclass
class ImportResult:
    success: bool
    place: PlaceSummary | None = None
    scrape_error: str | None = None
    upload_status_code: int | None = None
    upload_error: str | None = None
    raw_place_data: dict[str, Any] = field(default_factory=dict, repr=False)
    google_place_id: str = ""
    place_already_exists: bool = False
    goroute_place_id: str | None = None
    reviews_published: int = 0
    contributors_added: int = 0
    import_http_status: int | None = None
    blocked_by_google: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"success": self.success}
        if self.place:
            payload["place"] = self.place.to_dict()
        if self.blocked_by_google:
            payload["blockedByGoogle"] = True
        if self.google_place_id:
            payload["googlePlaceId"] = self.google_place_id
        if self.scrape_error:
            payload["scrapeError"] = self.scrape_error
        if self.upload_status_code is not None:
            payload["upload"] = {
                "statusCode": self.upload_status_code,
                "error": self.upload_error,
            }
            payload["importHttpStatus"] = self.upload_status_code
        payload["placeAlreadyExists"] = self.place_already_exists
        if self.goroute_place_id:
            payload["goroutePlaceId"] = self.goroute_place_id
        if self.reviews_published:
            payload["reviewsPublished"] = self.reviews_published
        if self.contributors_added:
            payload["contributorsAdded"] = self.contributors_added
        if self.place:
            payload["title"] = self.place.title
        return payload


def import_scraped_place(
    place_data: dict[str, Any],
    import_config: dict[str, Any] | None = None,
) -> ImportResult:
    """Format scraped place data and upload to default goroute /places/import."""
    summary = PlaceSummary.from_place_data(place_data)
    api_data = format_place_for_api(place_data)
    if import_config and import_config.get("enabled", True):
        upload_result = import_http_for_place(import_config, api_data)
    else:
        upload_result = upload_place_detailed(GOROUTE_API_URL, api_data, GOROUTE_API_HEADERS)

    return ImportResult(
        success=upload_result["success"],
        place=summary,
        google_place_id=summary.place_id,
        upload_status_code=upload_result.get("status_code"),
        upload_error=upload_result.get("error"),
        import_http_status=upload_result.get("status_code"),
        goroute_place_id=_extract_goroute_place_id(upload_result.get("response_body")),
        raw_place_data=place_data,
    )


def import_scraped_place_contribution(
    *,
    job_id: str,
    place_data: dict[str, Any],
    import_config: dict[str, Any],
    contribution: dict[str, Any],
) -> ImportResult:
    """Upload scraped place via GoRoute contribution import endpoint."""
    summary = PlaceSummary.from_place_data(place_data)
    body = build_contribution_import_body(
        job_id=job_id,
        place_data=place_data,
        contribution=contribution,
        place_already_exists=False,
    )
    upload_result = import_contribution(import_config, body)

    return ImportResult(
        success=upload_result["success"],
        place=summary,
        google_place_id=summary.place_id,
        upload_status_code=upload_result.get("status_code"),
        upload_error=upload_result.get("error"),
        import_http_status=upload_result.get("status_code"),
        place_already_exists=bool(upload_result.get("placeAlreadyExists", False)),
        goroute_place_id=upload_result.get("goroutePlaceId"),
        reviews_published=int(upload_result.get("reviewsPublished") or 0),
        contributors_added=int(upload_result.get("contributorsAdded") or 0),
        raw_place_data=place_data,
    )


def scrape_and_import(
    url: str,
    *,
    headless: bool = True,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
    max_scrolls: int = 100,
    include_reviews: bool = True,
    job_id: str | None = None,
    import_config: dict[str, Any] | None = None,
    contribution: dict[str, Any] | None = None,
    place_overrides: dict[str, Any] | None = None,
) -> ImportResult:
    """Scrape a Google Maps URL and upload using legacy or contribution import flow."""
    if is_google_maps_viewport_url(url):
        return ImportResult(
            success=False,
            scrape_error="Google Maps viewport URL is not a place. Resolve from a place name, address, or Place ID instead.",
        )

    place_data = scrape_place(
        url,
        headless=headless,
        max_reviews=max_reviews,
        max_scrolls=max_scrolls,
        include_reviews=include_reviews,
    )

    if place_data.get("status") == "failed":
        return ImportResult(
            success=False,
            scrape_error=place_data.get("error") or "Scrape failed",
            raw_place_data=place_data,
            blocked_by_google=bool(place_data.get("blockedByGoogle")),
        )

    title = " ".join(str(place_data.get("title") or place_data.get("name") or "").lower().split())
    if not title:
        return ImportResult(
            success=False,
            scrape_error="Scrape returned no place title; refusing to import.",
            raw_place_data=place_data,
        )
    if title in GENERIC_MAP_TITLES:
        return ImportResult(
            success=False,
            scrape_error="Google Maps resolved to a generic map result, not a specific place.",
            raw_place_data=place_data,
        )
    if title.startswith("http://") or title.startswith("https://"):
        return ImportResult(
            success=False,
            scrape_error="Scraped title looks like a URL, not a place name; refusing to import.",
            raw_place_data=place_data,
        )

    if place_overrides:
        place_data.update({key: value for key, value in place_overrides.items() if value is not None})

    if contribution and import_config and import_config.get("enabled", True):
        if not job_id:
            raise ValueError("job_id is required for contribution import flow")
        result = import_scraped_place_contribution(
            job_id=job_id,
            place_data=place_data,
            import_config=import_config,
            contribution=contribution,
        )
    else:
        result = import_scraped_place(place_data, import_config)

    result.raw_place_data = place_data
    return result


def import_http_for_place(import_config: dict[str, Any], api_data: dict[str, Any]) -> dict[str, Any]:
    from upload_to_api import import_http_detailed

    return import_http_detailed(
        import_config["url"],
        api_data,
        method=import_config.get("method", "POST"),
        headers=import_config.get("headers") or GOROUTE_API_HEADERS,
    )


def _extract_goroute_place_id(response_body: Any) -> str | None:
    if not isinstance(response_body, dict):
        return None
    data = response_body.get("data")
    if isinstance(data, dict):
        place_id = data.get("id")
        return str(place_id) if place_id else None
    return None
