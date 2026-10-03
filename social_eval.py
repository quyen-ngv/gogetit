"""Run the social video extraction on real links and print what the AI found.

Used to compare models and settings on the server without going through the app:

    docker exec google-maps-telegram-bot python social_eval.py https://vt.tiktok.com/ZSbyL8VhB/
    docker exec google-maps-telegram-bot python social_eval.py --model gpt-5.4-mini --frame-interval 2 URL

Map lookups are skipped; this measures the "watch the video" part only.
"""

from __future__ import annotations

import argparse
import logging
import sys

from social_location_extractor import extract_social_location


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--provider", default="OPENAI")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--language", default="vi")
    parser.add_argument("--frame-interval", type=int, default=3)
    parser.add_argument("--max-frames", type=int, default=60)
    parser.add_argument("--width", type=int, default=448)
    parser.add_argument("--quality", type=int, default=12)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("social_location_extractor").setLevel(logging.INFO)

    for url in args.urls:
        result = extract_social_location(
            url,
            language=args.language,
            max_frames=args.max_frames,
            frame_interval_seconds=args.frame_interval,
            image_max_width=args.width,
            image_jpeg_quality=args.quality,
            include_map_search=False,
            ai_provider=args.provider,
            ai_model=args.model,
        )
        extraction = result.get("extraction") or {}
        candidates = extraction.get("candidates") or []
        print(f"\n=== {url}")
        print(f"success={result.get('success')} error={result.get('error')}")
        print(f"media={result.get('media')}")
        print(f"trip_title={extraction.get('trip_title')!r} contentType={extraction.get('contentType')} "
              f"days={extraction.get('durationDays')}")
        print(f"candidates={len(candidates)} validation={extraction.get('candidateValidation')}")
        for candidate in candidates:
            print(f"  {candidate.get('sequence')}. {candidate.get('name')} "
                  f"[{candidate.get('mention_type')}, {candidate.get('resolutionStatus')}] "
                  f"{(candidate.get('description') or '')[:120]}")
        for mention in extraction.get("discarded_mentions") or []:
            print(f"  discarded: {mention.get('mention')} - {mention.get('reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
