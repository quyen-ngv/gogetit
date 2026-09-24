#!/usr/bin/env python3
"""Test whether the real title can be reconstructed straight from the corrupted
`title` URL instead of re-scraping Google Maps.

Google Maps place URLs are built as .../maps/place/<url-encoded place name>/@lat,lng...
i.e. the URL-encoding is the exact inverse of what a scrape's h1 text would give.
The old bug (see run_job.py, fixed 2026-09-02) left `title` holding exactly that
URL, so decoding the path segment should reconstruct the original name with no
network request at all.

This script only reads from the admin API (no scraping, no writes) and checks the
decoded result against titles already confirmed correct by a real scrape (see
debug/fix_broken_titles_sample.sql and debug/fix_broken_titles_full.sql).

Usage:
  python test_decode_titles_from_url.py
"""

from __future__ import annotations

from urllib.parse import unquote_plus, urlsplit

from fix_broken_titles import fetch_broken_places

# Same short-lived admin token as fix_broken_titles.py's default; swap when it rotates.
TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJwYXJ0bmVyIjpmYWxzZSwiYWRtaW4iOnRydWUsIm11c3RDaGFuZ2VQYXNzd29yZCI6ZmFsc2UsInVzZXJJZCI6Ijc0ZjJmMzMyLWMwMjgtNDlhZC04YTc0LWEyNmQ4NWI2ZWNmZSIsImVtYWlsIjoibHV4b2ZvbnNAZ21haWwuY29tIiwic3ViIjoiNzRmMmYzMzItYzAyOC00OWFkLThhNzQtYTI2ZDg1YjZlY2ZlIiwiaWF0IjoxNzg4MzMwMDc4LCJleHAiOjE3ODg0MTY0Nzh9.YdLiZUe52wm2uhoy73h4Y_NxdEcCWJ-wJPXBTPDarMs"
)

# id -> title, already confirmed correct by a real scrape (fix_broken_titles.py runs so far).
KNOWN_GOOD: dict[str, str] = {
    "5f80fb94-d80b-4c2a-a75d-10bb53bbd7de": "Ha Tu Goat Restaurant",
    "0e18d906-efce-4734-89e2-cbffd33709a8": "Ốc Bình Dân",
    "57c02742-2604-45fe-a1b1-951c08e5c3dc": "Nhà Hàng King Cua",
    "2a178631-5c8d-4586-b1d2-746d22490c3b": "Quán Bánh Tầm Cô Huệ",
}


def decode_title_from_place_url(url: str) -> str | None:
    """Pull the place-name path segment out of a Google Maps place URL and decode it."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if "place" not in segments:
        return None
    idx = segments.index("place")
    if idx + 1 >= len(segments):
        return None
    decoded = unquote_plus(segments[idx + 1]).strip()
    return decoded or None


def main() -> int:
    places = fetch_broken_places(TOKEN)
    print(f"Fetched {len(places)} broken places\n")

    matches = 0
    mismatches: list[tuple[str, str, str]] = []
    decode_failures: list[tuple[str, str]] = []
    decoded_by_id: dict[str, str] = {}

    for place in places:
        place_id = str(place.get("id") or "")
        title_url = str(place.get("title") or "")
        decoded = decode_title_from_place_url(title_url)
        if decoded is None:
            decode_failures.append((place_id, title_url[:80]))
            continue
        decoded_by_id[place_id] = decoded

        if place_id in KNOWN_GOOD:
            expected = KNOWN_GOOD[place_id]
            ok = decoded == expected
            matches += int(ok)
            print(f"{'OK      ' if ok else 'MISMATCH'} {place_id}: decoded={decoded!r} expected={expected!r}")
            if not ok:
                mismatches.append((place_id, decoded, expected))

    print(f"\n{matches}/{len(KNOWN_GOOD)} known-good titles matched exactly")
    print(f"{len(decode_failures)} place(s) had no decodable name in the URL")
    if mismatches:
        print("Mismatches:")
        for place_id, decoded, expected in mismatches:
            print(f"  {place_id}: decoded={decoded!r} != scraped={expected!r}")

    print(f"\n--- Sample of decoded titles (first 40 of {len(places)}) ---")
    for place in places[:40]:
        place_id = str(place.get("id") or "")
        print(f"{place_id} -> {decoded_by_id.get(place_id)!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
