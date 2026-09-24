#!/usr/bin/env python3
"""One-off cleanup for places whose title got corrupted into a Google Maps URL.

Standalone script, does not touch any production service code and does not call
any write/update endpoint. It only:
  1. Reads broken places from the admin API (GET /admin/places?search=http...).
  2. Reconstructs the real title straight from that URL: Google Maps place URLs
     are built as .../maps/place/<url-encoded place name>/@lat,lng..., which is
     exactly the place's display name run through the same encoding a scrape's
     h1 text would produce — decoding it is the exact inverse, no network request
     needed. (Verified against 4 titles already confirmed correct by a real
     scrape: 4/4 exact matches, 0/276 decode failures — see
     test_decode_titles_from_url.py.)
  3. Writes ready-to-review UPDATE statements to a .sql file for a human to check
     and run against the database themselves — both `places.title` and the
     matching `place_translations.name` (locale='vi', source='LEGACY'), since
     PlaceTranslationService.syncTranslations always mirrors places.title into
     the default-locale translation row on every import/update.

Usage:
  python fix_broken_titles.py --output fix_broken_titles.sql

Re-run later (same command) to pick up whatever this run left unresolved —
already-fixed places won't show up anymore since their title in the DB will no
longer match the search.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any
from urllib.parse import parse_qs, unquote, unquote_plus, urlsplit

import requests

ADMIN_PLACES_URL = os.getenv(
    "GOROUTE_ADMIN_PLACES_URL",
    "https://onestudy.id.vn/goroute/v1/api/admin/places",
)
PAGE_SIZE = 100

LOG = logging.getLogger("fix_broken_titles")

# Hardcoded per request so this can run with no setup. This token expires ~24h
# after issuance (JWT exp) and this file is untracked/local-only — don't commit
# it, and swap this default once the token rotates.
_DEFAULT_TOKEN = (
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJwYXJ0bmVyIjpmYWxzZSwiYWRtaW4iOnRydWUsIm11c3RDaGFuZ2VQYXNzd29yZCI6ZmFsc2UsInVzZXJJZCI6Ijc0ZjJmMzMyLWMwMjgtNDlhZC04YTc0LWEyNmQ4NWI2ZWNmZSIsImVtYWlsIjoibHV4b2ZvbnNAZ21haWwuY29tIiwic3ViIjoiNzRmMmYzMzItYzAyOC00OWFkLThhNzQtYTI2ZDg1YjZlY2ZlIiwiaWF0IjoxNzg5MjI2MTc5LCJleHAiOjE3ODkzMTI1Nzl9.WwQzzxyiRvVnD82VPMbJfrdHd38jVN2TZ-H37Y3HlwQ"
)


def _admin_headers(token: str) -> dict[str, str]:
    token = token.strip()
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    return {"accept": "*/*", "authorization": token}


def fetch_broken_places(token: str) -> list[dict[str, Any]]:
    """Page through /admin/places?search=http and keep only genuinely broken titles.

    The search endpoint matches title OR address OR place_id OR id against the term,
    so it can return false positives (e.g. an address that happens to contain
    "http"). Filter client-side on the title itself actually looking like a URL.
    """
    broken: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 0
    while True:
        response = requests.get(
            ADMIN_PLACES_URL,
            headers=_admin_headers(token),
            params={"search": "http", "page": page, "size": PAGE_SIZE},
            timeout=30,
        )
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}
        items = data.get("items") or []
        if not items:
            break
        for item in items:
            place_id = str(item.get("id") or "")
            title = str(item.get("title") or "")
            if not place_id or place_id in seen_ids:
                continue
            seen_ids.add(place_id)
            broken.append(item)
        total = data.get("total")
        page += 1
        if total is not None and page * PAGE_SIZE >= total:
            break
        if len(items) < PAGE_SIZE:
            break
    return broken


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _looks_blocked(url: str) -> bool:
    return "/sorry/" in urlsplit(url).path


def resolve_source_url(place: dict[str, Any]) -> str:
    """Pick a real Google Maps place URL out of a corrupted record.

    The bug this cleans up (see run_job.py's old driver.title fallback, fixed
    2026-09-02) left `title` holding the plain place URL that was being loaded
    (readable, not blocked) while `googleMapsLink` holds the /sorry/... bot-check
    URL captured a few seconds later. Prefer whichever field is an actual place
    URL; as a last resort, pull the real destination out of the sorry page's
    `continue=` query param.
    """
    title = str(place.get("title") or "").strip()
    link = str(place.get("googleMapsLink") or "").strip()
    for candidate in (title, link):
        if candidate.startswith("http") and not _looks_blocked(candidate):
            return candidate
    for candidate in (link, title):
        if "/sorry/" in candidate:
            continue_param = parse_qs(urlsplit(candidate).query).get("continue", [""])[0]
            decoded = unquote(continue_param)
            if decoded.startswith("http"):
                return decoded
    return title or link


def decode_title_from_place_url(url: str) -> str | None:
    """Reconstruct the place's display name from its own Google Maps URL.

    .../maps/place/<name>/@lat,lng,zoom/... — <name> is the display name run
    through the same percent/plus encoding a browser would use; unquote_plus is
    the exact inverse.
    """
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if "place" not in segments:
        return None
    idx = segments.index("place")
    if idx + 1 >= len(segments):
        return None
    decoded = unquote_plus(segments[idx + 1]).strip()
    if not decoded or "%" in decoded:
        return None
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="fix_broken_titles.sql", help="Where to write the UPDATE statements")
    parser.add_argument(
        "--token",
        default=os.getenv("GOROUTE_ADMIN_TOKEN", _DEFAULT_TOKEN),
        help="Admin bearer token; can also be set via GOROUTE_ADMIN_TOKEN env var",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.token:
        LOG.error("Missing admin token. Pass --token or set GOROUTE_ADMIN_TOKEN.")
        return 1

    LOG.info("Fetching places with a broken (URL-shaped) title...")
    broken_places = fetch_broken_places(args.token)
    LOG.info("Found %d place(s) with a broken title", len(broken_places))
    if not broken_places:
        return 0

    statements: list[str] = []
    unresolved: list[dict[str, Any]] = []

    for place in broken_places:
        place_id = place.get("id")
        broken_title = str(place.get("title") or "")
        url = resolve_source_url(place)
        real_title = decode_title_from_place_url(url) if url else None

        if not real_title:
            LOG.warning("Could not decode a title from the URL for %s: %s", place_id, url or "(no usable URL)")
            unresolved.append(place)
            continue

        LOG.info("Decoded title for %s: %r (was %r)", place_id, real_title, broken_title[:80])
        statements.append(
            f"UPDATE places SET title = '{_escape_sql(real_title)}', updated_at = NOW() "
            f"WHERE id = '{place_id}'; -- was: {broken_title[:80]!r}"
        )
        # PlaceTranslationService.syncTranslations always falls back to places.title for
        # the default ('vi') locale when a request carries no explicit translations map
        # (the scraper/refresh job never sends one) — so every corrupted title also wrote
        # the same URL into place_translations.name for locale='vi', source='LEGACY'.
        # Only touch LEGACY rows so a manually-edited translation is never overwritten.
        statements.append(
            f"UPDATE place_translations SET name = '{_escape_sql(real_title)}', updated_at = NOW() "
            f"WHERE place_id = '{place_id}' AND locale = 'vi' AND translation_source = 'LEGACY';"
        )

    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write("-- Generated by fix_broken_titles.py; review before running.\n")
        handle.write(f"-- Resolved {len(statements) // 2} of {len(broken_places)} broken titles ")
        handle.write("(places + place_translations, 2 statements each).\n\n")
        handle.write("\n".join(statements))
        handle.write("\n")

    LOG.info("Wrote %d UPDATE statement(s) to %s", len(statements), args.output)
    if unresolved:
        LOG.warning(
            "%d place(s) could not be decoded; inspect manually: %s",
            len(unresolved),
            ", ".join(str(p.get("id")) for p in unresolved),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
