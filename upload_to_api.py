#!/usr/bin/env python3
"""Upload places from output.json to API endpoint."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta
import re

from config import goroute_api_headers
from place_classification import current_group, derive_place_group, derive_sub_types, google_categories

try:
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests")
    sys.exit(1)


def _json_field_string(value: Any, empty_value: str) -> str:
    """Serialize scraper JSON fields without changing their established DB shape."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.strip():
        return value
    return empty_value


def format_place_for_api(place: dict[str, Any]) -> dict[str, Any]:
    """Format a place from output.json to match the API request body format."""
    # Convert reviewsPerRating to JSON string
    reviews_per_rating = place.get("reviewsPerRating", {})
    if isinstance(reviews_per_rating, dict):
        reviews_per_rating_str = json.dumps(reviews_per_rating, ensure_ascii=False)
    else:
        reviews_per_rating_str = str(reviews_per_rating) if reviews_per_rating else "{}"
    
    # Convert images to JSON string
    images = place.get("images", [])
    if isinstance(images, list):
        images_str = json.dumps(images, ensure_ascii=False)
    else:
        images_str = str(images) if images else "[]"
    
    # Convert userReviews to JSON string
    user_reviews = place.get("userReviews") or place.get("reviews", [])
    if isinstance(user_reviews, list):
        # Validate and log missing fields in reviews
        valid_reviews = []
        for idx, review in enumerate(user_reviews):
            missing_fields = []
            if not review.get("reviewId"):
                missing_fields.append("reviewId")
            if not review.get("name"):
                missing_fields.append("name")
            if not review.get("profileUrl"):
                missing_fields.append("profileUrl")
            
            if missing_fields:
                print(f"   WARNING: Review #{idx+1} missing fields: {', '.join(missing_fields)}")
                print(f"            Data: reviewId={review.get('reviewId', 'N/A')}, name={review.get('name', 'N/A')}, profileUrl={review.get('profileUrl', 'N/A')}")
            
            valid_reviews.append(review)
        
        user_reviews_str = json.dumps(valid_reviews, ensure_ascii=False)
        # Debug: log review count
        if valid_reviews:
            print(f"   DEBUG: Found {len(valid_reviews)} reviews in data")
        else:
            print(f"   DEBUG: userReviews is empty array")
    else:
        user_reviews_str = str(user_reviews) if user_reviews else "[]"
        print(f"   DEBUG: userReviews not a list, type={type(user_reviews)}")
    
    # Destinations - keep as array (not JSON string)
    destinations = place.get("destinations", [])
    if not isinstance(destinations, list):
        destinations = []

    translations = place.get("translations")
    if not isinstance(translations, dict):
        translations = None
    
    # Convert other objects to JSON strings
    open_hours_str = _json_field_string(place.get("openHours"), "{}")
    regular_value = place.get("regular")
    if regular_value is None:
        raw_data = place.get("rawData")
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except (TypeError, ValueError):
                raw_data = None
        if isinstance(raw_data, dict):
            opening_hours = raw_data.get("openingHours")
            if isinstance(opening_hours, dict):
                regular_value = opening_hours.get("regular")
    if regular_value is None:
        regular_value = place.get("openHours")
    regular_str = _json_field_string(regular_value, "{}")
    popular_times_str = _json_field_string(place.get("popularTimes"), "{}")
    reservations_str = _json_field_string(place.get("reservations"), "{}")
    order_online_str = _json_field_string(place.get("orderOnline"), "{}")
    menu_str = _json_field_string(place.get("menu"), "{}")
    complete_address_str = _json_field_string(place.get("completeAddress"), "{}")
    about_str = _json_field_string(place.get("about"), "[]")
    owner_str = _json_field_string(place.get("owner"), "{}")
    emails_str = _json_field_string(place.get("emails"), "[]")
    raw_data_str = _json_field_string(place.get("rawData"), "{}")
    
    categories = google_categories(place)
    # A legacy group still names its kinds (NATURE_AND_OUTDOORS -> NATURE) before it folds into ATTRACTIONS.
    scraped_group = (place.get("placeGroup") or "").upper()
    sub_types = derive_sub_types(categories, scraped_group)
    place_group = current_group(scraped_group)
    if not place_group or scraped_group == "OTHER":
        place_group = derive_place_group(categories)
    
    # Build API request body
    api_body = {
        "placeId": place.get("placeId") or place.get("place_id", ""),
        "cid": str(place.get("cid", "")) if place.get("cid") else "",
        "dataId": place.get("dataId", ""),
        "title": place.get("title") or place.get("name", ""),
        "translations": translations,
        "category": place.get("category", ""),
        "placeGroup": place_group,
        "subTypes": sub_types,
        "address": place.get("address", ""),
        "destinations": destinations,  # Keep as array
        "latitude": float(place.get("latitude")) if place.get("latitude") is not None else None,
        "longitude": float(place.get("longitude")) if place.get("longitude") is not None else None,
        "plusCode": place.get("plusCode") or place.get("plus_code", ""),
        "timezone": place.get("timezone", ""),
        "phone": place.get("phone", ""),
        "website": place.get("website") or place.get("webSite", ""),
        "googleMapsLink": place.get("googleMapsLink", ""),
        "reviewCount": int(place.get("reviewCount") or place.get("review_count", 0) or 0),
        "reviewRating": float(place.get("reviewRating") or place.get("review_rating", 0) or 0),
        "reviewsPerRating": reviews_per_rating_str,
        "thumbnail": place.get("thumbnail", ""),
        "images": images_str,
        "userReviews": user_reviews_str,
        "descriptions": place.get("descriptions") or place.get("description", ""),
        "status": place.get("status", ""),
        "visibilityStatus": place.get("visibilityStatus") or place.get("visibility_status"),
        "priceRange": place.get("priceRange") or place.get("price_range", ""),
        "openHours": open_hours_str,
        "regular": regular_str,
        "popularTimes": popular_times_str,
        "reservations": reservations_str,
        "orderOnline": order_online_str,
        "menu": menu_str,
        "completeAddress": complete_address_str,
        "about": about_str,
        "owner": owner_str,
        "emails": emails_str,
        "rawData": raw_data_str,
    }
    
    # Remove None values
    return {k: v for k, v in api_body.items() if v is not None}


