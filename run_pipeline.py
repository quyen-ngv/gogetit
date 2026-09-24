#!/usr/bin/env python3
"""
Complete pipeline: Scrape Google Maps → Upload to API

Usage:
  python run_pipeline.py --url "https://maps.app.goo.gl/abc123"
  python run_pipeline.py --urls urls.txt
"""

import argparse
import json
import sys
import time
from pathlib import Path

from config import GOROUTE_API_HEADERS, GOROUTE_API_URL
from run_job import scrape_place, setup_logging, read_urls, DEFAULT_MAX_REVIEWS
from upload_to_api import format_place_for_api, upload_place


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape and upload places to API")
    
    # Input options
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single Google Maps URL")
    group.add_argument("--urls", help="Text file with URLs (one per line)")
    
    # Scraping options
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=DEFAULT_MAX_REVIEWS,
        help=f"Max reviews returned per place (cap {DEFAULT_MAX_REVIEWS})",
    )
    parser.add_argument("--max-scrolls", type=int, default=30, help="Max scrolls for reviews")
    
    # Other options
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between places (seconds)")
    parser.add_argument("--save-json", help="Save output to JSON file (optional)")
    
    args = parser.parse_args(argv or sys.argv[1:])
    
    # Setup logging
    setup_logging("INFO", None)
    
    # Get URLs
    if args.url:
        urls = [args.url]
    else:
        urls = read_urls(args.urls)
    
    if not urls:
        print("No URLs to process")
        return 1
    
    print(f"Processing {len(urls)} place(s)...\n")
    
    all_places = []
    success_count = 0
    failed_count = 0
    
    for idx, url in enumerate(urls, start=1):
        print(f"{'='*60}")
        print(f"[{idx}/{len(urls)}] {url}")
        print(f"{'='*60}")
        
        # Step 1: Scrape
        print("📡 Scraping Google Maps...")
        try:
            place_data = scrape_place(
                url,
                headless=args.headless,
                max_reviews=args.max_reviews,
                max_scrolls=args.max_scrolls
            )
            
            if place_data.get("status") == "failed":
                print(f"❌ Scrape failed: {place_data.get('error', 'Unknown')}")
                failed_count += 1
                continue
            
            title = place_data.get("title", "Unknown")
            reviews = place_data.get("reviews_count_output", 0)
            rating = place_data.get("reviewRating", 0)
            
            print(f"✅ Scraped: {title}")
            print(f"   ⭐ {rating}/5 | 💬 {reviews} reviews")
            
            all_places.append(place_data)
            
        except Exception as e:
            print(f"❌ Error scraping: {e}")
            failed_count += 1
            continue
        
        # Step 2: Upload
        print("☁️  Uploading to API...")
        try:
            api_data = format_place_for_api(place_data)
            upload_success = upload_place(GOROUTE_API_URL, api_data, GOROUTE_API_HEADERS)
            
            if upload_success:
                print(f"✅ Upload successful")
                success_count += 1
            else:
                print(f"❌ Upload failed")
                failed_count += 1
                
        except Exception as e:
            print(f"❌ Upload error: {e}")
            failed_count += 1
        
        print()
        
        # Delay between places
        if idx < len(urls) and args.delay > 0:
            time.sleep(args.delay)
    
    # Save JSON if requested
    if args.save_json and all_places:
        output_data = {
            "generated_at": place_data.get("generated_at", ""),
            "count": len(all_places),
            "places": all_places
        }
        Path(args.save_json).write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"💾 Saved to {args.save_json}")
    
    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total:    {len(urls)}")
    print(f"Success:  {success_count}")
    print(f"Failed:   {failed_count}")
    print(f"{'='*60}")
    
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
