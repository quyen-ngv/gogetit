"""Extract place candidates from TikTok/Instagram videos using audio and frames."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from datetime import datetime
from urllib.parse import urlparse

import requests
import yt_dlp
from anthropic import Anthropic

from browser_runtime import acquire_browser_slot, release_browser_slot
from config import (
    AI_BASE_URL,
    AI_API_KEY,
    AI_MODEL,
    AI_PROVIDER,
    AI_REQUEST_TIMEOUT_SECONDS,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GROQ_API_KEY,
    GROQ_WHISPER_MODEL,
    LOCAL_WHISPER_CACHE_DIR,
    LOCAL_WHISPER_COMPUTE_TYPE,
    LOCAL_WHISPER_DEVICE,
    LOCAL_WHISPER_ENABLED,
    LOCAL_WHISPER_LANGUAGE,
    LOCAL_WHISPER_MODEL,
    OPENAI_API_KEY,
    OPENAI_CHAT_MODEL,
    OPENAI_WHISPER_MODEL,
    SOCIAL_YTDLP_COOKIE_FILE,
    SOCIAL_YTDLP_IMPERSONATE,
)
from place_searcher import search_google_maps


LOG = logging.getLogger("social_location_extractor")
_LOCAL_WHISPER = None

AI_GUARDRAIL_MIN_CONFIDENCE = 0.72
MAP_MIN_MATCH_SCORE = 0.68
MAP_AMBIGUITY_GAP = 0.08
AI_CANDIDATE_POLICY = "AI_EVIDENCE_JUDGE_V2"

_AI_CONFIRMED_IDENTITY_STATUSES = {"confirmed"}
_AI_SUPPORTED_MENTION_TYPES = {
    "explicit_audio_name",
    "exact_visible_sign",
    "multimodal_confirmed",
}
_EVIDENCE_SOURCES = {"audio", "frame", "slide", "caption_context"}

_GENERIC_PLACE_NAMES = {
    "beach",
    "bai bien",
    "cafe",
    "coffee shop",
    "cua hang",
    "diem ngam bien",
    "nha hang",
    "quan an",
    "quan banh can",
    "quan ca phe",
    "quan cafe",
    "restaurant",
    "shop",
    "tiem banh can",
}

SOCIAL_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|vt\.|vm\.)?(?:tiktok\.com|instagram\.com|instagr\.am)/",
    re.IGNORECASE,
)


def is_social_video_url(url: str) -> bool:
    return bool(SOCIAL_URL_RE.search(url.strip()))


def _run(command: list[str], *, timeout: int) -> None:
    LOG.debug("Running command: %s", " ".join(command[:4] + ["..."] if len(command) > 4 else command))
    subprocess.run(command, check=True, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return ""


def _metadata_context(info: dict[str, Any]) -> dict[str, Any]:
    thumbnail_url = _first_text(info.get("thumbnail"))
    if not thumbnail_url:
        thumbnails = info.get("thumbnails")
        if isinstance(thumbnails, list):
            thumbnail_url = next(
                (
                    _first_text(item.get("url"))
                    for item in thumbnails
                    if isinstance(item, dict) and _first_text(item.get("url"))
                ),
                "",
            )
    return {
        "platform": info.get("extractor_key") or info.get("extractor") or "",
        "title": _first_text(info.get("title")),
        "thumbnailUrl": thumbnail_url,
        "caption": _first_text(info.get("description")),
        "tags": info.get("tags") or [],
        "uploader": _first_text(info.get("uploader") or info.get("channel")),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or info.get("original_url") or "",
    }


def _yt_dlp_attempts() -> list[tuple[str, dict[str, Any]]]:
    common: dict[str, Any] = {
        "quiet": True,
        "noplaylist": True,
        "no_warnings": True,
        "retries": 2,
        "extractor_retries": 2,
        "socket_timeout": 30,
        "http_headers": {
            "Referer": "https://www.tiktok.com/",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }
    cookie_path = Path(SOCIAL_YTDLP_COOKIE_FILE) if SOCIAL_YTDLP_COOKIE_FILE else None
    attempts: list[tuple[str, dict[str, Any]]] = []
    if cookie_path and cookie_path.is_file():
        options = dict(common)
        options["cookiefile"] = str(cookie_path)
        if SOCIAL_YTDLP_IMPERSONATE:
            options["impersonate"] = SOCIAL_YTDLP_IMPERSONATE
        attempts.append(("cookie+impersonate", options))
    if SOCIAL_YTDLP_IMPERSONATE:
        options = dict(common)
        options["impersonate"] = SOCIAL_YTDLP_IMPERSONATE
        attempts.append(("impersonate", options))
    attempts.append(("default", common))
    return attempts


def _is_trusted_tiktok_media_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "tiktok.com" or host.endswith((".tiktok.com", ".tiktokcdn.com", ".byteoversea.com"))


def _exception_summary(exc: BaseException) -> str:
    message = str(exc).strip()
    return message if message else f"{type(exc).__name__}: {exc!r}"


def _extract_tiktok_embed(url: str) -> dict[str, Any]:
    if "tiktok.com" not in url.lower():
        raise RuntimeError("TikTok embed fallback only supports TikTok URLs")
    response = requests.get("https://www.tiktok.com/oembed", params={"url": url}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    html = str(payload.get("html") or "")
    video_id_match = re.search(r'data-video-id="(\d+)"', html)
    if not video_id_match:
        raise RuntimeError("TikTok oEmbed did not return a video ID")
    video_id = video_id_match.group(1)
    canonical_match = re.search(r'cite="([^"]+)"', html)
    canonical_url = canonical_match.group(1) if canonical_match else url

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    for argument in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=390,844",
    ):
        options.add_argument(argument)
    acquire_browser_slot()
    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(f"https://www.tiktok.com/player/v1/{video_id}?autoplay=1")

        def video_details(browser):
            videos = browser.find_elements("tag name", "video")
            if not videos:
                return None
            media_url = videos[0].get_attribute("src") or ""
            if not _is_trusted_tiktok_media_url(media_url):
                return None
            duration = browser.execute_script("return arguments[0].duration || null", videos[0])
            return media_url, _positive_float(duration)

        media_url, duration = WebDriverWait(driver, 30, poll_frequency=0.5).until(video_details)
    finally:
        try:
            if driver is not None:
                driver.quit()
        finally:
            release_browser_slot()

    LOG.info("TikTok embed fallback resolved media: video_id=%s duration=%s", video_id, duration)
    return {
        "id": video_id,
        "extractor_key": "TikTok",
        "title": str(payload.get("title") or ""),
        "description": str(payload.get("title") or ""),
        "uploader": str(payload.get("author_name") or ""),
        "uploader_url": str(payload.get("author_url") or ""),
        "thumbnail": str(payload.get("thumbnail_url") or ""),
        "duration": duration,
        "webpage_url": canonical_url,
        "_embed_media_url": media_url,
    }


def extract_metadata(url: str) -> tuple[dict[str, Any], str]:
    LOG.info("Extracting social metadata: url=%s", url)
    last_error: Exception | None = None
    for profile, base_options in _yt_dlp_attempts():
        opts = {**base_options, "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            LOG.info(
                "Metadata extracted: profile=%s platform=%s duration=%s title=%r",
                profile,
                info.get("extractor_key") or info.get("extractor") or "",
                info.get("duration"),
                _first_text(info.get("title"))[:120],
            )
            return info, profile
        except Exception as exc:
            last_error = exc
            LOG.warning("Social metadata attempt failed: profile=%s error=%s", profile, _exception_summary(exc)[:500])
    if "tiktok.com" in url.lower():
        try:
            return _extract_tiktok_embed(url), "tiktok-embed"
        except Exception as exc:
            last_error = exc
            LOG.warning("TikTok embed metadata fallback failed: error=%s", _exception_summary(exc)[:500])
    detail = _exception_summary(last_error) if last_error else "unknown metadata error"
    raise RuntimeError(f"SOCIAL_VIDEO_DOWNLOAD_BLOCKED: TikTok/Instagram metadata extraction failed: {detail}")


def _download_tiktok_embed_media(media_url: str, out_dir: Path) -> Path:
    if not _is_trusted_tiktok_media_url(media_url):
        raise RuntimeError("SOCIAL_VIDEO_DOWNLOAD_BLOCKED: TikTok returned an untrusted media URL")
    target = out_dir / "media.mp4"
    with requests.get(
        media_url,
        headers={"Referer": "https://www.tiktok.com/"},
        stream=True,
        timeout=(20, 60),
    ) as response:
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "video" not in content_type and "octet-stream" not in content_type:
            raise RuntimeError(f"SOCIAL_VIDEO_DOWNLOAD_BLOCKED: TikTok CDN returned {content_type or 'unknown content'}")
        with target.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    if target.stat().st_size <= 0:
        raise RuntimeError("SOCIAL_VIDEO_DOWNLOAD_BLOCKED: TikTok CDN returned an empty video")
    LOG.info("Media downloaded through TikTok embed: path=%s size=%s", target, target.stat().st_size)
    return target


def download_media(
    url: str,
    out_dir: Path,
    preferred_profile: str | None = None,
    media_url: str | None = None,
) -> Path:
    LOG.info("Downloading social media with yt-dlp")
    if preferred_profile == "tiktok-embed" and media_url:
        return _download_tiktok_embed_media(media_url, out_dir)

    outtmpl = str(out_dir / "media.%(ext)s")
    attempts = _yt_dlp_attempts()
    if preferred_profile:
        attempts.sort(key=lambda item: item[0] != preferred_profile)
    last_error: Exception | None = None
    for profile, base_options in attempts:
        opts = {
            **base_options,
            "outtmpl": outtmpl,
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
        }
        before = set(out_dir.glob("media.*"))
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            after = sorted(set(out_dir.glob("media.*")) - before, key=lambda p: p.stat().st_mtime, reverse=True)
            existing = after or sorted(out_dir.glob("media.*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if existing:
                LOG.info("Media downloaded: profile=%s path=%s size=%s", profile, existing[0], existing[0].stat().st_size)
                return existing[0]
        except Exception as exc:
            last_error = exc
            LOG.warning("Social download attempt failed: profile=%s error=%s", profile, _exception_summary(exc)[:500])

    if "tiktok.com" in url.lower():
        try:
            embed_info = _extract_tiktok_embed(url)
            embed_media_url = str(embed_info.get("_embed_media_url") or "")
            if embed_media_url:
                LOG.info("Falling back to TikTok player media after yt-dlp download failure")
                return _download_tiktok_embed_media(embed_media_url, out_dir)
        except Exception as exc:
            last_error = exc
            LOG.warning("TikTok player download fallback failed: error=%s", _exception_summary(exc)[:500])

    detail = _exception_summary(last_error) if last_error else "unknown download error"
    raise RuntimeError(f"SOCIAL_VIDEO_DOWNLOAD_BLOCKED: TikTok/Instagram download failed: {detail}")


def probe_media_duration(media_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(media_path),
            ],
            check=True,
            timeout=30,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return _positive_float(result.stdout.strip())
    except Exception:
        LOG.warning("Could not probe downloaded media duration: %s", media_path, exc_info=True)
        return None


def has_audio_stream(media_path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(media_path),
            ],
            check=True,
            timeout=30,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        LOG.warning("Could not inspect audio streams: media=%s", media_path.name, exc_info=True)
        return False


def extract_audio(media_path: Path, out_dir: Path, max_seconds: int | None) -> Path | None:
    LOG.info("Extracting audio: media=%s max_seconds=%s", media_path.name, max_seconds or "unlimited")
    if not has_audio_stream(media_path):
        LOG.info("Skipping audio extraction; media has no audio stream: media=%s", media_path.name)
        return None
    audio_path = out_dir / "audio.wav"
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(media_path),
            "-vn",
        ]
        # Chỉ thêm -t nếu max_seconds được chỉ định
        if max_seconds:
            command.extend(["-t", str(max_seconds)])
        
        command.extend([
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio_path),
        ])
        
        _run(
            command,
            timeout=max(120, max_seconds + 30) if max_seconds else 600,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        LOG.warning("Audio extraction failed; continuing without transcript: media=%s error=%s", media_path.name, stderr[-1000:])
        return None
    except Exception:
        LOG.warning("Audio extraction failed; continuing without transcript: media=%s", media_path.name, exc_info=True)
        return None
    LOG.info("Audio extracted: exists=%s size=%s", audio_path.exists(), audio_path.stat().st_size if audio_path.exists() else 0)
    return audio_path if audio_path.exists() and audio_path.stat().st_size > 1024 else None


def extract_frames(
    media_path: Path,
    out_dir: Path,
    max_frames: int,
    interval_seconds: int,
    image_max_width: int,
    image_jpeg_quality: int,
    duration_seconds: float | int | None = None,
) -> list[Path]:
    duration = _positive_float(duration_seconds)
    interval = float(interval_seconds)
    timestamps = _frame_timestamps(
        max_frames=max_frames,
        interval_seconds=interval,
        duration_seconds=duration,
    )
    LOG.info(
        "Extracting frames: media=%s max_frames=%s interval_seconds=%.3f start_second=1 image_max_width=%s jpeg_q=%s duration=%s",
        media_path.name,
        max_frames,
        interval,
        image_max_width,
        image_jpeg_quality,
        duration,
    )
    frame_dir = out_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    for index, timestamp in enumerate(timestamps, start=1):
        frame_path = frame_dir / f"frame_{index:02d}.jpg"
        try:
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(media_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale='min({image_max_width},iw)':-2",
                    "-q:v",
                    str(image_jpeg_quality),
                    str(frame_path),
                ],
                timeout=60,
            )
        except Exception:
            LOG.debug("Frame extraction failed at %.3fs", timestamp, exc_info=True)

    frames = sorted(frame_dir.glob("frame_*.jpg"))[:max_frames]
    LOG.info("Frames extracted: count=%s", len(frames))
    return frames


_FRAME_DEDUP_HAMMING_THRESHOLD = 6  # of 64 bits; near-identical frames rarely exceed this


def _average_hash(path: Path, hash_size: int = 8) -> int | None:
    """8x8 grayscale average hash (aHash). Cheap and deliberately coarse --
    it only needs to catch "basically the same frame", not fine detail."""
    try:
        from PIL import Image
    except ImportError:
        LOG.debug("Pillow not installed; frame dedup disabled")
        return None
    try:
        with Image.open(path) as img:
            pixels = list(img.convert("L").resize((hash_size, hash_size), Image.LANCZOS).getdata())
    except Exception:
        LOG.debug("Frame hash failed for %s", path.name, exc_info=True)
        return None
    average = sum(pixels) / len(pixels)
    bits = 0
    for pixel in pixels:
        bits = (bits << 1) | (1 if pixel >= average else 0)
    return bits


def dedupe_near_identical_frames(frames: list[Path]) -> list[Path]:
    """Drop frames that are visually near-identical to the most recently kept
    frame, comparing consecutive frames only (O(n), preserves order).

    A static or talking-head stretch of video samples several frames that
    carry no extra evidence over the one before it, yet each costs the same
    image tokens in the extraction call as a frame showing a new scene or a
    storefront sign. If Pillow is unavailable or a frame fails to decode,
    that frame is kept as-is -- this only ever removes frames, never risks
    dropping evidence the caller can't otherwise recover.
    """
    if len(frames) <= 2:
        return frames
    kept = [frames[0]]
    kept_hash = _average_hash(frames[0])
    for frame in frames[1:]:
        frame_hash = _average_hash(frame)
        if kept_hash is None or frame_hash is None:
            kept.append(frame)
            kept_hash = frame_hash
            continue
        distance = bin(kept_hash ^ frame_hash).count("1")
        if distance <= _FRAME_DEDUP_HAMMING_THRESHOLD:
            continue
        kept.append(frame)
        kept_hash = frame_hash
    return kept


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _frame_timestamps(
    *,
    max_frames: int,
    interval_seconds: float,
    duration_seconds: float | None,
) -> list[float]:
    if max_frames <= 0:
        return []

    start_second = 1.0
    if duration_seconds is not None and duration_seconds <= start_second:
        return [max(0.0, duration_seconds / 2)]

    timestamps: list[float] = []
    for index in range(max_frames):
        timestamp = start_second + (index * interval_seconds)
        if duration_seconds is not None:
            if timestamp > duration_seconds:
                break
            if timestamp == duration_seconds:
                timestamp = max(start_second, duration_seconds - 0.05)
        timestamps.append(timestamp)
    return timestamps


def compress_image(input_path: Path, output_path: Path, image_max_width: int, image_jpeg_quality: int) -> Path | None:
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"scale='min({image_max_width},iw)':-2",
                "-q:v",
                str(image_jpeg_quality),
                str(output_path),
            ],
            timeout=60,
        )
        if output_path.exists() and output_path.stat().st_size > 512:
            return output_path
    except Exception:
        LOG.debug("Image compression failed: input=%s", input_path, exc_info=True)
        return None


def _canonical_tiktok_post_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host != "tiktok.com" and not host.endswith(".tiktok.com"):
        return None
    post_match = re.match(r"^(/@[^/]+/(?:photo|video)/\d+)(?:/)?$", parsed.path)
    if not post_match:
        return None
    return f"https://www.tiktok.com{post_match.group(1)}"


def resolve_social_url(url: str) -> str:
    """Resolve trusted social short links and remove tracking parameters from TikTok post URLs."""
    candidate = url.strip()
    canonical = _canonical_tiktok_post_url(candidate)
    if canonical:
        return canonical
    try:
        host = (urlparse(candidate).hostname or "").lower()
    except ValueError:
        return candidate
    if host not in {"vt.tiktok.com", "vm.tiktok.com"}:
        return candidate
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    last_error: Exception | None = None
    for request_method, extra_kwargs in ((requests.head, {}), (requests.get, {"stream": True})):
        response = None
        try:
            response = request_method(
                candidate,
                allow_redirects=True,
                timeout=10,
                headers=headers,
                **extra_kwargs,
            )
            response.raise_for_status()
            canonical = _canonical_tiktok_post_url(response.url)
            if canonical:
                LOG.info("Resolved TikTok short URL: source=%s canonical=%s", candidate, canonical)
                return canonical
            last_error = RuntimeError(f"unsupported redirect target {response.url}")
        except Exception as exc:
            last_error = exc
        finally:
            if response is not None:
                response.close()
    LOG.warning("Could not resolve TikTok short URL: url=%s error=%s", candidate, last_error)
    return candidate


def is_carousel_candidate_url(url: str) -> bool:
    candidate = resolve_social_url(url)
    lowered = candidate.lower()
    if "tiktok.com" in lowered:
        return "/photo/" in urlparse(candidate).path.lower()
    if "instagram.com" in lowered or "instagr.am" in lowered:
        return "/p/" in lowered
    return False


def download_tiktok_carousel_with_browser(url: str, raw_dir: Path) -> list[Path]:
    try:
        match = re.search(r'/(?:photo|video)/(\d+)', url)
        if not match:
            response = requests.get("https://www.tiktok.com/oembed", params={"url": url}, timeout=30)
            response.raise_for_status()
            html = str(response.json().get("html") or "")
            match = re.search(r'data-video-id="(\d+)"', html)
        if not match:
            return []
        video_id = match.group(1)

        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait

        options = Options()
        for argument in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=390,844"):
            options.add_argument(argument)
        acquire_browser_slot()
        driver = None
        image_urls: list[str] = []
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(f"https://www.tiktok.com/player/v1/{video_id}")
            WebDriverWait(driver, 30).until(lambda browser: browser.find_elements("tag name", "img"))
            unchanged = 0
            for _ in range(100):
                before = len(image_urls)
                for image in driver.find_elements("tag name", "img"):
                    image_url = image.get_attribute("src") or ""
                    if "photomode" in image_url and _is_trusted_tiktok_media_url(image_url) and image_url not in image_urls:
                        image_urls.append(image_url)
                unchanged = unchanged + 1 if len(image_urls) == before else 0
                if unchanged >= 2:
                    break
                buttons = driver.find_elements("css selector", 'button[aria-label="Next image"]')
                if not buttons or buttons[0].get_attribute("disabled") is not None:
                    break
                driver.execute_script("arguments[0].click()", buttons[0])
                try:
                    WebDriverWait(driver, 3, poll_frequency=0.2).until(
                        lambda browser: any(
                            (image.get_attribute("src") or "") not in image_urls
                            and "photomode" in (image.get_attribute("src") or "")
                            for image in browser.find_elements("tag name", "img")
                        )
                    )
                except Exception:
                    pass
        finally:
            try:
                if driver is not None:
                    driver.quit()
            finally:
                release_browser_slot()

        files: list[Path] = []
        for index, image_url in enumerate(image_urls, start=1):
            image_response = requests.get(image_url, headers={"Referer": "https://www.tiktok.com/"}, timeout=30)
            image_response.raise_for_status()
            if not (image_response.headers.get("Content-Type") or "").lower().startswith("image/"):
                continue
            image_path = raw_dir / f"browser_{index:03}.jpg"
            image_path.write_bytes(image_response.content)
            files.append(image_path)
        LOG.info("TikTok browser carousel fallback downloaded: images=%s", len(files))
        return files
    except Exception:
        LOG.warning("TikTok browser carousel fallback failed: url=%s", url, exc_info=True)
        return []


def download_carousel_images(
    url: str,
    out_dir: Path,
    image_max_width: int,
    image_jpeg_quality: int,
) -> list[Path]:
    """Download all images from a social photo post; never download its audio/video."""
    source_url = resolve_social_url(url)
    if not is_carousel_candidate_url(source_url):
        return []
    raw_dir = out_dir / "carousel_raw"
    processed_dir = out_dir / "carousel_images"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "gallery-dl",
        "--destination", str(raw_dir),
        "--filename", "{num:03}.{extension}",
        "--filter", "extension.lower() in ('jpg', 'jpeg', 'png', 'webp', 'avif')",
        "--option", "extractor.tiktok.videos=false",
        "--option", "extractor.tiktok.audio=false",
        "--option", "extractor.instagram.videos=false",
        "--option", "extractor.tiktok.photos=true",
    ]
    cookie_path = Path(SOCIAL_YTDLP_COOKIE_FILE) if SOCIAL_YTDLP_COOKIE_FILE else None
    if cookie_path and cookie_path.is_file():
        command.extend(["--cookies", str(cookie_path)])
    command.append(source_url)
    try:
        result = subprocess.run(
            command,
            check=False,
            timeout=90,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        LOG.warning("gallery-dl is unavailable; trying the browser carousel fallback")
        result = subprocess.CompletedProcess(command, 127, stdout="", stderr="gallery-dl is unavailable")
    except subprocess.TimeoutExpired:
        LOG.warning("gallery-dl carousel detection timed out; trying the browser fallback: url=%s", source_url)
        result = subprocess.CompletedProcess(command, 124, stdout="", stderr="gallery-dl timed out")

    supported = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
    raw_images = sorted(
        (path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in supported),
        key=lambda path: str(path.relative_to(raw_dir)).lower(),
    )
    if len(raw_images) < 2 and "tiktok.com" in source_url.lower():
        raw_images = download_tiktok_carousel_with_browser(source_url, raw_dir)
    if len(raw_images) < 2:
        if result.returncode != 0:
            LOG.info("No carousel detected: gallery-dl exit=%s error=%s", result.returncode, result.stderr[-500:])
        if "/photo/" in urlparse(source_url).path.lower():
            raise RuntimeError(
                "SOCIAL_CAROUSEL_DOWNLOAD_BLOCKED: "
                "TikTok photo carousel was detected but its images could not be downloaded"
            )
        return []

    images: list[Path] = []
    for index, raw_path in enumerate(raw_images, start=1):
        output_path = processed_dir / f"image_{index:03}.jpg"
        compressed = compress_image(raw_path, output_path, image_max_width, image_jpeg_quality)
        if compressed:
            images.append(compressed)
    if len(images) < 2 and "/photo/" in urlparse(source_url).path.lower():
        raise RuntimeError(
            "SOCIAL_CAROUSEL_DOWNLOAD_BLOCKED: TikTok carousel images were downloaded but could not be processed"
        )
    LOG.info("Social image carousel downloaded: source_images=%s processed_images=%s", len(raw_images), len(images))
    return images


def extract_carousel_metadata(url: str) -> dict[str, Any]:
    source_url = resolve_social_url(url)
    if "tiktok.com" in source_url.lower() and "/photo/" not in urlparse(source_url).path.lower():
        try:
            response = requests.get("https://www.tiktok.com/oembed", params={"url": source_url}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            html = str(payload.get("html") or "")
            canonical_match = re.search(r'cite="([^"]+)"', html)
            return {
                "extractor_key": "TikTok",
                "title": str(payload.get("title") or ""),
                "description": str(payload.get("title") or ""),
                "uploader": str(payload.get("author_name") or ""),
                "thumbnail": str(payload.get("thumbnail_url") or ""),
                "webpage_url": canonical_match.group(1) if canonical_match else source_url,
            }
        except Exception:
            LOG.warning("Could not fetch TikTok carousel metadata", exc_info=True)
    return {
        "extractor_key": "TikTok" if "tiktok.com" in source_url.lower() else "Instagram" if "instagram.com" in source_url.lower() else "Social",
        "title": "",
        "description": "",
        "webpage_url": source_url,
    }


def download_metadata_images(
    info: dict[str, Any],
    out_dir: Path,
    max_images: int,
    image_max_width: int,
    image_jpeg_quality: int,
) -> list[Path]:
    LOG.info("Downloading metadata images: max_images=%s", max_images)
    image_dir = out_dir / "metadata_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []

    for item in info.get("thumbnails") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    for entry in info.get("entries") or []:
        if isinstance(entry, dict):
            for item in entry.get("thumbnails") or []:
                if isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]))

    files: list[Path] = []
    seen: set[str] = set()
    headers = {"user-agent": "Mozilla/5.0"}
    for url in urls:
        if url in seen or len(files) >= max_images:
            continue
        seen.add(url)
        try:
            response = requests.get(url, headers=headers, timeout=24)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                continue
            suffix = mimetypes.guess_extension(content_type.split(";", 1)[0]) or ".jpg"
            raw_path = image_dir / f"raw_{len(files) + 1:02d}{suffix}"
            compressed_path = image_dir / f"image_{len(files) + 1:02d}.jpg"
            raw_path.write_bytes(response.content)
            compressed = compress_image(raw_path, compressed_path, image_max_width, image_jpeg_quality)
            if compressed:
                files.append(compressed)
        except Exception:
            LOG.debug("Metadata image download failed: url=%s", url, exc_info=True)
            continue
    LOG.info("Metadata images downloaded: count=%s", len(files))
    return files


def transcribe_audio(audio_path: Path) -> tuple[str, str]:
    if GROQ_API_KEY:
        LOG.info("Transcribing audio with Groq: model=%s", GROQ_WHISPER_MODEL)
        with audio_path.open("rb") as file:
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data={"model": GROQ_WHISPER_MODEL},
                files={"file": (audio_path.name, file, "audio/wav")},
                timeout=120,
            )
        response.raise_for_status()
        text = response.json().get("text", "").strip()
        LOG.info("Groq transcript complete: chars=%s", len(text))
        return text, f"groq:{GROQ_WHISPER_MODEL}"

    if OPENAI_API_KEY:
        LOG.info("Transcribing audio with OpenAI: model=%s", OPENAI_WHISPER_MODEL)
        with audio_path.open("rb") as file:
            response = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data={"model": OPENAI_WHISPER_MODEL},
                files={"file": (audio_path.name, file, "audio/wav")},
                timeout=120,
            )
        response.raise_for_status()
        text = response.json().get("text", "").strip()
        LOG.info("OpenAI transcript complete: chars=%s", len(text))
        return text, f"openai:{OPENAI_WHISPER_MODEL}"

    if LOCAL_WHISPER_ENABLED:
        return transcribe_audio_local(audio_path)

    LOG.warning("Audio transcription skipped: no GROQ_API_KEY, OPENAI_API_KEY, and LOCAL_WHISPER_ENABLED=false")
    return "", "skipped:no_transcriber"


def _get_local_whisper_model():
    global _LOCAL_WHISPER
    if _LOCAL_WHISPER is not None:
        return _LOCAL_WHISPER

    LOG.info(
        "Loading local Whisper model: model=%s device=%s compute_type=%s cache_dir=%s",
        LOCAL_WHISPER_MODEL,
        LOCAL_WHISPER_DEVICE,
        LOCAL_WHISPER_COMPUTE_TYPE,
        LOCAL_WHISPER_CACHE_DIR or "default",
    )
    from faster_whisper import WhisperModel

    kwargs: dict[str, Any] = {
        "device": LOCAL_WHISPER_DEVICE,
        "compute_type": LOCAL_WHISPER_COMPUTE_TYPE,
    }
    if LOCAL_WHISPER_CACHE_DIR:
        kwargs["download_root"] = LOCAL_WHISPER_CACHE_DIR

    _LOCAL_WHISPER = WhisperModel(LOCAL_WHISPER_MODEL, **kwargs)
    LOG.info("Local Whisper model loaded")
    return _LOCAL_WHISPER


def transcribe_audio_local(audio_path: Path) -> tuple[str, str]:
    try:
        model = _get_local_whisper_model()
        LOG.info("Transcribing audio with local faster-whisper: language=%s", LOCAL_WHISPER_LANGUAGE or "auto")
        kwargs: dict[str, Any] = {"vad_filter": True}
        if LOCAL_WHISPER_LANGUAGE:
            kwargs["language"] = LOCAL_WHISPER_LANGUAGE
        segments, info = model.transcribe(str(audio_path), **kwargs)
        text = " ".join(segment.text.strip() for segment in segments if segment.text).strip()
        LOG.info(
            "Local Whisper transcript complete: chars=%s detected_language=%s language_probability=%s",
            len(text),
            getattr(info, "language", ""),
            getattr(info, "language_probability", ""),
        )
        return text, f"local-faster-whisper:{LOCAL_WHISPER_MODEL}"
    except Exception:
        LOG.exception("Local Whisper transcription failed")
        return "", "failed:local-faster-whisper"


def _image_block(path: Path) -> dict[str, Any]:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
    }


def _image_data_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        repaired = _repair_json_text(cleaned)
        if repaired != cleaned:
            try:
                LOG.warning("AI JSON needed repair: %s", exc)
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        excerpt_start = max(0, exc.pos - 240)
        excerpt_end = min(len(cleaned), exc.pos + 240)
        LOG.error(
            "AI JSON parse failed at line=%s column=%s char=%s excerpt=%r",
            exc.lineno,
            exc.colno,
            exc.pos,
            cleaned[excerpt_start:excerpt_end],
        )
        raise


def _repair_json_text(text: str) -> str:
    repaired = text
    repaired = re.sub(r'([}\]"0-9])\s*\n\s*("[-A-Za-z0-9_]+":)', r"\1,\n\2", repaired)
    repaired = re.sub(r'\b(true|false|null)\s*\n\s*("[-A-Za-z0-9_]+":)', r"\1,\n\2", repaired)
    repaired = re.sub(r"}\s*\n\s*{", "},\n{", repaired)
    return repaired


def _claude_extraction_tool_schema() -> dict[str, Any]:
    return {
        "name": "return_social_location_extraction",
        "description": "Return the social video place extraction result.",
        "input_schema": {
            "type": "object",
            "additionalProperties": True,
            "required": ["is_relevant", "relevance_reason", "found", "needs_confirmation", "summary", "useful_summary", "general_guidance", "discarded_mentions", "contentType", "destination", "durationDays", "candidates"],
            "properties": {
                "is_relevant": {"type": "boolean"},
                "relevance_reason": {"type": "string"},
                "found": {"type": "boolean"},
                "needs_confirmation": {"type": "boolean"},
                "summary": {"type": "string"},
                "useful_summary": {"type": "string"},
                "general_guidance": {"type": "array", "items": {"type": "string"}},
                "contentType": {"type": "string", "enum": ["ITINERARY", "PLACE_LIST"]},
                "destination": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "city": {"type": ["string", "null"]},
                        "region": {"type": ["string", "null"]},
                        "country": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                    },
                },
                "durationDays": {"type": ["integer", "null"], "minimum": 1},
                "durationBasis": {"type": ["string", "null"]},
                "durationConfidence": {"type": ["number", "null"]},
                "discarded_mentions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["mention", "identity_status", "reason"],
                        "properties": {
                            "mention": {"type": "string"},
                            "identity_status": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": [
                            "candidateRef",
                            "sequence",
                            "dayHint",
                            "timeHint",
                            "optionGroupId",
                            "optionIndex",
                            "relation",
                            "name",
                            "query",
                            "description",
                            "useful_info",
                            "visit_guidance",
                            "city_hint",
                            "region_hint",
                            "country_hint",
                            "address_hint",
                            "search_context",
                            "aliases",
                            "place_type_hint",
                            "identity_status",
                            "mention_type",
                            "inference_used",
                            "uncertainty_reason",
                            "evidence",
                            "confidence",
                        ],
                        "properties": {
                            "candidateRef": {"type": "string"},
                            "sequence": {"type": "integer", "minimum": 1},
                            "dayHint": {"type": ["integer", "null"], "minimum": 1},
                            "timeHint": {"type": ["string", "null"]},
                            "optionGroupId": {"type": ["string", "null"]},
                            "optionIndex": {"type": ["integer", "null"], "minimum": 1},
                            "relation": {"type": ["string", "null"]},
                            "name": {"type": "string"},
                            "query": {"type": "string"},
                            "description": {"type": "string"},
                            "useful_info": {"type": "array", "items": {"type": "string"}},
                            "visit_guidance": {"type": "array", "items": {"type": "string"}},
                            "city_hint": {"type": ["string", "null"]},
                            "region_hint": {"type": ["string", "null"]},
                            "country_hint": {"type": ["string", "null"]},
                            "address_hint": {"type": ["string", "null"]},
                            "search_context": {"type": ["string", "null"]},
                            "aliases": {"type": "array", "items": {"type": "string"}},
                            "place_type_hint": {"type": ["string", "null"]},
                            "identity_status": {
                                "type": "string",
                                "enum": ["CONFIRMED", "LIKELY", "AMBIGUOUS"],
                            },
                            "mention_type": {
                                "type": "string",
                                "enum": [
                                    "EXPLICIT_AUDIO_NAME",
                                    "EXACT_VISIBLE_SIGN",
                                    "MULTIMODAL_CONFIRMED",
                                    "CONTEXTUAL_INFERENCE",
                                    "GENERIC_REFERENCE",
                                ],
                            },
                            "inference_used": {"type": "boolean"},
                            "uncertainty_reason": {"type": ["string", "null"]},
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                    "required": ["source", "quote", "frame_index", "supports_identity"],
                                    "properties": {
                                        "source": {
                                            "type": "string",
                                            "enum": ["audio", "frame", "slide", "caption_context"],
                                        },
                                        "quote": {"type": "string"},
                                        "frame_index": {"type": ["integer", "null"]},
                                        "supports_identity": {"type": "boolean"},
                                        "explanation": {"type": ["string", "null"]},
                                    },
                                },
                            },
                            "confidence": {"type": "number"},
                        },
                    },
                },
            },
        },
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalized_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold()).replace("đ", "d")
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def _normalized_enum(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _fuzzy_quote_supported(transcript: str, quote: str) -> bool:
    normalized_transcript = _normalized_match_text(transcript)
    normalized_quote = _normalized_match_text(quote)
    if not normalized_transcript or not normalized_quote:
        return False
    if f" {normalized_quote} " in f" {normalized_transcript} ":
        return True

    transcript_tokens = normalized_transcript.split()
    quote_tokens = normalized_quote.split()
    if len(quote_tokens) < 2:
        return False
    transcript_token_set = set(transcript_tokens)
    token_coverage = sum(token in transcript_token_set for token in quote_tokens) / len(quote_tokens)
    if token_coverage < 0.60:
        return False

    minimum_window = max(2, len(quote_tokens) - 2)
    maximum_window = min(len(transcript_tokens), len(quote_tokens) + 3)
    best_ratio = 0.0
    for window_size in range(minimum_window, maximum_window + 1):
        for start in range(0, len(transcript_tokens) - window_size + 1):
            window = " ".join(transcript_tokens[start:start + window_size])
            best_ratio = max(best_ratio, SequenceMatcher(None, normalized_quote, window).ratio())
    return token_coverage >= 0.80 or best_ratio >= 0.58


def _candidate_confidence(candidate: dict[str, Any]) -> float:
    try:
        confidence = float(candidate.get("confidence"))
    except (TypeError, ValueError):
        return 0.0
    if 1 < confidence <= 100:
        confidence /= 100
    return min(max(confidence, 0.0), 1.0)


def _is_generic_candidate_name(candidate: dict[str, Any]) -> bool:
    name = _normalized_match_text(candidate.get("name"))
    if not name:
        return True
    without_context = name
    for field in ("city_hint", "region_hint", "country_hint"):
        context = _normalized_match_text(candidate.get(field))
        if context:
            without_context = re.sub(
                rf"(?:^|\s){re.escape(context)}(?:\s|$)",
                " ",
                without_context,
            )
    return " ".join(without_context.split()) in _GENERIC_PLACE_NAMES


def _candidate_evidence_decision(
    candidate: dict[str, Any],
    *,
    transcript: str,
    frame_count: int | None,
) -> tuple[bool, str, str | None]:
    name = str(candidate.get("name") or "").strip()
    if not name:
        return False, "missing_name", None
    if _is_generic_candidate_name(candidate):
        return False, "generic_or_unnamed_place", None

    identity_status = _normalized_enum(candidate.get("identity_status"))
    if identity_status not in _AI_CONFIRMED_IDENTITY_STATUSES:
        return False, "ai_identity_not_confirmed", None

    mention_type = _normalized_enum(candidate.get("mention_type"))
    if mention_type not in _AI_SUPPORTED_MENTION_TYPES:
        return False, "ai_marked_inferred_or_generic", None
    if _as_bool(candidate.get("inference_used")):
        return False, "ai_used_identity_inference", None

    confidence = _candidate_confidence(candidate)
    if confidence < AI_GUARDRAIL_MIN_CONFIDENCE:
        return False, "confidence_below_threshold", None

    valid_audio = False
    valid_visual = False
    for evidence in candidate.get("evidence") or []:
        if not isinstance(evidence, dict) or not _as_bool(evidence.get("supports_identity")):
            continue
        source = _normalized_enum(evidence.get("source"))
        quote = str(evidence.get("quote") or "").strip()
        if source == "audio" and quote and _fuzzy_quote_supported(transcript, quote):
            valid_audio = True
        elif source in {"frame", "slide"} and quote:
            try:
                frame_index = int(evidence.get("frame_index"))
            except (TypeError, ValueError):
                continue
            if frame_index > 0 and (frame_count is None or frame_index <= frame_count):
                valid_visual = True

    if mention_type == "explicit_audio_name" and valid_audio:
        return True, "accepted", "ai_confirmed_audio"
    if mention_type == "exact_visible_sign" and valid_visual:
        return True, "accepted", "ai_confirmed_visual"
    if mention_type == "multimodal_confirmed" and valid_audio and valid_visual:
        return True, "accepted", "ai_confirmed_multimodal"
    return False, "evidence_guardrail_failed", None


def retain_certain_candidates(
    extraction: dict[str, Any],
    *,
    transcript: str,
    frame_count: int | None = None,
) -> dict[str, Any]:
    raw_candidates = extraction.get("candidates") or []
    accepted: list[dict[str, Any]] = []
    rejected_reasons: dict[str, int] = {}
    evidence_verified_count = 0
    unresolved_count = 0
    used_candidate_refs: set[str] = set()

    for position, candidate in enumerate(raw_candidates, start=1):
        if not isinstance(candidate, dict):
            rejected_reasons["invalid_candidate"] = rejected_reasons.get("invalid_candidate", 0) + 1
            continue
        name = str(candidate.get("name") or "").strip()
        if not name:
            rejected_reasons["missing_name"] = rejected_reasons.get("missing_name", 0) + 1
            continue
        # Generic references such as "this cafe" are not a spot identity. They stay in the
        # explanatory discarded list; named but unresolved points are retained below.
        if _is_generic_candidate_name(candidate):
            rejected_reasons["generic_or_unnamed_place"] = rejected_reasons.get("generic_or_unnamed_place", 0) + 1
            continue
        allowed, reason, evidence_mode = _candidate_evidence_decision(
            candidate,
            transcript=transcript,
            frame_count=frame_count,
        )
        item = dict(candidate)
        item["confidence"] = _candidate_confidence(item)
        item["candidateRef"] = _unique_candidate_ref(
            item.get("candidateRef"), position, used_candidate_refs
        )
        item["sequence"] = position
        item["verification"] = {
            "status": "EVIDENCE_VERIFIED" if allowed else "UNRESOLVED",
            "policy": AI_CANDIDATE_POLICY,
            "evidenceMode": evidence_mode if allowed else None,
            "reason": None if allowed else reason,
        }
        item["resolutionStatus"] = "EVIDENCE_VERIFIED" if allowed else "UNRESOLVED"
        if allowed:
            evidence_verified_count += 1
        else:
            unresolved_count += 1
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            LOG.info(
                "Retaining unresolved named social candidate: name=%r reason=%s confidence=%.3f",
                item.get("name"),
                reason,
                _candidate_confidence(item),
            )
        accepted.append(item)

    extraction["candidates"] = accepted
    extraction["found"] = bool(accepted)
    extraction["needs_confirmation"] = False
    extraction["candidateValidation"] = {
        "policy": AI_CANDIDATE_POLICY,
        "inputCount": len(raw_candidates),
        "retainedCount": len(accepted),
        "evidenceVerifiedCount": evidence_verified_count,
        "unresolvedCount": unresolved_count,
        "rejectedCount": sum(rejected_reasons.get(key, 0) for key in ("invalid_candidate", "missing_name", "generic_or_unnamed_place")),
        "rejectedReasonCounts": rejected_reasons,
    }
    return extraction


def _unique_candidate_ref(
    value: Any,
    position: int,
    used: set[str],
) -> str:
    """Return a deterministic, storage-safe ref for one source position.

    The database uses candidateRef as the idempotent key for a saved spot. LLMs occasionally
    repeat a ref for two alternatives, so preserve the first ref and suffix later duplicates
    instead of allowing one malformed response to abort the whole batch.
    """
    base = str(value or "").strip() or f"social-{position:04d}"
    candidate_ref = base[:128]
    if candidate_ref not in used:
        used.add(candidate_ref)
        return candidate_ref

    suffix = f"-{position:04d}"
    candidate_ref = f"{base[:128 - len(suffix)]}{suffix}"
    counter = 2
    while candidate_ref in used:
        suffix = f"-{position:04d}-{counter}"
        candidate_ref = f"{base[:128 - len(suffix)]}{suffix}"
        counter += 1
    used.add(candidate_ref)
    return candidate_ref


def _normalize_extraction_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    content_type = _normalized_enum(parsed.get("contentType")).upper()
    parsed["contentType"] = content_type if content_type in {"ITINERARY", "PLACE_LIST"} else "PLACE_LIST"
    destination = parsed.get("destination")
    if not isinstance(destination, dict):
        destination = {}
    parsed["destination"] = {
        key: (str(destination.get(key)).strip() if destination.get(key) is not None and str(destination.get(key)).strip() else None)
        for key in ("name", "city", "region", "country", "address")
    }
    try:
        duration_days = int(parsed.get("durationDays"))
    except (TypeError, ValueError):
        duration_days = 0
    parsed["durationDays"] = duration_days if duration_days > 0 else None
    parsed["durationBasis"] = str(parsed.get("durationBasis") or "").strip() or None
    try:
        duration_confidence = float(parsed.get("durationConfidence"))
    except (TypeError, ValueError):
        duration_confidence = None
    parsed["durationConfidence"] = min(max(duration_confidence, 0.0), 1.0) if duration_confidence is not None else None
    parsed["summary"] = str(parsed.get("summary") or "").strip()
    parsed["useful_summary"] = str(parsed.get("useful_summary") or "").strip()
    parsed["general_guidance"] = _string_list(parsed.get("general_guidance"))
    parsed["discarded_mentions"] = [
        item for item in (parsed.get("discarded_mentions") or []) if isinstance(item, dict)
    ]

    candidates = parsed.get("candidates") or []
    normalized_candidates = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate["name"] = str(candidate.get("name") or "").strip()
        candidate["query"] = str(candidate.get("query") or candidate.get("name") or "").strip()
        candidate["candidateRef"] = str(candidate.get("candidateRef") or "").strip() or None
        try:
            sequence = int(candidate.get("sequence"))
        except (TypeError, ValueError):
            sequence = len(normalized_candidates) + 1
        candidate["sequence"] = max(1, sequence)
        for field in ("dayHint", "optionIndex"):
            try:
                value = int(candidate.get(field))
            except (TypeError, ValueError):
                value = 0
            candidate[field] = value if value > 0 else None
        for field in ("timeHint", "optionGroupId", "relation"):
            value = candidate.get(field)
            candidate[field] = str(value).strip() if value is not None and str(value).strip() else None
        candidate["useful_info"] = _string_list(candidate.get("useful_info"))
        candidate["visit_guidance"] = _string_list(candidate.get("visit_guidance"))
        candidate["aliases"] = _string_list(candidate.get("aliases"))
        candidate["identity_status"] = _normalized_enum(candidate.get("identity_status")).upper()
        candidate["mention_type"] = _normalized_enum(candidate.get("mention_type")).upper()
        candidate["inference_used"] = _as_bool(candidate.get("inference_used"))
        for field in (
            "city_hint",
            "region_hint",
            "country_hint",
            "address_hint",
            "search_context",
            "place_type_hint",
            "uncertainty_reason",
        ):
            value = candidate.get(field)
            candidate[field] = str(value).strip() if value is not None and str(value).strip() else None
        normalized_evidence: list[dict[str, Any]] = []
        for evidence in candidate.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            try:
                frame_index = int(evidence.get("frame_index"))
            except (TypeError, ValueError):
                frame_index = None
            normalized_evidence.append(
                {
                    **evidence,
                    "source": _normalized_enum(evidence.get("source")),
                    "quote": str(evidence.get("quote") or "").strip(),
                    "frame_index": frame_index,
                    "supports_identity": _as_bool(evidence.get("supports_identity")),
                }
            )
        candidate["evidence"] = normalized_evidence
        candidate["evidence_sources"] = list(dict.fromkeys(
            evidence["source"] for evidence in normalized_evidence if evidence["source"]
        ))
        candidate["evidence_text"] = [
            evidence["quote"] for evidence in normalized_evidence if evidence["quote"]
        ]
        normalized_candidates.append(candidate)

    parsed["candidates"] = normalized_candidates
    return parsed


def _safe_slug(value: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return (slug or "social-location")[:max_length]


def _default_debug_dir(url: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("debug") / "social-location" / f"{stamp}_{_safe_slug(urlparse_hint(url))}"


def urlparse_hint(url: str) -> str:
    match = re.search(r"/(?:video|reel|p)/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    return re.sub(r"^https?://", "", url)


def save_dry_run_artifacts(
    *,
    output_dir: Path,
    url: str,
    language: str,
    metadata: dict[str, Any],
    transcript: str,
    transcript_provider: str,
    image_paths: list[Path],
    audio_path: Path | None,
    media_path: Path | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "images"
    frames_dir.mkdir(parents=True, exist_ok=True)

    copied_images: list[str] = []
    for index, image_path in enumerate(image_paths, start=1):
        suffix = image_path.suffix or ".jpg"
        target = frames_dir / f"image_{index:02d}{suffix}"
        shutil.copy2(image_path, target)
        copied_images.append(str(target.resolve()))

    transcript_path = output_dir / "transcript.txt"
    transcript_path.write_text(transcript or "", encoding="utf-8")

    caption_path = output_dir / "caption.txt"
    caption_path.write_text(str(metadata.get("caption") or ""), encoding="utf-8")

    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    request_path = output_dir / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "url": url,
                "language": language,
                "note": "Caption/title/tags are context only; audio transcript and images are primary AI inputs.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ai_input_path = output_dir / "ai_input_text.txt"
    ai_input_path.write_text(
        "\n".join(
            [
                "SOCIAL LOCATION EXTRACTION INPUT",
                "",
                f"URL: {url}",
                f"OUTPUT_LANGUAGE: {language}",
                "",
                "TRUST RULES:",
                "- Audio transcript and visible text/signage in images are primary evidence.",
                "- Caption/title/tags are context only, not trusted by themselves.",
                "",
                "METADATA_CONTEXT_ONLY_NOT_TRUSTED:",
                json.dumps(metadata, ensure_ascii=False, indent=2),
                "",
                "AUDIO_TRANSCRIPT_PRIMARY_EVIDENCE:",
                transcript or "",
                "",
                "IMAGE_INPUTS:",
                *[f"- {path}" for path in copied_images],
                "",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "url": url,
        "language": language,
        "outputDir": str(output_dir.resolve()),
        "transcriptPath": str(transcript_path.resolve()),
        "captionPath": str(caption_path.resolve()),
        "aiInputTextPath": str(ai_input_path.resolve()),
        "metadataPath": str(metadata_path.resolve()),
        "requestPath": str(request_path.resolve()),
        "imageCount": len(copied_images),
        "imagePaths": copied_images,
        "transcriptProvider": transcript_provider,
        "transcriptChars": len(transcript or ""),
        "audioTempPath": str(audio_path.resolve()) if audio_path else None,
        "mediaTempPath": str(media_path.resolve()) if media_path else None,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Dry-run artifacts saved: output_dir=%s images=%s transcript_chars=%s", output_dir, len(copied_images), len(transcript or ""))
    return {**manifest, "manifestPath": str(manifest_path.resolve())}


def _resolve_ai_credentials(
    ai_provider: str | None,
    ai_model: str | None,
    ai_base_url: str | None,
) -> tuple[str, str, str, str]:
    """Shared provider/model/base_url/api_key resolution for every AI call
    this module makes (full extraction and the cheap topic pre-filter alike),
    so the two never drift onto different providers by accident."""
    provider = (ai_provider or AI_PROVIDER or "DEEPSEEK").strip().upper()
    provider = "ANTHROPIC" if provider == "CLAUDE" else provider
    if provider == "ANTHROPIC":
        model = (ai_model or AI_MODEL or CLAUDE_MODEL).strip()
        base_url = (ai_base_url or AI_BASE_URL).strip()
        api_key = ANTHROPIC_API_KEY
    elif provider == "DEEPSEEK":
        model = (ai_model or AI_MODEL or "deepseek-v4-flash").strip()
        base_url = (ai_base_url or AI_BASE_URL or DEEPSEEK_BASE_URL).strip()
        api_key = DEEPSEEK_API_KEY
    elif provider == "OPENAI":
        model = (ai_model or AI_MODEL or OPENAI_CHAT_MODEL).strip()
        base_url = (ai_base_url or AI_BASE_URL or "https://api.openai.com/v1").strip()
        api_key = OPENAI_API_KEY
    elif provider == "OPENAI_COMPATIBLE":
        model = (ai_model or AI_MODEL).strip()
        base_url = (ai_base_url or AI_BASE_URL).strip()
        api_key = AI_API_KEY
    else:
        raise ValueError(f"Unsupported AI provider: {provider}")

    if not api_key:
        raise RuntimeError(f"API key is required for AI provider {provider}")
    if not model:
        raise RuntimeError(f"AI model is required for provider {provider}")
    if provider != "ANTHROPIC" and not base_url:
        raise RuntimeError(f"AI base URL is required for provider {provider}")
    return provider, model, base_url, api_key


_PREFILTER_MAX_TRANSCRIPT_CHARS = 4000


def prefilter_topic_relevance(
    *,
    metadata: dict[str, Any],
    transcript: str,
    language: str,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    ai_base_url: str | None = None,
) -> dict[str, Any] | None:
    """Cheap text-only triage, called before frame extraction/the full vision
    call. It only ever answers "clearly unrelated" or "not sure" -- never
    "relevant" -- so a false positive here can only cost an unnecessary full
    pipeline run, never a wrongly dropped real place video. Any failure
    (network, parsing, missing signal) returns None and the caller falls
    through to the full pipeline unchanged.
    """
    caption = str(metadata.get("caption") or "").strip()
    title = str(metadata.get("title") or "").strip()
    tags = metadata.get("tags") or []
    transcript_excerpt = (transcript or "").strip()[:_PREFILTER_MAX_TRANSCRIPT_CHARS]

    # Nothing to triage on -- e.g. a silent video whose only evidence lives in
    # on-screen text/signage. Only the full vision call can judge that.
    if not (caption or title or tags or transcript_excerpt):
        return None

    try:
        provider, model, base_url, api_key = _resolve_ai_credentials(
            ai_provider, ai_model, ai_base_url
        )
    except Exception:
        LOG.warning("Topic pre-filter skipped: could not resolve AI credentials", exc_info=True)
        return None

    prompt = f"""
