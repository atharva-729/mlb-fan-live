"""Cached, polite HTTP GET helper.

Every successful response is written under ``data/raw/<host>/`` and served from
there on later calls, so nothing is ever fetched twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Collection, Mapping
from urllib.parse import urlencode, urlsplit

import requests

from fanpulse_live import config

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
MAX_RATE_LIMIT_SLEEP = 300.0
RETRY_STATUSES = {500, 502, 503, 504}

_session: requests.Session | None = None


class HttpError(RuntimeError):
    pass


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = config.user_agent()
    return _session


def _canonical_url(url: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return url
    query = urlencode(sorted((k, v) for k, v in params.items() if v is not None))
    return f"{url}?{query}" if query else url


def cache_path(url: str, params: Mapping[str, Any] | None = None) -> Path:
    """Where the response for this request lives under ``data/raw/``."""
    full = _canonical_url(url, params)
    parts = urlsplit(full)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", parts.path).strip("_")[:80] or "root"
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:12]
    return config.raw_dir() / parts.netloc.replace(":", "_") / f"{slug}__{digest}.json"


def _rate_limit_sleep(response: requests.Response, attempt: int) -> float:
    """Seconds to wait after a 429, from X-RateLimit-Reset (or Retry-After)."""
    for header in ("X-RateLimit-Reset", "Retry-After"):
        value = response.headers.get(header)
        if value is None:
            continue
        try:
            seconds = float(value)
        except ValueError:
            continue
        # Some services send an epoch timestamp instead of a delta.
        if seconds > 1_000_000_000:
            seconds -= time.time()
        return min(max(seconds, 0.0) + 0.5, MAX_RATE_LIMIT_SLEEP)
    return BACKOFF_SECONDS * 2**attempt


def get_json(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
    cache_if: Callable[[Any], bool] | None = None,
    retry_statuses: Collection[int] = RETRY_STATUSES,
    max_retries: int = MAX_RETRIES,
    backoff_seconds: float = BACKOFF_SECONDS,
) -> Any:
    """GET ``url`` and return parsed JSON, reading from the cache when present.

    ``cache_if`` lets a caller refuse to cache a response that isn't final yet
    (a game still in progress), since a cached response is never re-fetched.
    ``max_retries`` and ``backoff_seconds`` let a caller be more patient with a
    slow service; the wait doubles each attempt up to ``MAX_BACKOFF_SECONDS``.
    """
    path = cache_path(url, params)
    if path.exists():
        log.debug("cache hit %s", path)
        return json.loads(path.read_text(encoding="utf-8"))

    session = session or _get_session()
    full = _canonical_url(url, params)
    last_error = "no attempts made"

    for attempt in range(max_retries):
        backoff = min(backoff_seconds * 2**attempt, MAX_BACKOFF_SECONDS)
        try:
            response = session.get(full, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = repr(exc)
            delay = backoff
        else:
            if response.status_code == 200:
                data = response.json()
                if cache_if is not None and not cache_if(data):
                    log.debug("not caching %s (response not final)", full)
                    return data
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(path)
                return data
            last_error = f"HTTP {response.status_code}"
            if response.status_code == 429:
                delay = _rate_limit_sleep(response, attempt)
            elif response.status_code in retry_statuses:
                # A service that says when its rate-limit window resets is asking us to wait that long.
                told_when = any(h in response.headers for h in ("X-RateLimit-Reset", "Retry-After"))
                delay = _rate_limit_sleep(response, attempt) if told_when else backoff
            else:
                raise HttpError(f"GET {full} failed: {last_error}: {response.text[:200]}")

        if attempt < max_retries - 1:
            log.warning("GET %s: %s, retrying in %.1fs", full, last_error, delay)
            time.sleep(delay)

    raise HttpError(f"GET {full} failed after {max_retries} attempts: {last_error}")
