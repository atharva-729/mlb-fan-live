"""Reddit threads and comments via Arctic Shift.

Builds the ``threads`` and ``comments_raw`` tables for the threads pinned by ID
in ``config/game.yaml``. All timestamps are UTC. Arctic Shift is a free,
one-person service: every page is cached and uncached requests are spaced out.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pandas as pd

from fanpulse_live import http, storage

log = logging.getLogger(__name__)

BASE_URL = "https://arctic-shift.photon-reddit.com"

PAGE_SIZE = 100
REQUEST_SPACING_SECONDS = 1.0
# Arctic Shift answers 422 ("Timeout. Maybe slow down a bit") when a query times
# out on its side, with an X-RateLimit-Reset header saying when its per-minute
# window resets; the HTTP helper waits that long before each retry. Past these
# retries the service is having a bad spell and the caller should back off.
RETRY_STATUSES = http.RETRY_STATUSES | {422}
MAX_RETRIES = 6
BACKOFF_SECONDS = 5.0
# num_comments and score only settle about 36 hours after posting, so younger
# threads are fetched but not cached.
SETTLED_AFTER_SECONDS = 48 * 3600

POST_FIELDS = "id,title,num_comments,created_utc,subreddit,author"
COMMENT_FIELDS = "id,link_id,parent_id,author,body,created_utc,score,author_flair_text"

COMMENT_COLUMNS = [
    "comment_id",
    "thread_id",
    "subreddit",
    "author",
    "body",
    "created_utc",
    "score",
    "parent_id",
    "author_flair_text",
]


class ThreadLookupError(LookupError):
    pass


def _get(path: str, params: dict[str, Any], *, settled: bool) -> list[dict]:
    url = f"{BASE_URL}{path}"
    was_cached = http.cache_path(url, params).exists()
    response = http.get_json(
        url,
        params,
        cache_if=lambda _: settled,
        retry_statuses=RETRY_STATUSES,
        max_retries=MAX_RETRIES,
        backoff_seconds=BACKOFF_SECONDS,
    )
    if not was_cached:
        time.sleep(REQUEST_SPACING_SECONDS)
    return response.get("data") or []


def _is_settled(created_utc: float) -> bool:
    return time.time() - created_utc > SETTLED_AFTER_SECONDS


def fetch_threads_by_id(thread_ids: list[str]) -> list[dict]:
    """The posts for the configured thread IDs, in the order given. One small request, not cached."""
    posts = _get("/api/posts/ids", {"ids": ",".join(thread_ids), "fields": POST_FIELDS}, settled=False)
    by_id = {p["id"]: p for p in posts}
    missing = [thread_id for thread_id in thread_ids if thread_id not in by_id]
    if missing:
        raise ThreadLookupError(f"Arctic Shift returned no post for thread id(s): {', '.join(missing)}")
    return [by_id[thread_id] for thread_id in thread_ids]


def fetch_comments(
    thread_id: str, thread_created_utc: float, on_page: Callable[[int], None] | None = None
) -> list[dict]:
    """All comments in a thread, oldest first.

    ``after`` is exclusive and comments often share a second, so each page
    starts one second back from the last comment seen and duplicates are
    dropped by id. Paging stops when a page brings nothing new. ``on_page`` is
    called after every page with the number of comments fetched so far.
    """
    settled = _is_settled(thread_created_utc)
    seen: dict[str, dict] = {}
    after: int | None = None

    while True:
        params: dict[str, Any] = {
            "link_id": thread_id,
            "sort": "asc",
            "limit": PAGE_SIZE,
            "fields": COMMENT_FIELDS,
        }
        if after is not None:
            params["after"] = after
        page = _get("/api/comments/search", params, settled=settled)

        new = [c for c in page if c["id"] not in seen]
        for comment in new:
            seen[comment["id"]] = comment
        if on_page is not None:
            on_page(len(seen))
        if not page:
            break

        last = int(page[-1]["created_utc"])
        if new:
            after = last - 1
        elif len(page) < PAGE_SIZE:
            break
        else:
            # A full page of already-seen comments: a single second holds more
            # than a page. Step past it rather than loop forever.
            log.warning("thread %s: more than %d comments at second %d", thread_id, PAGE_SIZE, last)
            after = last
        log.debug("thread %s: %d comments so far", thread_id, len(seen))

    return sorted(seen.values(), key=lambda c: (c["created_utc"], c["id"]))


def build_threads(posts: list[dict], threads: list[dict], game_pk: int) -> pd.DataFrame:
    """One row per thread. ``threads`` is the config list; it supplies the thread type."""
    types = {t["id"]: t["type"] for t in threads}
    frame = pd.DataFrame(
        [
            {
                "thread_id": p["id"],
                "subreddit": p["subreddit"],
                "game_pk": game_pk,
                "thread_type": types[p["id"]],
                "title": p["title"],
                "author": p.get("author"),
                "created_utc": p["created_utc"],
                "num_comments": p.get("num_comments"),
            }
            for p in posts
        ]
    )
    frame["created_utc"] = pd.to_datetime(frame["created_utc"], unit="s", utc=True)
    return frame


def build_comments(comments: list[dict], thread_id: str, subreddit: str) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "comment_id": c["id"],
                "thread_id": thread_id,
                "subreddit": subreddit,
                "author": c.get("author"),
                "body": c.get("body"),
                "created_utc": c["created_utc"],
                "score": c.get("score"),
                "parent_id": c.get("parent_id"),
                "author_flair_text": c.get("author_flair_text"),
            }
            for c in comments
        ],
        columns=COMMENT_COLUMNS,
    )
    frame["created_utc"] = pd.to_datetime(frame["created_utc"], unit="s", utc=True)
    return frame


def ingest_threads(
    game_pk: int, threads: list[dict], on_progress: Callable[[str, int], None] | None = None
) -> dict[str, pd.DataFrame]:
    """Pull every configured thread (all subreddits, game and postgame) for one game.

    ``on_progress`` is called after every page with the thread id and its
    comment count so far.
    """
    posts = fetch_threads_by_id([t["id"] for t in threads])

    frames = []
    for post in posts:
        on_page = None if on_progress is None else (lambda n, thread_id=post["id"]: on_progress(thread_id, n))
        comments = fetch_comments(post["id"], post["created_utc"], on_page)
        log.info("r/%s thread %s: %d comments", post["subreddit"], post["id"], len(comments))
        frames.append(build_comments(comments, post["id"], post["subreddit"]))

    tables = {
        "threads": build_threads(posts, threads, game_pk),
        "comments_raw": pd.concat(frames, ignore_index=True),
    }
    for name, table in tables.items():
        storage.write_table(name, game_pk, table)
    return tables
