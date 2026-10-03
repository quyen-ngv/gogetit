import ai_call_log


def test_reports_next_to_the_callback_with_images_redacted(monkeypatch):
    sent = []

    class Response:
        status_code = 200
        text = ""

    def fake_post(url, data, headers, timeout):
        sent.append((url, data.decode(), headers))
        return Response()

    monkeypatch.setattr(ai_call_log.requests, "post", fake_post)
    binding = ai_call_log.bind("http://backend:8080/v1/api/internal/social-location/jobs/callback", "tok", "job-1")
    try:
        ai_call_log.record(operation="EXTRACT_CANDIDATES", provider="OPENAI", model="m", ok=True, request={
            "messages": [{"content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 500}},
                                      {"type": "image", "source": {"data": "B" * 600}}]}]},
            input_tokens=10, output_tokens=2)
    finally:
        ai_call_log.unbind(binding)

    [(url, body, headers)] = sent
    assert url == "http://backend:8080/v1/api/internal/ai-calls"
    assert headers["X-Internal-Token"] == "tok"
    assert '"traceId": "job-1"' in body and '"provider": "openai"' in body
    assert "AAAA" not in body and "BBBB" not in body and "<data-url" in body and "<base64 600 chars>" in body


def test_unbound_thread_reports_nothing(monkeypatch):
    monkeypatch.setattr(ai_call_log.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted")))
    ai_call_log.record(operation="X", provider="p", model="m", ok=True, request={})


def test_anthropic_input_includes_cache_reads():
    class Usage:
        input_tokens, cache_read_input_tokens, output_tokens = 100, 900, 50

    class Message:
        usage = Usage()

    assert ai_call_log.anthropic_usage(Message()) == (1000, 900, 50)