You are a fast triage filter in front of a travel/food place-extraction pipeline.
Decide only whether this social video is CLEARLY UNRELATED to any real-world travel destination, place, or food/restaurant topic.
Default to "not unrelated" whenever there is any doubt, any hint of a location/itinerary/food/venue, or when the text below is too sparse to judge confidently -- a later step that also looks at the video frames will make the careful call.
Only answer clearly_unrelated=true for unambiguous cases: pure dance/lipsync/comedy trends, product ads or unboxings unrelated to any venue, tutorials, personal vlogs with no place/food subject, gaming, or news/politics with no travel or food content.

Text (caption/title/tags/transcript excerpt, not necessarily reliable):
{json.dumps({"caption": caption, "title": title, "tags": tags, "transcript_excerpt": transcript_excerpt}, ensure_ascii=False)}

Return only this JSON object, no markdown, no commentary:
{{"clearly_unrelated": boolean, "reason": string}}
""".strip()

    try:
        if provider == "ANTHROPIC":
            client_kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            message = Anthropic(**client_kwargs).messages.create(
                model=model,
                max_tokens=120,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            output = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
            parsed = _extract_json(output)
            usage = getattr(message, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None) if usage else None
            output_tokens = getattr(usage, "output_tokens", None) if usage else None
        else:
            request_payload: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
            if provider == "OPENAI":
                request_payload["max_completion_tokens"] = 120
            else:
                request_payload["max_tokens"] = 120
            response = requests.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request_payload,
                timeout=min(30, AI_REQUEST_TIMEOUT_SECONDS),
            )
            if not response.ok:
                LOG.warning("Topic pre-filter HTTP %s: %s", response.status_code, response.text[:300])
                return None
            response_payload = response.json()
            output = response_payload["choices"][0]["message"]["content"]
            parsed = _extract_json(output or "")
            usage = response_payload.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
    except Exception:
        LOG.warning("Topic pre-filter call failed; falling through to full pipeline", exc_info=True)
        return None

    if not isinstance(parsed, dict) or not _as_bool(parsed.get("clearly_unrelated")):
        return None

    reason = str(parsed.get("reason") or "Content has no travel, food, or place subject").strip()
    LOG.info("Topic pre-filter rejected video before frame extraction: reason=%s", reason)
    return {
        "reason": reason,
        "provider": provider,
        "model": model,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": (input_tokens + output_tokens)
            if isinstance(input_tokens, int) and isinstance(output_tokens, int)
            else None,
        },
    }


def extract_candidates_with_ai(
    *,
    metadata: dict[str, Any],
    transcript: str,
    transcript_provider: str,
    image_paths: list[Path],
    language: str,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    ai_base_url: str | None = None,
) -> dict[str, Any]:
    provider, model, base_url, api_key = _resolve_ai_credentials(ai_provider, ai_model, ai_base_url)

    LOG.info(
        "Extracting candidates with AI: provider=%s model=%s transcript_chars=%s images=%s language=%s candidate_policy=%s",
        provider,
        model,
        len(transcript),
        len(image_paths),
        language,
        AI_CANDIDATE_POLICY,
    )
    context = {
        "metadata_context_only_not_trusted": metadata,
        "transcript_primary_evidence": transcript,
        "transcript_provider": transcript_provider,
        "image_count": len(image_paths),
        "response_language": language,
    }
    
    max_tokens = 8000
    
    system_prompt = f"""
