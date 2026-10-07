import pandas as pd

from fanpulse_live.analysis import lag

T0 = pd.Timestamp("2025-10-25T01:00:00Z")


def _times(seconds):
    return pd.Series([T0 + pd.Timedelta(seconds=s) for s in seconds])


def test_reaction_curve_finds_a_burst_at_a_known_lag():
    play_ends = _times([0, 1000, 2000])
    # One comment per 5s bucket as background, plus a burst 20-24s after each play.
    background = list(range(-300, 2400, 5))
    burst = [end + lag_s for end in (0, 1000, 2000) for lag_s in (20, 21, 22, 23, 24) for _ in range(4)]

    curve = lag.reaction_curve(_times(background + burst), play_ends, pd.Series([1.0, 1.0, 1.0]))
    stats = lag.lag_stats(curve)

    assert curve.idxmax() == 20
    assert stats["peak_lag_s"] == 25  # the end of the 20-25s bucket
    assert stats["tail_lag_s"] == 25
    assert stats["baseline"] == 1.0


def test_reaction_curve_weights_plays_by_their_swing():
    play_ends = _times([0, 1000])
    # The first play draws a reaction at 10s, the second at 40s.
    comments = _times([10] * 10 + [1040] * 10)

    heavy_first = lag.reaction_curve(comments, play_ends, pd.Series([0.9, 0.1]))
    heavy_second = lag.reaction_curve(comments, play_ends, pd.Series([0.1, 0.9]))

    assert heavy_first.idxmax() == 10
    assert heavy_second.idxmax() == 40


def test_tail_lag_covers_a_slow_tail():
    play_ends = _times([0])
    background = list(range(-300, 400, 5))
    # 60 comments at 10s, then a thinner tail out to 60s.
    reaction = [10] * 60 + [30] * 20 + [60] * 20

    stats = lag.lag_stats(lag.reaction_curve(_times(background + reaction), play_ends, pd.Series([1.0])))

    assert stats["peak_lag_s"] == 15
    assert stats["tail_lag_s"] == 65
