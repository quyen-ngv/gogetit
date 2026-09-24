#!/usr/bin/env python3
"""Scrape one Google Maps place and write raw + Java API payload to a JSON file.

Example:
  python local_test_google_maps_place.py --url "https://www.google.com/maps/place/..." \
      --output debug/met-place.json --headed
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_job import scrape_place
from upload_to_api import format_place_for_api


AUDIT_FIELDS = (
    "placeId", "cid", "dataId", "title", "category", "placeGroup",
    "address", "latitude", "longitude", "plusCode", "timezone", "phone",
    "website", "googleMapsLink", "reviewCount", "reviewRating",
    "reviewsPerRating", "thumbnail", "images", "descriptions", "status",
    "priceRange", "openHours", "popularTimes", "reservations", "orderOnline",
    "menu", "completeAddress", "about", "owner", "emails", "rawData",
)


def _is_present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def build_audit(place: dict[str, Any]) -> dict[str, Any]:
    fields = {field: _is_present(place.get(field)) for field in AUDIT_FIELDS}
    return {
        "presentCount": sum(fields.values()),
        "fieldCount": len(fields),
        "missingFields": [field for field, present in fields.items() if not present],
        "fields": fields,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape one Google Maps place and save diagnostic JSON.",
    )
    parser.add_argument("--url", required=True, help="Full Google Maps place URL")
    parser.add_argument(
        "--output",
        default="debug/place-detail-local.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--reviews",
        type=int,
        default=0,
        help="Number of reviews to scrape; 0 tests place details only",
    )
    parser.add_argument("--max-scrolls", type=int, default=100)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show Chrome while scraping",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    place = scrape_place(
        args.url,
        headless=not args.headed,
        max_reviews=max(args.reviews, 20),
        max_scrolls=max(args.max_scrolls, 1),
        include_reviews=args.reviews > 0,
        reviews_with_images_only=False,
        require_newest_sort=args.reviews > 0,
    )
    api_payload = format_place_for_api(place) if place.get("status") != "failed" else None
    result = {
        "meta": {
            "sourceUrl": args.url,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "success": place.get("status") != "failed",
            "reviewsRequested": args.reviews,
        },
        "audit": build_audit(place),
        "scrapedPlace": place,
        "javaApiPayload": api_payload,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote: {output_path}")
    print(
        f"Fields present: {result['audit']['presentCount']}/{result['audit']['fieldCount']}"
    )
    if result["audit"]["missingFields"]:
        print("Empty/not available: " + ", ".join(result["audit"]["missingFields"]))
    return 0 if result["meta"]["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
