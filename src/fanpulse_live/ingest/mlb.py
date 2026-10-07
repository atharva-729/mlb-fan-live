"""MLB Stats API: schedule lookup, live feed, win probability.

Builds the ``games``, ``plays``, ``play_events`` and ``win_prob`` tables. All timestamps are UTC.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from fanpulse_live import http, storage

log = logging.getLogger(__name__)

BASE_URL = "https://statsapi.mlb.com"
# Below these a season line says nothing: a pitcher's handful of at-bats, a position player's mop-up inning.
MIN_AT_BATS = 30
MIN_INNINGS = 10


class GameLookupError(LookupError):
    pass


def _is_final(status: dict) -> bool:
    return status.get("abstractGameState") == "Final"


def _schedule_is_final(schedule: dict) -> bool:
    games = [g for d in schedule.get("dates", []) for g in d.get("games", [])]
    return bool(games) and all(_is_final(g["status"]) for g in games)


def fetch_schedule(date: str, game_type: str | None = None) -> list[dict]:
    """Games on an official date (YYYY-MM-DD), optionally one game type ("W" = World Series)."""
    schedule = http.get_json(
        f"{BASE_URL}/api/v1/schedule",
        {"sportId": 1, "date": date, "gameType": game_type},
        cache_if=_schedule_is_final,
    )
    return [g for d in schedule.get("dates", []) for g in d.get("games", [])]


def find_game_pk(
    date: str, home: str | None = None, away: str | None = None, game_type: str | None = None
) -> int:
    """Look up a gamePk from the schedule by date and (partial) team names."""

    def matches(game: dict, side: str, wanted: str | None) -> bool:
        return wanted is None or wanted.lower() in game["teams"][side]["team"]["name"].lower()

    found = [
        g for g in fetch_schedule(date, game_type) if matches(g, "home", home) and matches(g, "away", away)
    ]
    if len(found) != 1:
        listing = ", ".join(
            f"{g['gamePk']} ({g['teams']['away']['team']['name']} @ {g['teams']['home']['team']['name']})"
            for g in found
        )
        raise GameLookupError(
            f"expected exactly one game on {date} for home={home!r} away={away!r}, "
            f"found {len(found)}: {listing or 'none'}"
        )
    return found[0]["gamePk"]


def fetch_feed(game_pk: int) -> dict:
    return http.get_json(
        f"{BASE_URL}/api/v1.1/game/{game_pk}/feed/live",
        cache_if=lambda feed: _is_final(feed["gameData"]["status"]),
    )


def fetch_win_probability(game_pk: int) -> list[dict]:
    return http.get_json(f"{BASE_URL}/api/v1/game/{game_pk}/winProbability")


def fetch_series_desc(game_pk: int) -> str | None:
    schedule = http.get_json(
        f"{BASE_URL}/api/v1/schedule",
        {"sportId": 1, "gamePk": game_pk},
        cache_if=_schedule_is_final,
    )
    for date in schedule.get("dates", []):
        for game in date.get("games", []):
            if game["gamePk"] == game_pk:
                return game.get("seriesDescription")
    return None


def fetch_season_line(player_id: int, season: int) -> str | None:
    """A player's regular-season line, like ``.282/.392/.622, 55 HR`` or ``2.35 ERA, 180.1 IP, 216 K``.

    The regular season is used because it was complete before any postseason
    game, so the line is what a fan could have known that night. A two-way
    player gets both halves. Cached per player.
    """
    response = http.get_json(
        f"{BASE_URL}/api/v1/people/{player_id}/stats",
        {"stats": "season", "group": "hitting,pitching", "season": season, "gameType": "R"},
    )
    stats = {s["group"]["displayName"]: s["splits"][0]["stat"] for s in response.get("stats", []) if s.get("splits")}
    parts = []
    hitting = stats.get("hitting")
    if hitting and hitting.get("atBats", 0) >= MIN_AT_BATS:
        parts.append(f"{hitting['avg']}/{hitting['obp']}/{hitting['slg']}, {hitting['homeRuns']} HR")
    pitching = stats.get("pitching")
    if pitching and float(pitching.get("inningsPitched", 0)) >= MIN_INNINGS:
        parts.append(f"{pitching['era']} ERA, {pitching['inningsPitched']} IP, {pitching['strikeOuts']} K")
    return "; ".join(parts) or None


def build_games(feed: dict, series_desc: str | None = None) -> pd.DataFrame:
    game = feed["gameData"]
    home, away = game["teams"]["home"], game["teams"]["away"]
    runs = feed["liveData"]["linescore"]["teams"]
    home_score, away_score = runs["home"]["runs"], runs["away"]["runs"]
    return pd.DataFrame(
        [
            {
                "game_pk": game["game"]["pk"],
                "date": game["datetime"]["officialDate"],
                "home_team": home["name"],
                "away_team": away["name"],
                "home_abbr": home["abbreviation"],
                "away_abbr": away["abbreviation"],
                # Short club names ("Blue Jays"), which is what game-thread titles use.
                "home_name": home["teamName"],
                "away_name": away["teamName"],
                "home_score": home_score,
                "away_score": away_score,
                "final_score": f"{away['abbreviation']} {away_score} @ {home['abbreviation']} {home_score}",
                "series_desc": series_desc,
                "start_time_utc": pd.Timestamp(game["datetime"]["dateTime"]),
            }
        ]
    )


def build_plays(feed: dict) -> pd.DataFrame:
    """Flatten ``liveData.plays.allPlays`` into one row per plate appearance."""
    game_pk = feed["gameData"]["game"]["pk"]
    rows: list[dict[str, Any]] = []
    for play in feed["liveData"]["plays"]["allPlays"]:
        about, result, matchup = play["about"], play["result"], play["matchup"]
        if not about.get("isComplete", True):
            continue
        rows.append(
            {
                "game_pk": game_pk,
                "at_bat_index": about["atBatIndex"],
                "inning": about["inning"],
                "half": about["halfInning"],
                "start_time_utc": about["startTime"],
                "end_time_utc": about["endTime"],
                "event": result.get("event"),
                "description": result.get("description"),
                "batter_id": matchup["batter"]["id"],
                "batter_name": matchup["batter"]["fullName"],
                "pitcher_id": matchup["pitcher"]["id"],
                "pitcher_name": matchup["pitcher"]["fullName"],
                "home_score": result["homeScore"],
                "away_score": result["awayScore"],
                "is_scoring_play": bool(about.get("isScoringPlay", False)),
            }
        )
    plays = pd.DataFrame(rows)
    for column in ("start_time_utc", "end_time_utc"):
        plays[column] = pd.to_datetime(plays[column], utc=True)
    return plays.sort_values("at_bat_index").reset_index(drop=True)


def build_play_events(feed: dict) -> pd.DataFrame:
    """One row per ``playEvents`` entry: every pitch, pickoff and action, with its own times."""
    game_pk = feed["gameData"]["game"]["pk"]
    rows: list[dict[str, Any]] = []
    for play in feed["liveData"]["plays"]["allPlays"]:
        for event in play["playEvents"]:
            count = event.get("count", {})
            rows.append(
                {
                    "game_pk": game_pk,
                    "at_bat_index": play["about"]["atBatIndex"],
                    "event_index": event["index"],
                    "type": event["type"],
                    "is_pitch": bool(event["isPitch"]),
                    "start_time_utc": event["startTime"],
                    "end_time_utc": event["endTime"],
                    "description": event["details"].get("description"),
                    "balls": count.get("balls"),
                    "strikes": count.get("strikes"),
                    "outs": count.get("outs"),
                }
            )
    events = pd.DataFrame(rows)
    for column in ("start_time_utc", "end_time_utc"):
        events[column] = pd.to_datetime(events[column], utc=True)
    return events.sort_values(["at_bat_index", "event_index"]).reset_index(drop=True)


def game_window(plays: pd.DataFrame, play_events: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First pitch and final out, from the feed's own times."""
    first_pitch = play_events.loc[play_events["is_pitch"], "start_time_utc"].min()
    return first_pitch, plays["end_time_utc"].max()


