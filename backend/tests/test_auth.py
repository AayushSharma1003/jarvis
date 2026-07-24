from jarvis_backend.server.auth import make_token, origin_allowed, token_valid


def test_token_roundtrip():
    t = make_token()
    assert token_valid(t, t)
    assert not token_valid(t, t + "x")
    assert not token_valid(t, "")
    assert not token_valid(t, None)


def test_a_non_string_token_is_refused_not_raised():
    """JSON gives us whatever the client typed. `{"token": 123}` used to reach
    `provided.encode()` and raise AttributeError out of the pre-auth path in
    server/app.py, where nothing catches it — so any local process could crash
    the handler without a token. It was already fail-safe (a dead connection is
    not an authenticated one); this makes it a plain refusal."""
    t = make_token()
    for bogus in (123, 1.5, True, ["x"], {"a": 1}, object()):
        assert not token_valid(t, bogus)


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
