"""The game as it stood at any moment, rebuilt from the MLB live feed.

Everything here answers "what was known at time t" and nothing later, so a
replay sees exactly what a live run would have seen: a play's result only once
the play has ended, a substitution only once it has been announced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

HIT_EVENTS = {"single", "double", "triple", "home_run"}
WALK_EVENTS = {"walk", "intent_walk"}
# Plate appearances that do not count as an at-bat.
NO_AT_BAT_EVENTS = WALK_EVENTS | {"hit_by_pitch", "sac_fly", "sac_bunt", "sac_fly_double_play", "catcher_interf"}
STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play"}
BASES = (("postOnFirst", "1st"), ("postOnSecond", "2nd"), ("postOnThird", "3rd"))
PINCH_HITTER_PREFIX = "Offensive Substitution: Pinch-hitter "


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    team: str  # team abbreviation


@dataclass(frozen=True)
class Play:
    index: int
    inning: int
    half: str  # "top" or "bottom"
    start: datetime
    end: datetime
    event_type: str
    description: str
    batter_id: int
    pitcher_id: int
    rbi: int
    home_score: int
    away_score: int
    outs_after: int
    runners_after: dict[str, int]  # base -> player id
    earned_runs: dict[int, int]  # pitcher id -> earned runs charged on this play
    home_wp_before: float
    home_wp_after: float
    events: list[dict] = field(repr=False)

    @property
    def wp_delta(self) -> float:
        return self.home_wp_after - self.home_wp_before


@dataclass(frozen=True)
class GameState:
    status: str  # "pregame", "in_play", "between_plays", "between_innings", "final"
    inning: int
    half: str
    outs: int
    balls: int
    strikes: int
    home_score: int
    away_score: int
    runners: dict[str, int]
    batter_id: int | None
    pitcher_id: int | None
    home_wp: float


class GameTimeline:
    """One finished or in-progress game, queryable by time."""

    def __init__(self, feed: dict, win_probability: list[dict]):
        game = feed["gameData"]
        self.home = game["teams"]["home"]["abbreviation"]
        self.away = game["teams"]["away"]["abbreviation"]
        self.home_name = game["teams"]["home"]["teamName"]
        self.away_name = game["teams"]["away"]["teamName"]

        names = {p["id"]: p["fullName"] for p in game["players"].values()}
        self.players: dict[int, Player] = {}
        for side, abbr in (("home", self.home), ("away", self.away)):
            for entry in feed["liveData"]["boxscore"]["teams"][side]["players"].values():
                player_id = entry["person"]["id"]
                self.players[player_id] = Player(player_id, names.get(player_id, entry["person"]["fullName"]), abbr)
        self._ids_by_name = {p.name: p.id for p in self.players.values()}

        wp = {entry["atBatIndex"]: entry for entry in win_probability}
        self.plays: list[Play] = []
        previous_end: datetime | None = None
        for raw in sorted(feed["liveData"]["plays"]["allPlays"], key=lambda p: p["atBatIndex"]):
            about = raw["about"]
            if not about.get("isComplete", True) or raw["atBatIndex"] not in wp:
                continue
            # A plate appearance is under way from its first substitution or mound
            # visit, which the feed logs before ``about.startTime`` (the first pitch).
            # Pre-game status advisories ride on the first play and are not part of it.
            start = min(
                [parse_time(about["startTime"])]
                + [
                    parse_time(e["startTime"])
                    for e in raw["playEvents"]
                    if e["details"].get("eventType") != "game_advisory"
                ]
            )
            if previous_end is not None:
                start = max(start, previous_end)
            previous_end = parse_time(about["endTime"])
            after = wp[raw["atBatIndex"]]["homeTeamWinProbability"] / 100
            added = wp[raw["atBatIndex"]]["homeTeamWinProbabilityAdded"] / 100
            earned: dict[int, int] = {}
            for runner in raw["runners"]:
                details = runner["details"]
                if details.get("isScoringEvent") and details.get("earned") and details.get("responsiblePitcher"):
                    pitcher = details["responsiblePitcher"]["id"]
                    earned[pitcher] = earned.get(pitcher, 0) + 1
            self.plays.append(
                Play(
                    index=raw["atBatIndex"],
                    inning=about["inning"],
                    half=about["halfInning"],
                    start=start,
                    end=previous_end,
                    event_type=raw["result"].get("eventType", ""),
                    description=raw["result"].get("description", ""),
                    batter_id=raw["matchup"]["batter"]["id"],
                    pitcher_id=raw["matchup"]["pitcher"]["id"],
                    rbi=raw["result"].get("rbi", 0),
                    home_score=raw["result"]["homeScore"],
                    away_score=raw["result"]["awayScore"],
                    outs_after=raw["count"]["outs"],
                    runners_after={base: raw["matchup"][key]["id"] for key, base in BASES if key in raw["matchup"]},
                    earned_runs=earned,
                    home_wp_before=after - added,
                    home_wp_after=after,
                    events=raw["playEvents"],
                )
            )
        self._position = {p.index: i for i, p in enumerate(self.plays)}

    def name(self, player_id: int) -> str:
        return self.players[player_id].name

    def first_pitch(self) -> datetime:
        return min(parse_time(e["startTime"]) for p in self.plays for e in p.events if e["isPitch"])

    def final_out(self) -> datetime:
        return self.plays[-1].end

    def completed(self, t: datetime) -> list[Play]:
        """Plays that had ended by ``t``."""
        return [p for p in self.plays if p.end <= t]

    def recent_plays(self, t: datetime, lookback_s: float) -> list[Play]:
        """Plays that ended in the last ``lookback_s`` seconds, newest first."""
        earliest = t - timedelta(seconds=lookback_s)
        return [p for p in reversed(self.plays) if earliest < p.end <= t]

    def _before(self, play: Play) -> Play | None:
        position = self._position[play.index]
        return self.plays[position - 1] if position > 0 else None

    def _same_half_before(self, play: Play) -> Play | None:
        previous = self._before(play)
        if previous and (previous.inning, previous.half) == (play.inning, play.half):
            return previous
        return None

    def _matchup_at(self, play: Play, t: datetime) -> tuple[int | None, int | None]:
        """Batter and pitcher as announced by ``t``, undoing substitutions still to come."""
        batter: int | None = play.batter_id
        pitcher: int | None = play.pitcher_id
        for event in play.events:
            if event["isPitch"] or parse_time(event["startTime"]) <= t:
                continue
            details = event["details"]
            description = details.get("description", "")
            if details.get("eventType") == "pitching_substitution":
                # The pitcher being replaced faced this half-inning's previous batter.
                previous = self._same_half_before(play)
                pitcher = previous.pitcher_id if previous else None
            elif description.startswith(PINCH_HITTER_PREFIX) and " replaces " in description:
                replaced = description.split(" replaces ", 1)[1].rstrip(". ")
                batter = self._ids_by_name.get(replaced)
        return batter, pitcher

    def at(self, t: datetime) -> GameState:
        started = [p for p in self.plays if p.start <= t]
        if not started:
            first = self.plays[0]
            return GameState("pregame", 1, "top", 0, 0, 0, 0, 0, {}, None, None, first.home_wp_before)

        play = started[-1]
        if play.end <= t:
            status = "between_plays"
            if play is self.plays[-1]:
                status = "final"
            elif play.outs_after == 3:
                status = "between_innings"
            return GameState(
                status, play.inning, play.half, play.outs_after, 0, 0, play.home_score, play.away_score,
                {} if play.outs_after == 3 else play.runners_after, None, None, play.home_wp_after,
            )  # fmt: skip

        previous = self._same_half_before(play)
        outs = previous.outs_after if previous else 0
        last = self._before(play)
        home_score, away_score = (last.home_score, last.away_score) if last else (0, 0)
        balls = strikes = 0
        for event in play.events:
            if parse_time(event["endTime"]) > t:
                continue
            count, details = event.get("count", {}), event["details"]
            balls, strikes = count.get("balls", balls), count.get("strikes", strikes)
            outs = count.get("outs", outs)
            home_score = details.get("homeScore", home_score)
            away_score = details.get("awayScore", away_score)

        batter, pitcher = self._matchup_at(play, t)
        return GameState(
            "in_play", play.inning, play.half, outs, balls, strikes, home_score, away_score,
            previous.runners_after if previous else {}, batter, pitcher, play.home_wp_before,
        )  # fmt: skip

    def batting_line(self, player_id: int, t: datetime) -> str | None:
        """Today's line as a batter up to ``t``, like ``1-for-3, HR, 2 RBI``."""
        plays = [p for p in self.completed(t) if p.batter_id == player_id]
        if not plays:
            return None
        at_bats = sum(p.event_type not in NO_AT_BAT_EVENTS for p in plays)
        hits = sum(p.event_type in HIT_EVENTS for p in plays)
        extras = {
            "HR": sum(p.event_type == "home_run" for p in plays),
            "RBI": sum(p.rbi for p in plays),
            "BB": sum(p.event_type in WALK_EVENTS for p in plays),
            "K": sum(p.event_type in STRIKEOUT_EVENTS for p in plays),
        }
        return ", ".join([f"{hits}-for-{at_bats}"] + [_counted(n, label) for label, n in extras.items() if n])

    def pitching_line(self, player_id: int, t: datetime) -> str | None:
        """Today's line as a pitcher up to ``t``, like ``5.0 IP, 5 ER, 4 H, 3 BB, 6 K``."""
        done = self.completed(t)
        plays = [p for p in done if p.pitcher_id == player_id]
        if not plays:
            return None
        outs = 0
        for play in plays:
            previous = self._same_half_before(play)
            outs += play.outs_after - (previous.outs_after if previous else 0)
        earned = sum(p.earned_runs.get(player_id, 0) for p in done)
        hits = sum(p.event_type in HIT_EVENTS for p in plays)
        walks = sum(p.event_type in WALK_EVENTS for p in plays)
        strikeouts = sum(p.event_type in STRIKEOUT_EVENTS for p in plays)
        return f"{outs // 3}.{outs % 3} IP, {earned} ER, {hits} H, {walks} BB, {strikeouts} K"

    def player_line(self, player_id: int, t: datetime) -> str | None:
        """Batting and/or pitching line, or None if the player has done nothing yet."""
        lines = [line for line in (self.batting_line(player_id, t), self.pitching_line(player_id, t)) if line]
        return "; ".join(lines) or None


def _counted(n: int, label: str) -> str:
    return label if n == 1 else f"{n} {label}"


def half_label(inning: int, half: str) -> str:
    return f"{'Top' if half == 'top' else 'Bottom'} {ordinal(inning)}"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def load_timeline(game_pk: int) -> GameTimeline:
    """The timeline for a game, from the cached feed (fetched if not yet cached)."""
    from fanpulse_live.ingest import mlb

    return GameTimeline(mlb.fetch_feed(game_pk), mlb.fetch_win_probability(game_pk))
