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
        f"{len(call.new_numbers)} new; stale: {call.window.stale}; questions per call: {sizes}"
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
