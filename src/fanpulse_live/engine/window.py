"""Which comments one (update, stream) call looks at.

The dashboard updates every ``update_every_s`` but each call looks back
``window_s``, widening up to ``max_window_s`` when a stream is quiet, so a
reading rests on enough comments to be stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from fanpulse_live.ingest.clean import DELETED_BODIES
from fanpulse_live.jev.state import WindowComment


@dataclass(frozen=True)
class Window:
    comments: pd.DataFrame  # oldest first, with an ``is_new`` column
    window_used_s: int
    stale: bool  # too few comments even at the widest window; carry the last reading forward


def select_window(
    stream_comments: pd.DataFrame,
    t: datetime,
    *,
    update_every_s: int,
    window_s: int,
    min_comments: int,
    max_window_s: int,
) -> Window:
    """Comments in ``(t - window, t]`` for one stream, widened until there are enough.

    ``stream_comments`` must be one stream's comments sorted by ``created_utc``.
    A comment is new if it arrived since the previous update.
    """
    created = stream_comments["created_utc"]
    end = created.searchsorted(pd.Timestamp(t), side="right")
    used = window_s
    while True:
        start = created.searchsorted(pd.Timestamp(t) - timedelta(seconds=used), side="right")
        if end - start >= min_comments or used >= max_window_s:
            break
        used = min(used + update_every_s, max_window_s)

    comments = stream_comments.iloc[start:end].copy()
    comments["is_new"] = comments["created_utc"] > pd.Timestamp(t) - timedelta(seconds=update_every_s)
    return Window(comments, used, stale=len(comments) < min_comments)


def parent_bodies(comments_raw: pd.DataFrame) -> dict[str, str]:
    """Body by comment id, for reply context. Deleted and removed parents are left out."""
    bodies = comments_raw.dropna(subset=["body"])
    bodies = bodies[~bodies["body"].str.strip().isin(DELETED_BODIES)]
    return dict(zip(bodies["comment_id"], bodies["body"]))


def to_window_comments(window: pd.DataFrame, parents: dict[str, str]) -> list[WindowComment]:
    """Window rows as the state builder takes them. A reply's parent is ``t1_<comment id>``."""
    result = []
    for row in window.itertuples():
        parent_id = row.parent_id or ""
        parent = parents.get(parent_id[3:]) if parent_id.startswith("t1_") else None
        result.append(WindowComment(row.created_utc.to_pydatetime(), row.body, bool(row.is_new), parent))
    return result
