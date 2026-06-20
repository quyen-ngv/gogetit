#!/usr/bin/env python3
"""Standalone batch job for Google Maps place details and reviews."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from seleniumbase import Driver
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError as exc:  # pragma: no cover - environment error
    raise SystemExit(
        "Missing dependency. Install with: pip install -r requirements.txt"
    ) from exc


REVIEW_WORDS = {
    "reviews", "review", "ratings", "rating",
    "đánh giá", "nhận xét", "bài đánh giá",
    "avis", "reseñas", "bewertungen", "recensioni",
}

COOKIE_BUTTON = (
    'button[aria-label*="Accept" i],'
    'button[jsname="hZCF7e"],'
    'button[data-mdc-dialog-action="accept"]'
)

REVIEW_CARD = "div[data-review-id]"
LOG = logging.getLogger("place_reviews_job")

# Vietnam destinations mapping
VIETNAM_DESTINATIONS = {
    "hanoi": "hanoi",
    "hà nội": "hanoi",
    "ha noi": "hanoi",
    "hạ long": "halong",
    "ha long": "halong",
    "quảng ninh": "halong",
    "quang ninh": "halong",
    "hội an": "hoian",
    "hoi an": "hoian",
    "đà nẵng": "danang",
    "da nang": "danang",
    "hồ chí minh": "hcm",
    "ho chi minh": "hcm",
    "sài gòn": "hcm",
    "saigon": "hcm",
    "tp hcm": "hcm",
    "tp.hcm": "hcm",
    "sa pa": "sapa",
    "sapa": "sapa",
    "lào cai": "sapa",
    "lao cai": "sapa",
    "huế": "hue",
    "hue": "hue",
    "thừa thiên huế": "hue",
    "thua thien hue": "hue",
    "ninh bình": "ninhbinh",
    "ninh binh": "ninhbinh",
    "đà lạt": "dalat",
    "da lat": "dalat",
    "lâm đồng": "dalat",
    "lam dong": "dalat",
    "phú quốc": "phuquoc",
    "phu quoc": "phuquoc",
    "kiên giang": "phuquoc",
    "kien giang": "phuquoc",
    "mũi né": "muine",
    "mui ne": "muine",
    "phan thiết": "muine",
    "phan thiet": "muine",
    "bình thuận": "muine",
    "binh thuan": "muine",
    "vũng tàu": "vungtau",
    "vung tau": "vungtau",
    "bà rịa": "vungtau",
    "ba ria": "vungtau",
    "cần thơ": "cantho",
    "can tho": "cantho",
    "quy nhơn": "quynhon",
    "quy nhon": "quynhon",
    "bình định": "quynhon",
    "binh dinh": "quynhon",
    "nha trang": "nhatrang",
    "khánh hòa": "nhatrang",
    "khanh hoa": "nhatrang",
    "hải phòng": "haiphong",
    "hai phong": "haiphong",
    "lạng sơn": "langson",
    "lang son": "langson",
    "cao bằng": "caobang",
    "cao bang": "caobang",
    "hà giang": "hagiang",
    "ha giang": "hagiang",
    "kon tum": "kontum",
    "kontum": "kontum",
    "buôn ma thuột": "buonmathuot",
    "buon ma thuot": "buonmathuot",
    "đắk lắk": "buonmathuot",
    "dak lak": "buonmathuot",
    "pleiku": "pleiku",
    "gia lai": "pleiku",
    "long xuyên": "longxuyen",
    "long xuyen": "longxuyen",
    "an giang": "longxuyen",
    "rạch giá": "rachgia",
    "rach gia": "rachgia",
    "cà mau": "camau",
    "ca mau": "camau",
    "bến tre": "bentre",
    "ben tre": "bentre",
    "vĩnh long": "vinhlong",
    "vinh long": "vinhlong",
    "mỹ tho": "mytho",
    "my tho": "mytho",
    "tiền giang": "mytho",
    "tien giang": "mytho",
    "sa đéc": "sadec",
    "sa dec": "sadec",
    "đồng tháp": "sadec",
    "dong thap": "sadec",
    "châu đốc": "chaudoc",
    "chau doc": "chaudoc",
    "hà tĩnh": "hatinh",
    "ha tinh": "hatinh",
    "vinh": "vinh",
    "nghệ an": "vinh",
    "nghe an": "vinh",
    "thanh hóa": "thanhhoa",
    "thanh hoa": "thanhhoa",
    "sầm sơn": "samson",
    "sam son": "samson",
    "bãi cháy": "baichay",
    "bai chay": "baichay",
    "tuần châu": "tuanchau",
    "tuan chau": "tuanchau",
    "cát bà": "catba",
    "cat ba": "catba",
    "hải phòng": "catba",
    "côn đảo": "condao",
    "con dao": "condao",
    "bà rịa vũng tàu": "condao",
    "lý sơn": "lyson",
    "ly son": "lyson",
    "cù lao chàm": "culaocham",
    "cu lao cham": "culaocham",
}


@dataclass
class Review:
    review_id: str = ""
    author: str = ""
    rating: float | None = None
    text: str = ""
    raw_date: str = ""
    review_date: str = ""
    likes: int = 0
    photos: list[str] = field(default_factory=list)
    profile_url: str = ""
    profile_picture: str = ""
    owner_text: str = ""
    sub_ratings: dict[str, Any] = field(default_factory=dict)
    reviewer_is_local_guide: bool = False
    reviewer_total_reviews: int = 0
    reviewer_total_photos: int = 0


def upscale_image_url(url: str, scale: int = 10) -> str:
    """
    Upscale Google Maps image URLs by multiplying size parameters.
    
    Converts URLs like:
    - https://...=w140-h140-p-k-no -> https://...=w1400-h1400-p-k-no
    - https://...=w32-h32-p-rp-mo -> https://...=w320-h320-p-rp-mo
    
    Args:
        url: Image URL from Google Maps
        scale: Multiplier for width/height (default: 10)
    
    Returns:
        URL with upscaled dimensions
    """
    if not url or not isinstance(url, str):
        return url
    
    # Pattern: =wNNN-hMMM or =wNNN or =sNNN
    # Replace width parameter (w)
    url = re.sub(
        r'=w(\d+)',
        lambda m: f'=w{int(m.group(1)) * scale}',
        url
    )
    
    # Replace height parameter (h)
    url = re.sub(
        r'-h(\d+)',
        lambda m: f'-h{int(m.group(1)) * scale}',
        url
    )
    
    # Replace size parameter (s) - used for square images
    url = re.sub(
        r'=s(\d+)',
        lambda m: f'=s{int(m.group(1)) * scale}',
        url
    )
    
    return url


def extract_destinations(address: str) -> list[str]:
    """
    Extract destination codes from address string.
    Returns list of destination short codes (e.g., ['hanoi', 'halong'])
    """
    if not address:
        return []
    
    address_lower = address.lower()
    found_destinations = set()
    
    # Check each destination pattern
    for pattern, code in VIETNAM_DESTINATIONS.items():
        if pattern in address_lower:
            found_destinations.add(code)
    
    return sorted(list(found_destinations))


def read_urls(path: str) -> list[str]:
    LOG.info("Reading URL list from %s", path)
    urls: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            urls.append(value)
    LOG.info("Loaded %d URL(s)", len(urls))
    return urls


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_float(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:[\.,]\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def first_int(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"([\d][\d\s,\.]*)", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def extract_coords(url: str) -> tuple[float | None, float | None]:
    match = re.search(r"@(-?[\d.]+),(-?[\d.]+)", url)
    if not match:
        match = re.search(r"!3d(-?[\d.]+)!4d(-?[\d.]+)", url)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def extract_place_id(original_url: str, resolved_url: str) -> str:
    """Extract Google Place ID (ChIJ format) from URL or use hex as fallback."""
    # Try to extract ChIJ format Place ID from URL
    for url in (resolved_url, original_url):
        # Look for !1s with ChIJ format (e.g., !1sChIJ...)
        match = re.search(r'!1s(ChI[JA][A-Za-z0-9_-]{19,})', url)
        if match:
            return match.group(1)
        
        # Look for place/ path with PlaceID
        match = re.search(r'/place/[^/]+/([A-Za-z0-9_-]{20,})', url)
        if match:
            place_id = match.group(1)
            if place_id.startswith('ChIJ') or place_id.startswith('ChAJ'):
                return place_id
        
        # Look for ftid parameter
        match = re.search(r'[?&]ftid=([A-Za-z0-9_-]+)', url)
        if match:
            place_id = match.group(1)
            if place_id.startswith('ChIJ') or place_id.startswith('ChAJ'):
                return place_id
        
        # Look for !16s%2Fg%2F pattern (URL encoded place reference)
        # This appears in resolved URLs like: !16s%2Fg%2F11bwy61jj_
        # We'll mark this for later conversion from page data
        match = re.search(r'!16s%2Fg%2F([A-Za-z0-9_-]+)', url)
        if match:
            # This is a place reference, not the full ChIJ format
            # Return marker so we know to extract from page
            return f"g:{match.group(1)}"
    
    # Fallback to hex data-id format (for use with other APIs)
    for url in (resolved_url, original_url):
        # Look for !1s0x...:0x... format
        match = re.search(r'!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)', url)
        if match:
            return match.group(1)
        
        # Look for CID query parameter
        cid = parse_qs(urlparse(url).query).get("cid", [""])[0].strip()
        if cid:
            return f"cid:{cid}"
    
    # Last resort: short URL format
    parsed = urlparse(original_url)
    if "maps.app.goo.gl" in parsed.netloc or "goo.gl" in parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return f"short:{parts[-1]}"
    
    return f"url:{abs(hash(resolved_url or original_url))}"


def setup_driver(headless: bool):
    LOG.info("Starting browser: headless=%s", headless)
    
    # Check if running in WSL and set Chrome path
    import os
    chrome_binary = None
    if os.path.exists('/mnt/c/Program Files/Google/Chrome/Application/chrome.exe'):
        chrome_binary = '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe'
    elif os.path.exists('/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe'):
        chrome_binary = '/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe'
    
    driver_kwargs = {
        'uc': True,
        'headless': headless,
        'page_load_strategy': 'normal',
        'incognito': True,
    }
    
    if chrome_binary:
        LOG.info(f"Using Chrome from Windows: {chrome_binary}")
        driver_kwargs['binary_location'] = chrome_binary
    
    driver = Driver(**driver_kwargs)
    driver.set_page_load_timeout(45)
    driver.set_window_size(1400, 900)
    
    # Add stealth settings to avoid detection
    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            '''
        })
        LOG.info("Stealth settings applied")
    except Exception as exc:
        LOG.debug("Could not apply stealth settings: %s", exc)
    
    LOG.info("Browser started")
    return driver


def dismiss_cookies(driver) -> None:
    LOG.debug("Checking cookie dialog")
    try:
        wait = WebDriverWait(driver, 3)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, COOKIE_BUTTON)))
        for button in driver.find_elements(By.CSS_SELECTOR, COOKIE_BUTTON):
            try:
                if button.is_displayed():
                    LOG.info("Dismissing cookie dialog")
                    button.click()
                    time.sleep(1)
                    return
            except Exception:
                continue
    except TimeoutException:
        LOG.debug("Cookie dialog not found")
        return
    except Exception as exc:
        LOG.debug("Cookie dialog handling failed: %s", exc)
        return


def navigate(driver, url: str) -> str:
    LOG.info("Warming up Google session")
    driver.get("https://www.google.com")
    time.sleep(1.5)
    dismiss_cookies(driver)
    LOG.info("Navigating to place URL: %s", url)
    driver.get(url)
    time.sleep(5)
    dismiss_cookies(driver)
    resolved_url = driver.current_url
    LOG.info("Resolved URL: %s", resolved_url)
    
    # Check for limited view warning (non-logged in users)
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
        limited_view_signals = [
            "limited view", "vue limitée", "eingeschränkte ansicht",
            "vista limitada", "תצוגה מוגבלת", "ограниченный просмотр",
            "限定ビュー", "受限视图"
        ]
        if any(signal in body_text for signal in limited_view_signals):
            LOG.warning("Google Maps showing limited view - reviews may be unavailable")
    except Exception:
        pass
    
    return resolved_url


JS_EXTRACT_RATING_SUMMARY = """
const text = (el) => (el && el.textContent || '').trim();
const attr = (el, name) => (el && el.getAttribute(name) || '').trim();

