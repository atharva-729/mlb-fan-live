"""Precomputes every update of the game: one Jev reading per stream every few seconds.

Steps from five minutes before first pitch to five minutes after the final
out. Each (update, stream) is one Jev call, or a few when a burst of new
comments spills past the question budget. Calls run in parallel and every
response is cached, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from fanpulse_live.engine import calls
from fanpulse_live.gamestate import GameTimeline
from fanpulse_live.jev import client, questions

log = logging.getLogger(__name__)

MARGIN = timedelta(minutes=5)
PARALLEL_CALLS = 8
TOP_CHOICES = 3


class CostLimitReached(RuntimeError):
    pass


def tick_times(timeline: GameTimeline, update_every_s: int) -> list[datetime]:
    """Update times from five minutes before first pitch to five minutes after the final out."""
    start = (timeline.first_pitch() - MARGIN).replace(microsecond=0)
    start -= timedelta(seconds=start.second % update_every_s)
    count = int((timeline.final_out() + MARGIN - start).total_seconds() // update_every_s)
    return [start + timedelta(seconds=update_every_s * i) for i in range(count + 1)]


@dataclass
class TickResult:
    window: dict  # one row of the ticks table
    comments: list[dict]  # one row per newly tagged comment
    input_tokens: int = 0
    cost: float = 0.0
    paid_calls: int = 0


def run_tick(
    timeline: GameTimeline,
    t: datetime,
    stream: dict,
    stream_comments: pd.DataFrame,
    parents: dict[str, str],
    subjects: dict[str, str | None],
    game_config: dict,
    cached_only: bool = False,
) -> TickResult:
    """One stream at one update: build the call, ask Jev, flatten the answers.

    With ``cached_only`` a reading Jev has not answered yet comes back marked
    ``pending`` instead of being asked for.
    """
    call = calls.prepare_call(timeline, t, stream, stream_comments, parents, subjects, game_config)
    row = {
        "t": pd.Timestamp(t),
        "stream": stream["id"],
        "window_used_s": call.window.window_used_s,
        "n_comments": len(call.window.comments),
        "n_new": len(call.new_numbers),
        "stale": call.window.stale,
        "pending": False,
        "mood": None, "mood_confidence": None, "target": None, "target_top": None,
        "emotion": None, "emotion_probs": None, "moment": None, "blame": None,
    }  # fmt: skip
    if call.window.stale:
        return TickResult(row, [])

    answers: dict[str, dict] = {}
    result = TickResult(row, [])
    try:
        for batch in call.question_batches:
            response = client.ask(call.state, batch, cached_only=cached_only)
            answers.update(response["answers"])
            if not response["cached"]:
                result.input_tokens += response["usage"]["input_tokens"]
                result.cost += response["usage"]["cost"]
                result.paid_calls += 1
    except client.CacheMiss:
        row["pending"] = True
        return TickResult(row, [])

    if "mood" in answers:
        row["mood"] = questions.score_to_unit(answers["mood"])
        row["mood_confidence"] = answers["mood"]["confidence"]
    row["emotion"] = answers["emotion"]["choice"]
    row["emotion_probs"] = json.dumps(answers["emotion"]["probabilities"])
    row["moment"] = answers["moment"]["noul"]
    row["blame"] = answers["blame"]["noul"]

    ids = list(call.shown["comment_id"])
    for k in call.new_numbers:
        subject, sentiment = answers[f"subj_{k}"], answers[f"sent_{k}"]
        result.comments.append(
            {
                "comment_id": ids[k - 1],
                "stream": stream["id"],
                "t": pd.Timestamp(t),
                "subject": subject["choice"],
                "subject_confidence": subject["confidence"],
                "sentiment": questions.score_to_unit(sentiment),
                "sentiment_confidence": sentiment["confidence"],
            }
        )
    return result


def run_game(
    timeline: GameTimeline,
    comments: pd.DataFrame,
    parents: dict[str, str],
    game_config: dict,
    *,
    max_cost: float,
    on_progress: Callable[[int, int, float, int], None] | None = None,
    times: list[datetime] | None = None,
    cached_only: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Every (update, stream) of the game. Returns the ticks table, the comment tags and run totals.

    Stops with ``CostLimitReached`` once this run has paid more than
    ``max_cost`` dollars; what was paid for stays cached. With ``cached_only``
    nothing is sent to Jev and unanswered readings are marked ``pending``.
    """
    times = times or tick_times(timeline, game_config["tick"]["update_every_s"])
    subjects = questions.subject_options(timeline)
    game_threads = comments[comments["thread_type"] == "game"]
    by_stream = {
        stream["id"]: game_threads[game_threads["stream"] == stream["id"]].sort_values("created_utc")
        for stream in game_config["streams"]
    }
    tasks = [(t, stream) for t in times for stream in game_config["streams"]]
    results: list[TickResult] = []
    totals = {"input_tokens": 0, "cost": 0.0, "paid_calls": 0}

    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        futures = [
            pool.submit(
                run_tick, timeline, t, stream, by_stream[stream["id"]], parents, subjects, game_config, cached_only
            )
            for t, stream in tasks
        ]
        try:
            for done, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                results.append(result)
                totals["input_tokens"] += result.input_tokens
                totals["cost"] += result.cost
                totals["paid_calls"] += result.paid_calls
                if totals["cost"] > max_cost:
                    raise CostLimitReached(f"spent ${totals['cost']:.2f}, over the ${max_cost:.2f} limit")
                if on_progress is not None and (done % 50 == 0 or done == len(futures)):
                    on_progress(done, len(futures), totals["cost"], totals["paid_calls"])
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    ticks = pd.DataFrame([r.window for r in results]).sort_values(["t", "stream"]).reset_index(drop=True)
    tags = pd.DataFrame(
        [row for r in results for row in r.comments],
        columns=["comment_id", "stream", "t", "subject", "subject_confidence", "sentiment", "sentiment_confidence"],
    ).sort_values(["t", "stream"]).reset_index(drop=True)
    fill_targets(ticks, tags, game_config["tick"]["window_s"])
    return ticks, tags, totals


