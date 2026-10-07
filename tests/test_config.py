from fanpulse_live import config


def test_game_config_matches_the_readme():
    loaded = config.load_game()

    assert loaded["game"]["date"] == "2025-10-24"
    assert loaded["game"]["youtube_video_id"] == "6BrK9Ep5M6o"
    assert (loaded["teams"]["home"]["abbr"], loaded["teams"]["away"]["abbr"]) == ("TOR", "LAD")
    assert loaded["tick"] == {
        "update_every_s": 5,
        "window_s": 20,
        "min_comments": 8,
        "max_window_s": 60,
        "play_lookback_s": 120,
    }


def test_threads_cover_game_and_postgame_in_each_subreddit():
    threads = config.load_game()["threads"]

    assert len({t["id"] for t in threads}) == 6
    assert {(t["subreddit"], t["type"]) for t in threads} == {
        (subreddit, kind)
        for subreddit in ("baseball", "Dodgers", "Torontobluejays")
        for kind in ("game", "postgame")
    }


def test_every_stream_maps_to_a_configured_subreddit_and_flair():
    loaded = config.load_game()
    subreddits = {t["subreddit"] for t in loaded["threads"]}

    assert len(loaded["streams"]) == 5
    for stream in loaded["streams"]:
        assert stream["subreddit"] in subreddits
        assert stream["flair"] in (None, "neutral", *loaded["flairs"])
