"""Finds the moments of the game: when several fanbases react at once to the same thing.

A stream is "hot" at an update when Jev says it is reacting to a specific
event and its raw comment volume spikes. An update is part of a moment when at
least two streams are hot. Nearby updates merge into one moment, which is then
linked to the play that most plausibly caused it.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from fanpulse_live.gamestate import GameTimeline

MOMENT_PROBABILITY = 0.85
VOLUME_Z = 2.0
# With no model to say whether a crowd is reacting to something (the baseline
# has none), volume alone has to carry it, so the bar is higher.
VOLUME_ONLY_Z = 3.0
MIN_STREAMS = 2
MERGE_SECONDS = 60
# Trailing stretch a stream's volume is compared against, in update intervals (10 minutes at 5s).
BASELINE_BUCKETS = 120
MIN_BASELINE_BUCKETS = 24


def volume_z(volume: pd.DataFrame) -> pd.DataFrame:
    """Rolling z-score of each stream's raw per-update comment count against its recent past."""
    frames = []
    for stream, group in volume.sort_values("t").groupby("stream"):
        past = group["n"].shift(1).rolling(BASELINE_BUCKETS, min_periods=MIN_BASELINE_BUCKETS)
        # A floor on the spread keeps a dead-quiet stretch from turning one comment into a spike.
        z = (group["n"] - past.mean()) / past.std().clip(lower=1.0)
        frames.append(group.assign(z=z.fillna(0.0)))
    return pd.concat(frames, ignore_index=True)


def hot_streams(ticks: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """One row per (update, stream) that is reacting to something: high P(moment) and a volume spike.

    A stale reading carries the stream's last P(moment) forward. A table with
    no P(moment) anywhere (the baseline) qualifies on a larger volume spike
    alone; readings still pending in a partly finished Jev run never qualify.
    """
    ticks = ticks.sort_values("t").copy()
    ticks["moment"] = pd.to_numeric(ticks["moment"], errors="coerce")
    volume_only = ticks["moment"].isna().all()
    if "pending" in ticks:
        ticks = ticks[~ticks["pending"].astype(bool)].copy()
    ticks["moment"] = ticks.groupby("stream")["moment"].ffill()
    merged = ticks.merge(volume_z(volume), on=["t", "stream"], how="inner")
    if volume_only:
        hot = merged[merged["z"] >= VOLUME_ONLY_Z]
    else:
        hot = merged[(merged["moment"] >= MOMENT_PROBABILITY) & (merged["z"] >= VOLUME_Z)]
    return hot[["t", "stream", "moment", "z", "n"]]


def find_moments(ticks: pd.DataFrame, volume: pd.DataFrame, timeline: GameTimeline, play_lookback_s: float) -> pd.DataFrame:
    """The game's moments, in time order, each with its streams, its strength and its play."""
    hot = hot_streams(ticks, volume)
    per_update = hot.groupby("t").agg(streams=("stream", "nunique"), strength=("z", "sum"))
    times = list(per_update.index[per_update["streams"] >= MIN_STREAMS])
    if not times:
        return pd.DataFrame(columns=MOMENT_COLUMNS)

    groups: list[list[pd.Timestamp]] = [[times[0]]]
    for t in times[1:]:
        if (t - groups[-1][-1]).total_seconds() <= MERGE_SECONDS:
            groups[-1].append(t)
        else:
            groups.append([t])

    rows = []
    for group in groups:
        start, end = group[0], group[-1]
        inside = hot[(hot["t"] >= start) & (hot["t"] <= end)]
        peak = per_update.loc[group, "strength"].idxmax()
        play = link_play(timeline, start, play_lookback_s)
        rows.append(
            {
                "start": start,
                "end": end,
                "peak": peak,
                "streams": sorted(inside["stream"].unique()),
                "n_streams": inside["stream"].nunique(),
                "p_moment": round(float(inside["moment"].mean()), 3) if inside["moment"].notna().any() else None,
                "peak_volume": int(inside.groupby("t")["n"].sum().max()),
                "strength": round(float(per_update.loc[group, "strength"].max()), 2),
                "play_index": play.index if play else None,
                "play_description": play.description if play else None,
                "play_seconds_before": round((start - play.end).total_seconds()) if play else None,
                "play_wp_delta": round(play.wp_delta, 4) if play else None,
                "inning": play.inning if play else None,
                "half": play.half if play else None,
            }
        )
    moments = pd.DataFrame(rows, columns=MOMENT_COLUMNS)
    moments.insert(0, "moment_id", [f"m{n:02d}" for n in range(1, len(moments) + 1)])
    return moments


MOMENT_COLUMNS = [
    "start", "end", "peak", "streams", "n_streams", "p_moment", "peak_volume", "strength",
    "play_index", "play_description", "play_seconds_before", "play_wp_delta", "inning", "half",
]  # fmt: skip


def link_play(timeline: GameTimeline, moment_start: pd.Timestamp, play_lookback_s: float):
    """The play a moment is about: of those that ended within the lookback, the biggest swing.

    Fans react late, so the latest play is often not the one being discussed.
    """
    t = moment_start.to_pydatetime()
    earliest = t - timedelta(seconds=play_lookback_s)
    candidates = [p for p in timeline.plays if earliest <= p.end <= t]
    return max(candidates, key=lambda p: abs(p.wp_delta)) if candidates else None