You are a meticulous social-video place extraction engine for a travel application.
Analyze the complete input before answering. Return one valid JSON object only.
You are the primary identity judge; do not rely on downstream literal string matching to make the decision for you.
Reason silently in two passes: first inventory every possible place mention, then challenge each identity using all audio, visual, route, and surrounding context.
Return every distinct named place or concrete visit point that the video presents as a useful travel spot. Keep uncertain identity in the candidate with an explicit unresolved verification state; do not silently discard a named mention because Maps cannot resolve it.
Allow normal transcription errors, accents, abbreviations, aliases, and natural paraphrases when the combined evidence still establishes the identity.
Never turn an unnamed activity, venue category, route instruction, or nearby landmark into a guessed business or POI. A named transport leg or named area may remain an activity when it is not a resolvable place.
For every candidate, recap what this specific video says or shows about that place; do not replace the recap with a generic encyclopedia description.
Each candidate query must be Google Maps-ready and include the exact name plus the most specific supported branch/address/district/city/region/country context.
Write user-facing descriptions and guidance in {language}, preserving proper names and quoted evidence in their original language where appropriate.
""".strip()

    prompt = f"""
Extract real-world place candidates from this TikTok/Instagram travel or food video.

Topic gate:
- Set is_relevant=true only when the video is materially about travel, a destination, a real-world place, food/restaurant/cafe review, or practical visit guidance.
- Set is_relevant=false for unrelated entertainment, personal content, sales, politics, gaming, generic memes, or content with no meaningful travel/food/place subject.
- When is_relevant=false, explain briefly in relevance_reason, set found=false, and return candidates=[].

