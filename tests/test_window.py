import pandas as pd

from fanpulse_live.engine import calls, window
from fanpulse_live.gamestate import parse_time

T = parse_time("2025-10-25T01:00:00Z")
TICK = {"update_every_s": 5, "window_s": 20, "min_comments": 3, "max_window_s": 60}


def _comments(seconds_ago, parent_ids=None):
    ordered = sorted(seconds_ago, reverse=True)
    return pd.DataFrame(
        {
            "comment_id": [f"c{s}" for s in ordered],
            "created_utc": [pd.Timestamp(T) - pd.Timedelta(seconds=s) for s in ordered],
            "body": [f"comment {s}s ago" for s in ordered],
            "parent_id": parent_ids or ["t3_thread"] * len(ordered),
        }
    )


def test_window_takes_the_last_twenty_seconds_and_marks_new_comments():
    selected = window.select_window(_comments([30, 19, 12, 4, 0]), T, **TICK)

    assert list(selected.comments["comment_id"]) == ["c19", "c12", "c4", "c0"]
    assert list(selected.comments["is_new"]) == [False, False, True, True]
    assert (selected.window_used_s, selected.stale) == (20, False)


def test_window_edges_are_open_at_the_start_and_closed_at_the_end():
    selected = window.select_window(_comments([20, 19, 5, 0]), T, **TICK)

    assert list(selected.comments["comment_id"]) == ["c19", "c5", "c0"]
    assert list(selected.comments["is_new"]) == [False, False, True]  # exactly 5s ago belonged to the last update


def test_quiet_stream_widens_only_as_far_as_it_needs():
    selected = window.select_window(_comments([50, 33, 28, 3]), T, **TICK)

    assert list(selected.comments["comment_id"]) == ["c33", "c28", "c3"]
    assert (selected.window_used_s, selected.stale) == (35, False)


def test_still_too_quiet_at_the_widest_window_is_stale():
    selected = window.select_window(_comments([90, 40, 3]), T, **TICK)

    assert (selected.window_used_s, selected.stale) == (60, True)
    assert len(selected.comments) == 2


def test_comments_after_t_are_never_seen():
    comments = _comments([10, 5, 2])
    later = pd.DataFrame(
        {
            "comment_id": ["future"],
            "created_utc": [pd.Timestamp(T) + pd.Timedelta(seconds=3)],
            "body": ["spoiler"],
            "parent_id": ["t3_thread"],
        }
    )

    selected = window.select_window(pd.concat([comments, later], ignore_index=True), T, **TICK)

    assert "future" not in list(selected.comments["comment_id"])


def test_reply_context_comes_from_the_parent_comment_unless_deleted():
    raw = pd.DataFrame(
        {
            "comment_id": ["p1", "p2", "p3"],
            "body": ["Banda is cooked", "[deleted]", None],
        }
    )
    parents = window.parent_bodies(raw)
    selected = window.select_window(_comments([9, 6, 3, 1], ["t3_thread", "t1_p1", "t1_p2", "t1_gone"]), T, **TICK)

    built = window.to_window_comments(selected.comments, parents)

    assert [c.parent_body for c in built] == [None, "Banda is cooked", None, None]
    assert [c.is_new for c in built] == [False, False, True, True]
    assert built[0].created == T - pd.Timedelta(seconds=9)


def _window(n_old, n_new):
    """n_old comments from before the update, then n_new new ones, oldest first."""
    frame = pd.DataFrame(
        {
            "comment_id": [f"old{i}" for i in range(n_old)] + [f"new{i}" for i in range(n_new)],
            "is_new": [False] * n_old + [True] * n_new,
        }
    )
    return frame


def test_a_quiet_window_is_shown_and_tagged_whole():
    shown = calls.choose_shown(_window(5, 3), max_context=30, max_tagged=8)

    assert len(shown) == 8
    assert list(shown["comment_id"][shown["is_new"]]) == ["new0", "new1", "new2"]


def test_a_burst_is_sampled_evenly_and_shown_with_the_latest_context():
    shown = calls.choose_shown(_window(60, 25), max_context=30, max_tagged=8)
    tagged = list(shown["comment_id"][shown["is_new"]])

    assert len(shown) == 30
    assert len(tagged) == 8
    assert tagged[0] == "new0" and tagged[-1] == "new24"  # spread across the burst, not only its tail
    assert list(shown["comment_id"]) == sorted(shown["comment_id"], key=list(_window(60, 25)["comment_id"]).index)
    # The 30 shown are the most recent: all 25 new comments and the 5 just before them.
    assert "old55" in set(shown["comment_id"]) and "old54" not in set(shown["comment_id"])


def test_labelled_comments_can_be_forced_into_the_tagged_sample():
    shown = calls.choose_shown(_window(10, 25), max_context=30, max_tagged=8, must_tag={"new3", "old2"})
    tagged = set(shown["comment_id"][shown["is_new"]])

    assert {"new3", "old2"} <= tagged
    assert len(tagged) == 8
