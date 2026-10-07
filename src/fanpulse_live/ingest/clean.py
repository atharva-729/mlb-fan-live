"""Drop comments that carry no fan signal: deleted, bots, empty, media-only."""

from __future__ import annotations

import re

import pandas as pd

BOT_AUTHORS = {"automoderator", "baseballbot", "dodgerbot", "sneakpeekbot", "remindmebot", "gifreversingbot"}
# Suffixes like "_bot" or "Bot"; a plain lowercase "bot" ending would catch real names (Talbot).
BOT_SUFFIX = re.compile(r"(?:[_-]bot|Bot)$")
DELETED_BODIES = {"[deleted]", "[removed]"}
# Inline gifs/images: ![gif](giphy|abc123) or ![img](xyz)
MEDIA_EMBED = re.compile(r"!\[(?:gif|img)\]\([^)]*\)")

REASONS = ("deleted", "bot", "empty", "media_only")


def drop_reason(author: str | None, body: str | None) -> str | None:
    """Why a comment should be dropped, or None to keep it."""
    text = (body or "").strip()
    if text in DELETED_BODIES:
        return "deleted"
    if author and (author.lower() in BOT_AUTHORS or BOT_SUFFIX.search(author)):
        return "bot"
    if not text:
        return "empty"
    if not MEDIA_EMBED.sub("", text).strip():
        return "media_only"
    return None


def clean_comments(comments: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Return the kept comments and a count of drops by reason."""
    reasons = pd.Series(
        [drop_reason(author, body) for author, body in zip(comments["author"], comments["body"])],
        index=comments.index,
        dtype="object",
    )
    kept = comments[reasons.isna()].reset_index(drop=True)
    dropped = reasons.dropna().value_counts().reindex(REASONS, fill_value=0)
    return kept, dropped