Trust rules:
- Audio transcript is high-trust evidence but may contain recognition errors, missing accents, or small word substitutions. Judge semantic support, not literal equality alone.
- Images/frames may establish identity through readable storefront signs, labels, maps, or titles. Distinguish real signage from creator overlays and unrelated UI.
- Do not let generic scenery, food photos, interiors, or weak visual guesses override a specific audio mention.
- Do not extract unnamed references such as "quán cafe này", "quán bánh căn", "đi ăn", "đi ngắm biển", or equivalent generic phrases.
- A route/access/support venue near a named destination is not a separate candidate unless its own exact proper name is directly evidenced.
- Never convert a generic category or activity into a likely Google Maps result, even when one result seems geographically plausible.
- If audio names a specific place and images are ambiguous, use your identity judgment; do not create extra candidates from the ambiguous imagery.
- If visual and audio identities conflict or a branch remains ambiguous, keep the named mention with uncertainty_reason and let downstream planning show it as an ACTIVITY; do not lose the source item.
- Caption/title/tags are context only. Never create a high-confidence candidate from caption alone.
- Never return a broad city, neighborhood, beach category, or other generic area unless its proper name is explicitly spoken or exactly visible.
- CONFIRMED means the evidence establishes one specific identity and no similarly plausible alternative remains. Confidence is calibrated judgment, not a substitute for evidence.
- EXPLICIT_AUDIO_NAME may tolerate minor transcript/OCR spelling differences when the spoken phrase semantically names the place.
- EXACT_VISIBLE_SIGN requires a readable identity-bearing sign/title and its 1-based supplied frame index.
- MULTIMODAL_CONFIRMED requires mutually supporting audio and visual identity evidence.
- CONTEXTUAL_INFERENCE and GENERIC_REFERENCE must never appear in candidates, regardless of confidence.
- Set inference_used=true whenever the proper name was guessed from activity, geography, a nearby POI, search knowledge, or outside knowledge. Such an item must be discarded.
- Every candidate must include at least one identity-supporting evidence entry. Quote the actual transcript phrase or visible text; do not manufacture a cleaner quote.
- If evidence is weak, multiple branches are possible, or the proper name is missing, record the mention and reason in discarded_mentions. Keep a named visit point in candidates when it is a distinct point the creator presents; mark its resolution as unresolved rather than dropping it.
- Write descriptive user-facing fields in this language: {language}.
- Keep place names, addresses, and quoted visible/sign text in their original language when appropriate.
- Besides identifying places, summarize useful trip/food/context information from the video.
- Put general useful information that is not tied to one specific place in useful_summary/general_guidance.
- Put place-specific useful information inside that candidate only.
- Candidate description must be a concise recap of what this video says or shows about that exact place. If the video only names it, say only that; never fill the recap with outside knowledge.
- Do not invent prices, opening hours, transport details, reservation requirements, or tips unless supported by audio, visible text, or reliable context in the input.
- Inspect the entire transcript and every supplied frame/slide before deciding the candidate list.
- Extract every distinct, directly evidenced real-world place. Never merge multiple explicitly named list items, branches, restaurants, attractions, or stops into one candidate.
- Treat listicle language, numbered slides, route stops, and phrases such as "N places" as strong evidence that multiple candidates must be enumerated individually.
- Preserve the order in which points appear in the video as the highest-priority signal. Use explicit day/time only as additional metadata; do not reorder for popularity or map score.
- When the video says "A or B", "choose one", "option 1/2", or presents alternatives at the same moment, create one candidate per option. Give them the same optionGroupId, increasing optionIndex, and a relation such as "OPTION" or "SAME_TIME". Never merge options into one candidate or hide one in a note.
- Set contentType to ITINERARY for a route, multi-day plan, or ordered visit sequence; set PLACE_LIST for a list of spots without a meaningful schedule. For PLACE_LIST, leave dayHint/timeHint null and do not invent a schedule.
- For ITINERARY, always return durationDays. If the video does not state duration, decide a reasonable duration automatically from its route, sequence, and spot density; set durationBasis and durationConfidence. This is autonomous and must not request human confirmation.
- A place may be supported by evidence spread across nearby slides/frames and audio; use the full-video context to disambiguate it while preserving the evidence for that individual place.
- Build query as a Google Maps-ready search phrase: exact place/business name plus the most specific supported address, district, city/region, and country context. Do not use a generic city alone when a specific POI is present.
- Put concise disambiguating context from the video in search_context (branch, neighborhood, landmark, route, cuisine, or nearby place). Do not invent context.
- There is no candidate-count quota. Return all distinct named spots supported by the video, including unresolved named spots; never truncate because there are many.
- Return only one valid JSON object matching the requested structure. Do not wrap the JSON in markdown or add commentary.

