"""Command-line entry point: ``fanpulse-live <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from fanpulse_live import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fanpulse-live",
        description="Real-time fan reaction engine: Reddit game threads synced to MLB play-by-play.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    find = subparsers.add_parser("find-game", help="look up a gamePk from the MLB schedule")
    find.add_argument("--date", required=True, help="official game date, YYYY-MM-DD")
    find.add_argument("--home", help="home team name (partial match)")
    find.add_argument("--away", help="away team name (partial match)")
    find.add_argument("--game-type", help='MLB game type, e.g. "W" for the World Series')
    find.set_defaults(func=cmd_find_game)

    show = subparsers.add_parser("show-config", help="print the game, threads, streams and tick settings")
    show.set_defaults(func=cmd_show_config)

    pull = subparsers.add_parser("pull", help="pull the game's MLB data and Reddit threads, clean and tag them")
    pull.add_argument(
        "--keep-trying",
        action="store_true",
        help="when Arctic Shift stops answering, wait and resume instead of failing",
    )
    pull.add_argument(
        "--source",
        choices=["arctic-shift-windows", "arctic-shift", "pullpush"],
        default="arctic-shift-windows",
        help="how to pull comments: arctic-shift-windows reads game threads out of their subreddits in "
        "short time windows (works when whole-thread searches time out) and stops an hour after the "
        "final out; arctic-shift and pullpush search each thread whole",
    )
    pull.add_argument(
        "--dump",
        nargs="+",
        metavar="FILE",
        help="read comments from local Reddit dump files (.zst) instead of an archive API",
    )
    pull.set_defaults(func=cmd_pull)

    volume = subparsers.add_parser("volume", help="chart comments per minute by stream against win probability")
    volume.set_defaults(func=cmd_volume)

    lag = subparsers.add_parser("lag", help="measure how long after a play each stream reacts")
    lag.set_defaults(func=cmd_lag)

    state = subparsers.add_parser("state", help="print the state Jev would see for one stream at one moment")
    state.add_argument("--at", required=True, help="UTC time, e.g. 2025-10-25T02:33:10Z")
    state.add_argument("--stream", required=True, help='stream id, e.g. "r/Dodgers"')
    state.add_argument("--ask", action="store_true", help="also send it to Jev and print the answers")
    state.set_defaults(func=cmd_state)

    sheet = subparsers.add_parser("label-sheet", help="draw the windows to hand-label and write the labelling page")
    sheet.add_argument("--per-stream", type=int, default=8, help="windows per stream (default 8, so 40 in all)")
    sheet.set_defaults(func=cmd_label_sheet)

    evaluate = subparsers.add_parser("evaluate", help="score Jev against the hand labels")
    evaluate.set_defaults(func=cmd_evaluate)

    ticks = subparsers.add_parser("ticks", help="run every update of the game through Jev (resumable)")
    ticks.add_argument("--max-cost", type=float, default=8.0, help="stop once this run has spent this many dollars")
    ticks.add_argument("--limit", type=int, help="only the first N updates, for a trial run")
    ticks.add_argument(
        "--cached-only",
        action="store_true",
        help="send nothing to Jev: use the answers already paid for and mark the rest pending",
    )
    ticks.set_defaults(func=cmd_ticks)

    build = subparsers.add_parser(
        "build", help="build baseline readings, moments and summaries, and export the dashboard's data"
    )
    build.add_argument("--no-summaries", action="store_true", help="skip the LLM one-liners for moments")
    build.set_defaults(func=cmd_build)

    fan_report = subparsers.add_parser("fanbase-report", help="write the offline report comparing the fanbases")
    fan_report.set_defaults(func=cmd_fanbase_report)

    serve = subparsers.add_parser("serve", help="serve the dashboard at http://localhost:8000")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--lan",
        action="store_true",
        help="also accept connections from other devices on the same network (default: this computer only)",
    )
    serve.set_defaults(func=cmd_serve)

    scores = subparsers.add_parser("baseline-scores", help="score every comment with the local RoBERTa baseline")
    scores.set_defaults(func=cmd_baseline_scores)

    return parser


def cmd_find_game(args: argparse.Namespace) -> int:
    from fanpulse_live.ingest import mlb

    try:
        print(mlb.find_game_pk(args.date, home=args.home, away=args.away, game_type=args.game_type))
    except mlb.GameLookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    from fanpulse_live import config

    loaded = config.load_game()
    game, teams = loaded["game"], loaded["teams"]

    print(f"{game['label']}: {teams['away']['name']} @ {teams['home']['name']}, {game['date']}")
    print(f"gamePk {game['game_pk']}, YouTube video {game['youtube_video_id']}\n")
    print("Threads:")
    for thread in loaded["threads"]:
        print(
            f"  {thread['id']}  r/{thread['subreddit']:<16}{thread['type']:<9}"
            f"{thread['expected_comments']:>6} comments expected"
        )
    print("\nStreams:")
    for stream in loaded["streams"]:
        print(f"  {stream['id']:<22}{stream['team'] or 'neutral'}")
    print("\nTick settings:")
    for name, value in loaded["tick"].items():
        print(f"  {name}: {value}")
    return 0


PULL_RETRY_WAIT_SECONDS = 120
# How long after the final out a game thread is still read when pulling by time window.
GAME_THREAD_TAIL_SECONDS = 3600


def _write_pull_progress(threads: list[dict], fetched: dict[str, int], status: str) -> None:
    """Rewrite ``data/pull_progress.md`` with how much of each thread is in."""
    from datetime import datetime

    from fanpulse_live import config

    expected_total = sum(t["expected_comments"] for t in threads)
    fetched_total = sum(fetched.values())
    lines = [
        "# Reddit pull progress",
        "",
        f"Updated {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"**{fetched_total:,} of about {expected_total:,} comments ({fetched_total / expected_total:.0%})**",
        "",
        f"Status: {status}",
        "",
        "| thread | subreddit | type | fetched | expected | done |",
        "|---|---|---|---:|---:|---:|",
    ]
    for thread in threads:
        count = fetched.get(thread["id"], 0)
        lines.append(
            f"| {thread['id']} | r/{thread['subreddit']} | {thread['type']} | {count:,} "
            f"| {thread['expected_comments']:,} | {count / thread['expected_comments']:.0%} |"
        )
    path = config.data_dir() / "pull_progress.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pull_reddit(game_pk: int, threads: list[dict], *, keep_trying: bool, source: str, end_utc: int) -> dict:
    """Pull the threads, keeping ``data/pull_progress.md`` current.

    The archives have spells where they answer nothing. With ``keep_trying``
    the pull waits and resumes from the cache instead of failing.
    """
    import time
    from datetime import datetime, timedelta

    from fanpulse_live import http
    from fanpulse_live.ingest import reddit

    fetched: dict[str, int] = {}

    def on_progress(thread_id: str, count: int) -> None:
        fetched[thread_id] = count
        _write_pull_progress(threads, fetched, f"pulling from {source}")

    while True:
        try:
            if source == "arctic-shift-windows":
                tables = reddit.ingest_threads_by_window(game_pk, threads, end_utc, on_progress)
            else:
                tables = reddit.ingest_threads(game_pk, threads, on_progress, reddit.SOURCES[source])
        except http.HttpError as exc:
            if not keep_trying:
                _write_pull_progress(threads, fetched, f"stopped: {str(exc)[-60:]}")
                raise
            resume_at = datetime.now() + timedelta(seconds=PULL_RETRY_WAIT_SECONDS)
            _write_pull_progress(
                threads, fetched, f"{source} is not answering; waiting, next attempt at {resume_at:%H:%M:%S}"
            )
            time.sleep(PULL_RETRY_WAIT_SECONDS)
        else:
            _write_pull_progress(threads, fetched, "done")
            return tables


def cmd_pull(args: argparse.Namespace) -> int:
    from fanpulse_live import config, storage
    from fanpulse_live.ingest import clean, mlb, reddit, streams

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]

    game_tables = mlb.ingest_game(game_pk)
    game, plays = game_tables["games"].iloc[0], game_tables["plays"]
    first_pitch, final_out = mlb.game_window(plays, game_tables["play_events"])

    if args.dump:
        fetched: dict[str, int] = {}

        def on_progress(thread_id: str, count: int) -> None:
            fetched[thread_id] = count
            _write_pull_progress(loaded["threads"], fetched, "reading dump files")

        reddit_tables = reddit.ingest_dump(game_pk, loaded["threads"], [Path(p) for p in args.dump], on_progress)
        _write_pull_progress(loaded["threads"], fetched, "done")
    else:
        reddit_tables = _pull_reddit(
            game_pk,
            loaded["threads"],
            keep_trying=args.keep_trying,
            source=args.source,
            end_utc=int(final_out.timestamp()) + GAME_THREAD_TAIL_SECONDS,
        )
    threads, raw = reddit_tables["threads"], reddit_tables["comments_raw"]

    comments, dropped = clean.clean_comments(raw)
    comments["stream"] = streams.assign_streams(comments, loaded["streams"], loaded["flairs"])
    comments["phase"] = streams.tag_phase(comments["created_utc"], first_pitch, final_out)
    comments["thread_type"] = comments["thread_id"].map(threads.set_index("thread_id")["thread_type"])
    # Usernames stay in comments_raw only; downstream tables carry a hash.
    comments.insert(3, "author_hash", comments.pop("author").map(streams.hash_author))
    storage.write_table("comments", game_pk, comments)

    print(f"{game.away_team} @ {game.home_team}, {game.date}, {game.series_desc} ({game.final_score})")
    print(f"gamePk {game_pk}: {len(plays)} plays, {int(game_tables['play_events']['is_pitch'].sum())} pitches")
    print(f"First pitch {first_pitch:%Y-%m-%d %H:%M:%S} UTC, final out {final_out:%H:%M:%S} UTC\n")

    print(f"{'thread':<9}{'subreddit':<18}{'type':<10}{'expected':>9}{'fetched':>9}{'kept':>8}{'fetched %':>11}")
    expected = {t["id"]: t["expected_comments"] for t in loaded["threads"]}
    for thread in threads.itertuples():
        fetched = int((raw["thread_id"] == thread.thread_id).sum())
        kept = int((comments["thread_id"] == thread.thread_id).sum())
        print(
            f"{thread.thread_id:<9}{'r/' + thread.subreddit:<18}{thread.thread_type:<10}"
            f"{expected[thread.thread_id]:>9}{fetched:>9}{kept:>8}{fetched / expected[thread.thread_id]:>11.1%}"
        )
    print(f"{'total':<37}{sum(expected.values()):>9}{len(raw):>9}{len(comments):>8}")
    print("Dropped in cleaning: " + ", ".join(f"{reason} {count}" for reason, count in dropped.items()))

    by_stream = (
        comments.groupby(["stream", "phase"]).size().unstack("phase", fill_value=0)
        .reindex(index=[s["id"] for s in loaded["streams"]], columns=["pregame", "in-game", "postgame"], fill_value=0)
    )
    print(f"\n{'stream':<24}{'pregame':>9}{'in-game':>9}{'postgame':>10}")
    for stream, row in by_stream.iterrows():
        print(f"{stream:<24}{row['pregame']:>9}{row['in-game']:>9}{row['postgame']:>10}")

    print("\nFlair check, most common r/baseball flairs in each stream:")
    split = comments[comments["stream"].str.contains(":")]
    for stream, group in split.groupby("stream"):
        top = group["author_flair_text"].fillna("(no flair)").value_counts().head(4)
        print(f"  {stream}: " + "; ".join(f"{flair} ({count})" for flair, count in top.items()))
    return 0


def _load_tables(game_pk: int, names: Sequence[str]) -> dict | None:
    from fanpulse_live import storage

    try:
        return {name: storage.read_table(name, game_pk) for name in names}
    except FileNotFoundError as exc:
        print(f"missing table ({exc.filename}); run `fanpulse-live pull` first", file=sys.stderr)
        return None


def cmd_volume(args: argparse.Namespace) -> int:
    import pandas as pd

    from fanpulse_live import config
    from fanpulse_live.ingest import mlb
    from fanpulse_live.viz import charts

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("games", "plays", "play_events", "win_prob", "comments"))
    if tables is None:
        return 1

    game, plays, comments = tables["games"].iloc[0], tables["plays"], tables["comments"]
    stream_ids = [s["id"] for s in loaded["streams"]]
    first_pitch, final_out = mlb.game_window(plays, tables["play_events"])

    margin = pd.Timedelta(minutes=20)
    shown = comments[
        (comments["thread_type"] == "game")
        & (comments["created_utc"] >= first_pitch - margin)
        & (comments["created_utc"] <= final_out + margin)
    ]
    per_minute = charts.comments_per_minute(shown, stream_ids)

    in_game = per_minute[(per_minute.index >= first_pitch.floor("min")) & (per_minute.index <= final_out)]
    minutes = pd.DataFrame({"minute": in_game.index})
    # A minute belongs to the half-inning of the last play that started before it ended.
    located = pd.merge_asof(
        minutes.assign(minute_end=minutes["minute"] + pd.Timedelta(minutes=1)),
        plays.sort_values("start_time_utc")[["start_time_utc", "inning", "half"]],
        left_on="minute_end",
        right_on="start_time_utc",
    ).set_index("minute")
    half_inning = located["half"].str[:3].str.title() + " " + located["inning"].astype("Int64").astype(str)
    sixth = (located["inning"] == 6).to_numpy()

    print(f"{game.away_team} @ {game.home_team}, {game.date} ({game.final_score})")
    print("In-game comments per minute, by stream:\n")
    print(f"{'stream':<24}{'median':>8}{'peak':>7}  {'peak at (UTC)':<15}{'in':<8}{'6th-inn peak':>13}{'x median':>10}")
    for stream in stream_ids:
        series = in_game[stream]
        peak_at = series.idxmax()
        sixth_peak = series[sixth].max()
        print(
            f"{stream:<24}{series.median():>8.0f}{series.max():>7}  {peak_at:%H:%M}{'':<10}"
            f"{half_inning[peak_at]:<8}{sixth_peak:>13}{sixth_peak / series.median():>9.1f}x"
        )

    path = charts.write_html(
        charts.volume_figure(game, plays, tables["win_prob"], per_minute), f"{game_pk}_volume_by_stream.html"
    )
    print(f"\nChart: {path}")
    return 0


def cmd_lag(args: argparse.Namespace) -> int:
    from fanpulse_live import config
    from fanpulse_live.analysis import lag
    from fanpulse_live.viz import charts

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("plays", "win_prob", "comments"))
    if tables is None:
        return 1

    plays = tables["plays"].merge(tables["win_prob"], on=["game_pk", "at_bat_index"])
    weights = plays["wp_delta"].abs()
    comments = tables["comments"][tables["comments"]["thread_type"] == "game"]

    curves, stats = {}, {}
    for stream in (s["id"] for s in loaded["streams"]):
        times = comments.loc[comments["stream"] == stream, "created_utc"]
        curves[stream] = lag.reaction_curve(times, plays["end_time_utc"], weights)
        stats[stream] = lag.lag_stats(curves[stream])

    print(f"Reaction lag over {len(plays)} plays, each weighted by its win-probability swing.")
    print("Lags are seconds after the play's end time; volume is per 5-second bucket.\n")
    print(f"{'stream':<24}{'run-up vol':>11}{'peak lag':>10}{'peak x run-up':>15}{'90% arrived by':>16}")
    for stream, s in stats.items():
        print(
            f"{stream:<24}{s['baseline']:>11.1f}{s['peak_lag_s']:>9.0f}s{s['peak_x_baseline']:>14.2f}x"
            f"{s['tail_lag_s']:>15.0f}s"
        )

    slowest = max(s["tail_lag_s"] for s in stats.values())
    print(f"\nSlowest stream has 90% of its reaction in by {slowest:.0f}s.")
    print(f"play_lookback_s is currently {loaded['tick']['play_lookback_s']}.")

    path = charts.write_html(
        charts.lag_figure(curves, {k: s["baseline"] for k, s in stats.items()}), f"{game_pk}_reaction_lag.html"
    )
    print(f"Chart: {path}")
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    import json

    from fanpulse_live import config, gamestate
    from fanpulse_live.engine import calls, window
    from fanpulse_live.jev import client, questions

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    streams = {s["id"]: s for s in loaded["streams"]}
    if args.stream not in streams:
        print(f"unknown stream {args.stream!r}; choose from: {', '.join(streams)}", file=sys.stderr)
        return 1
    tables = _load_tables(game_pk, ("comments", "comments_raw"))
    if tables is None:
        return 1

    timeline = gamestate.load_timeline(game_pk)
    comments = tables["comments"]
    in_stream = comments[(comments["stream"] == args.stream) & (comments["thread_type"] == "game")]
    call = calls.prepare_call(
        timeline,
        gamestate.parse_time(args.at),
        streams[args.stream],
        in_stream.sort_values("created_utc"),
        window.parent_bodies(tables["comments_raw"]),
        questions.subject_options(timeline),
        loaded,
    )

    print(json.dumps(call.state, indent=2, ensure_ascii=False))
    sizes = ", ".join(str(len(batch)) for batch in call.question_batches)
    print(
        f"\n{len(call.window.comments)} comments in a {call.window.window_used_s}s window, "
        f"{len(call.new_numbers)} tagged; stale: {call.window.stale}; questions per call: {sizes}"
    )
    if not args.ask:
        return 0
    if call.window.stale:
        print("Stale window: the engine would skip this call and carry the last reading forward.")
        return 0

    for batch in call.question_batches:
        result = client.ask(call.state, batch)
        usage = result["usage"]
        print(
            f"\nJev: {usage['input_tokens']} input tokens, ${usage['cost']:.6f}, "
            f"{result['latency_s']}s{' (cached)' if result['cached'] else ''}"
        )
        for name, answer in result["answers"].items():
            if answer["type"] == "noul":
                print(f"  {name:<8} P(yes) {answer['noul']:.2f}")
            elif answer["type"] == "score":
                print(f"  {name:<8} {questions.score_to_unit(answer):+.2f}  (confidence {answer['confidence']:.2f})")
            else:
                top = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])[:3]
                ranked = ", ".join(f"{option} {p:.2f}" for option, p in top if p > 0)
                print(f"  {name:<8} {ranked}")
    return 0


def cmd_label_sheet(args: argparse.Namespace) -> int:
    from collections import Counter

    from fanpulse_live import config, gamestate
    from fanpulse_live.analysis import labelling
    from fanpulse_live.engine import window
    from fanpulse_live.jev import questions

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments", "comments_raw"))
    if tables is None:
        return 1
    directory = config.data_dir() / "labels"
    if (directory / "sample.json").exists():
        print(
            f"{directory / 'sample.json'} already exists. Labels refer to that sample, so it is not redrawn; "
            f"delete the folder to start over.",
            file=sys.stderr,
        )
        return 1

    timeline = gamestate.load_timeline(game_pk)
    subjects = questions.subject_options(timeline)
    items = labelling.draw_sample(
        timeline,
        tables["comments"],
        window.parent_bodies(tables["comments_raw"]),
        subjects,
        loaded,
        per_stream=args.per_stream,
        busy_per_stream=args.per_stream // 2,
    )
    _, page = labelling.write_labelling_page(items, list(subjects), directory)

    by_stream = Counter(item["stream"] for item in items)
    by_kind = Counter(item["kind"] for item in items)
    sizes = sorted(len(item["state"]["comments"]) for item in items)
    print(f"{len(items)} windows, {sum(len(item['label_comments']) for item in items)} comments to label")
    print("  per stream: " + ", ".join(f"{stream} {count}" for stream, count in by_stream.items()))
    print("  " + ", ".join(f"{count} {kind}" for kind, count in by_kind.items()))
    print(f"  comments per window: median {sizes[len(sizes) // 2]}, largest {sizes[-1]}")
    print(f"\nOpen this in a browser: {page}")
    print(f"When done, save the downloaded labels.json into {directory}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    import json

    from fanpulse_live import config, gamestate, storage
    from fanpulse_live.analysis import evaluate, labelling
    from fanpulse_live.engine import calls, window
    from fanpulse_live.jev import client
    from fanpulse_live.viz import charts

    directory = config.data_dir() / "labels"
    try:
        items = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
        labels = labelling.load_labels(directory / "labels.json")
    except FileNotFoundError as exc:
        print(f"missing {exc.filename}; run `label-sheet`, label the page and save labels.json there", file=sys.stderr)
        return 1

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments", "comments_raw"))
    if tables is None:
        return 1
    timeline = gamestate.load_timeline(game_pk)
    streams = {s["id"]: s for s in loaded["streams"]}
    parents = window.parent_bodies(tables["comments_raw"])
    game_threads = tables["comments"][tables["comments"]["thread_type"] == "game"]
    team_of = {p.name: p.team for p in timeline.players.values()}
    team_names = {timeline.home: timeline.home_name, timeline.away: timeline.away_name}

    # Rebuild each labelled window as the tick engine now builds it, with the labelled
    # comments forced into the tagged sample, and put the labels in terms of its options.
    before = client.usage_summary()
    answers, comment_ids = {}, {}
    labels = json.loads(json.dumps(labels))
    for item in items:
        in_stream = game_threads[game_threads["stream"] == item["stream"]].sort_values("created_utc")
        t = gamestate.parse_time(item["time"])
        tick = loaded["tick"]
        full = window.select_window(
            in_stream, t, update_every_s=tick["update_every_s"], window_s=tick["window_s"],
            min_comments=tick["min_comments"], max_window_s=tick["max_window_s"],
        )  # fmt: skip
        page_ids = list(full.comments["comment_id"])
        labelled = {k: page_ids[k - 1] for k in item["label_comments"]}
        comment_ids.update({(item["id"], k): comment_id for k, comment_id in labelled.items()})
        call = calls.prepare_call(
            timeline, t, streams[item["stream"]], in_stream, parents, None, loaded, must_tag=set(labelled.values())
        )
        answers[item["id"]] = evaluate.ask_call(call, labelled)
        entry = labels[item["id"]]
        entry["target"] = evaluate.to_offered(entry["target"], call.subjects, team_of, team_names)
        for label in entry["comments"].values():
            label["subject"] = evaluate.to_offered(label["subject"], call.subjects, team_of, team_names)
    after = client.usage_summary()
    spent = {
        "calls": after["calls"] - before["calls"],
        "input_tokens": after["input_tokens"] - before["input_tokens"],
        "cost": after["cost"] - before["cost"],
        "median_latency_s": after["median_latency_s"],
    }

    tallies = evaluate.compare(items, labels, answers)
    text = evaluate.report(tallies, spent)

    # The RoBERTa baseline on the same comments, if it has been run.
    try:
        scores = storage.read_table("comment_scores", game_pk).set_index("comment_id")["sentiment"]
    except FileNotFoundError:
        text += "\n## Jev against the RoBERTa baseline\n\nNot run yet (`fanpulse-live baseline-scores`).\n"
    else:
        baseline_scores = {key: float(scores[cid]) for key, cid in comment_ids.items() if cid in scores.index}
        roberta = evaluate.compare_baseline(items, labels, baseline_scores)
        text += "\n" + evaluate.baseline_section(tallies["sentiment"], roberta)

    path = charts.reports_dir() / "phase2_eval.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"Saved: {path}")
    return 0


def cmd_ticks(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime

    from fanpulse_live import config, gamestate, storage
    from fanpulse_live.engine import ticks, window
    from fanpulse_live.jev import client

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments", "comments_raw"))
    if tables is None:
        return 1

    timeline = gamestate.load_timeline(game_pk)
    step = loaded["tick"]["update_every_s"]
    times = ticks.tick_times(timeline, step)
    if args.limit:
        times = times[: args.limit]
    progress_path = config.data_dir() / "ticks_progress.md"
    started = time.time()

    def write_progress(status: str, done: int = 0, total: int = 0, cost: float = 0.0, paid: int = 0) -> None:
        share = f"{done / total:.0%}" if total else "0%"
        progress_path.write_text(
            "# Tick run progress\n\n"
            f"Updated {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
            f"**{done:,} of {total:,} (update, stream) pairs ({share})**\n\n"
            f"Status: {status}\n\n"
            f"Paid Jev calls this run: {paid:,}; spent ${cost:.4f}; elapsed {(time.time() - started) / 60:.1f} min\n",
            encoding="utf-8",
        )

    write_progress("starting")
    try:
        tick_table, tags, totals = ticks.run_game(
            timeline,
            tables["comments"],
            window.parent_bodies(tables["comments_raw"]),
            loaded,
            max_cost=args.max_cost,
            on_progress=lambda done, total, cost, paid: write_progress("running", done, total, cost, paid),
            times=times,
            cached_only=args.cached_only,
        )
    except (ticks.CostLimitReached, client.JevError) as exc:
        write_progress(f"stopped: {str(exc)[:200]}")
        print(f"stopped: {exc}", file=sys.stderr)
        return 1

    storage.write_table("ticks", game_pk, tick_table)
    storage.write_table("comment_tags", game_pk, tags)
    storage.write_table("volume", game_pk, ticks.volume_buckets(tables["comments"], times, step))

    minutes = (time.time() - started) / 60
    write_progress("done", len(tick_table), len(tick_table), totals["cost"], totals["paid_calls"])
    print(f"{len(times):,} updates x {len(loaded['streams'])} streams = {len(tick_table):,} readings in {minutes:.1f} min")
    print(f"Paid Jev calls this run: {totals['paid_calls']:,}; {totals['input_tokens']:,} input tokens; ${totals['cost']:.4f}")
    print(f"Comments tagged: {len(tags):,}\n")
    print(f"{'stream':<24}{'readings':>9}{'stale':>8}{'widened':>9}{'pending':>9}{'stale %':>9}{'widened %':>11}")
    for stream, group in tick_table.groupby("stream"):
        stale = int(group["stale"].sum())
        widened = int(((group["window_used_s"] > loaded["tick"]["window_s"]) & ~group["stale"]).sum())
        print(
            f"{stream:<24}{len(group):>9}{stale:>8}{widened:>9}{int(group['pending'].sum()):>9}"
            f"{stale / len(group):>9.0%}{widened / len(group):>11.0%}"
        )
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    import yaml

    from fanpulse_live import config, export, gamestate, http, storage
    from fanpulse_live.engine import baseline_ticks, moments, summarize, ticks
    from fanpulse_live.ingest import mlb

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments", "comment_scores"))
    if tables is None:
        print("(comment_scores comes from `fanpulse-live baseline-scores`)", file=sys.stderr)
        return 1

    timeline = gamestate.load_timeline(game_pk)
    step = loaded["tick"]["update_every_s"]
    times = ticks.tick_times(timeline, step)
    comments = tables["comments"]
    volume = ticks.volume_buckets(comments, times, step)
    storage.write_table("volume", game_pk, volume)

    nickname_path = config.PROJECT_ROOT / "config" / "nicknames.yaml"
    nicknames = (yaml.safe_load(nickname_path.read_text(encoding="utf-8")) or {}).get("nicknames") or {}
    tagged = baseline_ticks.tag_comments(comments, tables["comment_scores"], timeline, nicknames)
    base_ticks, base_tags = baseline_ticks.build_ticks(tagged, times, loaded)
    storage.write_table("ticks_baseline", game_pk, base_ticks)
    storage.write_table("comment_tags_baseline", game_pk, base_tags)
    print(f"baseline: {len(base_ticks):,} readings, {len(base_tags):,} comments tagged, "
          f"{base_tags['subject'].notna().mean():.0%} of them naming a player")

    readings = {"baseline": (base_ticks, base_tags)}
    try:
        readings["jev"] = (storage.read_table("ticks", game_pk), storage.read_table("comment_tags", game_pk))
    except FileNotFoundError:
        print("jev: no tick tables yet (run `fanpulse-live ticks`)")

    stream_ids = [s["id"] for s in loaded["streams"]]
    start, end = int(times[0].timestamp()), int(times[-1].timestamp())
    comment_data, comment_index = export.comments_file(comments, stream_ids, start - 60, end)
    out = export.web_data_dir()
    season = int(loaded["game"]["date"][:4])
    season_lines = {}
    for player in timeline.players.values():
        try:
            line = mlb.fetch_season_line(player.id, season)
        except (http.HttpError, KeyError, ValueError) as exc:
            print(f"no season line for {player.name}: {str(exc)[:80]}")
            continue
        if line:
            season_lines[player.name] = line
    print(f"season lines for {len(season_lines)} of {len(timeline.players)} players")
    export._write(out / "game.json", export.game_file(timeline, times, season_lines))
    export._write(out / "comments.json", comment_data)

    sources = []
    summaries_work = not args.no_summaries
    for name, (tick_table, tags) in readings.items():
        found = moments.find_moments(tick_table, volume, timeline, loaded["tick"]["play_lookback_s"])
        storage.write_table(f"moments_{name}", game_pk, found)
        lines: dict[str, dict[str, str]] = {}
        for moment in found.to_dict("records") if summaries_work else []:
            prompt = summarize.build_prompt(moment, comments, tick_table, loaded["streams"], loaded["game"]["label"])
            if prompt is None:
                continue
            try:
                lines[moment["moment_id"]] = summarize.summarize(prompt)
            except summarize.SummaryError as exc:
                print(f"summaries skipped: {str(exc)[:160]}")
                summaries_work = False
                break
        data, coverage = export.source_file(tick_table, tags, volume, found, lines, stream_ids, times, comment_index)
        export._write(out / f"{name}.json", data)
        sources.append({"id": name, "label": export.SOURCE_LABELS[name], "coverage": round(coverage, 4), "moments": len(found)})
        print(f"{name}: {len(found)} moments, {len(lines)} with summaries, covers {coverage:.0%} of readings")

    # The fullest source is the default; Jev wins a tie.
    sources.sort(key=lambda s: (-round(s["coverage"], 2), s["id"] != "jev"))
    anchors = export.read_anchors(config.PROJECT_ROOT / "config" / "anchors.csv", timeline)
    for problem in export.check_anchors(anchors):
        print(f"anchor warning: {problem}")
    export._write(out / "manifest.json", export.manifest(loaded, timeline, times, sources, anchors))
    print(f"default source: {sources[0]['id']}; video anchors: {len(anchors)}")
    print(f"Dashboard data written to {out}")
    return 0


def cmd_fanbase_report(args: argparse.Namespace) -> int:
    import yaml

    from fanpulse_live import config, gamestate
    from fanpulse_live.analysis import baseline, fanbases
    from fanpulse_live.viz import charts

    loaded = config.load_game()
    game_pk = loaded["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments", "comment_scores"))
    if tables is None:
        return 1
    timeline = gamestate.load_timeline(game_pk)
    nickname_path = config.PROJECT_ROOT / "config" / "nicknames.yaml"
    nicknames = (yaml.safe_load(nickname_path.read_text(encoding="utf-8")) or {}).get("nicknames") or {}
    patterns = baseline.name_patterns(timeline, nicknames)

    tagged = tables["comments"].merge(tables["comment_scores"][["comment_id", "sentiment"]], on="comment_id")
    tagged["subject"] = [baseline.mentioned_player(body, patterns) for body in tagged["body"]]
    tagged = fanbases.with_fanbase(tagged, loaded["streams"])
    in_game = tagged[(tagged["thread_type"] == "game") & (tagged["phase"] == "in-game")]
    postgame = tagged[tagged["thread_type"] == "postgame"]

    echo = fanbases.echo_chamber(in_game, loaded["streams"])
    perception = fanbases.perception_vs_performance(in_game, timeline)
    tone = fanbases.tone_by_inning(in_game, timeline)
    recovered, curves = fanbases.recovery(in_game, timeline)
    after = fanbases.postgame_subjects(postgame)
    colors = charts.stream_colors([s["id"] for s in loaded["streams"]])

    findings = []
    for fanbase in ("Dodgers fans", "Blue Jays fans"):
        home, flaired = echo[echo["fanbase"] == fanbase].itertuples()
        findings.append(
            f"<li><b>{fanbase}, own subreddit vs r/baseball:</b> mean sentiment {home.mean_sentiment:+.2f} at home against "
            f"{flaired.mean_sentiment:+.2f} among flaired fans in r/baseball; {home.share_negative:.0%} against "
            f"{flaired.share_negative:.0%} of comments negative. Their minute-by-minute moods correlate at {home.minute_correlation:.2f}.</li>"
        )
        own = perception[(perception["fanbase"] == fanbase)]
        own = own[own["team"] == ("LAD" if fanbase == "Dodgers fans" else "TOR")]
        if len(own):
            loved, blamed = own.loc[own["sentiment"].idxmax()], own.loc[own["sentiment"].idxmin()]
            most = own.iloc[0]
            findings.append(
                f"<li><b>{fanbase}, who they talk about:</b> {most.player} most of all ({most.mentions} mentions, sentiment "
                f"{most.sentiment:+.2f}, WPA {most.wpa:+.1%}). Warmest toward {loved.player} ({loved.sentiment:+.2f}, WPA {loved.wpa:+.1%}); "
                f"coldest toward {blamed.player} ({blamed.sentiment:+.2f}, WPA {blamed.wpa:+.1%}).</li>"
            )
    for row in recovered.itertuples():
        back = "did not get back to its earlier level within 12 minutes" if row.seconds_to_recover is None or row.seconds_to_recover != row.seconds_to_recover \
            else f"was back to its earlier level {row.seconds_to_recover / 60:.1f} minutes after the play"
        findings.append(
            f"<li><b>{row.fanbase}, worst moment:</b> {html_escape(row.play)} ({row.team_wp_change:+.0%} win probability). Mood went from "
            f"{row.mood_before:+.2f} to a low of {row.mood_low:+.2f} after {row.seconds_to_low}s and {back}.</li>"
        )

    intro = (
        f"<p>{loaded['game']['label']}: {timeline.away_name} at {timeline.home_name}. {len(in_game):,} in-game comments "
        f"and {len(postgame):,} postgame comments across {len(loaded['streams'])} communities.</p>"
        "<p><b>How to read this.</b> Sentiment here is the RoBERTa baseline: the tone of each comment on a −1 to +1 scale, "
        "read without knowing the game or whose fan is talking, and taking sarcasm literally. \"Who a comment is about\" is "
        "name matching, so a comment that says \"he\" or \"this guy\" is not counted. Emotion types and blame aimed at "
        "managers need Jev's readings and are not in this version.</p>"
        f"<h2>Findings</h2><ul>{''.join(findings)}</ul>"
    )
    sections = [
        (
            "Home crowd vs the same fans in r/baseball",
            "<p>Does a team's own subreddit react differently from that team's flaired fans in the neutral subreddit? "
            "Swing is the spread of the minute-by-minute mood.</p>",
            fanbases.echo_figure(in_game, loaded["streams"], colors),
            fanbases.html_table(
                echo.drop(columns=["stream"]),
                {"mean_sentiment": "+.2f", "share_negative": ".0%", "share_positive": ".0%", "swing": ".2f", "minute_correlation": ".2f", "comments": ","},
            ),
        ),
        (
            "Perception vs performance",
            "<p>Each team's fans on their own players: how they talk about him against what he did. Up and left is a "
            "player fans like more than his game deserved; down and right is the reverse. Bigger dots are named more often.</p>",
            fanbases.perception_figure(perception, timeline),
            fanbases.html_table(
                # Each team's fans on their own ten most-named players, as in the chart.
                perception[
                    ((perception["fanbase"] == "Dodgers fans") & (perception["team"] == "LAD"))
                    | ((perception["fanbase"] == "Blue Jays fans") & (perception["team"] == "TOR"))
                ].groupby("fanbase", sort=False).head(10),
                {"sentiment": "+.2f", "wpa": "+.1%", "mentions": ","},
            ),
        ),
        (
            "Tone by inning",
            "<p>The mix of negative, neutral and positive comments as the game went on.</p>",
            fanbases.tone_figure(tone),
            "",
        ),
        (
            "Recovery after each team's worst moment",
            "<p>The play that cost each team the most win probability, and how its fans' mood moved around it.</p>",
            fanbases.recovery_figure(curves, recovered),
            fanbases.html_table(recovered, {"team_wp_change": "+.0%", "mood_before": "+.2f", "mood_low": "+.2f"}),
        ),
        (
            "Postgame: who each fanbase was still talking about",
            "<p>The most-named players in each community's postgame thread.</p>",
            None,
            fanbases.html_table(after, {"sentiment": "+.2f", "mentions": ","}),
        ),
    ]
    path = charts.reports_dir() / "fanbase_report.html"
    fanbases.write_report(path, "How the fanbases reacted", intro, sections)
    print(f"Report: {path}")
    for line in findings:
        print(" -", re_strip_tags(line))
    return 0


def html_escape(text: str) -> str:
    import html

    return html.escape(text)


def re_strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)


def cmd_serve(args: argparse.Namespace) -> int:
    import functools
    import http.server

    from fanpulse_live import config

    web = config.PROJECT_ROOT / "web"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web))
    print(f"Fan Pulse Live dashboard: http://localhost:{args.port}  (Ctrl+C to stop)")
    if args.lan:
        import socket

        # The address other devices reach this computer on: the one used for outbound traffic.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            print(f"From another device on this network: http://{probe.getsockname()[0]}:{args.port}")
    with http.server.ThreadingHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_baseline_scores(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime

    from fanpulse_live import config, storage
    from fanpulse_live.analysis import baseline

    game_pk = config.load_game()["game"]["game_pk"]
    tables = _load_tables(game_pk, ("comments",))
    if tables is None:
        return 1
    comments = tables["comments"]
    progress_path = config.data_dir() / "baseline_progress.md"
    started = time.time()

    def on_progress(done: int, total: int) -> None:
        if done % (baseline.BATCH_SIZE * 20) and done != total:
            return
        elapsed = time.time() - started
        progress_path.write_text(
            "# Baseline scoring progress\n\n"
            f"Updated {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
            f"**{done:,} of {total:,} comments ({done / total:.0%})**, {done / elapsed:.0f} per second, "
            f"{elapsed / 60:.1f} min elapsed\n",
            encoding="utf-8",
        )

    scores = baseline.score_texts(list(comments["body"]), on_progress)
    table = baseline.scores_table(list(comments["comment_id"]), scores)
    storage.write_table("comment_scores", game_pk, table)
    minutes = (time.time() - started) / 60
    progress_path.write_text(
        f"# Baseline scoring progress\n\nUpdated {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        f"**done: {len(table):,} comments in {minutes:.1f} min**\n",
        encoding="utf-8",
    )
    print(f"Scored {len(table):,} comments in {minutes:.1f} min; mean sentiment {table['sentiment'].mean():+.3f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
