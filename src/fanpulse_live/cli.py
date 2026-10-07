"""Command-line entry point: ``fanpulse-live <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