Return this schema:
{{
  "is_relevant": boolean,
  "relevance_reason": string,
  "found": boolean,
  "needs_confirmation": boolean,
  "summary": string,                 // in {language}
  "useful_summary": string,          // in {language}, helpful non-place-specific information from the video
  "general_guidance": [string],      // in {language}, practical tips/instructions not tied to a single place
  "discarded_mentions": [
    {{
      "mention": string,
      "identity_status": "LIKELY"|"AMBIGUOUS",
      "reason": string
    }}
  ],
  "contentType": "ITINERARY"|"PLACE_LIST",
  "destination": {{"name": string|null, "city": string|null, "region": string|null, "country": string|null, "address": string|null}},
  "durationDays": integer|null,       // required for ITINERARY; null for PLACE_LIST
  "durationBasis": string|null,       // explain the automatic duration decision
  "durationConfidence": number|null,
  "candidates": [
    {{
      "candidateRef": string,         // unique within this video, e.g. social-0001
      "sequence": integer,             // 1-based order in the video
      "dayHint": integer|null,
      "timeHint": string|null,
      "optionGroupId": string|null,
      "optionIndex": integer|null,
      "relation": string|null,
      "name": string,
      "query": string,
      "description": string,          // in {language}, concise recap of what this video says/shows about this place
      "useful_info": [string],        // in {language}, useful facts/details specifically about this place
      "visit_guidance": [string],     // in {language}, practical guidance specifically for this place
      "city_hint": string|null,
      "region_hint": string|null,
      "country_hint": string|null,
      "address_hint": string|null,
      "search_context": string|null,   // concise video context used to disambiguate Google Maps search
      "aliases": [string],
      "place_type_hint": string|null,
      "identity_status": "CONFIRMED"|"LIKELY"|"AMBIGUOUS",
      "mention_type": "EXPLICIT_AUDIO_NAME"|"EXACT_VISIBLE_SIGN"|"MULTIMODAL_CONFIRMED"|"CONTEXTUAL_INFERENCE"|"GENERIC_REFERENCE",
      "inference_used": boolean,
      "uncertainty_reason": string|null,
      "evidence": [
        {{
          "source": "audio"|"frame"|"slide"|"caption_context",
          "quote": string,
          "frame_index": integer|null,
          "supports_identity": boolean,
          "explanation": string|null
        }}
      ],
      "confidence": number
    }}
  ]
}}

