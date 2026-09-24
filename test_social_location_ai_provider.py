import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import social_location_extractor as extractor


class _FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"is_relevant":true,"relevance_reason":"travel review",'
                            '"found":true,"needs_confirmation":false,"summary":"summary",'
                            '"useful_summary":"useful","general_guidance":[],"candidates":[]}'
                        )
                    }
                }
            ]
        }


class SocialLocationAiProviderTest(unittest.TestCase):
    def test_deepseek_uses_openai_compatible_chat_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "frame.jpg"
            image_path.write_bytes(b"small-frame")

            with patch.object(extractor, "DEEPSEEK_API_KEY", "test-secret"), patch.object(
                extractor.requests, "post", return_value=_FakeResponse()
            ) as post:
                result = extractor.extract_candidates_with_ai(
                    metadata={"title": "Travel"},
                    transcript="Cafe Giang in Hanoi",
                    transcript_provider="test",
                    image_paths=[image_path],
                    language="vi",
                    ai_provider="deepseek",
                    ai_model="deepseek-v4-flash",
                    ai_base_url="https://api.deepseek.com",
                )

        self.assertEqual("DEEPSEEK", result["provider"])
        self.assertEqual("deepseek-v4-flash", result["model"])
        request_url = post.call_args.args[0]
        request_json = post.call_args.kwargs["json"]
        self.assertEqual("https://api.deepseek.com/chat/completions", request_url)
        self.assertEqual("json_object", request_json["response_format"]["type"])
        self.assertIn("max_tokens", request_json)
        self.assertNotIn("max_completion_tokens", request_json)
        self.assertEqual("system", request_json["messages"][0]["role"])
        image_content = request_json["messages"][1]["content"]
        self.assertEqual("SUPPLIED_FRAME_INDEX: 1", image_content[1]["text"])
        self.assertTrue(image_content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual("high", image_content[2]["image_url"]["detail"])

    def test_openai_uses_max_completion_tokens(self):
        with patch.object(extractor, "OPENAI_API_KEY", "test-secret"), patch.object(
            extractor.requests, "post", return_value=_FakeResponse()
        ) as post:
            result = extractor.extract_candidates_with_ai(
                metadata={"title": "Travel"},
                transcript="Cafe Giang in Hanoi",
                transcript_provider="test",
                image_paths=[],
                language="vi",
                ai_provider="openai",
                ai_model="gpt-5-mini",
                ai_base_url="https://api.openai.com/v1",
            )

        request_json = post.call_args.kwargs["json"]
        self.assertEqual("OPENAI", result["provider"])
        self.assertEqual(
            {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150},
            result["usage"],
        )
        self.assertIn("max_completion_tokens", request_json)
        self.assertNotIn("max_tokens", request_json)
        self.assertNotIn("temperature", request_json)
        serialized_messages = str(request_json["messages"]).lower()
        self.assertIn("json", serialized_messages)
        self.assertIn("valid json object", serialized_messages)

    def test_prompt_assigns_identity_judgement_to_ai(self):
        with patch.object(extractor, "OPENAI_API_KEY", "test-secret"), patch.object(
            extractor.requests, "post", return_value=_FakeResponse()
        ) as post:
            extractor.extract_candidates_with_ai(
                metadata={"title": "Many places"},
                transcript="A video covering several destinations",
                transcript_provider="test",
                image_paths=[],
                language="vi",
                ai_provider="openai",
                ai_model="gpt-5-mini",
                ai_base_url="https://api.openai.com/v1",
            )

        messages = post.call_args.kwargs["json"]["messages"]
        prompt = messages[1]["content"][0]["text"]
        self.assertIn("complete input", messages[0]["content"])
        self.assertIn("primary identity judge", messages[0]["content"])
        self.assertIn("CONFIRMED means", prompt)
        self.assertIn("discarded_mentions", prompt)
        self.assertIn("quán cafe này", prompt)
        self.assertIn("There is no candidate-count quota", prompt)
        self.assertNotIn("Return at most", prompt)
        self.assertIn("entire transcript and every supplied frame/slide", prompt)
        self.assertIn("Never merge multiple explicitly named list items", prompt)

    def test_named_mentions_are_kept_even_when_evidence_is_unresolved(self):
        extraction = {
            "found": True,
            "needs_confirmation": False,
            "candidates": [
                {
                    "name": "Hang Heo",
                    "confidence": 0.97,
                    "identity_status": "CONFIRMED",
                    "mention_type": "EXPLICIT_AUDIO_NAME",
                    "inference_used": False,
                    "evidence": [{
                        "source": "audio",
                        "quote": "địa điểm này tên là Hang Heo",
                        "frame_index": None,
                        "supports_identity": True,
                    }],
                },
                {
                    "name": "Hang Heo coffee",
                    "confidence": 0.99,
                    "identity_status": "LIKELY",
                    "mention_type": "CONTEXTUAL_INFERENCE",
                    "inference_used": True,
                    "evidence": [{
                        "source": "audio",
                        "quote": "băng qua quán cafe này là tới Hang Heo",
                        "frame_index": None,
                        "supports_identity": False,
                    }],
                },
                {
                    "name": "Bai Tranh Beach",
                    "confidence": 0.99,
                    "identity_status": "AMBIGUOUS",
                    "mention_type": "CONTEXTUAL_INFERENCE",
                    "inference_used": True,
                    "evidence": [{
                        "source": "frame",
                        "quote": "đi ngắm biển",
                        "frame_index": 3,
                        "supports_identity": False,
                    }],
                },
                {
                    "name": "Tiệm Bánh Căn Tháng Năm",
                    "confidence": 0.99,
                    "identity_status": "LIKELY",
                    "mention_type": "GENERIC_REFERENCE",
                    "inference_used": True,
                    "evidence": [{
                        "source": "frame",
                        "quote": "đi ăn bánh căn",
                        "frame_index": 4,
                        "supports_identity": False,
                    }],
                },
            ],
        }

        result = extractor.retain_certain_candidates(
            extraction,
            transcript=(
                "địa điểm này tên là Hang Heo, băng qua quán cafe này là tới Hang Heo; "
                "sau đó đi ăn bánh căn và đi ngắm biển"
            ),
            frame_count=5,
        )

        self.assertEqual(4, len(result["candidates"]))
        self.assertEqual(
            ["Hang Heo", "Hang Heo coffee", "Bai Tranh Beach"],
            [item["name"] for item in result["candidates"][:3]],
        )
        self.assertTrue(result["candidates"][3]["name"])
        self.assertEqual(4, result["candidateValidation"]["inputCount"])
        self.assertEqual(1, result["candidateValidation"]["evidenceVerifiedCount"])
        self.assertEqual(0, result["candidateValidation"]["rejectedCount"])
        self.assertEqual(3, result["candidateValidation"]["unresolvedCount"])
        self.assertEqual("UNRESOLVED", result["candidates"][1]["resolutionStatus"])
        self.assertFalse(result["needs_confirmation"])

    def test_repeated_or_accent_variants_remain_separate_source_mentions(self):
        evidence = [{
            "source": "audio",
            "quote": "Hôm nay đến Hàng Heo ở Nha Trang",
            "frame_index": None,
            "supports_identity": True,
        }]
        extraction = {
            "candidates": [
                {
                    "name": "Hang Heo",
                    "confidence": 0.9,
                    "identity_status": "CONFIRMED",
                    "mention_type": "EXPLICIT_AUDIO_NAME",
                    "inference_used": False,
                    "evidence": evidence,
                },
                {
                    "name": "Hàng Heo",
                    "confidence": 0.98,
                    "identity_status": "CONFIRMED",
                    "mention_type": "EXPLICIT_AUDIO_NAME",
                    "inference_used": False,
                    "evidence": evidence,
                },
            ]
        }

        result = extractor.retain_certain_candidates(
            extraction,
            transcript="Hôm nay đến Hàng Heo ở Nha Trang.",
        )

        self.assertEqual(2, len(result["candidates"]))
        self.assertEqual(["Hang Heo", "Hàng Heo"], [item["name"] for item in result["candidates"]])
        self.assertEqual(["social-0001", "social-0002"], [item["candidateRef"] for item in result["candidates"]])
        self.assertEqual(0, result["candidateValidation"]["rejectedCount"])

    def test_duplicate_ai_candidate_refs_are_made_unique_without_dropping_options(self):
        evidence = [{
            "source": "audio",
            "quote": "Hôm nay đến hai lựa chọn",
            "frame_index": None,
            "supports_identity": True,
        }]
        extraction = {
            "candidates": [
                {
                    "candidateRef": "lunch",
                    "name": "Option A",
                    "optionGroupId": "lunch",
                    "optionIndex": 1,
                    "confidence": 0.9,
                    "identity_status": "CONFIRMED",
                    "mention_type": "EXPLICIT_AUDIO_NAME",
                    "inference_used": False,
                    "evidence": evidence,
                },
                {
                    "candidateRef": "lunch",
                    "name": "Option B",
                    "optionGroupId": "lunch",
                    "optionIndex": 2,
                    "confidence": 0.9,
                    "identity_status": "CONFIRMED",
                    "mention_type": "EXPLICIT_AUDIO_NAME",
                    "inference_used": False,
                    "evidence": evidence,
                },
            ]
        }

        result = extractor.retain_certain_candidates(
            extraction,
            transcript="Hôm nay đến hai lựa chọn",
        )

        self.assertEqual(["lunch", "lunch-0002"], [
            item["candidateRef"] for item in result["candidates"]
        ])
        self.assertEqual([1, 2], [item["optionIndex"] for item in result["candidates"]])

    def test_ai_confirmed_audio_tolerates_transcription_variation(self):
        extraction = {
            "candidates": [{
                "name": "Café Giảng",
                "confidence": 0.84,
                "identity_status": "CONFIRMED",
                "mention_type": "EXPLICIT_AUDIO_NAME",
                "inference_used": False,
                "evidence": [{
                    "source": "audio",
                    "quote": "Cafe Giang ở Hà Nội",
                    "frame_index": None,
                    "supports_identity": True,
                }],
            }]
        }

        result = extractor.retain_certain_candidates(
            extraction,
            transcript="Hôm nay mình ghé ca phe giang o ha noi.",
        )

        self.assertEqual(["Café Giảng"], [item["name"] for item in result["candidates"]])

    def test_map_search_uses_video_context_and_publishes_each_candidate(self):
        candidates = [
            {
                "name": "The Workshop",
                "query": "The Workshop",
                "address_hint": "District 1",
                "city_hint": "Ho Chi Minh City",
                "country_hint": "Vietnam",
                "search_context": "specialty coffee near Nguyen Hue",
            },
            {
                "name": "Cafe Giang",
                "query": "Cafe Giang Hanoi",
                "city_hint": "Hanoi",
                "country_hint": "Vietnam",
            },
        ]
        progress = []
        with patch.object(
            extractor,
            "search_google_maps",
            side_effect=[
                {"success": True, "count": 1, "candidates": [{"placeId": "one", "title": "The Workshop"}]},
                {"success": True, "count": 1, "candidates": [{"placeId": "two", "title": "Cafe Giang"}]},
            ],
        ) as search:
            result = extractor.enrich_with_map_search(
                candidates,
                search_limit=1,
                headless=True,
                progress_callback=lambda items, processed, total: progress.append(
                    (len(items), processed, total)
                ),
            )

        self.assertEqual(2, len(result))
        self.assertEqual([(1, 1, 2), (2, 2, 2)], progress)
        self.assertEqual(
            "The Workshop District 1 Ho Chi Minh City Vietnam specialty coffee near Nguyen Hue",
            search.call_args_list[0].args[0],
        )
        self.assertEqual("Cafe Giang Hanoi Vietnam", search.call_args_list[1].args[0])

    def test_map_search_keeps_weak_matches_and_duplicate_place_ids_in_source_order(self):
        candidates = [
            {"name": "Hang Heo", "query": "Hang Heo Nha Trang", "confidence": 0.95},
            {"name": "Hàng Heo", "query": "Hàng Heo Nha Trang", "confidence": 0.99},
            {"name": "Bai Tranh Beach", "query": "Bai Tranh Beach Nha Trang", "confidence": 0.99},
        ]
        with patch.object(
            extractor,
            "search_google_maps",
            side_effect=[
                {"success": True, "candidates": [{"placeId": "hang-heo", "title": "Hang Heo"}]},
                {"success": True, "candidates": [{"placeId": "hang-heo", "title": "Hàng Heo"}]},
                {"success": True, "candidates": [{"placeId": "nha-trang-beach", "title": "Bãi biển Nha Trang"}]},
            ],
        ):
            result = extractor.enrich_with_map_search(
                candidates,
                search_limit=1,
                headless=True,
            )

        self.assertEqual(3, len(result))
        self.assertEqual(["Hang Heo", "Hàng Heo", "Bai Tranh Beach"], [item["name"] for item in result])
        self.assertEqual(["Hang Heo", "Hàng Heo", "Bai Tranh Beach"], [item["name"] for item in result])
        self.assertTrue(all(item.get("resolutionStatus") in {"RESOLVED", "UNRESOLVED"} for item in result))
        self.assertEqual(3, len({item["name"] for item in result}))

    def test_options_that_resolve_to_one_place_are_not_merged(self):
        candidates = [
            {"name": "Option A", "query": "same cafe", "optionGroupId": "lunch", "optionIndex": 1},
            {"name": "Option B", "query": "same cafe", "optionGroupId": "lunch", "optionIndex": 2},
        ]
        with patch.object(
            extractor,
            "search_google_maps",
            return_value={"success": True, "candidates": [{"placeId": "same-place", "title": "Same cafe"}]},
        ):
            result = extractor.enrich_with_map_search(candidates, search_limit=1, headless=True)

        self.assertEqual(2, len(result))
        self.assertEqual([1, 2], [item["optionIndex"] for item in result])
        self.assertEqual(["lunch", "lunch"], [item["optionGroupId"] for item in result])

    def test_map_resolution_allows_suffixes_but_rejects_close_alternatives(self):
        suffix_match = extractor._verified_map_search(
            {"name": "The Workshop", "aliases": [], "city_hint": "Ho Chi Minh City"},
            {
                "success": True,
                "candidates": [{
                    "placeId": "workshop",
                    "title": "The Workshop Coffee",
                    "address": "Ho Chi Minh City",
                }],
            },
        )
        ambiguous_match = extractor._verified_map_search(
            {"name": "Hang Heo", "aliases": ["Hang Heo Coffee"]},
            {
                "success": True,
                "candidates": [
                    {"placeId": "attraction", "title": "Hang Heo"},
                    {"placeId": "coffee", "title": "Hang Heo Coffee"},
                ],
            },
        )

        self.assertEqual("workshop", suffix_match["candidates"][0]["placeId"])
        self.assertIsNone(ambiguous_match)

    def test_resolves_tiktok_short_url_to_canonical_photo_url(self):
        response = Mock()
        response.url = "https://www.tiktok.com/@puputripvietnam/photo/7483494457631919367?_r=1&_t=tracking"
        response.raise_for_status.return_value = None
        short_url = "https://vt.tiktok.com/ZSV1pKhBp/"

        with patch.object(extractor.requests, "head", return_value=response):
            resolved = extractor.resolve_social_url(short_url)

        self.assertEqual(
            "https://www.tiktok.com/@puputripvietnam/photo/7483494457631919367",
            resolved,
        )

    def test_tiktok_carousel_fallback_receives_canonical_url(self):
        canonical_url = "https://www.tiktok.com/@puputripvietnam/photo/7483494457631919367"
        with tempfile.TemporaryDirectory() as temp_dir:
            fallback_images = [Path(temp_dir) / "raw-1.jpg", Path(temp_dir) / "raw-2.jpg"]
            with patch.object(extractor, "resolve_social_url", return_value=canonical_url), patch.object(
                extractor.subprocess,
                "run",
                return_value=Mock(returncode=1, stdout="", stderr="blocked"),
            ) as run, patch.object(
                extractor, "download_tiktok_carousel_with_browser", return_value=fallback_images
            ) as browser_fallback, patch.object(
                extractor, "compress_image", side_effect=lambda _source, output, *_args: output
            ):
                images = extractor.download_carousel_images(canonical_url, Path(temp_dir), 384, 10)

        self.assertEqual(2, len(images))
        self.assertEqual(canonical_url, run.call_args.args[0][-1])
        browser_fallback.assert_called_once_with(canonical_url, browser_fallback.call_args.args[1])

    def test_tiktok_photo_download_failure_does_not_fall_through_to_video(self):
        error = RuntimeError("SOCIAL_CAROUSEL_DOWNLOAD_BLOCKED: carousel blocked")
        with patch.object(extractor, "download_carousel_images", side_effect=error), patch.object(
            extractor, "extract_metadata"
        ) as video_metadata:
            result = extractor.extract_social_location("https://www.tiktok.com/@user/photo/123")

        self.assertFalse(result["success"])
        self.assertEqual("SOCIAL_CAROUSEL_DOWNLOAD_BLOCKED", result["error"]["code"])
        video_metadata.assert_not_called()

    def test_video_download_falls_back_to_tiktok_player_after_ytdlp_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = Path(temp_dir) / "media.mp4"
            expected.write_bytes(b"video")
            with patch.object(
                extractor,
                "_yt_dlp_attempts",
                return_value=[("default", {})],
            ), patch.object(
                extractor.yt_dlp,
                "YoutubeDL",
                side_effect=RuntimeError("universal data unavailable"),
            ), patch.object(
                extractor,
                "_extract_tiktok_embed",
                return_value={"_embed_media_url": "https://www.tiktok.com/aweme/v1/play/?item_id=123"},
            ) as embed, patch.object(
                extractor,
                "_download_tiktok_embed_media",
                return_value=expected,
            ) as player_download:
                result = extractor.download_media(
                    "https://www.tiktok.com/@user/video/123",
                    Path(temp_dir),
                    preferred_profile="default",
                )

        self.assertEqual(expected, result)
        embed.assert_called_once()
        player_download.assert_called_once_with(
            "https://www.tiktok.com/aweme/v1/play/?item_id=123",
            Path(temp_dir),
        )

    def test_blank_exception_message_is_preserved_with_type(self):
        self.assertIn("TimeoutError", extractor._exception_summary(TimeoutError()))

    def test_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "Unsupported AI provider"):
            extractor.extract_candidates_with_ai(
                metadata={},
                transcript="",
                transcript_provider="test",
                image_paths=[],
                language="vi",
                ai_provider="unknown",
            )

    def test_carousel_skips_video_and_audio_pipeline(self):
        carousel_result = {"success": True, "media": {"mediaType": "IMAGE_CAROUSEL"}}
        with patch.object(
            extractor, "download_carousel_images", return_value=[Path("one.jpg"), Path("two.jpg")]
        ), patch.object(
            extractor, "extract_carousel_metadata", return_value={"extractor_key": "TikTok", "title": "Trip"}
        ), patch.object(
            extractor, "complete_carousel_extraction", return_value=carousel_result
        ) as complete, patch.object(extractor, "extract_metadata") as video_metadata, patch.object(
            extractor, "extract_audio"
        ) as extract_audio:
            result = extractor.extract_social_location("https://www.tiktok.com/@user/photo/123")

        self.assertEqual(carousel_result, result)
        complete.assert_called_once()
        self.assertEqual("IMAGE_CAROUSEL", complete.call_args.kwargs["metadata"]["mediaType"])
        video_metadata.assert_not_called()
        extract_audio.assert_not_called()

    def test_audio_extraction_skips_media_without_audio_stream(self):
        with patch.object(extractor, "has_audio_stream", return_value=False), patch.object(
            extractor, "_run"
        ) as run:
            result = extractor.extract_audio(Path("silent.mp4"), Path("."), 180)

        self.assertIsNone(result)
        run.assert_not_called()

    def test_topic_prefilter_skips_call_with_no_signal(self):
        with patch.object(extractor.requests, "post") as post:
            result = extractor.prefilter_topic_relevance(
                metadata={}, transcript="", language="vi"
            )

        self.assertIsNone(result)
        post.assert_not_called()

    def test_topic_prefilter_returns_none_when_ai_finds_it_relevant(self):
        response = _FakeResponse()
        response.json = lambda: {
            "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            "choices": [{"message": {"content": '{"clearly_unrelated": false, "reason": "mentions a cafe"}'}}],
        }
        with patch.object(extractor, "DEEPSEEK_API_KEY", "test-secret"), patch.object(
            extractor.requests, "post", return_value=response
        ):
            result = extractor.prefilter_topic_relevance(
                metadata={"title": "Cafe hopping"},
                transcript="This cafe in Hanoi is great",
                language="vi",
                ai_provider="deepseek",
                ai_base_url="https://api.deepseek.com",
            )

        self.assertIsNone(result)

    def test_topic_prefilter_rejects_clearly_unrelated_content(self):
        response = _FakeResponse()
        response.json = lambda: {
            "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            "choices": [{"message": {"content": '{"clearly_unrelated": true, "reason": "pure dance trend"}'}}],
        }
        with patch.object(extractor, "DEEPSEEK_API_KEY", "test-secret"), patch.object(
            extractor.requests, "post", return_value=response
        ) as post:
            result = extractor.prefilter_topic_relevance(
                metadata={"title": "Dance challenge"},
                transcript="",
                language="vi",
                ai_provider="deepseek",
                ai_base_url="https://api.deepseek.com",
            )

        self.assertIsNotNone(result)
        self.assertEqual("pure dance trend", result["reason"])
        self.assertEqual("DEEPSEEK", result["provider"])
        self.assertEqual({"inputTokens": 40, "outputTokens": 8, "totalTokens": 48}, result["usage"])
        # Small, text-only request: no image content and a tight token budget.
        request_json = post.call_args.kwargs["json"]
        self.assertEqual(120, request_json["max_tokens"])
        self.assertIsInstance(request_json["messages"][0]["content"], str)

    def test_topic_prefilter_fails_open_on_http_error(self):
        response = _FakeResponse()
        response.ok = False
        response.status_code = 500
        response.text = "boom"
        with patch.object(extractor, "DEEPSEEK_API_KEY", "test-secret"), patch.object(
            extractor.requests, "post", return_value=response
        ):
            result = extractor.prefilter_topic_relevance(
                metadata={"title": "Dance challenge"},
                transcript="",
                language="vi",
                ai_provider="deepseek",
                ai_base_url="https://api.deepseek.com",
            )

        self.assertIsNone(result)

    def test_extract_social_location_short_circuits_on_topic_prefilter(self):
        info = {"extractor_key": "TikTok", "title": "Funny dance", "duration": 30}
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "media.mp4"
            media_path.write_bytes(b"video")
            with patch.object(
                extractor, "download_carousel_images", return_value=[]
            ), patch.object(
                extractor, "extract_metadata", return_value=(info, "default")
            ), patch.object(
                extractor, "download_media", return_value=media_path
            ), patch.object(
                extractor, "extract_audio", return_value=None
            ), patch.object(
                extractor,
                "prefilter_topic_relevance",
                return_value={
                    "reason": "Pure dance trend, no place mentioned",
                    "provider": "DEEPSEEK",
                    "model": "deepseek-v4-flash",
                    "usage": {"inputTokens": 80, "outputTokens": 10, "totalTokens": 90},
                },
            ) as prefilter, patch.object(
                extractor, "extract_frames"
            ) as extract_frames, patch.object(
                extractor, "extract_candidates_with_ai"
            ) as extract_candidates:
                result = extractor.extract_social_location(
                    "https://www.tiktok.com/@user/video/123"
                )

        self.assertFalse(result["success"])
        self.assertEqual("REJECTED_TOPIC", result["rejectedStatus"])
        self.assertEqual("IRRELEVANT_SOCIAL_VIDEO", result["error"]["code"])
        self.assertEqual("Pure dance trend, no place mentioned", result["error"]["message"])
        self.assertEqual(0, result["media"]["frameCount"])
        prefilter.assert_called_once()
        extract_frames.assert_not_called()
        extract_candidates.assert_not_called()

    def test_dedupe_near_identical_frames_drops_static_run(self):
        from PIL import Image

        # A solid-color image hashes identically under average-hash regardless
        # of its color (every pixel equals the mean), so frames need internal
        # structure -- a split-tone pattern -- for the hash to reflect it.
        def split_tone_frame(path: Path, left: int, right: int) -> None:
            image = Image.new("RGB", (64, 64))
            for x in range(64):
                shade = left if x < 32 else right
                for y in range(64):
                    image.putpixel((x, y), (shade, shade, shade))
            image.save(path, "JPEG")

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / f"frame_{index:02d}.jpg" for index in range(4)]
            # Three near-identical frames (dark left / light right, with tiny
            # jitter) followed by one with the tones swapped -- a visually
            # distinct scene change.
            split_tone_frame(paths[0], 40, 210)
            split_tone_frame(paths[1], 45, 205)
            split_tone_frame(paths[2], 38, 208)
            split_tone_frame(paths[3], 210, 40)

            kept = extractor.dedupe_near_identical_frames(paths)

        self.assertEqual([paths[0], paths[3]], kept)

    def test_dedupe_near_identical_frames_keeps_short_lists_untouched(self):
        paths = [Path("a.jpg"), Path("b.jpg")]
        self.assertEqual(paths, extractor.dedupe_near_identical_frames(paths))


if __name__ == "__main__":
    unittest.main()