def import_http_detailed(
    api_url: str,
    body: dict[str, Any],
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    idempotent_codes: tuple[str, ...] = (),
    idempotent_statuses: tuple[int, ...] = (),
) -> dict[str, Any]:
    """Send JSON to an HTTP endpoint and return structured result."""
    request_headers = goroute_api_headers(headers)
    request_method = method.upper()

    print(f"\n{'='*80}")
    print(f"HTTP {request_method} {api_url}")
    print(f"{'='*80}")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(f"{'='*80}\n")

    try:
        response = requests.request(
            request_method,
            api_url,
            headers=request_headers,
            json=body,
            timeout=timeout,
            verify=False,
        )
        response_body = _safe_response_body(response)

        if response.status_code in (200, 201):
            print(f"✓ HTTP {response.status_code}")
            return {
                "success": True,
                "status_code": response.status_code,
                "response_body": response_body,
            }

        if _is_idempotent_response(
            response.status_code,
            response_body,
            idempotent_codes=idempotent_codes,
            idempotent_statuses=idempotent_statuses,
        ):
            print(f"✓ HTTP {response.status_code} (idempotent)")
            return {
                "success": True,
                "status_code": response.status_code,
                "response_body": response_body,
                "idempotent": True,
            }

        print(f"✗ HTTP {response.status_code}")
        print(f"   Error: {response_body}")
        return {
            "success": False,
            "status_code": response.status_code,
            "error": str(response_body),
            "response_body": response_body,
        }

    except requests.exceptions.Timeout:
        print(f"✗ Timeout (>{timeout}s)")
        return {"success": False, "status_code": None, "error": f"Request timeout (>{timeout}s)"}
    except Exception as exc:
        print(f"✗ {str(exc)[:100]}")
        return {"success": False, "status_code": None, "error": str(exc)}


def _is_idempotent_response(
    status_code: int,
    response_body: Any,
    *,
    idempotent_codes: tuple[str, ...],
    idempotent_statuses: tuple[int, ...],
) -> bool:
    if status_code not in idempotent_statuses:
        return False

    body_text = json.dumps(response_body, ensure_ascii=False) if isinstance(response_body, dict) else str(response_body)
    markers = idempotent_codes + ("ALREADY_PROCESSED",)

    if isinstance(response_body, dict):
        code = str(response_body.get("code") or response_body.get("error") or response_body.get("status") or "")
        if any(marker in code.upper() for marker in markers):
            return True

    return any(marker in body_text.upper() for marker in markers)


def upload_place_detailed(
    api_url: str,
    place_data: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Upload a single place to the API and return structured result."""
    place_title = place_data.get("title", "Unknown")
    review_count = place_data.get("reviewCount", 0)
    print(f"⏳ Uploading: {place_title} ({review_count} reviews)...", end=" ", flush=True)
    return import_http_detailed(api_url, place_data, headers=headers)


def _safe_response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def upload_place(api_url: str, place_data: dict[str, Any], headers: dict[str, str]) -> bool:
    """Upload a single place to the API."""
    return upload_place_detailed(api_url, place_data, headers)["success"]


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Upload places to API")
    parser.add_argument("--input", "-i", required=True, help="Path to output.json file")
    parser.add_argument("--api-url", default="https://onestudy.id.vn/goroute/v1/api/places/import", 
                        help="API endpoint URL")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between uploads (seconds)")
    
    args = parser.parse_args(argv or sys.argv[1:])
    
    # Read input file
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        return 1
    
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        return 1
    
    places = data.get("places", [])
    if not places:
        print("No places found")
        return 0
    
    print(f"Uploading {len(places)} place(s)...")
    
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    success = 0
    failed = 0
    
    for idx, place in enumerate(places, start=1):
        try:
            api_data = format_place_for_api(place)
        except Exception as e:
            print(f"✗ Error formatting place {idx}: {e}")
            failed += 1
            continue
        
        if upload_place(args.api_url, api_data, headers):
            success += 1
        else:
            failed += 1
        
        if idx < len(places) and args.delay > 0:
            time.sleep(args.delay)
    
    print(f"\nDone: {success} succeeded, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
