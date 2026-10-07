import pandas as pd
import pytest

from fanpulse_live.ingest import clean, reddit

THREADS = [
    {"id": "gt", "subreddit": "baseball", "type": "game"},
    {"id": "pg", "subreddit": "Dodgers", "type": "postgame"},
]


def _post(post_id, subreddit, title="a thread"):
    return {
        "id": post_id,
        "title": title,
        "created_utc": 1761346800,
        "subreddit": subreddit,
        "author": "BaseballBot",
        "num_comments": 10,
    }


def test_fetch_threads_by_id_keeps_config_order(monkeypatch):
    posts = [_post("pg", "Dodgers"), _post("gt", "baseball")]
    monkeypatch.setattr(reddit, "_get", lambda url, params, **kwargs: posts)

    assert [p["id"] for p in reddit.fetch_threads_by_id(["gt", "pg"])] == ["gt", "pg"]


def test_fetch_threads_by_id_names_missing_threads(monkeypatch):
    monkeypatch.setattr(reddit, "_get", lambda url, params, **kwargs: [_post("gt", "baseball")])

    with pytest.raises(reddit.ThreadLookupError, match="pg"):
        reddit.fetch_threads_by_id(["gt", "pg"])


def test_build_threads_takes_type_from_config():
    frame = reddit.build_threads([_post("gt", "baseball"), _post("pg", "Dodgers")], THREADS, game_pk=1)

    assert list(frame["thread_type"]) == ["game", "postgame"]
    assert list(frame["subreddit"]) == ["baseball", "Dodgers"]
    assert frame.loc[0, "created_utc"] == pd.Timestamp("2025-10-24T23:00:00Z")


def _comment(comment_id, second):
    return {"id": comment_id, "created_utc": second, "body": "x", "author": "a", "link_id": "t3_t", "parent_id": "t3_t"}


def test_fetch_comments_pages_without_losing_same_second_comments(monkeypatch):
    """Arctic Shift's ``after`` is exclusive, so comments sharing the boundary second must not be lost."""
    monkeypatch.setattr(reddit, "PAGE_SIZE", 3)
    everything = [_comment("a", 10), _comment("b", 11), _comment("c", 12), _comment("d", 12), _comment("e", 15)]
    requests_made = []

    def fake_get(url, params, **kwargs):
        requests_made.append(params.get("after"))
        after = params.get("after", -1)
        return [c for c in everything if c["created_utc"] > after][: params["limit"]]

    monkeypatch.setattr(reddit, "_get", fake_get)

    comments = reddit.fetch_comments("t", thread_created_utc=0)

    assert [c["id"] for c in comments] == ["a", "b", "c", "d", "e"]
    assert requests_made == [None, 11, 14]


def test_fetch_comments_pages_an_archive_whose_after_is_inclusive(monkeypatch):
    """PullPush returns the ``after`` second itself and takes ``size``, not ``limit``."""
    monkeypatch.setattr(reddit, "PAGE_SIZE", 3)
    everything = [_comment("a", 10), _comment("b", 11), _comment("c", 12), _comment("d", 12), _comment("e", 15)]
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append((url, params.get("after"), kwargs["trim"], kwargs["spacing_seconds"]))
        after = params.get("after", -1)
        return [c for c in everything if c["created_utc"] >= after][: params["size"]]

    monkeypatch.setattr(reddit, "_get", fake_get)

    comments = reddit.fetch_comments("t", thread_created_utc=0, source=reddit.PULLPUSH)

    assert [c["id"] for c in comments] == ["a", "b", "c", "d", "e"]
    assert [after for _, after, _, _ in calls] == [None, 12, 15]
    assert all(url == reddit.PULLPUSH.url and trim and spacing == 3.0 for url, _, trim, spacing in calls)


def _archive(everything, requests_made):
    """A fake Arctic Shift: ``after`` and ``before`` both exclude their own second."""

    def fake_get(url, params, **kwargs):
        requests_made.append((params.get("after"), params.get("before")))
        matching = [
            c
            for c in everything
            if params.get("after", -1) < c["created_utc"] < params.get("before", 10**12)
            and ("link_id" not in params or c["link_id"] == f"t3_{params['link_id']}")
        ]
        return matching[: params["limit"]]

    return fake_get


def test_fetch_subreddit_comments_walks_windows_and_pages_inside_them(monkeypatch):
    monkeypatch.setattr(reddit, "PAGE_SIZE", 2)
    monkeypatch.setattr(reddit, "WINDOW_SECONDS", 10)
    everything = [_comment(name, second) for name, second in [("a", 100), ("b", 103), ("c", 103), ("d", 109), ("e", 110), ("f", 125)]]
    requests_made = []
    monkeypatch.setattr(reddit, "_get", _archive(everything, requests_made))

    comments = reddit.fetch_subreddit_comments("baseball", 100, 130)

    assert [c["id"] for c in comments] == ["a", "b", "c", "d", "e", "f"]
    # The first window needs four pages; a comment exactly on a window edge (110) belongs to the next one.
    assert requests_made == [(99, 110), (102, 110), (102, 110), (103, 110), (109, 120), (119, 130)]


