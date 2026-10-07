"""Everything one (update, stream) Jev call needs, assembled from the game and a stream's comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from fanpulse_live.engine.window import Window, select_window, to_window_comments
from fanpulse_live.gamestate import GameTimeline
from fanpulse_live.jev import questions
from fanpulse_live.jev import state as jev_state


@dataclass(frozen=True)
class PreparedCall:
    state: dict
    question_batches: list[dict[str, dict]]  # one Jev call each; the first carries the window questions
    window: Window
    new_numbers: list[int]  # the [k] of each new comment, in window order


def prepare_call(
    timeline: GameTimeline,
    t: datetime,
    stream: dict,
    stream_comments: pd.DataFrame,
    parents: dict[str, str],
    subjects: dict[str, str | None],
    game_config: dict,
) -> PreparedCall:
    """The state and questions for one stream at time ``t``.

    ``stream_comments`` is that stream's comments sorted by ``created_utc``;
    ``game_config`` is the loaded ``config/game.yaml``.
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
    comments = to_window_comments(window.comments, parents)
    state = jev_state.build_state(
        timeline,
        t,
        stream,
        comments,
        label=game_config["game"]["label"],
        window_s=window.window_used_s,
        play_lookback_s=tick["play_lookback_s"],
    )
    team_names = {timeline.home: timeline.home_name, timeline.away: timeline.away_name}
    new_numbers = jev_state.new_comment_numbers(comments)
    batches = questions.question_batches(team_names.get(stream["team"]), subjects, new_numbers)
    return PreparedCall(state, batches, window, new_numbers)
