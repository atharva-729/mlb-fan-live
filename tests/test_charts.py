import pandas as pd

from fanpulse_live.viz import charts

STREAMS = ["r/Dodgers", "r/Torontobluejays"]


def test_comments_per_minute_fills_quiet_minutes_and_missing_streams():
    comments = pd.DataFrame(
        {
            "created_utc": pd.to_datetime(
                ["2025-10-25T01:00:05Z", "2025-10-25T01:00:50Z", "2025-10-25T01:03:10Z"]
            ),
            "stream": ["r/Dodgers", "r/Dodgers", "r/Dodgers"],
        }
    )

    per_minute = charts.comments_per_minute(comments, STREAMS)

    assert list(per_minute.columns) == STREAMS
    assert list(per_minute["r/Dodgers"]) == [2, 0, 0, 1]
    assert per_minute["r/Torontobluejays"].sum() == 0


def test_win_prob_steps_starts_from_the_first_plays_before_value():
    plays = pd.DataFrame(
        {
            "game_pk": [1, 1],
            "at_bat_index": [0, 1],
            "start_time_utc": pd.to_datetime(["2025-10-25T00:14:00Z", "2025-10-25T00:16:00Z"]),
            "end_time_utc": pd.to_datetime(["2025-10-25T00:15:00Z", "2025-10-25T00:17:00Z"]),
            "description": ["strikeout", "home run"],
        }
    )
    win_prob = pd.DataFrame(
        {
            "game_pk": [1, 1],
            "at_bat_index": [0, 1],
            "home_wp_before": [0.5, 0.52],
            "home_wp_after": [0.52, 0.4],
        }
    )

    steps = charts.win_prob_steps(plays, win_prob)

    assert list(steps["home_wp"]) == [0.5, 0.52, 0.4]
    assert steps.loc[0, "time"] == plays.loc[0, "start_time_utc"]


def test_stream_colors_are_fixed_by_position():
    assert charts.stream_colors(STREAMS) == {"r/Dodgers": "#2a78d6", "r/Torontobluejays": "#eb6834"}
