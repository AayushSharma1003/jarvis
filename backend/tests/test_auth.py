from jarvis_backend.server.auth import make_token, origin_allowed, token_valid


def test_token_roundtrip():
    t = make_token()
    assert token_valid(t, t)
    assert not token_valid(t, t + "x")
    assert not token_valid(t, "")
    assert not token_valid(t, None)


def test_tokens_unique_and_long():
    a, b = make_token(), make_token()
    assert a != b
    assert len(a) >= 40


def test_origin_rules():
    assert origin_allowed(None)  # non-browser clients send no Origin
    assert origin_allowed("tauri://localhost")
    assert origin_allowed("http://tauri.localhost")
    assert origin_allowed("http://localhost:1420")
    assert not origin_allowed("http://evil.example")
    assert not origin_allowed("http://localhost:9999")
    assert not origin_allowed("")
