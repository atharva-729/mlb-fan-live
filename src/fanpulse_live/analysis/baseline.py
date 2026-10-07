"""The free local baseline: RoBERTa sentiment per comment, and name matching for who it is about.

This is what Jev is compared against, and what the dashboard runs on when
there are no Jev readings. It reads tone only: it does not see the game, does
not know whose fans are talking, and takes sarcasm at face value.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable

import numpy as np
import pandas as pd

from fanpulse_live.gamestate import GameTimeline
from fanpulse_live.ingest.clean import clean_text

MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
LABELS = ("neg", "neu", "pos")
BATCH_SIZE = 64
MAX_TOKENS = 128
# First names shorter than this are too likely to be ordinary words or shared ("Will", "Max").
MIN_FIRST_NAME = 6
# Surnames that are also everyday words. "Call me a doomer" is not about Alex
# Call, so these players are matched by full name or nickname only.
ORDINARY_WORDS = {"call", "little", "france", "dean", "straw"}


def score_texts(texts: list[str], on_progress: Callable[[int, int], None] | None = None) -> np.ndarray:
    """Negative, neutral and positive probability for each text, as an (n, 3) array."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL)
    model.eval()
    # The model's own label order, mapped onto ours.
    order = [next(i for i, name in model.config.id2label.items() if name.lower().startswith(label)) for label in LABELS]

    cleaned = [clean_text(text, 500) for text in texts]
    # Batches of similar length waste far less padding.
    by_length = sorted(range(len(cleaned)), key=lambda i: len(cleaned[i]))
    scores = np.zeros((len(cleaned), 3), dtype=np.float32)
    with torch.no_grad():
        for start in range(0, len(by_length), BATCH_SIZE):
            batch = by_length[start : start + BATCH_SIZE]
            encoded = tokenizer(
                [cleaned[i] for i in batch], padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt"
            )
            probabilities = torch.softmax(model(**encoded).logits, dim=-1).numpy()
            scores[batch] = probabilities[:, order]
            if on_progress is not None:
                on_progress(min(start + BATCH_SIZE, len(by_length)), len(by_length))
    return scores


def scores_table(comment_ids: list[str], scores: np.ndarray) -> pd.DataFrame:
    """One row per comment; ``sentiment`` is P(positive) - P(negative), on -1..+1."""
    frame = pd.DataFrame(scores, columns=list(LABELS))
    frame.insert(0, "comment_id", comment_ids)
    frame["sentiment"] = (frame["pos"] - frame["neg"]).round(4)
    return frame


def _plain(text: str) -> str:
    """Lowercase without accents, so "Gimenez" finds "Giménez"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def name_patterns(timeline: GameTimeline, nicknames: dict[str, int] | None = None) -> list[tuple[re.Pattern, str]]:
    """A pattern per player: full name, surname, an unambiguous first name, and any nicknames.

    A surname or first name shared by two players on the rosters is left out,
    since a bare "Hernández" cannot be told apart.
    """
    players = list(timeline.players.values())
    surnames: dict[str, list[str]] = {}
    first_names: dict[str, list[str]] = {}
    for player in players:
        parts = _plain(player.name).replace(" jr.", "").split()
        first_names.setdefault(parts[0], []).append(player.name)
        surnames.setdefault(parts[-1], []).append(player.name)

    aliases: dict[str, set[str]] = {player.name: {_plain(player.name)} for player in players}
    for surname, owners in surnames.items():
        if len(owners) == 1 and surname not in ORDINARY_WORDS:
            aliases[owners[0]].add(surname)
    for first, owners in first_names.items():
        if len(owners) == 1 and len(first) >= MIN_FIRST_NAME and first not in surnames and first not in ORDINARY_WORDS:
            aliases[owners[0]].add(first)
    names_by_id = {player.id: player.name for player in players}
    for nickname, player_id in (nicknames or {}).items():
        if player_id in names_by_id:
            aliases[names_by_id[player_id]].add(_plain(nickname))

    return [
        (re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True)) + r")(?![a-z])"), name)
        for name, names in aliases.items()
    ]


def mentioned_player(text: str, patterns: list[tuple[re.Pattern, str]]) -> str | None:
    """The player a comment names, taking the earliest mention; None if it names nobody."""
    plain = _plain(text)
    best: tuple[int, str] | None = None
    for pattern, name in patterns:
        match = pattern.search(plain)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), name)
    return best[1] if best else None
