"""Builds the state Jev reads on each call: who is talking, the game right now, and the comments.

Nothing later than time ``t`` goes in, and no season statistics: Jev reports
what fans feel, and numbers beyond today's line would pull its reading toward
what the stats say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fanpulse_live.gamestate import GameState, GameTimeline, Play, Player, half_label
from fanpulse_live.ingest.clean import clean_text
from fanpulse_live.jev.questions import subject_guide

REPLY_CONTEXT_CHARS = 80


@dataclass(frozen=True)
class WindowComment:
    created: datetime
    body: str
    is_new: bool  # marked * and asked about; in a burst only a sample of the new comments is
    parent_body: str | None = None


def community_label(stream: dict, timeline: GameTimeline) -> str:
    """Who the commenters are, from a stream entry in ``config/game.yaml``."""
    names = {timeline.home: timeline.home_name, timeline.away: timeline.away_name}
    subreddit = f"r/{stream['subreddit']}"
    if stream["team"] is None:
        return (
            f"{subreddit}, commenters without a {timeline.away_name} or {timeline.home_name} flair: "
            f"neutral viewers and fans of other teams"
        )
    team = names[stream["team"]]
    if stream["flair"] is None:
        return f"{subreddit}, the {team} fan community: {team} fans"
    return f"{subreddit}, a general baseball community, commenters with a {team} flair: {team} fans"


def _runners_text(runners: dict[str, int]) -> str:
    if not runners:
        return "bases empty"
    if len(runners) == 3:
        return "bases loaded"
    bases = [base for base in ("1st", "2nd", "3rd") if base in runners]
    return f"runner{'s' if len(bases) > 1 else ''} on " + " and ".join(bases)


def _score_text(state: GameState, timeline: GameTimeline) -> str:
    if state.home_score == state.away_score:
        return f"Tied {state.home_score}-{state.away_score}"
    if state.home_score > state.away_score:
        return f"{timeline.home_name} lead {state.home_score}-{state.away_score}"
    return f"{timeline.away_name} lead {state.away_score}-{state.home_score}"


def _player_text(player_id: int, timeline: GameTimeline) -> str:
    player = timeline.players[player_id]
    team = timeline.home_name if player.team == timeline.home else timeline.away_name
    return f"{player.name} ({team})"


def render_situation(label: str, state: GameState, timeline: GameTimeline) -> str:
    """One paragraph: inning, score, outs, runners, count, batter and pitcher."""
    matchup = f"{label}: {timeline.away_name} (away) at {timeline.home_name} (home)."
    if state.status == "pregame":
        return f"{matchup} The game has not started yet."
    score = _score_text(state, timeline)
    half = half_label(state.inning, state.half)
    if state.status == "final":
        return f"{matchup} The game is over. Final: {score.replace(' lead ', ' won ')}."
    if state.status == "between_innings":
        return f"{matchup} Between innings, after the {half.lower()}. {score}."

    outs = f"{state.outs} out{'' if state.outs == 1 else 's'}"
    text = f"{matchup} {half}, {outs}, {_runners_text(state.runners)}. {score}."
    if state.status == "between_plays":
        return f"{text} The next batter is coming up."
    text += f" Count {state.balls}-{state.strikes}."
    if state.batter_id:
        text += f" At bat: {_player_text(state.batter_id, timeline)}."
    if state.pitcher_id:
        text += f" Pitching: {_player_text(state.pitcher_id, timeline)}."
    return text


def render_win_probability(home_wp: float, team: str | None, timeline: GameTimeline) -> str:
    """Win probability from this fanbase's side; both sides for a neutral crowd."""
    if team is None:
        return f"{timeline.home_name} {home_wp:.0%}, {timeline.away_name} {1 - home_wp:.0%}"
    if team == timeline.home:
        return f"{timeline.home_name} {home_wp:.0%}"
    return f"{timeline.away_name} {1 - home_wp:.0%}"


def _swing_text(play: Play, team: str | None, timeline: GameTimeline) -> str:
    delta = play.wp_delta if team in (None, timeline.home) else -play.wp_delta
    name = timeline.away_name if team == timeline.away else timeline.home_name
    return f"{name} win probability {delta * 100:+.0f} points"


def render_recent_plays(
    timeline: GameTimeline, t: datetime, lookback_s: float, team: str | None
) -> list[str]:
    """Plays that ended within the lookback, newest first, with how long ago and the swing."""
    return [
        f"{(t - play.end).total_seconds():.0f}s ago: {play.description} ({_swing_text(play, team, timeline)})"
        for play in timeline.recent_plays(t, lookback_s)
    ]


def render_player_lines(timeline: GameTimeline, t: datetime, state: GameState, lookback_s: float) -> list[str]:
    """Today's line for the current batter and pitcher and everyone in the recent plays."""
    ids = [state.batter_id, state.pitcher_id]
    for play in timeline.recent_plays(t, lookback_s):
        ids += [play.batter_id, play.pitcher_id]
    lines = []
    for player_id in dict.fromkeys(i for i in ids if i):
        line = timeline.player_line(player_id, t)
        if line:
            lines.append(f"{_player_text(player_id, timeline)}: {line}")
    return lines


def render_rosters(timeline: GameTimeline, players: list[Player]) -> dict[str, str]:
    """Which team each offered player is on, once per call, so the subject options can be bare names."""
    rosters = {}
    for abbr, team in ((timeline.away, timeline.away_name), (timeline.home, timeline.home_name)):
        names = [player.name for player in players if player.team == abbr]
        if names:
            rosters[team] = ", ".join(names)
    return rosters


def render_comments(comments: list[WindowComment], t: datetime) -> list[str]:
    """Numbered comments, oldest first; ``*`` marks the ones new since the last update."""
    rendered = []
    for k, comment in enumerate(sorted(comments, key=lambda c: c.created), start=1):
        marker = "*" if comment.is_new else ""
        text = f"[{k}]{marker} {(t - comment.created).total_seconds():.0f}s ago: {clean_text(comment.body)}"
        if comment.parent_body:
            parent = clean_text(comment.parent_body, REPLY_CONTEXT_CHARS)
            text += f' ↳ replying to: "{parent}"'
        rendered.append(text)
    return rendered


def new_comment_numbers(comments: list[WindowComment]) -> list[int]:
    """The ``[k]`` numbers of the new comments, matching ``render_comments``."""
    ordered = sorted(comments, key=lambda c: c.created)
    return [k for k, comment in enumerate(ordered, start=1) if comment.is_new]


def build_state(
    timeline: GameTimeline,
    t: datetime,
    stream: dict,
    comments: list[WindowComment],
    *,
    label: str,
    window_s: float,
    play_lookback_s: float,
    players: list[Player] | None = None,
) -> dict:
    """The state for one (update, stream) call."""
    state = timeline.at(t)
    return {
        "community": community_label(stream, timeline),
        "game_situation": render_situation(label, state, timeline),
        "win_probability": render_win_probability(state.home_wp, stream["team"], timeline),
        "recent_plays": render_recent_plays(timeline, t, play_lookback_s, stream["team"])
        or [f"No play has ended in the last {play_lookback_s:.0f} seconds."],
        "players_today": render_player_lines(timeline, t, state, play_lookback_s),
        "players": render_rosters(timeline, players if players is not None else list(timeline.players.values())),
        "subject_guide": subject_guide(timeline),
        "comments_note": f"Recent comments from the last {window_s:.0f} seconds, oldest first. "
        f"A * after the number marks a new comment you are asked about.",
        "comments": render_comments(comments, t),
    }
