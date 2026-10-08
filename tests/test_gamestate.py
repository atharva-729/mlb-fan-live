import pytest

from fanpulse_live.gamestate import GameTimeline, half_label, parse_time
from tests.feeds import make_feed, make_win_probability


@pytest.fixture
def timeline():
    return GameTimeline(make_feed(), make_win_probability())


def at(timeline, clock):
    return timeline.at(parse_time(f"2025-10-25T00:{clock}Z"))


def test_pregame_advisories_do_not_start_the_game(timeline):
    state = at(timeline, "05:00")

    assert state.status == "pregame"
    assert state.home_wp == 0.5


def test_count_and_outs_follow_the_pitches(timeline):
    state = at(timeline, "10:40")

    assert (state.status, state.inning, state.half) == ("in_play", 1, "top")
    assert (state.balls, state.strikes, state.outs) == (1, 1, 0)
    assert (state.batter_id, state.pitcher_id) == (1, 10)


def test_between_plays_keeps_outs_and_drops_the_matchup(timeline):
    state = at(timeline, "11:10")

    assert state.status == "between_plays"
    assert state.outs == 1
    assert state.batter_id is None
    assert state.home_wp == 0.52


def test_substitutions_show_only_once_announced(timeline):
    """The plate appearance starts at its first substitution, not its first pitch."""
    pinch_hitter_in = at(timeline, "11:30")
    reliever_in = at(timeline, "12:00")

    assert pinch_hitter_in.status == "in_play"
    assert (pinch_hitter_in.batter_id, pinch_hitter_in.pitcher_id) == (2, 10)  # Starter still pitching
    assert (reliever_in.batter_id, reliever_in.pitcher_id) == (2, 11)
    assert reliever_in.outs == 1


def test_runners_carry_into_the_next_state(timeline):
    state = at(timeline, "13:30")

    assert state.runners == {"1st": 2}
    assert state.home_wp == 0.48


def test_score_updates_when_the_run_scores_and_game_ends(timeline):
    assert at(timeline, "20:10").home_score == 0
    final = at(timeline, "21:00")
    assert (final.status, final.home_score, final.away_score) == ("final", 1, 0)


def test_recent_plays_are_newest_first_and_bounded_by_the_lookback(timeline):
    t = parse_time("2025-10-25T00:13:30Z")

    assert [p.index for p in timeline.recent_plays(t, 90)] == [1]
    assert [p.index for p in timeline.recent_plays(t, 200)] == [1, 0]
    assert timeline.recent_plays(parse_time("2025-10-25T00:12:59Z"), 90) == []


def test_player_lines_use_only_completed_plays(timeline):
    before = parse_time("2025-10-25T00:20:10Z")
    after = parse_time("2025-10-25T00:21:00Z")

    assert timeline.batting_line(1, after) == "0-for-1, K"
    assert timeline.batting_line(12, before) is None
    assert timeline.batting_line(12, after) == "1-for-1, HR, RBI"
    assert timeline.pitching_line(10, after) == "0.1 IP, 0 ER, 0 H, 0 BB, 1 K"
    assert timeline.pitching_line(11, after) == "0.0 IP, 0 ER, 1 H, 0 BB, 0 K"
    assert timeline.pitching_line(20, after) == "0.0 IP, 1 ER, 1 H, 0 BB, 0 K"


def test_first_pitch_and_final_out(timeline):
    assert timeline.first_pitch() == parse_time("2025-10-25T00:10:00Z")
    assert timeline.final_out() == parse_time("2025-10-25T00:20:30Z")


def test_half_label():
    assert half_label(1, "top") == "Top 1st"
    assert half_label(2, "bottom") == "Bottom 2nd"
    assert half_label(11, "top") == "Top 11th"


def test_recent_players_are_the_matchup_and_the_last_plays(timeline):
    during_single = parse_time("2025-10-25T00:12:40Z")  # Baker batting against Relief, 100s after the strikeout

    assert timeline.recent_players(during_single, 60) == {2, 11}
    assert timeline.recent_players(during_single, 900) == {1, 10, 2, 11}
    assert timeline.recent_players(parse_time("2025-10-25T00:05:00Z"), 900) == set()
