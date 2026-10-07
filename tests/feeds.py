"""A three-play game in the MLB live-feed shape, for tests.

Top 1st: Able strikes out, then Baker (pinch-hitting for Dropped, against the
reliever Relief who replaces Starter mid-at-bat) singles. Bottom 1st: Homer
hits a solo home run off Visitor.
"""

AWAY_IDS = {1: "Al Able", 2: "Bo Baker", 3: "Dan Dropped", 20: "Vic Visitor"}
HOME_IDS = {10: "Sam Starter", 11: "Rex Relief", 12: "Hal Homer"}


def _time(clock: str) -> str:
    return f"2025-10-25T00:{clock}.000Z"


def _pitch(index, start, end, balls, strikes, outs, home=0, away=0):
    return {
        "index": index,
        "type": "pitch",
        "isPitch": True,
        "startTime": _time(start),
        "endTime": _time(end),
        "details": {"description": "pitch", "homeScore": home, "awayScore": away},
        "count": {"balls": balls, "strikes": strikes, "outs": outs},
    }


def _action(index, start, description, event_type, outs=0):
    return {
        "index": index,
        "type": "action",
        "isPitch": False,
        "startTime": _time(start),
        "endTime": _time(start),
        "details": {"description": description, "eventType": event_type, "homeScore": 0, "awayScore": 0},
        "count": {"balls": 0, "strikes": 0, "outs": outs},
    }


def _play(index, half, start, end, event_type, description, batter, pitcher, outs, events, **extra):
    names = {**AWAY_IDS, **HOME_IDS}
    matchup = {
        "batter": {"id": batter, "fullName": names[batter]},
        "pitcher": {"id": pitcher, "fullName": names[pitcher]},
        **extra.get("post", {}),
    }
    return {
        "atBatIndex": index,
        "about": {
            "atBatIndex": index,
            "inning": 1,
            "halfInning": half,
            "startTime": _time(start),
            "endTime": _time(end),
            "isComplete": True,
        },
        "result": {
            "eventType": event_type,
            "description": description,
            "rbi": extra.get("rbi", 0),
            "homeScore": extra.get("home", 0),
            "awayScore": 0,
        },
        "count": {"balls": 0, "strikes": 0, "outs": outs},
        "matchup": matchup,
        "runners": extra.get("runners", []),
        "playEvents": events,
    }


def make_feed() -> dict:
    plays = [
        _play(
            0, "top", "10:00", "11:00", "strikeout", "Al Able strikes out swinging.", 1, 10, 1,
            [
                _action(0, "00:00", "Status Change - Warmup", "game_advisory"),
                _pitch(1, "10:00", "10:05", 0, 1, 0),
                _pitch(2, "10:30", "10:35", 1, 1, 0),
                _pitch(3, "10:55", "11:00", 1, 3, 1),
            ],
        ),  # fmt: skip
        _play(
            1, "top", "12:30", "13:00", "single", "Bo Baker singles to left.", 2, 11, 1,
            [
                _action(0, "11:20", "Offensive Substitution: Pinch-hitter Bo Baker replaces Dan Dropped.", "offensive_substitution", 1),
                _action(1, "11:50", "Pitching Change: Rex Relief replaces Sam Starter.", "pitching_substitution", 1),
                _pitch(2, "12:30", "12:35", 1, 0, 1),
                _pitch(3, "12:55", "13:00", 1, 0, 1),
            ],
            post={"postOnFirst": {"id": 2, "fullName": "Bo Baker"}},
        ),  # fmt: skip
        _play(
            2, "bottom", "20:00", "20:30", "home_run", "Hal Homer homers (1) to left field.", 12, 20, 0,
            [_pitch(0, "20:00", "20:30", 0, 0, 0, home=1)],
            rbi=1,
            home=1,
            runners=[
                {
                    "details": {
                        "isScoringEvent": True,
                        "earned": True,
                        "responsiblePitcher": {"id": 20},
                        "runner": {"id": 12},
                    }
                }
            ],
        ),  # fmt: skip
    ]

    def box(ids):
        return {"players": {f"ID{i}": {"person": {"id": i, "fullName": name}} for i, name in ids.items()}}

    return {
        "gameData": {
            "game": {"pk": 1},
            "teams": {
                "home": {"abbreviation": "TOR", "teamName": "Blue Jays", "name": "Toronto Blue Jays"},
                "away": {"abbreviation": "LAD", "teamName": "Dodgers", "name": "Los Angeles Dodgers"},
            },
            "players": {f"ID{i}": {"id": i, "fullName": name} for i, name in {**AWAY_IDS, **HOME_IDS}.items()},
        },
        "liveData": {
            "plays": {"allPlays": plays},
            "boxscore": {"teams": {"home": box(HOME_IDS), "away": box(AWAY_IDS)}},
        },
    }


def make_win_probability() -> list[dict]:
    return [
        {"atBatIndex": 0, "homeTeamWinProbability": 52.0, "homeTeamWinProbabilityAdded": 2.0},
        {"atBatIndex": 1, "homeTeamWinProbability": 48.0, "homeTeamWinProbabilityAdded": -4.0},
        {"atBatIndex": 2, "homeTeamWinProbability": 60.0, "homeTeamWinProbabilityAdded": 12.0},
    ]