const getRatingText = () => {
  const f7 = document.querySelector('div.F7nice');
  if (f7) {
    for (const span of f7.querySelectorAll('span[aria-hidden="true"]')) {
      const value = text(span);
      if (/^\\d+[.,]\\d+$/.test(value) || /^\\d+$/.test(value)) return value;
    }
  }
  for (const selector of ['span.MW4etd', 'div.fontDisplayLarge']) {
    const value = text(document.querySelector(selector));
    if (value) return value;
  }
  return '';
};

const getTotalReviewText = () => {
  const isTotalReviewLabel = (label) => {
    const trimmed = String(label || '').trim();
    if (!trimmed) return false;
    if (/^[1-5]\\s*(?:stars?|sao)\\s*,/i.test(trimmed)) return false;
    return /^(\\d[\\d\\s.,]*)\\s+(?:reviews?|bài\\s*(?:viết|đánh giá))\\b/i.test(trimmed);
  };

  const ratingBlock = document.querySelector('div.F7nice');
  if (ratingBlock && ratingBlock.parentElement) {
    for (const el of ratingBlock.parentElement.querySelectorAll('button, span, a')) {
      const label = attr(el, 'aria-label') || text(el);
      if (isTotalReviewLabel(label)) return label;
    }
  }

  for (const el of document.querySelectorAll('button[jsaction*="reviewChart"], span.UY7F9, button.HHrUdb')) {
    const label = attr(el, 'aria-label') || text(el);
    if (isTotalReviewLabel(label)) return label;
  }
  return '';
};