def build_win_prob(game_pk: int, win_probability: list[dict]) -> pd.DataFrame:
    """Home win probability before and after each plate appearance, on a 0..1 scale.

    The API reports percentages: ``homeTeamWinProbability`` is the value after
    the play and ``homeTeamWinProbabilityAdded`` is the change the play caused.
    """
    rows = []
    for entry in win_probability:
        after = entry["homeTeamWinProbability"] / 100
        delta = entry["homeTeamWinProbabilityAdded"] / 100
        rows.append(
            {
                "game_pk": game_pk,
                "at_bat_index": entry["atBatIndex"],
                "home_wp_before": round(after - delta, 4),
                "home_wp_after": round(after, 4),
                "wp_delta": round(delta, 4),
            }
        )
    return pd.DataFrame(rows).sort_values("at_bat_index").reset_index(drop=True)


def ingest_game(game_pk: int) -> dict[str, pd.DataFrame]:
    """Fetch (or read from cache) one finished game and write its parquet tables."""
    feed = fetch_feed(game_pk)
    if not _is_final(feed["gameData"]["status"]):
        state = feed["gameData"]["status"].get("detailedState")
        raise RuntimeError(f"game {game_pk} is not final yet (status: {state}); replay ingest runs after the game")

    tables = {
        "games": build_games(feed, fetch_series_desc(game_pk)),
        "plays": build_plays(feed),
        "play_events": build_play_events(feed),
        "win_prob": build_win_prob(game_pk, fetch_win_probability(game_pk)),
    }

    missing = set(tables["plays"]["at_bat_index"]) - set(tables["win_prob"]["at_bat_index"])
    if missing:
        log.warning("game %s: %d plays have no win probability: %s", game_pk, len(missing), sorted(missing))

    for name, table in tables.items():
        storage.write_table(name, game_pk, table)
    return tables
