import pandas as pd
import pytest

from fanpulse_live.ingest import mlb


def _play(index, half, inning, home_score, away_score, scoring=False, complete=True):
    return {
        "about": {
            "atBatIndex": index,
            "halfInning": half,
            "inning": inning,
            "startTime": f"2025-10-25T00:0{index}:00.000Z",
            "endTime": f"2025-10-25T00:0{index}:30.500Z",
            "isComplete": complete,
            "isScoringPlay": scoring,
        },
        "result": {
            "event": "Single",
            "description": f"play {index}",
            "homeScore": home_score,
            "awayScore": away_score,
        },
        "matchup": {
            "batter": {"id": 100 + index, "fullName": f"Batter {index}"},
            "pitcher": {"id": 200, "fullName": "Pitcher"},
        },
        "playEvents": [
            {
                "index": 1,
                "type": "pitch",
                "isPitch": True,
                "startTime": f"2025-10-25T00:0{index}:20.000Z",
                "endTime": f"2025-10-25T00:0{index}:30.500Z",
                "details": {"description": "In play, no out"},
                "count": {"balls": 0, "strikes": 1, "outs": 0},
            },
            {
                "index": 0,
                "type": "action",
                "isPitch": False,
                "startTime": f"2025-10-25T00:0{index}:00.000Z",
                "endTime": f"2025-10-25T00:0{index}:05.000Z",
                "details": {"description": "Status Change - In Progress"},
                "count": {"balls": 0, "strikes": 0, "outs": 0},
            },
        ],
    }


@pytest.fixture
def feed():
    return {
        "gameData": {
            "game": {"pk": 1},
            "datetime": {"officialDate": "2025-10-24", "dateTime": "2025-10-25T00:08:00Z"},
            "status": {"abstractGameState": "Final"},
            "teams": {
                "home": {"name": "Toronto Blue Jays", "abbreviation": "TOR", "teamName": "Blue Jays"},
                "away": {"name": "Los Angeles Dodgers", "abbreviation": "LAD", "teamName": "Dodgers"},
            },
        },
        "liveData": {
            "linescore": {"teams": {"home": {"runs": 11}, "away": {"runs": 4}}},
            "plays": {
                "allPlays": [
                    _play(1, "bottom", 1, 1, 0, scoring=True),
                    _play(0, "top", 1, 0, 0),
                    _play(2, "top", 2, 1, 0, complete=False),
                ]
            },
        },
    }


def test_build_plays_flattens_sorts_and_parses_utc(feed):
    plays = mlb.build_plays(feed)

    assert list(plays["at_bat_index"]) == [0, 1]  # sorted, incomplete play dropped
    assert plays.loc[1, "half"] == "bottom"
    assert plays.loc[1, "is_scoring_play"]
    assert plays.loc[0, "batter_id"] == 100
    assert plays.loc[0, "end_time_utc"] == pd.Timestamp("2025-10-25T00:00:30.500Z")
    assert str(plays["start_time_utc"].dt.tz) == "UTC"


def test_build_play_events_keeps_every_event_in_order(feed):
    events = mlb.build_play_events(feed)

    assert list(zip(events["at_bat_index"], events["event_index"]))[:4] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert list(events["is_pitch"][:2]) == [False, True]
    assert events.loc[1, "description"] == "In play, no out"
    assert events.loc[1, "strikes"] == 1


def test_game_window_starts_at_the_first_pitch_not_the_first_action(feed):
    first_pitch, final_out = mlb.game_window(mlb.build_plays(feed), mlb.build_play_events(feed))

    assert first_pitch == pd.Timestamp("2025-10-25T00:00:20Z")
    assert final_out == pd.Timestamp("2025-10-25T00:01:30.500Z")


def test_build_games_uses_linescore(feed):
    game = mlb.build_games(feed, "World Series").iloc[0]

    assert (game.home_score, game.away_score) == (11, 4)
    assert game.final_score == "LAD 4 @ TOR 11"
    assert game.series_desc == "World Series"


def test_build_win_prob_converts_percent_and_derives_before():
    raw = [
        {"atBatIndex": 1, "homeTeamWinProbability": 40.0, "homeTeamWinProbabilityAdded": -12.2},
        {"atBatIndex": 0, "homeTeamWinProbability": 52.2, "homeTeamWinProbabilityAdded": 2.2},
    ]

    wp = mlb.build_win_prob(1, raw)

    assert list(wp["at_bat_index"]) == [0, 1]
    assert wp.loc[0, ["home_wp_before", "home_wp_after", "wp_delta"]].tolist() == [0.5, 0.522, 0.022]
    assert wp.loc[1, "home_wp_before"] == wp.loc[0, "home_wp_after"]


def _schedule_game(pk, away, home):
    return {
        "gamePk": pk,
        "status": {"abstractGameState": "Final"},
        "teams": {"away": {"team": {"name": away}}, "home": {"team": {"name": home}}},
    }


def test_find_game_pk_matches_teams(monkeypatch):
    games = [
        _schedule_game(10, "Detroit Tigers", "Cleveland Guardians"),
        _schedule_game(20, "Los Angeles Dodgers", "Toronto Blue Jays"),
    ]
    monkeypatch.setattr(mlb, "fetch_schedule", lambda date, game_type=None: games)

    assert mlb.find_game_pk("2025-10-24", home="blue jays", away="Dodgers") == 20
    with pytest.raises(mlb.GameLookupError, match="found 2"):
        mlb.find_game_pk("2025-10-24")
    with pytest.raises(mlb.GameLookupError, match="none"):
        mlb.find_game_pk("2025-10-24", home="Yankees")
