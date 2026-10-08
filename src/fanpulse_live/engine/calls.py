"""Everything one (update, stream) Jev call needs, assembled from the game and a stream's comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from fanpulse_live import config
from fanpulse_live.analysis import baseline
from fanpulse_live.engine.window import Window, select_window, to_window_comments
from fanpulse_live.gamestate import GameTimeline, Player
from fanpulse_live.jev import questions
from fanpulse_live.jev import state as jev_state

# Defaults for the two cost caps, used when config/game.yaml does not set them.
MAX_CONTEXT_COMMENTS = 30
MAX_TAGGED_PER_UPDATE = 8
# How far back a play makes its batter and pitcher a subject option.
RECENT_PLAYERS_SECONDS = 900

_patterns: dict[int, list] = {}


def offered_players(timeline: GameTimeline, t: datetime, comments: list[jev_state.WindowComment]) -> list[Player]:
    """The players offered as subjects for one call, by team then name.

    Listing every player in every subject question was the largest cost of a
    call, and most are never the answer. Offered are the players in the action
    over the last fifteen minutes, who are who "he" usually means, and anyone a
    shown comment or the comment it replies to names outright. Anyone else is
    covered by the "another ... player" options.
    """
    if id(timeline) not in _patterns:
        _patterns[id(timeline)] = baseline.name_patterns(timeline, config.load_nicknames())
    patterns = _patterns[id(timeline)]
    ids = timeline.recent_players(t, RECENT_PLAYERS_SECONDS)
    named = set()
    for comment in comments:
        named |= baseline.mentioned_players(f"{comment.body} {comment.parent_body or ''}", patterns)
    players = [p for p in timeline.players.values() if p.id in ids or p.name in named]
    return sorted(players, key=lambda p: (p.team, p.name))


@dataclass(frozen=True)
class PreparedCall:
    state: dict
    question_batches: list[dict[str, dict]]  # one Jev call each; the first carries the window questions
    window: Window  # every comment in the window, for counts
    shown: pd.DataFrame  # the comments Jev sees, oldest first; ``is_new`` marks the ones it tags
    new_numbers: list[int]  # the [k] of each tagged comment, in shown order
    subjects: dict[str, str | None]


def choose_shown(
    window: pd.DataFrame, max_context: int, max_tagged: int, must_tag: set[str] | None = None
) -> pd.DataFrame:
    """Cut a busy window down to what Jev is shown and what it is asked about.

    Of the new comments, at most ``max_tagged`` are tagged, spread evenly over
    the update so a burst is sampled rather than only its tail (``must_tag``
    ids are always among them). Jev then sees those plus the most recent other
    comments, up to ``max_context`` in all, in time order.
    """
    must_tag = must_tag or set()
    new_positions = [i for i, is_new in enumerate(window["is_new"]) if is_new]
    forced = [i for i, comment_id in enumerate(window["comment_id"]) if comment_id in must_tag]
    free = [i for i in new_positions if i not in forced]
    room = max(max_tagged - len(forced), 0)
    if room <= 0:
        free = []
    elif room == 1:
        free = free[-1:]
    elif len(free) > room:
        free = [free[round(j * (len(free) - 1) / (room - 1))] for j in range(room)]
    tagged = set(forced) | set(free)

    keep = set(tagged)
    for i in range(len(window) - 1, -1, -1):
        if len(keep) >= max_context:
            break
        keep.add(i)
    shown = window.iloc[sorted(keep)].copy()
    shown["is_new"] = [i in tagged for i in sorted(keep)]
    return shown


def prepare_call(
    timeline: GameTimeline,
    t: datetime,
    stream: dict,
    stream_comments: pd.DataFrame,
    parents: dict[str, str],
    subjects: dict[str, str | None] | None,
    game_config: dict,
    must_tag: set[str] | None = None,
) -> PreparedCall:
    """The state and questions for one stream at time ``t``.

    ``stream_comments`` is that stream's comments sorted by ``created_utc``;
    ``game_config`` is the loaded ``config/game.yaml``. ``subjects`` is
    ignored: the subject options depend on the moment and are built here.
    """
    tick = game_config["tick"]
    window = select_window(
        stream_comments,
        t,
        update_every_s=tick["update_every_s"],
        window_s=tick["window_s"],
        min_comments=tick["min_comments"],
        max_window_s=tick["max_window_s"],
    )
    shown = choose_shown(
        window.comments,
        tick.get("max_context_comments", MAX_CONTEXT_COMMENTS),
        tick.get("max_tagged_per_update", MAX_TAGGED_PER_UPDATE),
        must_tag,
    )
    comments = to_window_comments(shown, parents)
    players = offered_players(timeline, t, comments)
    state = jev_state.build_state(
        timeline,
        t,
        stream,
        comments,
        label=game_config["game"]["label"],
        window_s=window.window_used_s,
        play_lookback_s=tick["play_lookback_s"],
        players=players,
    )
    team_names = {timeline.home: timeline.home_name, timeline.away: timeline.away_name}
    new_numbers = jev_state.new_comment_numbers(comments)
    options = questions.subject_options(timeline, players)
    batches = questions.question_batches(team_names.get(stream["team"]), options, new_numbers)
    return PreparedCall(state, batches, window, shown, new_numbers, options)
