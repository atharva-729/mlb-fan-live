import json
from datetime import timedelta

import pytest

from fanpulse_live.gamestate import GameTimeline, parse_time
from fanpulse_live.ingest.clean import clean_text
from fanpulse_live.jev import client, questions
from fanpulse_live.jev import state as jev_state
from tests.feeds import make_feed, make_win_probability

DODGERS = {"id": "r/Dodgers", "subreddit": "Dodgers", "flair": None, "team": "LAD"}
JAYS_FLAIR = {"id": "r/baseball:TOR-flair", "subreddit": "baseball", "flair": "TOR", "team": "TOR"}
NEUTRAL = {"id": "r/baseball:neutral", "subreddit": "baseball", "flair": "neutral", "team": None}


@pytest.fixture
def timeline():
    return GameTimeline(make_feed(), make_win_probability())


@pytest.mark.parametrize(
    "body, expected",
    [
        ("**WHAT** a *swing*", "WHAT a swing"),
        ("look https://i.imgur.com/x.gif now", "look [link] now"),
        ("see [this clip](https://x.com/a) lol", "see this clip lol"),
        ("![gif](giphy|abc123) LET'S GO", "[gif] LET'S GO"),
        ("> he is cooked\n\nno he isn't", "he is cooked no he isn't"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
    ],
)
def test_clean_text(body, expected):
    assert clean_text(body) == expected


def test_clean_text_truncates():
    assert clean_text("a" * 400) == "a" * 299 + "…"


def test_community_label_says_whose_fans_these_are(timeline):
    assert jev_state.community_label(DODGERS, timeline) == "r/Dodgers, the Dodgers fan community: Dodgers fans"
    assert "commenters with a Blue Jays flair: Blue Jays fans" in jev_state.community_label(JAYS_FLAIR, timeline)
    assert "neutral viewers" in jev_state.community_label(NEUTRAL, timeline)


def test_win_probability_is_from_the_fanbase_side(timeline):
    assert jev_state.render_win_probability(0.6, "TOR", timeline) == "Blue Jays 60%"
    assert jev_state.render_win_probability(0.6, "LAD", timeline) == "Dodgers 40%"
    assert jev_state.render_win_probability(0.6, None, timeline) == "Blue Jays 60%, Dodgers 40%"


def test_build_state_for_the_away_fans(timeline):
    t = parse_time("2025-10-25T00:13:20Z")
    comments = [
        jev_state.WindowComment(t - timedelta(seconds=3), "exactly", True, parent_body="Relief is *cooked*"),
        jev_state.WindowComment(t - timedelta(seconds=15), "LET'S GO **BAKER**", False),
    ]

    state = jev_state.build_state(timeline, t, DODGERS, comments, label="Test Game", window_s=20, play_lookback_s=90)

    assert state["game_situation"] == (
        "Test Game: Dodgers (away) at Blue Jays (home). Top 1st, 1 out, runner on 1st. Tied 0-0. "
        "The next batter is coming up."
    )
    assert state["win_probability"] == "Dodgers 52%"
    assert state["recent_plays"] == ["20s ago: Bo Baker singles to left. (Dodgers win probability +4 points)"]
    assert state["players_today"] == [
        "Bo Baker (Dodgers): 1-for-1",
        "Rex Relief (Blue Jays): 0.0 IP, 0 ER, 1 H, 0 BB, 0 K",
    ]
    assert state["comments"] == [
        "[1] 15s ago: LET'S GO BAKER",
        '[2]* 3s ago: exactly ↳ replying to: "Relief is cooked"',
    ]
    assert jev_state.new_comment_numbers(comments) == [2]
    assert state["rosters"]["Dodgers"] == "Al Able, Bo Baker, Dan Dropped, Vic Visitor"


def test_state_never_mentions_a_play_that_has_not_ended(timeline):
    t = parse_time("2025-10-25T00:20:10Z")  # the home run is in the air

    state = jev_state.build_state(timeline, t, DODGERS, [], label="Test Game", window_s=20, play_lookback_s=90)

    assert "homers" not in json.dumps(state)
    assert "Hal Homer (Blue Jays)" in state["game_situation"]


def test_subject_options_list_every_player_then_the_rest(timeline):
    options = questions.subject_options(timeline)

    assert list(options)[:4] == ["Al Able", "Bo Baker", "Dan Dropped", "Vic Visitor"]
    assert options["Al Able"] is None
    assert list(options)[-7:] == [
        "Dodgers manager", "Blue Jays manager", "umpire", "Dodgers team", "Blue Jays team", "broadcast", "other",
    ]  # fmt: skip


def test_window_questions_change_mood_for_a_neutral_crowd(timeline):
    subjects = questions.subject_options(timeline)

    team = questions.window_questions("Dodgers", subjects)
    neutral = questions.window_questions(None, subjects)

    assert list(team) == ["mood", "target", "emotion", "moment", "blame"]
    assert "Dodgers fans" in team["mood"]["instructions"]
    assert neutral["mood"]["criteria"] == questions.NEUTRAL_MOOD_LEVELS
    assert team["moment"]["type"] == "noul"


def test_question_batches_spill_a_burst_into_extra_calls(timeline):
    subjects = questions.subject_options(timeline)

    quiet = questions.question_batches("Dodgers", subjects, [3, 4])
    burst = questions.question_batches("Dodgers", subjects, list(range(1, 41)))

    assert len(quiet) == 1 and "subj_3" in quiet[0] and "sent_4" in quiet[0]
    assert [len(batch) for batch in burst] == [63, 22]  # 5 window + 29 comments, then 11 comments
    assert all(len(batch) <= questions.MAX_QUESTIONS for batch in burst)
    assert "mood" not in burst[1]


def test_score_to_unit_maps_levels_onto_minus_one_to_one():
    legend = {str(i): "" for i in range(5)}

    assert questions.score_to_unit({"score": 0, "legend": legend}) == -1
    assert questions.score_to_unit({"score": 2, "legend": legend}) == 0
    assert questions.score_to_unit({"score": 3.5, "legend": legend}) == 0.75


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.bodies.append(json)
        return self.responses.pop(0)


ANSWER = {"answers": {"q": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 100, "cost": 0.0000042}}
QUESTION = {"q": {"type": "noul", "instructions": "Is it?"}}


@pytest.fixture
def jev_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FANPULSE_LIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-test")
    sleeps = []
    monkeypatch.setattr(client.time, "sleep", sleeps.append)
    return sleeps


def test_ask_sends_the_documented_body_and_caches(jev_env):
    session = FakeSession([FakeResponse(200, ANSWER)])

    first = client.ask({"a": 1}, QUESTION, session=session)
    second = client.ask({"a": 1}, QUESTION, session=session)

    assert session.bodies == [{"model": "typesafe/jev-test", "state": {"a": 1}, "questions": QUESTION}]
    assert (first["cached"], second["cached"]) == (False, True)
    assert second["answers"] == ANSWER["answers"]
    assert client.usage_summary()["calls"] == 1
    assert client.usage_summary()["input_tokens"] == 100


def test_ask_keys_the_cache_on_state_and_questions(jev_env):
    session = FakeSession([FakeResponse(200, ANSWER), FakeResponse(200, ANSWER)])

    client.ask({"a": 1}, QUESTION, session=session)
    client.ask({"a": 2}, QUESTION, session=session)

    assert len(session.bodies) == 2


def test_ask_retries_rate_limits_but_not_bad_requests(jev_env):
    retried = FakeSession([FakeResponse(429, {"error": "slow down"}), FakeResponse(200, ANSWER)])
    assert client.ask({"a": 1}, QUESTION, session=retried)["answers"] == ANSWER["answers"]
    assert jev_env == [client.BACKOFF_SECONDS]

    rejected = FakeSession([FakeResponse(400, {"error": "bad question"})])
    with pytest.raises(client.JevError, match="400"):
        client.ask({"a": 3}, QUESTION, session=rejected)
    assert len(rejected.bodies) == 1


def test_ask_needs_a_key(jev_env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")

    with pytest.raises(client.JevError, match="OPENROUTER_API_KEY"):
        client.ask({"a": 1}, QUESTION, session=FakeSession([]))
