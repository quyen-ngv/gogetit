"""Resolve Google Maps URL to place identifiers without full review scrape."""

from __future__ import annotations

from typing import Any

from run_job import close_driver, js_place_detail, navigate, setup_driver


def resolve_place_url(url: str, *, headless: bool = True) -> dict[str, Any]:
    """Open URL in browser, extract place metadata, close browser (no review scrape)."""
    driver = setup_driver(headless)
    try:
        resolved_url = navigate(driver, url)
        place = js_place_detail(driver, url, resolved_url)

        google_place_id = place.get("placeId") or place.get("place_id") or ""
        cid = place.get("cid")
        if cid is not None:
            cid = str(cid)

        return {
            "googlePlaceId": google_place_id,
            "cid": cid or "",
            "title": place.get("title") or place.get("name") or "",
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
            "normalizedUrl": resolved_url or place.get("googleMapsLink") or url,
            "dataId": place.get("dataId") or "",
        }
    finally:
        try:
            close_driver(driver)
        except Exception:
            pass