const parseCount = (raw) => {
  const digits = String(raw || '').replace(/[^\\d]/g, '');
  return digits ? parseInt(digits, 10) : 0;
};

const parseReviewBreakdownLabel = (label) => {
  if (!label) return null;
  const match = label.match(/([1-5])\\s*(?:stars?|sao)\\s*,\\s*([\\d][\\d\\s.,]*)/i);
  if (!match) return null;
  const count = parseCount(match[2]);
  if (count <= 0) return null;
  return { stars: match[1], count };
};

const extractReviewsPerRating = () => {
  const reviewsPerRating = {};
  const selectors = [
    '[jsaction*="pane.reviewChart.moreReviews"]',
    'table.yaTUxe tr',
    'table.yaTUxe [aria-label]',
  ];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      const parsed = parseReviewBreakdownLabel(attr(el, 'aria-label'));
      if (parsed) reviewsPerRating[parsed.stars] = parsed.count;
    }
  }
  if (Object.keys(reviewsPerRating).length < 5) {
    for (const el of document.querySelectorAll('[aria-label*="sao"], [aria-label*="star" i]')) {
      const parsed = parseReviewBreakdownLabel(attr(el, 'aria-label'));
      if (parsed) reviewsPerRating[parsed.stars] = parsed.count;
    }
  }
  return reviewsPerRating;
};

