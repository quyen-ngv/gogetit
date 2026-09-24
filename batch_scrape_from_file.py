#!/usr/bin/env python3
"""
Batch scraper for Google Maps places from URLs file.

Usage:
1. Create urls.txt with one Google Maps URL per line
2. Run: python batch_scrape_from_file.py
3. Script will scrape and upload each URL automatically
"""

import logging
import os
import sys
import time
from datetime import datetime

from config import GOROUTE_API_HEADERS, GOROUTE_API_URL
from run_job import scrape_place, setup_logging, DEFAULT_MAX_REVIEWS
from upload_to_api import format_place_for_api, upload_place as api_upload_place

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def load_urls(file_path: str) -> list[str]:
    """Load URLs from file, one per line."""
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return []
    
    urls = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            url = line.strip()
            # Skip empty lines and comments
            if url and not url.startswith('#'):
                urls.append(url)
    
    return urls


def process_url(url: str, idx: int, total: int) -> bool:
    """Process a single URL: scrape and upload."""
    prefix = f"[{idx}/{total}]"
    
    logger.info(f"{prefix} Processing URL: {url}")
    print(f"\n{prefix} ⏳ Đang xử lý: {url}")
    
    try:
        # Step 1: Scrape place
        print(f"{prefix} 📡 Đang lấy thông tin từ Google Maps...")
        
        place_data = scrape_place(
            url,
            headless=True,
            max_reviews=DEFAULT_MAX_REVIEWS,
            max_scrolls=100,
        )
        
        if place_data.get("status") == "failed":
            error_msg = place_data.get('error', 'Unknown error')
            logger.error(f"{prefix} Scrape failed: {error_msg}")
            print(f"{prefix} ❌ Lỗi khi scrape: {error_msg}")
            return False
        
        place_title = place_data.get("title", "Unknown")
        review_count = place_data.get("reviews_count_output", 0)
        place_id = place_data.get("placeId", "N/A")
        rating = place_data.get("reviewRating", 0)
        
        print(f"{prefix} ✅ Đã lấy thông tin:")
        print(f"     📍 {place_title}")
        print(f"     ⭐ {rating}/5")
        print(f"     💬 {review_count} reviews")
        
        # Step 2: Upload to API
        print(f"{prefix} ☁️ Đang upload lên API...")
        
        try:
            api_data = format_place_for_api(place_data)
            success = api_upload_place(GOROUTE_API_URL, api_data, GOROUTE_API_HEADERS)
            
            if success:
                logger.info(f"{prefix} Upload successful: {place_title} ({place_id})")
                print(f"{prefix} ✅ Upload thành công!")
                print(f"     📍 {place_title}")
                print(f"     🆔 {place_id}")
                print(f"     ⭐ {rating}/5 ({review_count} reviews)")
                return True
            else:
                logger.error(f"{prefix} Upload failed: {place_title}")
                print(f"{prefix} ❌ Upload thất bại: {place_title}")
                return False
                
        except Exception as e:
            logger.error(f"{prefix} Upload error: {e}", exc_info=True)
            print(f"{prefix} ❌ Lỗi upload: {str(e)[:100]}")
            return False
            
    except Exception as e:
        logger.error(f"{prefix} Processing error: {e}", exc_info=True)
        print(f"{prefix} ❌ Lỗi: {str(e)[:200]}")
        return False


def main():
    """Main function."""
    # Setup run_job logging to file only
    setup_logging("WARNING", "batch_scraper.log")
    
    # Load URLs from file
    urls_file = "urls.txt"
    logger.info(f"Loading URLs from {urls_file}")
    print(f"[INFO] Dang doc file: {urls_file}")
    
    urls = load_urls(urls_file)
    
    if not urls:
        logger.error("No URLs found in file")
        print(f"[ERROR] Khong tim thay URL nao trong file {urls_file}")
        print(f"   Tao file {urls_file} va them cac Google Maps URL (moi dong mot URL)")
        return 1
    
    total = len(urls)
    logger.info(f"Found {total} URLs to process")
    print(f"[OK] Tim thay {total} URLs")
    print(f"[INFO] Bat dau: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Process each URL
    success_count = 0
    failed_count = 0
    
    for idx, url in enumerate(urls, start=1):
        success = process_url(url, idx, total)
        
        if success:
            success_count += 1
        else:
            failed_count += 1
        
        # Delay between URLs (except for last one)
        if idx < total:
            delay = 3
            print(f"\n[WAIT] Cho {delay}s truoc khi xu ly URL tiep theo...")
            time.sleep(delay)
    
    # Summary
    print("\n" + "=" * 60)
    print("KET QUA:")
    print(f"   [OK] Thanh cong: {success_count}/{total}")
    print(f"   [FAIL] That bai: {failed_count}/{total}")
    print(f"[INFO] Ket thuc: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    logger.info(f"Batch complete: {success_count} success, {failed_count} failed")
    
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
