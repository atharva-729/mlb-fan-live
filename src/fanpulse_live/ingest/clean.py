"""Drop comments that carry no fan signal: deleted, bots, empty, media-only."""

from __future__ import annotations

import html
import re

import pandas as pd

BOT_AUTHORS = {"automoderator", "baseballbot", "dodgerbot", "sneakpeekbot", "remindmebot", "gifreversingbot"}
# Suffixes like "_bot" or "Bot"; a plain lowercase "bot" ending would catch real names (Talbot).
BOT_SUFFIX = re.compile(r"(?:[_-]bot|Bot)$")
DELETED_BODIES = {"[deleted]", "[removed]"}
# Inline gifs/images: ![gif](giphy|abc123) or ![img](xyz)
MEDIA_EMBED = re.compile(r"!\[(?:gif|img)\]\([^)]*\)")

REASONS = ("deleted", "bot", "empty", "media_only")

MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|/)[^)]*\)")
URL = re.compile(r"https?://\S+")
QUOTE_OR_HEADING = re.compile(r"^\s*(?:>+|#+)\s*", re.MULTILINE)
EMPHASIS = re.compile(r"(\*\*|__|~~|\*|`|\^)")
WHITESPACE = re.compile(r"\s+")


def clean_text(body: str, limit: int = 300) -> str:
    """Comment text as a model should read it: one line, no markup, links and gifs as tokens."""
    text = html.unescape(body)
    text = MEDIA_EMBED.sub(" [gif] ", text)
    text = MARKDOWN_LINK.sub(lambda m: m.group(1) or "[link]", text)
    text = URL.sub("[link]", text)
    text = QUOTE_OR_HEADING.sub("", text)
    text = EMPHASIS.sub("", text)
    text = WHITESPACE.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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