def fill_targets(ticks: pd.DataFrame, tags: pd.DataFrame, window_s: int) -> None:
    """Set each reading's main subject from the comment tags of its window, in place.

    The subject is the most common one among comments tagged in the last
    ``window_s`` seconds, ignoring "other"; ``target_top`` keeps the leading
    few with their share. Asking Jev for this directly cost one more long
    question per call and said nothing the tags do not.
    """
    targets, tops = {}, {}
    for stream, group in tags.groupby("stream"):
        group = group[group["subject"] != "other"]
        times = group["t"].reset_index(drop=True)
        subjects = group["subject"].to_numpy()
        for t in ticks.loc[(ticks["stream"] == stream) & ~ticks["stale"] & ~ticks["pending"], "t"]:
            lo = times.searchsorted(t - pd.Timedelta(seconds=window_s), side="right")
            hi = times.searchsorted(t, side="right")
            if hi <= lo:
                continue
            counts = pd.Series(subjects[lo:hi]).value_counts()
            targets[(t, stream)] = counts.index[0]
            tops[(t, stream)] = json.dumps(
                [[name, round(n / (hi - lo), 3)] for name, n in counts.head(TOP_CHOICES).items()], ensure_ascii=False
            )
    keys = list(zip(ticks["t"], ticks["stream"]))
    ticks["target"] = [targets.get(key) for key in keys]
    ticks["target_top"] = [tops.get(key) for key in keys]


def volume_buckets(comments: pd.DataFrame, times: list[datetime], update_every_s: int) -> pd.DataFrame:
    """Raw comment counts per update interval and stream, for spike detection.

    The count at ``t`` is the comments posted in ``(t - update_every_s, t]``.
    """
    game_threads = comments[comments["thread_type"] == "game"]
    edges = pd.DatetimeIndex([pd.Timestamp(times[0]) - pd.Timedelta(seconds=update_every_s)] + [pd.Timestamp(t) for t in times])
    frames = []
    for stream, group in game_threads.groupby("stream"):
        cut = pd.cut(group["created_utc"], edges, labels=edges[1:], right=True)
        counts = cut.value_counts().reindex(edges[1:], fill_value=0)
        frames.append(pd.DataFrame({"t": edges[1:], "stream": stream, "n": counts.to_numpy()}))
    return pd.concat(frames, ignore_index=True).sort_values(["t", "stream"]).reset_index(drop=True)