Context:
{json.dumps(context, ensure_ascii=False)}

Return all named candidates in source order. found is true when at least one distinct named point exists. If the media contains no useful point, return an empty candidates list and let the downstream mode decide whether a place list can be shown.
"""
    if provider == "ANTHROPIC":
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame_index, path in enumerate(image_paths, start=1):
            content.append({"type": "text", "text": f"SUPPLIED_FRAME_INDEX: {frame_index}"})
            content.append(_image_block(path))
        tool_schema = _claude_extraction_tool_schema()
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        message = Anthropic(**client_kwargs).messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system_prompt,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            messages=[{"role": "user", "content": content}],
        )
        parsed = None
        for block in message.content:
            if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == tool_schema["name"]:
                parsed = block.input
                break
        if parsed is None:
            output = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
            parsed = _extract_json(output)
    else:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame_index, path in enumerate(image_paths, start=1):
            user_content.append({"type": "text", "text": f"SUPPLIED_FRAME_INDEX: {frame_index}"})
            user_content.append({
                "type": "image_url",
                "image_url": {"url": _image_data_url(path), "detail": "high"},
            })
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        if provider == "OPENAI":
            # Current OpenAI reasoning models reject the legacy max_tokens field.
            request_payload["max_completion_tokens"] = max_tokens
        else:
            # Preserve the payload expected by DeepSeek and arbitrary OpenAI-compatible APIs.
            request_payload["temperature"] = 0
            request_payload["max_tokens"] = max_tokens
        response = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_payload,
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
        if not response.ok:
            raise RuntimeError(
                f"{provider} chat completion failed with HTTP {response.status_code}: {response.text[:1000]}"
            )
        response_payload = response.json()
        try:
            output = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"{provider} returned an unexpected response payload") from exc
        parsed = _extract_json(output or "")
    if not isinstance(parsed, dict):
        raise ValueError(f"AI extraction returned unexpected payload type: {type(parsed).__name__}")
    parsed = _normalize_extraction_payload(parsed)
    parsed["provider"] = provider
    parsed["model"] = model
    if provider == "ANTHROPIC":
        usage = getattr(message, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            parsed["usage"] = {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": (input_tokens + output_tokens)
                if isinstance(input_tokens, int) and isinstance(output_tokens, int)
                else None,
            }
    else:
        usage = response_payload.get("usage") or {}
        if usage:
            parsed["usage"] = {
                "inputTokens": usage.get("prompt_tokens"),
                "outputTokens": usage.get("completion_tokens"),
                "totalTokens": usage.get("total_tokens"),
            }
    LOG.info(
        "AI extraction complete: provider=%s model=%s found=%s candidates=%s needs_confirmation=%s",
        provider,
        model,
        parsed.get("found"),
        len(parsed["candidates"]),
        parsed.get("needs_confirmation"),
    )
    return parsed


def _contextual_map_query(candidate: dict[str, Any]) -> str:
    query = str(candidate.get("query") or candidate.get("name") or "").strip()
    if not query:
        return ""

    normalized_query = query.casefold()
    for field in ("address_hint", "city_hint", "region_hint", "country_hint", "search_context"):
        value = str(candidate.get(field) or "").strip()
        if not value or value.casefold() in normalized_query:
            continue
        query = f"{query} {value}".strip()
        normalized_query = query.casefold()
    return query


def _name_similarity(left: Any, right: Any) -> float:
    normalized_left = _normalized_match_text(left)
    normalized_right = _normalized_match_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0

    left_tokens = set(normalized_left.split())
    right_tokens = set(normalized_right.split())
    intersection = left_tokens.intersection(right_tokens)
    if not intersection:
        return SequenceMatcher(None, normalized_left, normalized_right).ratio() * 0.55
    precision = len(intersection) / len(right_tokens)
    recall = len(intersection) / len(left_tokens)
    token_f1 = 2 * precision * recall / (precision + recall)
    sequence_ratio = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    score = (0.60 * token_f1) + (0.40 * sequence_ratio)
    if min(len(left_tokens), len(right_tokens)) >= 2 and (
        left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)
    ):
        score = max(score, 0.90)
    return min(score, 1.0)


def _map_candidate_match_score(candidate: dict[str, Any], map_candidate: dict[str, Any]) -> float:
    identity_names = [candidate.get("name"), *_string_list(candidate.get("aliases"))]
    name_score = max(
        (_name_similarity(name, map_candidate.get("title")) for name in identity_names),
        default=0.0,
    )
    candidate_contexts = [
        candidate.get(field)
        for field in (
            "address_hint",
            "city_hint",
            "region_hint",
            "country_hint",
            "place_type_hint",
            "search_context",
        )
        if candidate.get(field)
    ]
    map_context = " ".join(
        str(map_candidate.get(field) or "")
        for field in ("address", "category", "title")
    )
    context_score = max(
        (_name_similarity(context, map_context) for context in candidate_contexts),
        default=0.0,
    )
    return min(1.0, (0.86 * name_score) + (0.14 * context_score)) if candidate_contexts else name_score


def _verified_map_search(
    candidate: dict[str, Any],
    map_search: Any,
) -> dict[str, Any] | None:
    if not isinstance(map_search, dict) or not bool(map_search.get("success")):
        return None
    matches: list[tuple[float, dict[str, Any]]] = []
    for map_candidate in map_search.get("candidates") or []:
        if not isinstance(map_candidate, dict):
            continue
        has_stable_identity = any(
            str(map_candidate.get(field) or "").strip()
            for field in ("placeId", "cid", "googleMapsLink", "resolvedUrl")
        )
        if not has_stable_identity:
            continue
        matches.append((_map_candidate_match_score(candidate, map_candidate), map_candidate))
    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    best_score, best_candidate = matches[0]
    if best_score < MAP_MIN_MATCH_SCORE:
        return None
    if len(matches) > 1:
        second_score, second_candidate = matches[1]
        different_identity = _map_identity_key(best_candidate) != _map_identity_key(second_candidate)
        if different_identity and best_score - second_score < MAP_AMBIGUITY_GAP:
            return None

    verified = dict(map_search)
    verified["candidates"] = [best_candidate]
    verified["count"] = 1
    verified["matchScore"] = round(best_score, 4)
    verified["resolutionPolicy"] = "WEIGHTED_NAME_CONTEXT_V2"
    return verified


def _map_identity_key(map_candidate: dict[str, Any]) -> str:
    for field in ("placeId", "cid", "googleMapsLink", "resolvedUrl"):
        value = str(map_candidate.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    return ""


def _resolved_candidate_key(candidate: dict[str, Any]) -> str:
    map_search = candidate.get("mapSearch")
    if not isinstance(map_search, dict):
        return ""
    map_candidates = map_search.get("candidates") or []
    first = map_candidates[0] if map_candidates and isinstance(map_candidates[0], dict) else None
    if not first:
        return ""
    place_id = str(first.get("placeId") or "").strip()
    if place_id:
        return f"place:{place_id}"
    cid = str(first.get("cid") or "").strip()
    if cid:
        return f"cid:{cid}"
    title = _normalized_match_text(first.get("title"))
    latitude = first.get("latitude")
    longitude = first.get("longitude")
    return f"fallback:{title}:{latitude}:{longitude}"


def enrich_with_map_search(
    candidates: list[dict[str, Any]],
    *,
    search_limit: int,
    headless: bool,
    progress_callback: Callable[[list[dict[str, Any]], int, int], None] | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    LOG.info("Starting map search enrichment: candidates=%s search_limit=%s", len(candidates), search_limit)
    for processed, candidate in enumerate(candidates, start=1):
        item = dict(candidate)
        query = _contextual_map_query(item)
        item["resolvedSearchQuery"] = query or None
        if not query:
            item["resolutionStatus"] = "UNRESOLVED"
            item["verification"] = {
                **dict(item.get("verification") or {}),
                "status": "UNRESOLVED",
                "policy": AI_CANDIDATE_POLICY,
                "reason": "missing_map_query",
            }
            LOG.info("Keeping candidate without a map query: name=%r", item.get("name"))
            enriched.append(item)
            if progress_callback:
                progress_callback(list(enriched), processed, len(candidates))
            continue
        try:
            LOG.info("Map search candidate: query=%r", query)
            raw_map_search = search_google_maps(query, limit=max(search_limit, 3), headless=headless)
            verified_map_search = _verified_map_search(item, raw_map_search)
            if verified_map_search is None:
                LOG.info(
                    "Keeping unresolved candidate because Maps resolution was weak or ambiguous: name=%r query=%r",
                    item.get("name"),
                    query,
                )
                item["resolutionStatus"] = "UNRESOLVED"
                item["verification"] = {
                    **dict(item.get("verification") or {}),
                    "status": "UNRESOLVED",
                    "policy": AI_CANDIDATE_POLICY,
                    "reason": "weak_or_ambiguous_map_match",
                }
            else:
                item["mapSearch"] = verified_map_search
                verification = dict(item.get("verification") or {})
                verification.update({
                    "status": "VERIFIED",
                    "mapMatch": "WEIGHTED_NAME_CONTEXT",
                    "mapMatchScore": item["mapSearch"].get("matchScore"),
                })
                item["verification"] = verification
                item["resolutionStatus"] = "RESOLVED"
                LOG.info(
                    "Map search complete: query=%r count=%s",
                    query,
                    item["mapSearch"].get("count"),
                )
        except Exception as exc:
            LOG.exception("Map search failed: query=%r", query)
            item["resolutionStatus"] = "UNRESOLVED"
            item["verification"] = {
                **dict(item.get("verification") or {}),
                "status": "UNRESOLVED",
                "policy": AI_CANDIDATE_POLICY,
                "reason": "map_search_failed",
            }
        # Do not deduplicate by resolved place. Repeated mentions and A/B alternatives are
        # separate source activities even when Maps returns the same POI.
        enriched.append(item)
        if progress_callback:
            progress_callback(list(enriched), processed, len(candidates))
    return enriched


def apply_map_validation(
    extraction: dict[str, Any],
    *,
    map_input_count: int,
    verified_candidates: list[dict[str, Any]],
) -> None:
    extraction["candidates"] = verified_candidates
    extraction["found"] = bool(verified_candidates)
    extraction["needs_confirmation"] = False
    validation = dict(extraction.get("candidateValidation") or {})
    validation.update(
        {
            "mapInputCount": map_input_count,
            "mapVerifiedCount": sum(1 for item in verified_candidates if item.get("resolutionStatus") == "RESOLVED"),
            "mapRejectedCount": 0,
            "unresolvedCount": sum(1 for item in verified_candidates if item.get("resolutionStatus") != "RESOLVED"),
        }
    )
    extraction["candidateValidation"] = validation


def prepare_social_media_inputs(
    *,
    url: str,
    temp_dir: Path,
    max_audio_seconds: int | None,
    max_duration_seconds: int,
    max_frames: int,
    frame_interval_seconds: int,
    image_max_width: int,
    image_jpeg_quality: int,
) -> dict[str, Any]:
    source_url = resolve_social_url(url)
    carousel_images = download_carousel_images(source_url, temp_dir, image_max_width, image_jpeg_quality)
    if len(carousel_images) >= 2:
        info = extract_carousel_metadata(source_url)
        metadata = _metadata_context(info)
        metadata["mediaType"] = "IMAGE_CAROUSEL"
        metadata["imageCount"] = len(carousel_images)
        LOG.info("Skipping carousel audio download and transcription: images=%s", len(carousel_images))
        return {
            "info": info,
            "metadata": metadata,
            "mediaPath": None,
            "audioPath": None,
            "transcript": "",
            "transcriptProvider": "skipped:image_carousel",
            "images": carousel_images,
            "rejection": None,
        }

    info, download_profile = extract_metadata(source_url)
    metadata = _metadata_context(info)
    metadata["mediaType"] = "VIDEO"
    duration = _positive_float(metadata.get("duration"))
    if duration is not None and duration > max_duration_seconds:
        return {
            "rejection": {
                "success": False,
                "rejectedStatus": "REJECTED_DURATION",
                "url": url,
                "metadata": metadata,
                "error": {
                    "code": "SOCIAL_VIDEO_DURATION_EXCEEDED",
                    "message": f"Video duration {int(duration)}s exceeds the {max_duration_seconds}s limit",
                    "videoDurationSeconds": int(duration),
                    "maxDurationSeconds": max_duration_seconds,
                },
            }
        }

    media_path = download_media(
        source_url,
        temp_dir,
        preferred_profile=download_profile,
        media_url=info.get("_embed_media_url"),
    )
    if duration is None:
        duration = probe_media_duration(media_path)
        if duration is not None:
            metadata["duration"] = duration
        if duration is None:
            return {
                "rejection": {
                    "success": False,
                    "url": url,
                    "metadata": metadata,
                    "error": {
                        "code": "SOCIAL_VIDEO_DURATION_UNAVAILABLE",
                        "message": "Could not verify video duration",
                    },
                }
            }
        if duration > max_duration_seconds:
            return {
                "rejection": {
                    "success": False,
                    "rejectedStatus": "REJECTED_DURATION",
                    "url": url,
                    "metadata": metadata,
                    "error": {
                        "code": "SOCIAL_VIDEO_DURATION_EXCEEDED",
                        "message": f"Video duration {int(duration)}s exceeds the {max_duration_seconds}s limit",
                        "videoDurationSeconds": int(duration),
                        "maxDurationSeconds": max_duration_seconds,
                    },
                }
            }

    audio_seconds = max_audio_seconds if max_audio_seconds is not None else metadata.get("duration") or 600
    audio_path = extract_audio(media_path, temp_dir, audio_seconds)
    transcript = ""
    transcript_provider = "skipped:no_audio"
    if audio_path:
        transcript, transcript_provider = transcribe_audio(audio_path)

    images = extract_frames(
        media_path,
        temp_dir,
        max_frames,
        frame_interval_seconds,
        image_max_width,
        image_jpeg_quality,
        metadata.get("duration"),
    )
    if len(images) < 2:
        LOG.info("Few frames extracted; trying metadata images: current_frames=%s", len(images))
        images += download_metadata_images(
            info,
            temp_dir,
            max_frames - len(images),
            image_max_width,
            image_jpeg_quality,
        )
    return {
        "info": info,
        "metadata": metadata,
        "mediaPath": media_path,
        "audioPath": audio_path,
        "transcript": transcript,
        "transcriptProvider": transcript_provider,
        "images": images,
        "rejection": None,
    }


def complete_carousel_extraction(
    *,
    url: str,
    language: str,
    metadata: dict[str, Any],
    images: list[Path],
    temp_dir: Path,
    dry_run: bool,
    debug_output_dir: str | None,
    image_max_width: int,
    image_jpeg_quality: int,
    include_map_search: bool,
    map_search_limit: int,
    headless: bool,
    keep_temp: bool,
    ai_provider: str | None,
    ai_model: str | None,
    ai_base_url: str | None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    transcript_provider = "skipped:image_carousel"
    if dry_run:
        output_dir = Path(debug_output_dir) if debug_output_dir else _default_debug_dir(url)
        artifacts = save_dry_run_artifacts(
            output_dir=output_dir,
            url=url,
            language=language,
            metadata=metadata,
            transcript="",
            transcript_provider=transcript_provider,
            image_paths=images,
            audio_path=None,
            media_path=None,
        )
        return {
            "success": True,
            "dryRun": True,
            "url": url,
            "language": language,
            "metadata": metadata,
            "media": {
                "downloaded": True,
                "mediaType": "IMAGE_CAROUSEL",
                "audioProvider": transcript_provider,
                "transcriptAvailable": False,
                "frameCount": len(images),
                "imageMaxWidth": image_max_width,
                "imageJpegQuality": image_jpeg_quality,
            },
            "artifacts": artifacts,
            "extraction": {
                "found": False,
                "needs_confirmation": True,
                "summary": "Dry run completed before AI extraction.",
                "candidates": [],
                "model": None,
            },
            "tempDir": str(temp_dir) if keep_temp else None,
        }

    extraction = extract_candidates_with_ai(
        metadata=metadata,
        transcript="",
        transcript_provider=transcript_provider,
        image_paths=images,
        language=language,
        ai_provider=ai_provider,
        ai_model=ai_model,
        ai_base_url=ai_base_url,
    )
    if not bool(extraction.get("is_relevant")):
        reason = str(extraction.get("relevance_reason") or "Post is not about travel, food, or a place review")
        return {
            "success": False,
            "rejectedStatus": "REJECTED_TOPIC",
            "url": url,
            "language": language,
            "metadata": metadata,
            "media": {
                "downloaded": True,
                "mediaType": "IMAGE_CAROUSEL",
                "audioProvider": transcript_provider,
                "transcriptAvailable": False,
                "frameCount": len(images),
            },
            "extraction": extraction,
            "error": {"code": "IRRELEVANT_SOCIAL_VIDEO", "message": reason},
        }
    extraction = retain_certain_candidates(extraction, transcript="", frame_count=len(images))
    candidates = extraction.get("candidates") or []
    if include_map_search and candidates:
        def publish_progress(enriched: list[dict[str, Any]], processed: int, total: int) -> None:
            if not progress_callback:
                return
            partial_extraction = dict(extraction)
            partial_extraction["candidates"] = enriched
            progress_callback({
                "success": True,
                "partial": True,
                "url": url,
                "language": language,
                "metadata": metadata,
                "media": {
                    "downloaded": True,
                    "mediaType": "IMAGE_CAROUSEL",
                    "audioProvider": transcript_provider,
                    "transcriptAvailable": False,
                    "frameCount": len(images),
                },
                "progress": {"processedCandidates": processed, "totalCandidates": total},
                "extraction": partial_extraction,
            })

        verified_candidates = enrich_with_map_search(
            candidates,
            search_limit=map_search_limit,
            headless=headless,
            progress_callback=publish_progress,
        )
        apply_map_validation(
            extraction,
            map_input_count=len(candidates),
            verified_candidates=verified_candidates,
        )
    return {
        "success": True,
        "url": url,
        "language": language,
        "metadata": metadata,
        "media": {
            "downloaded": True,
            "mediaType": "IMAGE_CAROUSEL",
            "audioProvider": transcript_provider,
            "transcriptAvailable": False,
            "frameCount": len(images),
            "imageMaxWidth": image_max_width,
            "imageJpegQuality": image_jpeg_quality,
        },
        "extraction": extraction,
        "tempDir": str(temp_dir) if keep_temp else None,
    }


def extract_social_location(
    url: str,
    *,
    language: str = "vi",
    dry_run: bool = False,
    debug_output_dir: str | None = None,
    max_audio_seconds: int | None = None,
    max_duration_seconds: int = 180,
    max_frames: int = 50,
    frame_interval_seconds: int = 3,
    image_max_width: int = 384,
    image_jpeg_quality: int = 10,
    include_map_search: bool = True,
    map_search_limit: int = 1,
    headless: bool = True,
    keep_temp: bool = False,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    ai_base_url: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="social_location_"))
    LOG.info(
        "Social extraction started: url=%s temp_dir=%s language=%s dry_run=%s max_audio_seconds=%s max_frames=%s frame_interval_seconds=%s image_max_width=%s jpeg_q=%s candidate_policy=%s include_map_search=%s",
        url,
        temp_dir,
        language,
        dry_run,
        max_audio_seconds or "unlimited",
        max_frames,
        frame_interval_seconds or "auto",
        image_max_width,
        image_jpeg_quality,
        AI_CANDIDATE_POLICY,
        include_map_search,
    )
    try:
        source_url = resolve_social_url(url)
        carousel_images = download_carousel_images(source_url, temp_dir, image_max_width, image_jpeg_quality)
        if len(carousel_images) >= 2:
            carousel_info = extract_carousel_metadata(source_url)
            carousel_metadata = _metadata_context(carousel_info)
            carousel_metadata["mediaType"] = "IMAGE_CAROUSEL"
            carousel_metadata["imageCount"] = len(carousel_images)
            LOG.info("Processing image carousel without audio: images=%s", len(carousel_images))
            return complete_carousel_extraction(
                url=url,
                language=language,
                metadata=carousel_metadata,
                images=carousel_images,
                temp_dir=temp_dir,
                dry_run=dry_run,
                debug_output_dir=debug_output_dir,
                image_max_width=image_max_width,
                image_jpeg_quality=image_jpeg_quality,
                include_map_search=include_map_search,
                map_search_limit=map_search_limit,
                headless=headless,
                keep_temp=keep_temp,
                ai_provider=ai_provider,
                ai_model=ai_model,
                ai_base_url=ai_base_url,
                progress_callback=progress_callback,
            )

        info, download_profile = extract_metadata(source_url)
        metadata = _metadata_context(info)
        duration = _positive_float(metadata.get("duration"))
        if duration is not None and duration > max_duration_seconds:
            return {
                "success": False,
                "rejectedStatus": "REJECTED_DURATION",
                "url": url,
                "metadata": metadata,
                "error": {
                    "code": "SOCIAL_VIDEO_DURATION_EXCEEDED",
                    "message": f"Video duration {int(duration)}s exceeds the {max_duration_seconds}s limit",
                    "videoDurationSeconds": int(duration),
                    "maxDurationSeconds": max_duration_seconds,
                },
            }
        media_path = download_media(
            source_url,
            temp_dir,
            preferred_profile=download_profile,
            media_url=info.get("_embed_media_url"),
        )
        if duration is None:
            duration = probe_media_duration(media_path)
            if duration is not None:
                metadata["duration"] = duration
            if duration is None:
                return {
                    "success": False,
                    "url": url,
                    "metadata": metadata,
                    "error": {
                        "code": "SOCIAL_VIDEO_DURATION_UNAVAILABLE",
                        "message": "Could not verify video duration",
                    },
                }
            if duration is not None and duration > max_duration_seconds:
                return {
                    "success": False,
                    "rejectedStatus": "REJECTED_DURATION",
                    "url": url,
                    "metadata": metadata,
                    "error": {
                        "code": "SOCIAL_VIDEO_DURATION_EXCEEDED",
                        "message": f"Video duration {int(duration)}s exceeds the {max_duration_seconds}s limit",
                        "videoDurationSeconds": int(duration),
                        "maxDurationSeconds": max_duration_seconds,
                    },
                }

        # Extract audio với max_audio_seconds có thể None
        # Nếu None → extract toàn bộ, nếu có giá trị → giới hạn, fallback = duration or 600
        audio_seconds = max_audio_seconds
        if audio_seconds is None:
            audio_seconds = metadata.get("duration") or 600
        audio_path = extract_audio(media_path, temp_dir, audio_seconds)
        transcript = ""
        transcript_provider = "skipped:no_audio"
        if audio_path:
            transcript, transcript_provider = transcribe_audio(audio_path)

        # Cheap text-only triage before the expensive part (ffmpeg frame
        # extraction + the full vision LLM call). Only ever short-circuits on
        # a confident "clearly unrelated" verdict; anything else falls
        # through to the unchanged full pipeline below. Dry runs skip this so
        # debug artifacts always reflect the full pipeline.
        if not dry_run:
            prefilter_rejection = prefilter_topic_relevance(
                metadata=metadata,
                transcript=transcript,
                language=language,
                ai_provider=ai_provider,
                ai_model=ai_model,
                ai_base_url=ai_base_url,
            )
            if prefilter_rejection is not None:
                return {
                    "success": False,
                    "rejectedStatus": "REJECTED_TOPIC",
                    "url": url,
                    "language": language,
                    "metadata": metadata,
                    "media": {
                        "downloaded": True,
                        "audioProvider": transcript_provider,
                        "transcriptAvailable": bool(transcript),
                        "frameCount": 0,
                    },
                    "extraction": {
                        "is_relevant": False,
                        "relevance_reason": prefilter_rejection["reason"],
                        "found": False,
                        "needs_confirmation": False,
                        "summary": "",
                        "useful_summary": "",
                        "general_guidance": [],
                        "discarded_mentions": [],
                        "candidates": [],
                        "provider": prefilter_rejection["provider"],
                        "model": prefilter_rejection["model"],
                        "usage": prefilter_rejection["usage"],
                        "prefiltered": True,
                    },
                    "error": {
                        "code": "IRRELEVANT_SOCIAL_VIDEO",
                        "message": prefilter_rejection["reason"],
                    },
                }

        # Extract frames với interval có thể None (auto calculate từ duration)
        actual_interval = frame_interval_seconds
        frames = extract_frames(
            media_path,
            temp_dir,
            max_frames,
            actual_interval,
            image_max_width,
            image_jpeg_quality,
            metadata.get("duration"),
        )
        if len(frames) < 2:
            LOG.info("Few frames extracted; trying metadata images: current_frames=%s", len(frames))
            frames = frames + download_metadata_images(
                info,
                temp_dir,
                max_frames - len(frames),
                image_max_width,
                image_jpeg_quality,
            )
        deduped_frames = dedupe_near_identical_frames(frames)
        if len(deduped_frames) != len(frames):
            LOG.info(
                "Dropped near-duplicate frames before AI extraction: kept=%s dropped=%s",
                len(deduped_frames),
                len(frames) - len(deduped_frames),
            )
        frames = deduped_frames

        if dry_run:
            output_dir = Path(debug_output_dir) if debug_output_dir else _default_debug_dir(url)
            artifacts = save_dry_run_artifacts(
                output_dir=output_dir,
                url=url,
                language=language,
                metadata=metadata,
                transcript=transcript,
                transcript_provider=transcript_provider,
                image_paths=frames,
                audio_path=audio_path,
                media_path=media_path,
            )
            result = {
                "success": True,
                "dryRun": True,
                "url": url,
                "language": language,
                "metadata": metadata,
                "media": {
                    "downloaded": True,
                    "audioProvider": transcript_provider,
                    "transcriptAvailable": bool(transcript),
                    "frameCount": len(frames),
                    "imageMaxWidth": image_max_width,
                    "imageJpegQuality": image_jpeg_quality,
                },
                "artifacts": artifacts,
                "extraction": {
                    "found": False,
                    "needs_confirmation": True,
                    "summary": "Dry run completed before Claude extraction.",
                    "candidates": [],
                    "model": None,
                },
                "tempDir": str(temp_dir) if keep_temp else None,
            }
            LOG.info("Social dry-run completed: url=%s output_dir=%s", url, artifacts.get("outputDir"))
            return result

        extraction = extract_candidates_with_ai(
            metadata=metadata,
            transcript=transcript,
            transcript_provider=transcript_provider,
            image_paths=frames,
            language=language,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_base_url=ai_base_url,
        )

        if not bool(extraction.get("is_relevant")):
            reason = str(extraction.get("relevance_reason") or "Video is not about travel, food, or a place review")
            return {
                "success": False,
                "rejectedStatus": "REJECTED_TOPIC",
                "url": url,
                "language": language,
                "metadata": metadata,
                "media": {
                    "downloaded": True,
                    "audioProvider": transcript_provider,
                    "transcriptAvailable": bool(transcript),
                    "frameCount": len(frames),
                },
                "extraction": extraction,
                "error": {"code": "IRRELEVANT_SOCIAL_VIDEO", "message": reason},
            }

        extraction = retain_certain_candidates(
            extraction,
            transcript=transcript,
            frame_count=len(frames),
        )
        candidates = extraction.get("candidates") or []
        if include_map_search and candidates:
            def publish_progress(enriched: list[dict[str, Any]], processed: int, total: int) -> None:
                if not progress_callback:
                    return
                partial_extraction = dict(extraction)
                partial_extraction["candidates"] = enriched
                progress_callback({
                    "success": True,
                    "partial": True,
                    "url": url,
                    "language": language,
                    "metadata": metadata,
                    "media": {
                        "downloaded": True,
                        "mediaType": "VIDEO",
                        "audioProvider": transcript_provider,
                        "transcriptAvailable": bool(transcript),
                        "frameCount": len(frames),
                    },
                    "progress": {"processedCandidates": processed, "totalCandidates": total},
                    "extraction": partial_extraction,
                })

            candidates = enrich_with_map_search(
                candidates,
                search_limit=map_search_limit,
                headless=headless,
                progress_callback=publish_progress,
            )
            apply_map_validation(
                extraction,
                map_input_count=len(extraction.get("candidates") or []),
                verified_candidates=candidates,
            )

        result = {
            "success": True,
            "url": url,
            "language": language,
            "metadata": metadata,
            "media": {
                "downloaded": True,
                "audioProvider": transcript_provider,
                "transcriptAvailable": bool(transcript),
                "frameCount": len(frames),
                "imageMaxWidth": image_max_width,
                "imageJpegQuality": image_jpeg_quality,
            },
            "extraction": extraction,
            "tempDir": str(temp_dir) if keep_temp else None,
        }
        LOG.info(
            "Social extraction completed: url=%s found=%s candidates=%s transcript=%s frames=%s",
            url,
            extraction.get("found"),
            len(extraction.get("candidates") or []),
            bool(transcript),
            len(frames),
        )
        return result
    except RuntimeError as exc:
        message = str(exc)
        known_download_errors = ("SOCIAL_VIDEO_DOWNLOAD_BLOCKED", "SOCIAL_CAROUSEL_DOWNLOAD_BLOCKED")
        error_code, separator, detail = message.partition(":")
        if separator and error_code in known_download_errors:
            detail = detail.strip()
            LOG.warning("Social media download blocked: url=%s code=%s error=%s", url, error_code, detail)
            return {
                "success": False,
                "url": url,
                "error": {"code": error_code, "message": detail},
            }
        LOG.exception("Social extraction failed: url=%s", url)
        return {"success": False, "url": url, "error": {"code": "EXTRACTION_FAILED", "message": message}}
    except Exception as exc:
        LOG.exception("Social extraction failed: url=%s", url)
        return {"success": False, "url": url, "error": {"code": "EXTRACTION_FAILED", "message": str(exc)}}
    finally:
        if not keep_temp:
            LOG.info("Cleaning temp dir: %s", temp_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            LOG.info("Keeping temp dir for debugging: %s", temp_dir)
