import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from fanpulse_live import export
from fanpulse_live.analysis import baseline
from fanpulse_live.engine import baseline_ticks, moments, summarize, ticks
from fanpulse_live.gamestate import GameTimeline, parse_time
from tests.feeds import make_feed, make_win_probability

CONFIG = {
    "game": {"label": "Test Game", "youtube_video_id": "abc"},
    "tick": {"update_every_s": 5, "window_s": 20, "min_comments": 2, "max_window_s": 60, "play_lookback_s": 120},
    "streams": [
        {"id": "r/Dodgers", "subreddit": "Dodgers", "flair": None, "team": "LAD"},
        {"id": "r/Torontobluejays", "subreddit": "Torontobluejays", "flair": None, "team": "TOR"},
        {"id": "r/baseball:neutral", "subreddit": "baseball", "flair": "neutral", "team": None},
    ],
}


@pytest.fixture
def timeline():
    return GameTimeline(make_feed(), make_win_probability())


def test_name_patterns_match_surnames_full_names_and_nicknames(timeline):
    patterns = baseline.name_patterns(timeline, {"Hammer": 12})

    assert baseline.mentioned_player("BAKER WHAT", patterns) == "Bo Baker"
    assert baseline.mentioned_player("hal homer is him", patterns) == "Hal Homer"
    assert baseline.mentioned_player("the Hammer strikes", patterns) == "Hal Homer"
    assert baseline.mentioned_player("relief finally", patterns) == "Rex Relief"
    assert baseline.mentioned_player("homer off Visitor, wow", patterns) == "Hal Homer"  # earliest mention wins
    assert baseline.mentioned_player("what a game", patterns) is None
    assert baseline.mentioned_player("unable to watch", patterns) is None  # "Able" inside a word


def test_scores_table_turns_probabilities_into_one_number():
    table = baseline.scores_table(["a", "b"], np.array([[0.8, 0.1, 0.1], [0.05, 0.15, 0.8]], dtype=np.float32))

    assert list(table["comment_id"]) == ["a", "b"]
    assert list(table["sentiment"]) == pytest.approx([-0.7, 0.75])


def _comments(timeline):
    """A quiet game with a burst in every stream 40-55s after the home run."""
    start = timeline.first_pitch() - timedelta(seconds=300)
    homer_end = timeline.plays[-1].end
    rows = []
    for stream in CONFIG["streams"]:
        seconds = list(range(0, 1100, 10))
        burst = [(homer_end - start).total_seconds() + s for s in np.arange(40, 55, 0.5)]
        for n, s in enumerate(sorted(seconds + burst)):
            in_burst = s in burst
            rows.append(
                {
                    "comment_id": f"{stream['id']}-{n}",
                    "stream": stream["id"],
                    "thread_type": "game",
                    "created_utc": pd.Timestamp(start) + pd.Timedelta(seconds=float(s)),
                    "body": "HOMER!!!" if in_burst else "just chatting",
                    "parent_id": "t3_thread",
                }
            )
    comments = pd.DataFrame(rows)
    scores = pd.DataFrame(
        {
            "comment_id": comments["comment_id"],
            "sentiment": [
                (0.9 if stream == "r/Torontobluejays" else -0.8) if body == "HOMER!!!" else 0.0
                for stream, body in zip(comments["stream"], comments["body"])
            ],
        }
    )
    return comments, scores


def test_baseline_ticks_and_moments_find_the_home_run(timeline):
    comments, scores = _comments(timeline)
    times = ticks.tick_times(timeline, 5)
    tagged = baseline_ticks.tag_comments(comments, scores, timeline)
    tick_table, tags = baseline_ticks.build_ticks(tagged, times, CONFIG)
    volume = ticks.volume_buckets(comments, times, 5)

    assert len(tick_table) == len(times) * 3
    assert volume["n"].sum() == ((comments["created_utc"] > times[0] - timedelta(seconds=5)) & (comments["created_utc"] <= times[-1])).sum()
    assert tick_table.loc[tick_table["stream"] == "r/baseball:neutral", "mood"].isna().all()

    peak = pd.Timestamp(timeline.plays[-1].end + timedelta(seconds=55)).floor("5s")
    at_peak = tick_table[tick_table["t"] == peak].set_index("stream")
    assert at_peak.loc["r/Torontobluejays", "mood"] > 0.5
    assert at_peak.loc["r/Dodgers", "mood"] < -0.5
    assert at_peak.loc["r/Dodgers", "target"] == "Hal Homer"
    assert set(tags["subject"].dropna()) == {"Hal Homer"}

    found = moments.find_moments(tick_table, volume, timeline, 120)

    assert len(found) == 1
    moment = found.iloc[0]
    assert moment["moment_id"] == "m01"
    assert moment["n_streams"] == 3
    assert moment["play_description"] == "Hal Homer homers (1) to left field."
    assert 30 <= moment["play_seconds_before"] <= 60
    assert moment["p_moment"] is None or pd.isna(moment["p_moment"])


def test_moments_need_the_model_to_agree_when_there_is_one(timeline):
    comments, scores = _comments(timeline)
    times = ticks.tick_times(timeline, 5)
    tick_table, _ = baseline_ticks.build_ticks(baseline_ticks.tag_comments(comments, scores, timeline), times, CONFIG)
    volume = ticks.volume_buckets(comments, times, 5)

    tick_table["moment"] = 0.2  # a model that says nobody is reacting to anything
    assert moments.find_moments(tick_table, volume, timeline, 120).empty

    tick_table["moment"] = 0.95
    assert len(moments.find_moments(tick_table, volume, timeline, 120)) == 1


