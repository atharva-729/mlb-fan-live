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
