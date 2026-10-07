"""Route each comment to its fanbase stream and tag it pregame / in-game / postgame."""

from __future__ import annotations

import hashlib

import pandas as pd

NEUTRAL = "neutral"


def flair_side(flair: str | None, flairs: dict[str, list[str]]) -> str:
    """Which fanbase a flair marks: a key of ``flairs``, or neutral.

    A flair naming both teams (or neither) is neutral.
    """
    # A missing flair arrives as None from the API and as NaN out of a parquet table.
    text = flair.lower() if isinstance(flair, str) else ""
    sides = [side for side, needles in flairs.items() if any(needle.lower() in text for needle in needles)]
    return sides[0] if len(sides) == 1 else NEUTRAL


def assign_streams(comments: pd.DataFrame, streams: list[dict], flairs: dict[str, list[str]]) -> pd.Series:
    """The stream id for each comment.

    A subreddit whose streams carry a ``flair`` is split by the author's flair;
    otherwise every comment in it goes to its single stream.
    """
    by_key = {(s["subreddit"].lower(), s["flair"]): s["id"] for s in streams}
    split = {s["subreddit"].lower() for s in streams if s["flair"] is not None}

    def stream_id(subreddit: str, flair: str | None) -> str:
        subreddit = subreddit.lower()
        return by_key[(subreddit, flair_side(flair, flairs) if subreddit in split else None)]

    return pd.Series(
        [stream_id(s, f) for s, f in zip(comments["subreddit"], comments["author_flair_text"])],
        index=comments.index,
        dtype="object",
    )


def tag_phase(created_utc: pd.Series, first_pitch: pd.Timestamp, final_out: pd.Timestamp) -> pd.Series:
    phase = pd.Series("in-game", index=created_utc.index, dtype="object")
    phase[created_utc < first_pitch] = "pregame"
    phase[created_utc > final_out] = "postgame"
    return phase


def hash_author(author: str | None) -> str | None:
    """A stable stand-in for a username, enough to count unique commenters."""
    if not author or author == "[deleted]":
        return None
    return hashlib.sha256(author.encode("utf-8")).hexdigest()[:12]