def test_link_play_prefers_the_biggest_swing_in_the_lookback(timeline):
    after_single = pd.Timestamp(parse_time("2025-10-25T00:13:40Z"))

    assert moments.link_play(timeline, after_single, 60).index == 1  # only the single is in range
    assert moments.link_play(timeline, after_single, 200).index == 1  # the single (-4) outweighs the strikeout (+2)
    assert moments.link_play(timeline, pd.Timestamp(parse_time("2025-10-25T00:19:00Z")), 60) is None


def test_export_aligns_everything_to_the_updates(timeline, tmp_path):
    comments, scores = _comments(timeline)
    times = ticks.tick_times(timeline, 5)
    tagged = baseline_ticks.tag_comments(comments, scores, timeline)
    tick_table, tags = baseline_ticks.build_ticks(tagged, times, CONFIG)
    volume = ticks.volume_buckets(comments, times, 5)
    found = moments.find_moments(tick_table, volume, timeline, 120)
    stream_ids = [s["id"] for s in CONFIG["streams"]]

    game = export.game_file(timeline, times)
    comment_data, index = export.comments_file(comments, stream_ids, int(times[0].timestamp()), int(times[-1].timestamp()))
    data, coverage = export.source_file(tick_table, tags, volume, found, {"m01": {"Dodgers fans": "Gutted."}}, stream_ids, times, index)

    assert all(len(series) == len(times) for series in game["state"].values())
    assert game["state"]["status"][0] == "pregame" and game["state"]["status"][-1] == "final"
    assert game["players"]["Hal Homer"]["lines"][-1][1] == "1-for-1, HR, RBI"
    assert game["players"]["Vic Visitor"]["lines"][-1][1] == "0.0 IP, 1 ER, 1 H, 0 BB, 0 K"
    assert comment_data["t"] == sorted(comment_data["t"])
    assert len(data["ticks"]["r/Dodgers"]["mood"]) == len(times)
    assert len(data["tags"]["subject"]) == len(comment_data["t"])
    assert data["subjects"] == ["Hal Homer"]
    assert data["moments"][0]["lines"] == {"Dodgers fans": "Gutted."}
    assert coverage == 1.0
    export._write(tmp_path / "x.json", data)  # no NaN may reach the JSON
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8"))["moments"][0]["id"] == "m01"

    manifest = export.manifest(CONFIG, timeline, times, [{"id": "baseline"}], [])
    assert manifest["nTicks"] == len(times)
    assert [s["fanbase"] for s in manifest["streams"]] == ["Dodgers fans", "Blue Jays fans", "Neutral fans"]


def test_read_anchors_matches_half_innings_to_first_pitches(timeline, tmp_path):
    path = tmp_path / "anchors.csv"
    path.write_text("half_inning,video_seconds,note\nB1,1580,\nT1,754,first pitch\nT9,,\n", encoding="utf-8")

    anchors = export.read_anchors(path, timeline)

    assert [a["half"] for a in anchors] == ["T1", "B1"]
    assert anchors[0]["wall"] == int(parse_time("2025-10-25T00:10:00Z").timestamp())
    assert export.read_anchors(tmp_path / "missing.csv", timeline) == []


def test_half_innings_say_where_each_one_starts_and_ends(timeline):
    halves = export.half_innings(timeline)

    assert [h["code"] for h in halves] == ["T1", "B1"]
    assert halves[0]["batter"] == "Al Able" and halves[0]["pitcher"] == "Sam Starter"
    assert halves[0]["firstPitch"] == int(parse_time("2025-10-25T00:10:00Z").timestamp())
    assert halves[0]["end"] == int(parse_time("2025-10-25T00:13:00Z").timestamp())
    assert halves[1]["batter"] == "Hal Homer"


def test_parse_lines_reads_json_inside_a_code_fence():
    reply = 'Here you go:\n```json\n{"Dodgers fans": " Blaming the bullpen. ", "Mets fans": "n/a"}\n```'

    assert summarize.parse_lines(reply) == {"Dodgers fans": "Blaming the bullpen."}
    with pytest.raises(ValueError):
        summarize.parse_lines("no json here")


def test_sync_points_add_a_mid_inning_pitching_change(timeline):
    points = export.sync_points(timeline)

    assert [p["code"] for p in points] == ["T1", "T1P1", "B1"]
    change = points[1]
    assert change["what"] == "Rex Relief's first pitch" and change["batter"] == "Bo Baker"
    assert change["wall"] == int(parse_time("2025-10-25T00:12:30Z").timestamp())


def test_anchors_can_use_pitching_change_points_and_are_checked(timeline, tmp_path):
    path = tmp_path / "anchors.csv"
    path.write_text("half_inning,video_seconds,note\nT1,100,\nT1P1,200,\nB1,900,\n", encoding="utf-8")

    anchors = export.read_anchors(path, timeline)

    assert [a["half"] for a in anchors] == ["T1", "T1P1", "B1"]
    # T1P1 to B1 is 450s of game; 700s of video cannot fit in it.
    assert export.check_anchors(anchors) == ["T1P1 to B1: 700s of video for 450s of game, so one of the two is misplaced"]
    assert export.check_anchors(anchors[:2]) == []
