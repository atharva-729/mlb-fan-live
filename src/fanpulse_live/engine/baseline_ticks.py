"""The tick tables built from the free baseline instead of Jev.

Same shape as the Jev tables, so moments, export and the dashboard work on
either. What the baseline cannot give is left empty: emotion, P(moment) and
blame. Mood is the mean RoBERTa sentiment of the window, which treats a fan's
tone as their feeling about their own team.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

import pandas as pd

from fanpulse_live.analysis import baseline
from fanpulse_live.engine.window import select_window
from fanpulse_live.gamestate import GameTimeline

TOP_CHOICES = 3
# A window's main subject must be named by at least this many of its comments.
MIN_MENTIONS = 2


def tag_comments(
    comments: pd.DataFrame, scores: pd.DataFrame, timeline: GameTimeline, nicknames: dict[str, int] | None = None
) -> pd.DataFrame:
    """Game-thread comments with the baseline's sentiment and the player each one names, if any."""
    patterns = baseline.name_patterns(timeline, nicknames)
    tagged = comments[comments["thread_type"] == "game"].merge(scores[["comment_id", "sentiment"]], on="comment_id")
    tagged["subject"] = [baseline.mentioned_player(body, patterns) for body in tagged["body"]]
    return tagged.sort_values("created_utc").reset_index(drop=True)


def build_ticks(tagged: pd.DataFrame, times: list[datetime], game_config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The baseline ticks table and comment tags for every (update, stream)."""
    tick = game_config["tick"]
    rows, tag_rows = [], []
    for stream in game_config["streams"]:
        in_stream = tagged[tagged["stream"] == stream["id"]]
        for t in times:
            window = select_window(
                in_stream,
                t,
                update_every_s=tick["update_every_s"],
                window_s=tick["window_s"],
                min_comments=tick["min_comments"],
                max_window_s=tick["max_window_s"],
            )
            inside = window.comments
            row = {
                "t": pd.Timestamp(t),
                "stream": stream["id"],
                "window_used_s": window.window_used_s,
                "n_comments": len(inside),
                "n_new": int(inside["is_new"].sum()),
                "stale": window.stale,
                "pending": False,
                "mood": None, "mood_confidence": None, "target": None, "target_top": None,
                "emotion": None, "emotion_probs": None, "moment": None, "blame": None,
            }  # fmt: skip
            if not window.stale:
                if stream["team"] is not None:
                    row["mood"] = round(float(inside["sentiment"].mean()), 3)
                mentions = Counter(inside["subject"].dropna())
                top = [[name, round(count / len(inside), 3)] for name, count in mentions.most_common(TOP_CHOICES)]
                if top and mentions.most_common(1)[0][1] >= MIN_MENTIONS:
                    row["target"] = top[0][0]
                row["target_top"] = json.dumps(top, ensure_ascii=False)
            rows.append(row)

            new = inside[inside["is_new"]]
            for comment in new.itertuples():
                tag_rows.append(
                    {
                        "comment_id": comment.comment_id,
                        "stream": stream["id"],
                        "t": pd.Timestamp(t),
                        "subject": comment.subject,
                        "subject_confidence": None,
                        "sentiment": round(float(comment.sentiment), 3),
                        "sentiment_confidence": None,
                    }
                )

    ticks = pd.DataFrame(rows).sort_values(["t", "stream"]).reset_index(drop=True)
    tags = pd.DataFrame(tag_rows).sort_values(["t", "stream"]).reset_index(drop=True)
    return ticks, tags
