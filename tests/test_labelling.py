import json
from datetime import timedelta

import pandas as pd
import pytest

from fanpulse_live.analysis import labelling
from fanpulse_live.gamestate import GameTimeline, parse_time
from fanpulse_live.jev import questions
from tests.feeds import make_feed, make_win_probability

CONFIG = {
    "game": {"label": "Test Game"},
    "tick": {"update_every_s": 5, "window_s": 20, "min_comments": 3, "max_window_s": 60, "play_lookback_s": 120},
    "streams": [
        {"id": "r/Dodgers", "subreddit": "Dodgers", "flair": None, "team": "LAD"},
        {"id": "r/baseball:neutral", "subreddit": "baseball", "flair": "neutral", "team": None},
    ],
}


@pytest.fixture
def timeline():
    return GameTimeline(make_feed(), make_win_probability())


def _steady_comments(timeline):
    """One comment every 2 seconds in each stream, from before first pitch to after the final out."""
    start = timeline.first_pitch() - timedelta(seconds=60)
    seconds = int((timeline.final_out() - start).total_seconds()) + 60
    rows = [
        {
            "comment_id": f"{stream['id']}-{s}",
            "stream": stream["id"],
            "thread_type": "game",
            "created_utc": pd.Timestamp(start) + pd.Timedelta(seconds=s),
            "body": f"comment at {s}",
            "parent_id": "t3_thread",
        }
        for stream in CONFIG["streams"]
        for s in range(0, seconds, 2)
    ]
    return pd.DataFrame(rows)


def test_tick_grid_runs_first_pitch_to_final_out_on_the_update_interval(timeline):
    grid = labelling.tick_grid(timeline, 5)

    assert grid[0] == parse_time("2025-10-25T00:10:00Z")
    assert grid[-1] == parse_time("2025-10-25T00:20:30Z")
    assert all((b - a).total_seconds() == 5 for a, b in zip(grid, grid[1:]))


def test_draw_sample_is_repeatable_and_covers_every_stream(timeline):
    comments = _steady_comments(timeline)
    subjects = questions.subject_options(timeline)

    first = labelling.draw_sample(timeline, comments, {}, subjects, CONFIG, per_stream=4, busy_per_stream=1)
    again = labelling.draw_sample(timeline, comments, {}, subjects, CONFIG, per_stream=4, busy_per_stream=1)

    assert first == again
    assert sorted(item["id"] for item in first) == [f"w{n:02d}" for n in range(1, 9)]
    assert {item["stream"] for item in first} == {"r/Dodgers", "r/baseball:neutral"}
    assert len({(item["stream"], item["time"]) for item in first}) == 8
    for item in first:
        assert 2 <= len(item["label_comments"]) <= 3
        assert all(1 <= k <= len(item["state"]["comments"]) for k in item["label_comments"])


def test_labelling_page_embeds_the_sample_and_labels_load_back(timeline, tmp_path):
    comments = _steady_comments(timeline)
    subjects = questions.subject_options(timeline)
    items = labelling.draw_sample(timeline, comments, {}, subjects, CONFIG, per_stream=2, busy_per_stream=1)

    sample_path, page_path = labelling.write_labelling_page(items, list(subjects), tmp_path)

    assert json.loads(sample_path.read_text(encoding="utf-8")) == items
    page = page_path.read_text(encoding="utf-8")
    assert "__DATA__" not in page
    assert items[0]["state"]["community"] in page

    exported = tmp_path / "labels.json"
    exported.write_text(json.dumps({"labels": {"w01": {"mood": 1, "target": "other", "moment": True, "comments": {}}}}))
    assert labelling.load_labels(exported)["w01"]["mood"] == 1
