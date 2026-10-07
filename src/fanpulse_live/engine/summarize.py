"""One line per fanbase for each moment, written by a small LLM.

Jev returns probabilities, not prose, so this is the one place a text model
is used. It runs only on moments, a few dozen calls per game, each cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from pathlib import Path

import pandas as pd
import requests

from fanpulse_live import config
from fanpulse_live.ingest.clean import clean_text

log = logging.getLogger(__name__)

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
COMMENTS_PER_FANBASE = 25
# Comments are sampled from the moment's start to this long after its peak.
AFTER_PEAK_SECONDS = 30
TIMEOUT_SECONDS = 60
MAX_RETRIES = 4

FANBASES = {"LAD": "Dodgers fans", "TOR": "Blue Jays fans", None: "Neutral fans"}

SYSTEM = (
    "You write one-line captions for a live sports dashboard that shows how different fanbases react to a moment "
    "in a baseball game. For each fanbase you are given, write one sentence of at most 16 words that says what "
    "that fanbase is feeling and about whom or what, based only on the comments and readings provided. Be "
    "specific: name the player or decision they are reacting to. Plain language, present tense, no hashtags, no "
    "quotation marks, no profanity. Do not invent events. Reply with a JSON object mapping each fanbase name "
    "exactly as given to its sentence, and nothing else."
)


class SummaryError(RuntimeError):
    pass


def model() -> str:
    return os.getenv("SUMMARY_MODEL") or DEFAULT_MODEL


def fanbase_of(stream: dict) -> str:
    return FANBASES[stream["team"]]


def build_prompt(
    moment: dict, comments: pd.DataFrame, ticks: pd.DataFrame, streams: list[dict], label: str, seed: int = 0
) -> str | None:
    """What the model sees for one moment, or None if no fanbase has comments in it."""
    start = moment["start"]
    end = moment["peak"] + pd.Timedelta(seconds=AFTER_PEAK_SECONDS)
    rng = random.Random(seed)
    sections = []
    for fanbase in FANBASES.values():
        ids = [s["id"] for s in streams if fanbase_of(s) == fanbase]
        inside = comments[
            comments["stream"].isin(ids)
            & (comments["thread_type"] == "game")
            & (comments["created_utc"] >= start)
            & (comments["created_utc"] <= end)
        ]
        if inside.empty:
            continue
        readings = ticks[ticks["stream"].isin(ids) & (ticks["t"] == moment["peak"]) & ~ticks["stale"]]
        notes = []
        if readings["mood"].notna().any():
            notes.append(f"mood {readings['mood'].mean():+.2f} on a -1 to +1 scale")
        if len(readings):
            notes.append("dominant emotion " + ", ".join(sorted(set(readings["emotion"]))))
            notes.append("mostly about " + ", ".join(sorted(set(readings["target"]))))
        bodies = [clean_text(body, 200) for body in inside["body"]]
        sample = rng.sample(bodies, min(COMMENTS_PER_FANBASE, len(bodies)))
        sections.append(
            f"## {fanbase}\nReadings: {'; '.join(notes) or 'none'}\nComments:\n" + "\n".join(f"- {c}" for c in sample)
        )
    if not sections:
        return None
    play = moment.get("play_description") or "No single play; see the comments."
    return f"Game: {label}\nWhat just happened: {play}\n\n" + "\n\n".join(sections)


def _cache_path(key: str) -> Path:
    return config.raw_dir() / "summaries" / f"{key}.json"


def summarize(prompt: str, *, session: requests.Session | None = None) -> dict[str, str]:
    """The fanbase lines for one prompt. Cached by (model, prompt)."""
    model_id = model()
    key = hashlib.sha256(json.dumps([model_id, SYSTEM, prompt]).encode("utf-8")).hexdigest()[:24]
    path = _cache_path(key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SummaryError("OPENROUTER_API_KEY is not set; put it in .env")
    body = {
        "model": model_id,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.3,
    }
    session = session or requests.Session()
    last_error = "no attempts made"
    for attempt in range(MAX_RETRIES):
        try:
            response = session.post(
                CHAT_URL, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            last_error = repr(exc)
        else:
            if response.status_code == 200:
                try:
                    lines = parse_lines(response.json()["choices"][0]["message"]["content"])
                except (KeyError, IndexError, ValueError) as exc:
                    last_error = f"unusable reply: {exc!r}"
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
                    return lines
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code in (400, 401, 402, 403, 404):
                    raise SummaryError(f"summary call failed: {last_error}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(2**attempt)
    raise SummaryError(f"summary call failed after {MAX_RETRIES} attempts: {last_error}")


def parse_lines(content: str) -> dict[str, str]:
    """The JSON object in a model reply, tolerating a code fence around it."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in reply")
    lines = json.loads(content[start : end + 1])
    if not isinstance(lines, dict) or not all(isinstance(v, str) for v in lines.values()):
        raise ValueError("reply is not a map of fanbase to sentence")
    return {name: text.strip() for name, text in lines.items() if name in FANBASES.values()}
