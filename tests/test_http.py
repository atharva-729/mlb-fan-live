import json

import pytest

from fanpulse_live import http


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FANPULSE_LIVE_DATA_DIR", str(tmp_path))
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    return sleeps


def test_response_is_cached_and_not_refetched(tmp_path):
    session = FakeSession([FakeResponse(payload={"a": 1})])
    url = "https://example.com/api/v1/thing"

    assert http.get_json(url, {"x": 1}, session=session) == {"a": 1}
    assert http.get_json(url, {"x": 1}, session=session) == {"a": 1}

    assert len(session.calls) == 1
    path = http.cache_path(url, {"x": 1})
    assert path.exists()
    assert path.is_relative_to(tmp_path / "raw" / "example.com")


def test_cache_key_ignores_param_order_none_values_but_not_values():
    url = "https://example.com/api"
    assert http.cache_path(url, {"a": 1, "b": 2}) == http.cache_path(url, {"b": 2, "a": 1})
    assert http.cache_path(url, {"a": 1, "b": None}) == http.cache_path(url, {"a": 1})
    assert http.cache_path(url, {"a": 1}) != http.cache_path(url, {"a": 2})


def test_retries_on_server_error(isolated_data_dir):
    session = FakeSession([FakeResponse(503), FakeResponse(payload={"ok": True})])

    assert http.get_json("https://example.com/flaky", session=session) == {"ok": True}
    assert len(session.calls) == 2
    assert isolated_data_dir == [http.BACKOFF_SECONDS]


def test_429_sleeps_for_rate_limit_reset(isolated_data_dir):
    session = FakeSession(
        [FakeResponse(429, headers={"X-RateLimit-Reset": "7"}), FakeResponse(payload=[1, 2])]
    )

    assert http.get_json("https://example.com/limited", session=session) == [1, 2]
    assert isolated_data_dir == [7.5]


def test_client_error_raises_without_retry_or_cache():
    session = FakeSession([FakeResponse(422, payload={"error": "bad"})])
    url = "https://example.com/bad"

    with pytest.raises(http.HttpError, match="422"):
        http.get_json(url, session=session)

    assert len(session.calls) == 1
    assert not http.cache_path(url).exists()


def test_cache_if_false_skips_cache():
    session = FakeSession([FakeResponse(payload={"final": False}), FakeResponse(payload={"final": True})])
    url = "https://example.com/live"

    def is_final(data):
        return data["final"]

    assert http.get_json(url, session=session, cache_if=is_final) == {"final": False}
    assert not http.cache_path(url).exists()
    assert http.get_json(url, session=session, cache_if=is_final) == {"final": True}
    assert http.cache_path(url).exists()


def test_gives_up_after_max_retries():
    session = FakeSession([FakeResponse(500)] * http.MAX_RETRIES)

    with pytest.raises(http.HttpError, match="after"):
        http.get_json("https://example.com/down", session=session)

    assert len(session.calls) == http.MAX_RETRIES
