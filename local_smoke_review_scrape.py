#!/usr/bin/env python3
"""Local end-to-end smoke test for the Google Maps review scraper.

This script deliberately calls ``scrape_place`` directly. It never calls the
GoRoute backend, so it cannot delete existing reviews or write DB/MinIO data.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from run_job import scrape_place, scrape_place_reviews_only


DEFAULT_PLACE_URL = (
    "https://www.google.com/maps/place/Nh%C3%A0+H%C3%A0ng+H%E1%BA%A3i+S%E1%BA%A3n+"
    "Cua+Bi%E1%BB%83n+C%C3%A0+Mau/@10.743429,106.7318685,17z/data=!3m1!4b1!4m6!3m5!"
    "1s0x31752578815c5905:0xda01acb14f443382!8m2!3d10.743429!4d106.7318685!16s%2Fg%2F11fxzvd3rm?hl=en"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape image reviews locally without calling GoRoute APIs.")
    parser.add_argument("--url", default=DEFAULT_PLACE_URL)
    parser.add_argument("--reviews", type=int, default=3)
    parser.add_argument("--max-scrolls", type=int, default=60)
    parser.add_argument(
        "--google-place-id",
        default="",
        help="Use the faster reviews-only flow with this known Google Place ID",
    )
    parser.add_argument("--headed", action="store_true")
    return parser.parse_args()


def _assert_valid_result(place: dict[str, Any], requested_reviews: int) -> list[dict[str, Any]]:
    assert place.get("scrapeStatus") == "ok", place.get("error") or "Place scrape did not complete"
    if "title" in place:
        assert str(place.get("title") or "").strip(), "Scraped place has no title"

    reviews = list(place.get("reviews") or [])
    assert reviews, "No image-backed reviews were scraped"
    assert len(reviews) <= requested_reviews, "Scraper returned more reviews than requested"
    assert all(review.get("images") for review in reviews), "A returned review has no image"

    review_ids = [str(review.get("reviewId") or "").strip() for review in reviews]
    assert all(review_ids), "A returned review has no reviewId"
    assert len(review_ids) == len(set(review_ids)), "Duplicate reviewId returned"
    assert all(
        0 <= float(review.get("authenticityScore") or 0) <= 1
        for review in reviews
    ), "Authenticity score is outside 0..1"
    return reviews


def main() -> int:
    args = _parse_args()
    requested_reviews = max(1, min(args.reviews, 200))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    started_at = time.monotonic()
    if args.google_place_id.strip():
        place = scrape_place_reviews_only(
            args.url,
            google_place_id=args.google_place_id.strip(),
            headless=not args.headed,
            max_reviews=requested_reviews,
            max_scrolls=max(1, args.max_scrolls),
            require_newest_sort=True,
        )
    else:
        place = scrape_place(
            args.url,
            headless=not args.headed,
            max_reviews=requested_reviews,
            max_scrolls=max(1, args.max_scrolls),
            include_reviews=True,
            reviews_with_images_only=True,
            require_newest_sort=True,
        )
    elapsed_seconds = round(time.monotonic() - started_at, 2)
    reviews = _assert_valid_result(place, requested_reviews)

    print(json.dumps({
        "success": True,
        "title": place.get("title"),
        "googlePlaceId": place.get("placeId") or args.google_place_id,
        "mode": "reviews-only" if args.google_place_id.strip() else "full-place",
        "requestedImageReviews": requested_reviews,
        "scrapedImageReviews": len(reviews),
        "uniqueReviewIds": len({review["reviewId"] for review in reviews}),
        "elapsedSeconds": elapsed_seconds,
        "authenticityScores": [review.get("authenticityScore") for review in reviews],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
