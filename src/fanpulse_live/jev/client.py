"""Thin client for Jev, TypeSafe's decision model, through OpenRouter's Decisions API.

One call sends a ``state`` and a map of typed questions and gets back a typed
answer with probabilities for each. Request shape, from the OpenRouter Jev
tutorial and confirmed with a live call::

    POST https://openrouter.ai/api/alpha/decisions
    {"model": "typesafe/jev-1.13", "state": <str | dict | list>,
     "questions": {"<id>": {"type": "noul" | "choice" | "score",
                            "instructions": "...", "criteria": ...}}}

Every response is cached under ``data/raw/jev/`` by a hash of (model, state,
questions), so an identical call is never paid for twice, and every paid call
is appended to ``data/jev_usage.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from fanpulse_live import config

log = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"

TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_SECONDS = 1.0
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

_session: requests.Session | None = None
_usage_lock = threading.Lock()


class JevError(RuntimeError):
    pass


def model() -> str:
    return os.getenv("JEV_MODEL") or DEFAULT_MODEL


def _api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise JevError("OPENROUTER_API_KEY is not set; put it in .env")
    return key


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def request_hash(state: Any, questions: dict[str, dict], model_id: str) -> str:
    payload = json.dumps({"model": model_id, "state": state, "questions": questions}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def cache_path(key: str) -> Path:
    return config.raw_dir() / "jev" / key[:2] / f"{key}.json"


def usage_log_path() -> Path:
    return config.data_dir() / "jev_usage.jsonl"


def _log_usage(key: str, response: dict, n_questions: int) -> None:
    usage = response.get("usage") or {}
    entry = {
        "time": time.time(),
        "key": key,
        "model": response.get("model"),
        "n_questions": n_questions,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost": usage.get("cost"),
        "latency_s": response["latency_s"],
    }
    path = usage_log_path()
    with _usage_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def ask(
    state: Any,
    questions: dict[str, dict],
    *,
    model_id: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """Answer ``questions`` about ``state``.

    Returns the API response (``answers``, ``usage``, ...) plus ``latency_s``
    and ``cached``. A cached response keeps the latency of the call that paid
    for it.
    """
    model_id = model_id or model()
    key = request_hash(state, questions, model_id)
    path = cache_path(key)
    if path.exists():
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}

    session = session or _get_session()
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    body = {"model": model_id, "state": state, "questions": questions}
    last_error = "no attempts made"

    for attempt in range(MAX_RETRIES):
        started = time.perf_counter()
        try:
            response = session.post(DECISIONS_URL, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = repr(exc)
        else:
            latency = time.perf_counter() - started
            if response.status_code == 200:
                data = response.json()
                if "answers" not in data:
                    raise JevError(f"Jev returned no answers: {response.text[:300]}")
                data["latency_s"] = round(latency, 3)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(path)
                _log_usage(key, data, len(questions))
                return {**data, "cached": False}
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code not in RETRY_STATUSES:
                raise JevError(f"Jev call failed: {last_error}")

        if attempt < MAX_RETRIES - 1:
            delay = BACKOFF_SECONDS * 2**attempt
            log.warning("Jev call: %s, retrying in %.1fs", last_error, delay)
            time.sleep(delay)

    raise JevError(f"Jev call failed after {MAX_RETRIES} attempts: {last_error}")


def usage_summary() -> dict[str, float]:
    """Totals over every paid call logged so far."""
    path = usage_log_path()
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    latencies = sorted(e["latency_s"] for e in entries)
    return {
        "calls": len(entries),
        "input_tokens": sum(e["input_tokens"] or 0 for e in entries),
        "cost": sum(e["cost"] or 0 for e in entries),
        "median_latency_s": latencies[len(latencies) // 2] if latencies else 0.0,
    }
