"""Writes everything the dashboard reads, as static JSON under ``web/data/``.

The dashboard only ever looks things up by time, so the files are plain
arrays aligned to the list of updates: reading ``i`` of every series belongs
to update ``i``. One file holds the game itself, one the comments, and one
per model ("source") holds that model's readings, tags and moments.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from fanpulse_live import config
from fanpulse_live.gamestate import GameTimeline
from fanpulse_live.ingest.clean import clean_text
from fanpulse_live.jev.state import community_label

COMMENT_CHARS = 240
SOURCE_LABELS = {"jev": "Jev", "baseline": "Baseline (RoBERTa + name matching)"}
FANBASE_LABELS = {"LAD": "Dodgers fans", "TOR": "Blue Jays fans", None: "Neutral fans"}


def web_data_dir() -> Path:
    return config.PROJECT_ROOT / "web" / "data"


def _epoch(t) -> int:
    return int(pd.Timestamp(t).timestamp())


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def _clean(value):
    """None for a missing value of any kind; pandas hands missing numbers back as NaN."""
    return None if value is None or (isinstance(value, float) and value != value) or value is pd.NA else value


def game_file(timeline: GameTimeline, times: list[datetime], season_lines: dict[str, str] | None = None) -> dict:
    """The scoreboard at every update, the plays, and each player's lines: today's, running, and the season's.

    The season lines are for display beside fan sentiment. They are never sent to a model.
    """
    keys = ("status", "inning", "half", "outs", "balls", "strikes", "home", "away", "runners", "batter", "pitcher", "wp")
    state = {key: [] for key in keys}
    for t in times:
        s = timeline.at(t)
        state["status"].append(s.status)
        state["inning"].append(s.inning)
        state["half"].append(s.half)
        state["outs"].append(s.outs)
        state["balls"].append(s.balls)
        state["strikes"].append(s.strikes)
        state["home"].append(s.home_score)
        state["away"].append(s.away_score)
        state["runners"].append("".join("1" if base in s.runners else "0" for base in ("1st", "2nd", "3rd")))
        state["batter"].append(timeline.name(s.batter_id) if s.batter_id else None)
        state["pitcher"].append(timeline.name(s.pitcher_id) if s.pitcher_id else None)
        state["wp"].append(round(s.home_wp, 4))

    plays = [
        {
            "end": _epoch(p.end),
            "inning": p.inning,
            "half": p.half,
            "description": p.description,
            "wpDelta": round(p.wp_delta, 4),
            "wpAfter": round(p.home_wp_after, 4),
            "home": p.home_score,
            "away": p.away_score,
        }
        for p in timeline.plays
    ]

    lines: dict[str, list] = {}
    for play in timeline.plays:
        for player_id in (play.batter_id, play.pitcher_id):
            line = timeline.player_line(player_id, play.end)
            entries = lines.setdefault(timeline.name(player_id), [])
            if line and (not entries or entries[-1][1] != line):
                entries.append([_epoch(play.end), line])
    players = {
        p.name: {"team": p.team, "lines": lines.get(p.name, []), "season": (season_lines or {}).get(p.name)}
        for p in sorted(timeline.players.values(), key=lambda p: p.name)
    }
    return {"state": state, "plays": plays, "players": players}


def comments_file(comments: pd.DataFrame, stream_ids: list[str], start: int, end: int) -> tuple[dict, dict[str, int]]:
    """Game-thread comments in the replay span, oldest first, and each comment id's position."""
    shown = comments[comments["thread_type"] == "game"].sort_values("created_utc")
    seconds = shown["created_utc"].map(_epoch)
    shown = shown[(seconds >= start) & (seconds <= end)]
    index = {comment_id: i for i, comment_id in enumerate(shown["comment_id"])}
    stream_index = {stream: i for i, stream in enumerate(stream_ids)}
    data = {
        "t": [_epoch(t) for t in shown["created_utc"]],
        "s": [stream_index[s] for s in shown["stream"]],
        "x": [clean_text(body, COMMENT_CHARS) for body in shown["body"]],
    }
    return data, index


