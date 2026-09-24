"""Build and send GoRoute contribution import payloads."""

from __future__ import annotations

from typing import Any

from upload_to_api import format_place_for_api, import_http_detailed


def build_contribution_import_body(
    *,
    job_id: str,
    place_data: dict[str, Any],
    contribution: dict[str, Any],
    place_already_exists: bool = False,
) -> dict[str, Any]:
    """Build POST body for /internal/places/import/contribution."""
    place_payload = format_place_for_api(place_data)
    place_payload["visibilityStatus"] = place_payload.get("visibilityStatus") or "INACTIVE"

    return {
        "jobId": job_id,
        "contributionGroupId": contribution["contributionGroupId"],
        "gorouteJobId": contribution["gorouteJobId"],
        "placeAlreadyExists": place_already_exists,
        "skipPlaceInsertIfExists": contribution.get("skipPlaceInsertIfExists", True),
        "place": place_payload,
        "gorouteReviews": contribution.get("gorouteReviews") or [],
        "contributorUserIds": contribution.get("contributorUserIds") or [],
    }


def parse_contribution_import_response(response_body: Any) -> dict[str, Any]:
    if not isinstance(response_body, dict):
        return {}

    return {
        "goroutePlaceId": (
            response_body.get("goroutePlaceId")
            or response_body.get("placeId")
            or response_body.get("id")
        ),
        "placeAlreadyExists": bool(response_body.get("placeAlreadyExists", False)),
        "reviewsPublished": int(response_body.get("reviewsPublished") or 0),
        "contributorsAdded": int(response_body.get("contributorsAdded") or 0),
    }


def import_contribution(
    import_config: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    """Call GoRoute contribution import endpoint."""
    url = import_config["url"]
    method = import_config.get("method", "POST")
    headers = import_config.get("headers") or {}

    result = import_http_detailed(
        url,
        body,
        method=method,
        headers=headers,
        idempotent_codes=("ALREADY_PROCESSED",),
        idempotent_statuses=(409,),
    )

    if result.get("success"):
        parsed = parse_contribution_import_response(result.get("response_body"))
        result.update(parsed)

    return result
