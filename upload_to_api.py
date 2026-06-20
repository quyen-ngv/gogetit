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

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests")
    sys.exit(1)


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
    
    # Convert other objects to JSON strings
    open_hours = place.get("openHours", {})
    open_hours_str = json.dumps(open_hours, ensure_ascii=False) if isinstance(open_hours, dict) else "{}"
    
    popular_times = place.get("popularTimes", {})
    popular_times_str = json.dumps(popular_times, ensure_ascii=False) if isinstance(popular_times, dict) else "{}"
    
    raw_data = place.get("rawData", {})
    raw_data_str = json.dumps(raw_data, ensure_ascii=False) if isinstance(raw_data, dict) else "{}"
    
    # Determine placeGroup from category if not set
    place_group = place.get("placeGroup", "OTHER")
    if not place_group or place_group == "OTHER":
        category_lower = (place.get("category", "") or "").lower()
        if any(word in category_lower for word in ["restaurant", "cafe", "food", "pho", "bar", "eatery", "bistro", "diner", "noodle", "bun", "com", "banh"]):
            place_group = "FOOD_AND_DRINK"
        elif any(word in category_lower for word in ["hotel", "resort", "hostel", "accommodation", "lodging", "motel", "guesthouse"]):
            place_group = "ACCOMMODATION"
        elif any(word in category_lower for word in ["museum", "temple", "pagoda", "church", "heritage", "historical", "monument", "shrine", "cultural"]):
            place_group = "CULTURE_AND_HERITAGE"
        elif any(word in category_lower for word in ["park", "beach", "mountain", "nature", "garden", "forest", "lake", "waterfall", "outdoor"]):
            place_group = "NATURE_AND_OUTDOORS"
        elif any(word in category_lower for word in ["shop", "store", "market", "mall", "shopping", "boutique"]):
            place_group = "SHOPPING_AND_MARKET"
        elif any(word in category_lower for word in ["attraction", "tourist", "landmark", "viewpoint", "entertainment", "amusement"]):
            place_group = "ATTRACTIONS"
    
    # Build API request body
    api_body = {
        "placeId": place.get("placeId") or place.get("place_id", ""),
        "cid": str(place.get("cid", "")) if place.get("cid") else "",
        "dataId": place.get("dataId", ""),
        "title": place.get("title") or place.get("name", ""),
        "category": place.get("category", ""),
        "placeGroup": place_group,
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
        "priceRange": place.get("priceRange") or place.get("price_range", ""),
        "openHours": open_hours_str,
        "popularTimes": popular_times_str,
        "rawData": raw_data_str,
    }
    
    # Remove None values
    return {k: v for k, v in api_body.items() if v is not None}


def upload_place(api_url: str, place_data: dict[str, Any], headers: dict[str, str]) -> bool:
    """Upload a single place to the API."""
    place_title = place_data.get("title", "Unknown")
    review_count = place_data.get("reviewCount", 0)
    
    # Log full request body
    print(f"\n{'='*80}")
    print(f"REQUEST BODY for: {place_title} ({review_count} reviews)")
    print(f"{'='*80}")
    print(json.dumps(place_data, ensure_ascii=False, indent=2))
    print(f"{'='*80}\n")
    
    print(f"⏳ Uploading: {place_title}...", end=" ", flush=True)
    
    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=place_data,
            timeout=60,
            verify=False
        )
        
        if response.status_code in (200, 201):
            print(f"✓")
            return True
        else:
            print(f"✗ HTTP {response.status_code}")
            try:
                error_msg = response.json()
                print(f"   Error: {error_msg}")
            except:
                print(f"   Response: {response.text[:200]}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"✗ Timeout (>60s)")
        return False
    except Exception as e:
        print(f"✗ {str(e)[:100]}")
        return False


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