def source_file(
    ticks: pd.DataFrame,
    tags: pd.DataFrame,
    volume: pd.DataFrame,
    moments: pd.DataFrame,
    summaries: dict[str, dict[str, str]],
    stream_ids: list[str],
    times: list[datetime],
    comment_index: dict[str, int],
) -> tuple[dict, float]:
    """One model's readings aligned to the updates, plus how much of the game it covers."""
    tick_index = {_epoch(t): i for i, t in enumerate(times)}
    n = len(times)
    series: dict[str, dict[str, list]] = {}
    answered = possible = 0
    for stream in stream_ids:
        rows = ticks[ticks["stream"] == stream]
        columns = {key: [None] * n for key in ("mood", "target", "emotion", "moment", "blame", "n")}
        flags = {"stale": [0] * n, "pending": [0] * n}
        for row in rows.itertuples():
            i = tick_index.get(_epoch(row.t))
            if i is None:
                continue
            for key in ("mood", "target", "emotion", "moment", "blame"):
                columns[key][i] = _clean(getattr(row, key))
            columns["n"][i] = int(row.n_comments)
            flags["stale"][i] = int(bool(row.stale))
            flags["pending"][i] = int(bool(getattr(row, "pending", False)))
            if not row.stale:
                possible += 1
                answered += not bool(getattr(row, "pending", False))
        series[stream] = {**columns, **flags}

    counts = {stream: [0] * n for stream in stream_ids}
    for row in volume.itertuples():
        i = tick_index.get(_epoch(row.t))
        if i is not None and row.stream in counts:
            counts[row.stream][i] = int(row.n)

    subjects = sorted(s for s in tags["subject"].dropna().unique())
    subject_index = {name: i for i, name in enumerate(subjects)}
    tag_subject = [-1] * len(comment_index)
    tag_sentiment: list[float | None] = [None] * len(comment_index)
    tagged = [0] * len(comment_index)
    for row in tags.itertuples():
        i = comment_index.get(row.comment_id)
        if i is None:
            continue
        tagged[i] = 1
        subject = _clean(row.subject)
        tag_subject[i] = subject_index[subject] if subject is not None else -1
        tag_sentiment[i] = _clean(row.sentiment)

    moment_rows = []
    for row in moments.itertuples():
        moment_rows.append(
            {
                "id": row.moment_id,
                "start": _epoch(row.start),
                "peak": _epoch(row.peak),
                "end": _epoch(row.end),
                "streams": list(row.streams),
                "pMoment": _clean(row.p_moment),
                "peakVolume": int(row.peak_volume),
                "strength": float(row.strength),
                "play": _clean(row.play_description),
                "wpDelta": _clean(row.play_wp_delta),
                "inning": None if _clean(row.inning) is None else int(row.inning),
                "half": _clean(row.half),
                "lines": summaries.get(row.moment_id, {}),
            }
        )

    data = {
        "ticks": series,
        "volume": counts,
        "subjects": subjects,
        "tags": {"subject": tag_subject, "sentiment": tag_sentiment, "tagged": tagged},
        "moments": moment_rows,
    }
    return data, (answered / possible if possible else 0.0)


def read_anchors(path: Path, timeline: GameTimeline) -> list[dict]:
    """Hand-entered video anchors matched to the first pitch of their half-inning.

    Each row of ``anchors.csv`` gives the video time of a half-inning's first
    pitch ("T1", "B6"). Returns ``[{video, wall}]`` in video order; an empty
    list means the video is not synced yet.
    """
    if not path.exists():
        return []
    first_pitch: dict[str, int] = {}
    for play in timeline.plays:
        code = f"{'T' if play.half == 'top' else 'B'}{play.inning}"
        if code not in first_pitch:
            pitches = [e for e in play.events if e["isPitch"]]
            if pitches:
                first_pitch[code] = _epoch(pitches[0]["startTime"])
    anchors = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("half_inning") or "").strip().upper()
            if code in first_pitch and (row.get("video_seconds") or "").strip():
                anchors.append({"video": float(row["video_seconds"]), "wall": first_pitch[code], "half": code})
    return sorted(anchors, key=lambda a: a["video"])


def manifest(game_config: dict, timeline: GameTimeline, times: list[datetime], sources: list[dict], anchors: list[dict]) -> dict:
    return {
        "label": game_config["game"]["label"],
        "youtubeVideoId": game_config["game"]["youtube_video_id"],
        "home": {"abbr": timeline.home, "name": timeline.home_name},
        "away": {"abbr": timeline.away, "name": timeline.away_name},
        "firstPitch": _epoch(timeline.first_pitch()),
        "finalOut": _epoch(timeline.final_out()),
        "tickStart": _epoch(times[0]),
        "tickStep": game_config["tick"]["update_every_s"],
        "nTicks": len(times),
        "streams": [
            {
                "id": s["id"],
                "team": s["team"],
                "fanbase": FANBASE_LABELS[s["team"]],
                "community": community_label(s, timeline),
            }
            for s in game_config["streams"]
        ],
        "sources": sources,
        "anchors": anchors,
    }