def test_ingest_threads_by_window_keeps_only_each_threads_comments(monkeypatch, tmp_path):
    monkeypatch.setenv("FANPULSE_LIVE_DATA_DIR", str(tmp_path))
    posts = [dict(_post("gt", "baseball"), created_utc=100), dict(_post("pg", "Dodgers"), created_utc=150)]
    monkeypatch.setattr(reddit, "fetch_threads_by_id", lambda ids: posts)
    everything = [
        dict(_comment("a", 105), link_id="t3_gt"),
        dict(_comment("x", 106), link_id="t3_some_other_post"),
        dict(_comment("b", 140), link_id="t3_gt"),
        dict(_comment("late", 500), link_id="t3_gt"),  # after end_utc: not fetched
        dict(_comment("p", 160), link_id="t3_pg"),
    ]
    monkeypatch.setattr(reddit, "_get", _archive(everything, []))
    progress = {}

    tables = reddit.ingest_threads_by_window(1, THREADS, 200, lambda thread_id, n: progress.update({thread_id: n}))

    raw = tables["comments_raw"]
    assert list(zip(raw["comment_id"], raw["thread_id"])) == [("a", "gt"), ("b", "gt"), ("p", "pg")]
    assert progress == {"gt": 2, "pg": 1}


def test_read_dump_comments_streams_only_the_wanted_threads(tmp_path):
    import json

    import zstandard

    lines = [
        {"id": "b", "link_id": "t3_gt", "created_utc": "20", "body": "second"},
        {"id": "x", "link_id": "t3_other", "created_utc": 5, "body": "another thread"},
        {"id": "a", "link_id": "t3_gt", "created_utc": 10, "body": "first"},
        # Mentions a wanted thread id in its text but belongs to another thread.
        {"id": "y", "link_id": "t3_other", "created_utc": 6, "body": "see t3_gt"},
        {"id": "c", "link_id": "t3_pg", "created_utc": 30, "body": "postgame"},
    ]
    path = tmp_path / "baseball_comments.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress("\n".join(json.dumps(l) for l in lines).encode() + b"\n"))
    progress = []

    found = reddit.read_dump_comments([path], ["gt", "pg"], lambda thread_id, n: progress.append((thread_id, n)))

    assert [c["id"] for c in found["gt"]] == ["a", "b"]  # oldest first, string timestamps handled
    assert [c["id"] for c in found["pg"]] == ["c"]
    assert progress == [("gt", 2), ("pg", 1)]


def test_trim_keeps_only_the_fields_we_store():
    response = {"data": [{"id": "a", "body": "hi", "author": "fan", "all_awardings": [], "permalink": "/r/x"}]}

    trimmed = reddit._trim_comments(response)["data"][0]

    assert trimmed["body"] == "hi"
    assert set(trimmed) == set(reddit.COMMENT_FIELDS.split(","))


def test_fetch_comments_empty_thread(monkeypatch):
    monkeypatch.setattr(reddit, "_get", lambda url, params, **kwargs: [])

    assert reddit.fetch_comments("t", thread_created_utc=0) == []
    assert reddit.build_comments([], "t", "baseball").empty


def test_build_comments_parses_utc_and_tags_subreddit():
    frame = reddit.build_comments(
        [{"id": "c1", "created_utc": 1761350400, "body": "hi", "author": "a", "score": 4, "parent_id": "t3_t"}],
        "t",
        "Dodgers",
    )

    assert frame.loc[0, "created_utc"] == pd.Timestamp("2025-10-25T00:00:00Z")
    assert frame.loc[0, "thread_id"] == "t"
    assert frame.loc[0, "subreddit"] == "Dodgers"
    assert frame.loc[0, "author_flair_text"] is None


def test_ingest_threads_combines_all_threads(monkeypatch, tmp_path):
    monkeypatch.setenv("FANPULSE_LIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reddit, "fetch_threads_by_id", lambda ids: [_post("gt", "baseball"), _post("pg", "Dodgers")])
    by_thread = {"gt": [_comment("a", 10), _comment("b", 11)], "pg": [_comment("c", 20)]}

    def fake_fetch(thread_id, created, on_page=None, source=None):
        on_page(len(by_thread[thread_id]))
        return by_thread[thread_id]

    monkeypatch.setattr(reddit, "fetch_comments", fake_fetch)
    progress = []

    tables = reddit.ingest_threads(1, THREADS, lambda thread_id, count: progress.append((thread_id, count)))

    assert progress == [("gt", 2), ("pg", 1)]
    assert list(tables["comments_raw"]["subreddit"]) == ["baseball", "baseball", "Dodgers"]
    assert (tmp_path / "processed" / "comments_raw" / "1.parquet").exists()
    assert (tmp_path / "processed" / "threads" / "1.parquet").exists()


@pytest.mark.parametrize(
    "author, body, expected",
    [
        ("fan", "[deleted]", "deleted"),
        ("fan", "[removed]", "deleted"),
        ("AutoModerator", "Please read the rules", "bot"),
        ("BaseballBot", "Line score", "bot"),
        ("DodgerBot", "Line score", "bot"),
        ("stats_bot", "here are stats", "bot"),
        ("fan", "   ", "empty"),
        ("fan", None, "empty"),
        ("fan", "![gif](giphy|kCrGOt5ojlVbG)", "media_only"),
        ("fan", "![gif](giphy|abc) LET'S GO", None),
        ("Talbot", "great, another walk, love that", None),
        ("[deleted]", "still a real comment", None),
    ],
)
def test_drop_reason(author, body, expected):
    assert clean.drop_reason(author, body) == expected


def test_clean_comments_counts_drops():
    comments = pd.DataFrame(
        {
            "comment_id": ["1", "2", "3"],
            "author": ["fan", "AutoModerator", "fan"],
            "body": ["nice hit", "rules", "[deleted]"],
        }
    )

    kept, dropped = clean.clean_comments(comments)

    assert list(kept["comment_id"]) == ["1"]
    assert dropped.to_dict() == {"deleted": 1, "bot": 1, "empty": 0, "media_only": 0}