return {
  ratingText: getRatingText(),
  reviewsText: getTotalReviewText(),
  reviewsPerRating: extractReviewsPerRating(),
};
"""


def js_extract_rating_summary(driver) -> dict[str, Any]:
    """Extract overall rating, total review count, and per-star breakdown from the page."""
    return driver.execute_script(JS_EXTRACT_RATING_SUMMARY) or {}


def apply_rating_summary(
    place: dict[str, Any],
    summary: dict[str, Any],
    *,
    per_rating_only: bool = False,
) -> None:
    """Merge rating summary fields into place dict when values are available."""
    if not per_rating_only:
        rating = first_float(summary.get("ratingText", ""))
        review_count = first_int(summary.get("reviewsText", ""))
        if rating is not None:
            place["reviewRating"] = rating
            place["review_rating"] = rating
        if review_count is not None:
            place["reviewCount"] = review_count
            place["review_count"] = review_count

    per_rating = summary.get("reviewsPerRating") or {}
    if isinstance(per_rating, dict) and per_rating:
        normalized = {str(k): int(v) for k, v in per_rating.items() if v}
        if len(normalized) >= len(place.get("reviewsPerRating") or {}):
            place["reviewsPerRating"] = normalized
            place["reviews_per_rating"] = normalized
            if not per_rating_only and not place.get("reviewCount"):
                total = sum(normalized.values())
                place["reviewCount"] = total
                place["review_count"] = total


def js_place_detail(driver, input_url: str, resolved_url: str) -> dict[str, Any]:
    LOG.info("Extracting place detail")
    lat, lng = extract_coords(resolved_url)
    
    # First try to extract Place ID from URL
    place_id = extract_place_id(input_url, resolved_url)
    
    raw = driver.execute_script(
        """
        const text = (el) => (el && el.textContent || '').trim();
        const attr = (el, name) => (el && el.getAttribute(name) || '').trim();
        const pickText = (selectors) => {
          for (const selector of selectors) {
            const value = text(document.querySelector(selector));
            if (value) return value;
          }
          return '';
        };
        const pickAttr = (selectors, name) => {
          for (const selector of selectors) {
            const value = attr(document.querySelector(selector), name);
            if (value) return value;
          }
          return '';
        };
        const byAria = (needles) => {
          for (const el of document.querySelectorAll('button[aria-label], a[aria-label]')) {
            const label = attr(el, 'aria-label');
            const low = label.toLowerCase();
            if (needles.some((needle) => low.includes(needle))) {
              return {label, href: attr(el, 'href'), text: text(el)};
            }
          }
          return {label: '', href: '', text: ''};
        };
        
        // Try to extract Google Place ID from page meta/data
        const getPlaceIdFromPage = () => {
          // Check meta tags
          const metaPlace = document.querySelector('meta[itemprop="placeId"]');
          if (metaPlace) {
            const val = attr(metaPlace, 'content');
            if (val && (val.startsWith('ChIJ') || val.startsWith('ChAJ'))) return val;
          }
          
          // Check data attributes on buttons and divs
          const selectors = ['[data-place-id]', '[data-pid]', 'button[jslog]', 'div[jslog]'];
          for (const sel of selectors) {
            const elements = document.querySelectorAll(sel);
            for (const el of elements) {
              const val = attr(el, 'data-place-id') || attr(el, 'data-pid');
              if (val && (val.startsWith('ChIJ') || val.startsWith('ChAJ'))) return val;
              
              // Check jslog attribute which often contains place IDs
              const jslog = attr(el, 'jslog');
              if (jslog) {
                const match = jslog.match(/ChI[JA][A-Za-z0-9_-]{19,}/);
                if (match) return match[0];
              }
            }
          }
          
          // Check in APP_INITIALIZATION_STATE or APP_OPTIONS in scripts
          const scripts = document.querySelectorAll('script');
          const allMatches = [];
          
          for (const script of scripts) {
            const content = script.textContent || '';
            
            // Look for ChIJ... format place IDs (19+ chars after ChIJ/ChAJ)
            // Use global regex to find all matches
            const regex = /ChI[JA][A-Za-z0-9_-]{19,}/g;
            let match;
            while ((match = regex.exec(content)) !== null) {
              const placeId = match[0];
              // Validate length (typical ChIJ IDs are 27-28 chars)
              if (placeId.length >= 23 && placeId.length <= 35) {
                allMatches.push(placeId);
              }
            }
          }
          
          // Return the first valid match (most likely to be the place ID)
          if (allMatches.length > 0) {
            // Remove duplicates
            const unique = [...new Set(allMatches)];
            return unique[0];
          }
          
          // Check window global objects
          try {
            const windowStr = JSON.stringify({
              APP_INITIALIZATION_STATE: window.APP_INITIALIZATION_STATE || {},
              APP_OPTIONS: window.APP_OPTIONS || {}
            });
            const match = windowStr.match(/ChI[JA][A-Za-z0-9_-]{19,}/);
            if (match) return match[0];
          } catch (e) {}
          
          return '';
        };
        
        // Extract data-id from DOM (actual data-item-id attribute)
        const getDataId = () => {
          // Try multiple possible selectors
          const selectors = [
            'button[data-item-id]',
            'div[data-id]',
            '[data-place-id]',
            'button[jslog]'
          ];
          
          for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
              const dataId = attr(el, 'data-item-id') || 
                           attr(el, 'data-id') || 
                           attr(el, 'data-place-id');
              if (dataId && dataId !== 'address') {
                return dataId;
              }
            }
          }
          return '';
        };
        
        const categories = Array.from(document.querySelectorAll(
          'button[jsaction*="pane.rating.category"], button.DkEaL, button[jsaction*="category"]'
        )).map((el) => text(el)).filter(Boolean);
        
        // Extract place images (exclude profile pictures and duplicates)
        const seenImages = new Set();
        const images = Array.from(document.querySelectorAll('img[src*="googleusercontent"]'))
          .map((el) => {
            const src = attr(el, 'src');
            const alt = attr(el, 'alt');
            
            // Skip if no src
            if (!src) return null;
            
            // Skip profile pictures (contain 'a-' in path or very small size)
            if (src.includes('/a-/') || src.includes('/a/')) return null;
            
            // Normalize URL by removing size parameters for deduplication
            // Example: =w400-h400 or =s64 or =w1400-h1400 → base URL same
            const baseUrl = src.split('=')[0]; // Get URL before = parameter
            
            // Skip if already seen (deduplicate by base URL)
            if (seenImages.has(baseUrl)) return null;
            seenImages.add(baseUrl);
            
            // Skip very small images (likely icons/avatars)
            // Check for size parameters like =w32-h32 or =s64
            if (src.match(/[=\-][wsh](32|48|64|96)/i)) return null;
            
            return {title: alt, image: src};
          })
          .filter(item => item !== null)
          .slice(0, 30);  // Limit to 30 images
        
        const address = byAria(['address', 'địa chỉ', 'dia chi']);
        const phone = byAria(['phone', 'call', 'điện thoại', 'dien thoai', 'telephone']);
        const website = byAria(['website', 'trang web', 'site web']);
        const plusCode = byAria(['plus code']);
        const menu = byAria(['menu', 'thực đơn', 'thuc don']);
        
        // Extract reservations info
        const reservations = byAria(['reservations', 'reserve', 'đặt bàn', 'book']);
        
        // Extract order online info
        const orderOnline = byAria(['order online', 'đặt món', 'delivery']);
        
        return {
          placeIdFromPage: getPlaceIdFromPage(),
          title: pickText(['h1.DUwDvf', 'h1']),
          category: pickText(['button.DkEaL', 'button[jsaction*="category"]']),
          categories,
          address: address.label || address.text,
          phone: phone.label || phone.text,
          website: website.href || website.label || website.text,
          plusCode: plusCode.label || plusCode.text,
          status: pickText(['span.ZDu9vd']),
          description: pickText(['div.PYvSYb', 'div.WeS02d div.PYvSYb']),
          priceRange: pickText(['span.mgr77e']),
          thumbnail: pickAttr(['button[jsaction*="heroHeaderImage"] img'], 'src'),
          menu: {link: menu.href, source: menu.text || menu.label},
          images,
          dataId: getDataId(),
          reservations: {available: !!reservations.href, link: reservations.href, text: reservations.text},
          orderOnline: {available: !!orderOnline.href, link: orderOnline.href, text: orderOnline.text},
        };
        """
    ) or {}
    
    rating_summary = js_extract_rating_summary(driver)
    
    # Use Place ID from page if found and it's in ChIJ format
    page_place_id = raw.get("placeIdFromPage", "")
    if page_place_id and (page_place_id.startswith("ChIJ") or page_place_id.startswith("ChAJ")):
        # Always prefer ChIJ format from page over other formats
        LOG.info("Using ChIJ Place ID from page: %s (URL extraction: %s)", page_place_id, place_id)
        place_id = page_place_id
    elif not (place_id.startswith("ChIJ") or place_id.startswith("ChAJ")):
        # If we don't have ChIJ format yet, keep what we extracted from URL
        LOG.info("No ChIJ Place ID found, using: %s", place_id)

    title = raw.get("title") or (driver.title or "").replace(" - Google Maps", "").strip()
    categories = raw.get("categories") or []
    category = raw.get("category") or (categories[0] if categories else "")
    rating = first_float(rating_summary.get("ratingText", ""))
    review_count = first_int(rating_summary.get("reviewsText", ""))
    reviews_per_rating = rating_summary.get("reviewsPerRating") or {}
    if isinstance(reviews_per_rating, dict):
        reviews_per_rating = {str(k): int(v) for k, v in reviews_per_rating.items() if v}
    else:
        reviews_per_rating = {}
    
    # Extract CID from place_id or URL
    cid = ""
    if ":" in place_id and "0x" in place_id:
        # Format: 0x3135abeaa030f36b:0xbe242aab5ec1e373
        parts = place_id.split(":")
        if len(parts) == 2 and parts[1].startswith("0x"):
            # Convert hex to decimal for CID
            try:
                cid = str(int(parts[1], 16))
            except ValueError:
                cid = parts[1]  # Keep as-is if conversion fails
    
    # Try to extract from resolved URL if not found yet
    if not cid:
        cid_match = re.search(r'[?&]cid=(\d+)', resolved_url)
        if cid_match:
            cid = cid_match.group(1)
    
    # Determine place group from category
    place_group = "OTHER"
    category_lower = category.lower()
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
    
    # Extract timezone from coordinates (simple approximation for Vietnam)
    timezone = "Asia/Saigon" if lat and lng and 8 < lat < 24 and 102 < lng < 110 else "UTC"
    
    # Build Google Maps link with CID
    google_maps_link = resolved_url
    if cid:
        google_maps_link = f"https://maps.google.com/?cid={cid}"
    
    LOG.info(
        "Place detail extracted: title=%r rating=%s review_count=%s place_id=%s",
        title,
        rating,
        review_count,
        place_id,
    )
    
    # Upscale thumbnail and images; prepend thumbnail as first gallery item
    thumbnail = upscale_image_url(raw.get("thumbnail", ""), scale=10)
    images = raw.get("images", []) or []
    for img in images:
        if isinstance(img, dict) and "image" in img:
            img["image"] = upscale_image_url(img["image"], scale=10)

    if thumbnail:
        thumb_base = thumbnail.split("=", 1)[0]
        images = [
            img
            for img in images
            if not (isinstance(img, dict) and img.get("image", "").split("=", 1)[0] == thumb_base)
        ]
        images.insert(0, {"title": title, "image": thumbnail})
        images = images[:30]

    return {
        "input_url": input_url,
        "resolved_url": resolved_url,
        "place_id": place_id,
        "placeId": place_id,  # Alias
        "cid": cid,
        "dataId": raw.get("dataId", ""),
        "link": resolved_url,
        "googleMapsLink": google_maps_link,
        "title": title,
        "name": title,
        "category": category,
        "placeGroup": place_group,
        "categories": categories,
        "address": raw.get("address", ""),
        "destinations": extract_destinations(raw.get("address", "")),
        "phone": raw.get("phone", ""),
        "website": raw.get("website", ""),
        "webSite": raw.get("website", ""),
        "plusCode": raw.get("plusCode", ""),
        "plus_code": raw.get("plusCode", ""),
        "timezone": timezone,
        "reviewRating": rating,
        "review_rating": rating,
        "reviewCount": review_count,
        "review_count": review_count,
        "reviewsPerRating": reviews_per_rating,
        "reviews_per_rating": reviews_per_rating,
        "latitude": lat,
        "longitude": lng,
        "longtitude": lng,
        "status": raw.get("status", ""),
        "description": raw.get("description", ""),
        "descriptions": raw.get("description", ""),  # Alias
        "thumbnail": thumbnail,
        "priceRange": raw.get("priceRange", ""),
        "price_range": raw.get("priceRange", ""),
        "images": images,
        "menu": raw.get("menu", {}),
        "reservations": raw.get("reservations", {}),
        "orderOnline": raw.get("orderOnline", {}),
        "openHours": {},
        "open_hours": {},
        "popularTimes": {},
        "popular_times": {},
        "owner": {},
        "completeAddress": {},
        "complete_address": {},
        "about": [],
        "reviewsLink": "",
        "reviews_link": "",
        "emails": [],
        "rawData": {},  # Will be populated if needed
    }


def click_sort_newest(driver) -> None:
    """Click the sort button and select 'Newest' to get reviews in chronological order."""
    LOG.info("Attempting to sort reviews by newest")
    
    try:
        # Wait a bit for reviews to load
        time.sleep(2)
        
        # Find sort button - multiple possible selectors
        sort_selectors = [
            'button[aria-label*="Sort" i]',
            'button[data-value*="Sort" i]',
            'button:has-text("Sort")',
            'button[jsaction*="sort" i]'
        ]
        
        sort_button = None
        for selector in sort_selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if btn.is_displayed():
                        sort_button = btn
                        break
                if sort_button:
                    break
            except Exception:
                continue
        
        if not sort_button:
            LOG.warning("Sort button not found, reviews will be in default order")
            return
        
        LOG.info("Clicking sort button")
        driver.execute_script("arguments[0].click();", sort_button)
        time.sleep(1.5)
        
        # Find and click "Newest" option
        # Look for menu items with text containing "newest", "mới nhất", "最新", etc.
        newest_keywords = ["newest", "most recent", "mới nhất", "最新", "最近", "récent", "neueste"]
        
        menu_items = driver.find_elements(By.CSS_SELECTOR, '[role="menuitemradio"], [role="menuitem"], div[data-index]')
        for item in menu_items:
            try:
                item_text = (item.text or "").lower()
                if any(keyword in item_text for keyword in newest_keywords):
                    LOG.info("Clicking 'Newest' sort option: %r", item.text)
                    driver.execute_script("arguments[0].click();", item)
                    time.sleep(2)
                    LOG.info("Successfully sorted reviews by newest")
                    return
            except Exception:
                continue
        
        LOG.warning("'Newest' sort option not found in menu")
        
    except Exception as exc:
        LOG.warning("Could not sort reviews by newest: %s", exc)


def click_reviews_tab(driver) -> None:
    LOG.info("Opening reviews tab/panel")
    
    # Try multiple strategies to find and click reviews tab
    max_timeout = 15
    end_time = time.time() + max_timeout
    
    # Strategy 1: Find tabs with role="tab"
    tabs = driver.find_elements(By.CSS_SELECTOR, '[role="tab"], button[role="tab"]')
    LOG.debug("Found %d possible tab elements", len(tabs))
    
    for tab in tabs:
        if time.time() > end_time:
            break
        try:
            label = ((tab.text or "") + " " + (tab.get_attribute("aria-label") or "")).lower()
            if any(word in label for word in REVIEW_WORDS):
                LOG.info("Clicking reviews tab: %r", label[:120])
                # Try multiple click methods
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tab)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", tab)
                    time.sleep(3)
                    if verify_reviews_tab_clicked(driver):
                        LOG.info("Successfully verified reviews tab is open")
                        # Sort by newest after opening reviews tab
                        click_sort_newest(driver)
                        return
                except Exception:
                    try:
                        tab.click()
                        time.sleep(3)
                        if verify_reviews_tab_clicked(driver):
                            LOG.info("Successfully verified reviews tab is open")
                            # Sort by newest after opening reviews tab
                            click_sort_newest(driver)
                            return
                    except Exception:
                        continue
        except Exception:
            continue
    
    # Strategy 2: Look for buttons/links with review keywords
    buttons = driver.find_elements(By.CSS_SELECTOR, 'button, a')
    for button in buttons:
        if time.time() > end_time:
            break
        try:
            text = ((button.text or "") + " " + (button.get_attribute("aria-label") or "")).lower()
            if any(word in text for word in REVIEW_WORDS):
                LOG.info("Clicking reviews button/link: %r", text[:120])
                try:
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(3)
                    if verify_reviews_tab_clicked(driver):
                        LOG.info("Successfully verified reviews tab is open")
                        # Sort by newest after opening reviews tab
                        click_sort_newest(driver)
                        return
                except Exception:
                    try:
                        button.click()
                        time.sleep(3)
                        if verify_reviews_tab_clicked(driver):
                            LOG.info("Successfully verified reviews tab is open")
                            # Sort by newest after opening reviews tab
                            click_sort_newest(driver)
                            return
                    except Exception:
                        continue
        except Exception:
            continue
    
    # Strategy 3: Try data-tab-index="1" (reviews often at index 1)
    try:
        tab_by_index = driver.find_elements(By.CSS_SELECTOR, '[data-tab-index="1"]')
        for tab in tab_by_index:
            try:
                LOG.info("Trying tab at index 1")
                driver.execute_script("arguments[0].click();", tab)
                time.sleep(3)
                if verify_reviews_tab_clicked(driver):
                    LOG.info("Successfully verified reviews tab is open")
                    # Sort by newest after opening reviews tab
                    click_sort_newest(driver)
                    return
            except Exception:
                continue
    except Exception:
        pass
    
    # Fallback: clicking rating area often opens reviews
    try:
        LOG.info("Reviews tab not found; clicking rating area fallback")
        driver.execute_script("document.querySelector('.F7nice, .fontDisplayLarge')?.click()")
        time.sleep(3)
        # Try to sort even with fallback method
        click_sort_newest(driver)
    except Exception as exc:
        LOG.warning("Could not open reviews panel: %s", exc)


def verify_reviews_tab_clicked(driver) -> bool:
    """Verify that the reviews tab was successfully clicked."""
    try:
        # Check for review cards
        cards = driver.find_elements(By.CSS_SELECTOR, REVIEW_CARD)
        if cards and len(cards) > 0:
            return True
        
        # Check if URL contains "review"
        if "review" in driver.current_url.lower():
            return True
        
        # Check for sort button (appears with reviews)
        sort_buttons = driver.find_elements(By.CSS_SELECTOR, 'button[aria-label*="Sort" i]')
        if sort_buttons:
            return True
        
        # Check for reviews pane
        panes = driver.find_elements(By.CSS_SELECTOR, 'div.m6QErb.DxyBCb.kA9KIf.dS8AEf')
        if panes:
            return True
            
        return False
    except Exception as exc:
        LOG.debug("Error verifying reviews tab: %s", exc)
        return False


def expand_review(card) -> None:
    """Click 'More' button to expand full review text."""
    selectors = [
        'button[jsaction*="expandReview"]',
        'button[aria-expanded="false"][jsaction*="review" i]',
        'button[aria-label*="More" i]',
        'button[aria-label*="Xem thêm" i]',  # Vietnamese
        'button.kyuRq',
        'button.w8nwRe',  # Alternative class
    ]
    for selector in selectors:
        try:
            buttons = card.find_elements(By.CSS_SELECTOR, selector)
            for button in buttons:
                try:
                    if button.is_displayed():
                        button.click()
                        time.sleep(0.1)  # Small delay after expand
                        return  # Exit after first successful click
                except Exception:
                    pass
        except Exception:
            continue


def first_text(card, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            value = card.find_element(By.CSS_SELECTOR, selector).text.strip()
            if value:
                return value
        except Exception:
            continue
    return ""


def first_attr(card, selectors: list[str], attr_name: str) -> str:
    for selector in selectors:
        try:
            value = (card.find_element(By.CSS_SELECTOR, selector).get_attribute(attr_name) or "").strip()
            if value:
                return value
        except Exception:
            continue
    return ""


def get_review_id(card) -> str:
    """Extract review id from the card or nested controls."""
    candidates = [
        card.get_attribute("data-review-id") or "",
        first_attr(card, ['button[data-review-id]'], "data-review-id"),
        first_attr(card, ['div[data-review-id]'], "data-review-id"),
    ]
    for value in candidates:
        value = (value or "").strip()
        if value:
            return value
    return ""


def parse_review(card, known_review_id: str = "") -> Review:
    review_id = known_review_id or get_review_id(card)
    expand_review(card)
    
    # Try multiple selectors for author name (Google changes these frequently)
    author = first_text(card, [
        'div[class*="d4r55"]',           # Current selector
        'div[class*="ReviewerProfile"]', # Alternative 1
        'button[data-review-id] div',    # Alternative 2 - inside profile button
        'div.TSUbDb a',                  # Alternative 3
        'a[href*="/contrib/"]',          # Alternative 4 - profile link
    ])
    
    # If still empty, try to extract from aria-label or data attributes
    if not author:
        try:
            profile_button = card.find_element(By.CSS_SELECTOR, 'button[data-review-id]')
            aria_label = profile_button.get_attribute("aria-label") or ""
            # Extract name from aria-label like "Photo of John Doe"
            if aria_label:
                author = aria_label.replace("Photo of", "").strip()
        except Exception:
            pass
    
    # Try multiple ways to get profile URL
    profile_url = first_attr(card, ['button[data-review-id]'], "data-href")
    
    # Fallback: construct from href attribute or look for profile links
    if not profile_url:
        profile_url = first_attr(card, [
            'a[href*="/contrib/"]',
            'button[data-review-id] a',
        ], "href")
    
    # Last resort: try to find any link that looks like a Google Maps profile
    if not profile_url:
        try:
            links = card.find_elements(By.CSS_SELECTOR, 'a[href]')
            for link in links:
                href = link.get_attribute("href") or ""
                if "/contrib/" in href or "/reviews" in href:
                    profile_url = href
                    break
        except Exception:
            pass
    
    profile_picture_raw = first_attr(card, ['button[data-review-id] img'], "src")
    # Upscale profile picture
    profile_picture = upscale_image_url(profile_picture_raw, scale=10)

    rating = None
    label = first_attr(card, ['span[role="img"][aria-label]', 'span[class*="kvMYJc" i]'], "aria-label")
    if label:
        rating = first_float(label)

    raw_date = first_text(card, ['span[class*="rsqaWe"]', 'span[class*="xRkPPb" i]'])
    text = first_text(card, [
        'span[jsname="bN97Pc"]',
        'span[jsname="fbQN7e"]',
        'div.MyEned span.wiI7pd',
    ])
    likes = first_int(first_text(card, ['button[jsaction*="toggleThumbsUp" i]'])) or 0

    photos: list[str] = []
    for button in card.find_elements(By.CSS_SELECTOR, 'button.Tya61d, button[style*="url"]'):
        style = button.get_attribute("style") or ""
        # Match url("...") or url('...') or url(...)
        match = re.search(r'url\(["\']?([^"\'\\)]+)["\']?\)', style)
        if match and match.group(1) not in photos:
            # Upscale image URL (multiply dimensions by 10)
            photo_url = upscale_image_url(match.group(1), scale=10)
            photos.append(photo_url)

    owner_text = first_text(card, ['div.CDe7pd div.wiI7pd', 'div[class*="owner" i] div.wiI7pd'])
    reviewer_info = first_text(card, ['div.RfnDt'])
    reviewer_is_local_guide = "local guide" in reviewer_info.lower()
    reviewer_total_reviews = 0
    reviewer_total_photos = 0
    review_match = re.search(r"(\d+)\s+reviews?", reviewer_info, re.I)
    photo_match = re.search(r"(\d+)\s+photos?", reviewer_info, re.I)
    if review_match:
        reviewer_total_reviews = int(review_match.group(1))
    if photo_match:
        reviewer_total_photos = int(photo_match.group(1))

    review = Review(
        review_id=review_id,
        author=author,
        rating=rating,
        text=text,
        raw_date=raw_date,
        review_date="",  # Will be populated if needed
        likes=likes,
        photos=photos,
        profile_url=profile_url,
        profile_picture=profile_picture,
        owner_text=owner_text,
        sub_ratings={},
        reviewer_is_local_guide=reviewer_is_local_guide,
        reviewer_total_reviews=reviewer_total_reviews,
        reviewer_total_photos=reviewer_total_photos,
    )
    
    # Validate critical fields and log warnings
    missing_fields = []
    if not review.review_id:
        missing_fields.append("review_id")
    if not review.author:
        missing_fields.append("author/name")
    if not review.profile_url:
        missing_fields.append("profile_url")
    
    if missing_fields:
        LOG.warning(
            "Review missing critical fields: %s | review_id=%s author=%r profile_url=%s",
            ", ".join(missing_fields),
            review.review_id or "MISSING",
            review.author or "MISSING",
            review.profile_url or "MISSING"
        )
    
    LOG.debug(
        "Parsed review: id=%s author=%r rating=%s text_len=%d photos=%d",
        review.review_id,
        review.author,
        review.rating,
        len(review.text or ""),
        len(review.photos),
    )
    return review


def find_reviews_pane(driver):
    LOG.info("Finding reviews scroll pane")
    selectors = [
        'div[role="main"] div.m6QErb.DxyBCb.kA9KIf.dS8AEf',
        'div[role="main"] div.m6QErb.DxyBCb',
        'div[role="main"] div.m6QErb',
        'div[role="main"]',
    ]
    for selector in selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            if element and element.is_displayed():
                LOG.info("Reviews pane found with selector: %s", selector)
                return element
        except Exception:
            continue
    LOG.warning("Reviews pane not found; will use window scroll fallback")
    return None


def scrape_reviews(driver, max_reviews: int, max_scrolls: int) -> list[dict[str, Any]]:
    LOG.info("Starting review scrape: max_reviews=%s max_scrolls=%s", max_reviews, max_scrolls)
    click_reviews_tab(driver)
    
    # Wait for reviews to load after clicking tab
    time.sleep(3)
    
    pane = find_reviews_pane(driver)
    seen: set[str] = set()
    reviews: list[Review] = []
    idle = 0
    consecutive_no_cards = 0
    last_scroll_position = 0
    scroll_stuck_count = 0

    # Pre-setup scroll script for better performance
    scroll_script = "window.scrollBy(0, 1200);"
    if pane:
        try:
            driver.execute_script("window.scrollablePane = arguments[0];", pane)
            scroll_script = "window.scrollablePane.scrollBy(0, window.scrollablePane.scrollHeight);"
            LOG.info("Set up optimized scroll script for pane")
        except Exception as exc:
            LOG.debug("Could not set up pane scroll script: %s", exc)

    for scroll_index in range(max_scrolls):
        # Find cards in the pane, or fallback to entire page
        if pane:
            try:
                cards = pane.find_elements(By.CSS_SELECTOR, REVIEW_CARD)
            except Exception:
                cards = driver.find_elements(By.CSS_SELECTOR, REVIEW_CARD)
        else:
            cards = driver.find_elements(By.CSS_SELECTOR, REVIEW_CARD)
            
        stats = {
            "missing_id": 0,
            "duplicate": 0,
            "empty_after_parse": 0,
            "stale": 0,
            "parse_error": 0,
        }
        
        LOG.info(
            "Review scroll %d/%d: visible_cards=%d collected=%d idle=%d",
            scroll_index + 1,
            max_scrolls,
            len(cards),
            len(reviews),
            idle,
        )
        
        # Check for no cards situation
        if len(cards) == 0:
            consecutive_no_cards += 1
            LOG.info("No review cards found in iteration (consecutive: %d)", consecutive_no_cards)
            
            if consecutive_no_cards > 5:
                LOG.warning("No cards found for 5+ iterations - might be at end")
                break
                
            # Try aggressive scrolling
            try:
                driver.execute_script(scroll_script)
                time.sleep(0.8)
                driver.execute_script("window.scrollBy(0, 1000);")
                time.sleep(1.2)
            except Exception as exc:
                LOG.debug("Scroll error: %s", exc)
            continue
        else:
            consecutive_no_cards = 0
            
        added = 0
        fresh_cards = []
        
        # First pass: identify fresh cards
        for card in cards:
            try:
                review_id = get_review_id(card)
                if not review_id:
                    stats["missing_id"] += 1
                    continue
                # Skip if already collected in previous iterations
                if review_id in seen:
                    stats["duplicate"] += 1
                    continue
                fresh_cards.append((card, review_id))
            except StaleElementReferenceException:
                stats["stale"] += 1
                continue
            except Exception as exc:
                LOG.debug("Error getting review ID: %s", exc)
                continue
        
        LOG.debug(
            "Card filtering: total_cards=%d fresh=%d missing_id=%d duplicate=%d stale=%d",
            len(cards),
            len(fresh_cards),
            stats["missing_id"],
            stats["duplicate"],
            stats["stale"],
        )
        
        # Second pass: parse fresh cards
        for card, review_id in fresh_cards:
            try:
                review = parse_review(card, review_id)
                if not review.review_id:
                    stats["empty_after_parse"] += 1
                    LOG.warning("Review parsed but missing ID: review_id from card=%r", review_id)
                    continue
                seen.add(review.review_id)
                reviews.append(review)
                added += 1
                LOG.info(
                    "Collected review %d%s: id=%s author=%r rating=%s",
                    len(reviews),
                    f"/{max_reviews}" if max_reviews > 0 else "",
                    review.review_id,
                    review.author,
                    review.rating,
                )
                if max_reviews > 0 and len(reviews) >= max_reviews:
                    LOG.info("Reached max_reviews=%d", max_reviews)
                    return [asdict(item) for item in reviews]
            except StaleElementReferenceException:
                stats["stale"] += 1
                LOG.debug("Skipped stale review card: id=%s", review_id)
                continue
            except Exception as exc:
                stats["parse_error"] += 1
                LOG.warning("Parse error for review id=%s: %s", review_id, exc, exc_info=True)
                continue

        if added == 0 and cards:
            LOG.info(
                "No new reviews from %d visible card(s): missing_id=%d duplicate=%d "
                "empty_after_parse=%d stale=%d parse_error=%d",
                len(cards),
                stats["missing_id"],
                stats["duplicate"],
                stats["empty_after_parse"],
                stats["stale"],
                stats["parse_error"],
            )

        if added == 0:
            idle += 1
        else:
            idle = 0
            
        if idle >= 5:
            LOG.info("Stopping reviews scrape after %d idle scrolls", idle)
            break

        # Check if scroll is stuck
        if pane:
            try:
                current_scroll = driver.execute_script("return arguments[0].scrollTop;", pane)
                if current_scroll == last_scroll_position and added == 0:
                    scroll_stuck_count += 1
                    LOG.warning("Scroll stuck at %dpx (stuck_count: %d)", current_scroll, scroll_stuck_count)
                    
                    if scroll_stuck_count > 5:
                        LOG.warning("Scroll stuck - trying alternative method")
                        try:
                            driver.execute_script("arguments[0].lastElementChild.scrollIntoView();", pane)
                            time.sleep(2)
                        except Exception:
                            pass
                        scroll_stuck_count = 0
                else:
                    scroll_stuck_count = 0
                    last_scroll_position = current_scroll
            except Exception:
                pass

        # Perform scroll
        try:
            driver.execute_script(scroll_script)
            # Extra scroll when no new reviews found
            if added == 0:
                time.sleep(0.5)
                driver.execute_script("window.scrollBy(0, 500);")
        except Exception as exc:
            LOG.debug("Scroll error: %s", exc)
            try:
                driver.execute_script("window.scrollBy(0, 1200);")
            except Exception:
                pass
        
        # Dynamic sleep based on activity
        if added > 5:
            sleep_time = 0.7
        elif added == 0:
            sleep_time = 2.0
        else:
            sleep_time = 1.0
        time.sleep(sleep_time)

    LOG.info("Review scrape finished: collected=%d", len(reviews))
    return [asdict(item) for item in reviews]


def scrape_place(url: str, *, headless: bool, max_reviews: int, max_scrolls: int) -> dict[str, Any]:
    LOG.info("Starting place scrape: %s", url)
    driver = setup_driver(headless)
    try:
        resolved_url = navigate(driver, url)
        place = js_place_detail(driver, url, resolved_url)
        place["status"] = "ok"
        reviews_list = scrape_reviews(driver, max_reviews, max_scrolls)

        # Re-extract per-star breakdown after reviews panel opens
        apply_rating_summary(place, js_extract_rating_summary(driver), per_rating_only=True)
        
        # Format reviews to match API requirements (camelCase fields)
        google_place_id = place.get("placeId", "") or place.get("place_id", "")
        formatted_reviews = [format_review_for_output(r, google_place_id) for r in reviews_list]
        
        place["reviews"] = formatted_reviews
        place["userReviews"] = formatted_reviews  # Alias for google-maps-scraper compatibility
        place["reviews_count_output"] = len(formatted_reviews)
        LOG.info(
            "Place scrape completed: title=%r reviews=%d",
            place.get("title", ""),
            len(formatted_reviews),
        )
        return place
    except Exception as exc:
        LOG.exception("Place scrape failed: %s", url)
        return {
            "input_url": url,
            "status": "failed",
            "error": str(exc),
            "reviews": [],
            "userReviews": [],  # Add alias here too
            "reviews_count_output": 0,
        }
    finally:
        try:
            LOG.info("Closing browser")
            driver.quit()
        except Exception:
            pass


def format_review_for_output(review_dict: dict[str, Any], google_place_id: str = "") -> dict[str, Any]:
    """Format review to match backend API requirements."""
    # Parse when/raw_date to ISO format if possible
    when_value = review_dict.get("raw_date", "")
    # For now, keep as-is since we don't have review_date parsed to ISO
    # Backend should handle relative dates like "46 minutes ago"
    # Or we use review_date if available (ISO format)
    if review_dict.get("review_date"):
        when_value = review_dict.get("review_date")
    
    return {
        # Backend required fields (camelCase only)
        "reviewId": review_dict.get("review_id", ""),
        "googlePlaceId": google_place_id,
        "name": review_dict.get("author", ""),
        "profileUrl": review_dict.get("profile_url", ""),
        "profilePicture": review_dict.get("profile_picture", ""),
        "isLocalGuide": review_dict.get("reviewer_is_local_guide", False),
        "totalReviews": review_dict.get("reviewer_total_reviews", 0),
        "totalPhotos": review_dict.get("reviewer_total_photos", 0),
        "rating": int(review_dict.get("rating", 0)) if review_dict.get("rating") else 0,
        "description": review_dict.get("text", ""),
        "when": when_value,
        "images": review_dict.get("photos", []),
        "likes": review_dict.get("likes", 0),
    }


def write_output(path: str, places: list[dict[str, Any]]) -> None:
    LOG.info("Writing output: path=%s places=%d", path, len(places))
    
    # Format and deduplicate reviews in each place
    for place in places:
        if "reviews" in place and isinstance(place["reviews"], list):
            # Get place ID for reviews
            google_place_id = place.get("placeId", "") or place.get("place_id", "")
            reviews = place["reviews"]

            # scrape_place() already formats reviews (camelCase). Do not format twice.
            already_formatted = bool(
                reviews and isinstance(reviews[0], dict) and "reviewId" in reviews[0]
            )
            id_key = "reviewId" if already_formatted else "review_id"

            # Deduplicate by review id before formatting
            seen_ids: set[str] = set()
            unique_reviews = []
            for r in reviews:
                review_id = r.get(id_key, "")
                if review_id and review_id not in seen_ids:
                    seen_ids.add(review_id)
                    unique_reviews.append(r)
                elif not review_id:
                    # Keep reviews without ID (shouldn't happen but just in case)
                    unique_reviews.append(r)
            
            if len(unique_reviews) < len(reviews):
                LOG.warning(
                    "Removed %d duplicate reviews from output (before: %d, after: %d)",
                    len(reviews) - len(unique_reviews),
                    len(reviews),
                    len(unique_reviews),
                )

            if already_formatted:
                formatted_reviews = unique_reviews
            else:
                formatted_reviews = [
                    format_review_for_output(r, google_place_id) for r in unique_reviews
                ]

            place["reviews"] = formatted_reviews
            place["userReviews"] = formatted_reviews  # Alias for google-maps-scraper
            place["reviews_count_output"] = len(formatted_reviews)
        
        # Ensure all expected fields have proper types (not stringified)
        # Empty objects/arrays should be proper types, not strings
        if "reviewsPerRating" not in place or place["reviewsPerRating"] is None:
            place["reviewsPerRating"] = {}
        if "openHours" not in place or place["openHours"] is None:
            place["openHours"] = {}
        if "popularTimes" not in place or place["popularTimes"] is None:
            place["popularTimes"] = {}
        if "images" not in place or place["images"] is None:
            place["images"] = []
        if "emails" not in place or place["emails"] is None:
            place["emails"] = []
        if "about" not in place or place["about"] is None:
            place["about"] = []
        if "menu" not in place or place["menu"] is None:
            place["menu"] = {}
        if "owner" not in place or place["owner"] is None:
            place["owner"] = {}
        if "completeAddress" not in place or place["completeAddress"] is None:
            place["completeAddress"] = {}
    
    output = {
        "generated_at": now_iso(),
        "count": len(places),
        "places": places,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Output written: %s", out.resolve())


def setup_logging(level: str, log_file: str | None) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Google Maps place details and reviews.")
    parser.add_argument("--urls", required=True, help="Text file with one Google Maps URL per line.")
    parser.add_argument("--output", "-o", required=True, help="JSON output path.")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode.")
    parser.add_argument("--max-reviews", type=int, default=0, help="Max reviews per place. 0 means unlimited.")
    parser.add_argument("--max-scrolls", type=int, default=80, help="Max review-pane scrolls per place.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Log verbosity. Use DEBUG for selector/card-level details.",
    )
    parser.add_argument("--log-file", default=None, help="Optional log file path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level, args.log_file)
    urls = read_urls(args.urls)
    if not urls:
        print(f"No URLs found in {args.urls}", file=sys.stderr)
        return 2

    places: list[dict[str, Any]] = []
    LOG.info(
        "Job started: urls=%d output=%s headless=%s max_reviews=%s max_scrolls=%s",
        len(urls),
        args.output,
        args.headless,
        args.max_reviews,
        args.max_scrolls,
    )
    for index, url in enumerate(urls, start=1):
        LOG.info("Processing URL %d/%d: %s", index, len(urls), url)
        places.append(
            scrape_place(
                url,
                headless=args.headless,
                max_reviews=args.max_reviews,
                max_scrolls=args.max_scrolls,
            )
        )
        write_output(args.output, places)

    LOG.info("Job finished: wrote %d places to %s", len(places), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
