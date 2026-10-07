import pandas as pd
import pytest

from fanpulse_live import config
from fanpulse_live.ingest import streams

FLAIRS = {"LAD": ["Dodgers"], "TOR": ["Blue Jays"]}


@pytest.mark.parametrize(
    "flair, expected",
    [
        (":tor2: Toronto Blue Jays", "TOR"),
        (":lad: :worldseriestrophy: Los Angeles Dodgers • World Series Tr…", "LAD"),
        (":texworldseries: Texas Rangers", "neutral"),
        (None, "neutral"),
        ("", "neutral"),
        ("los angeles dodgers", "LAD"),
        (":lad: Los Angeles Dodgers • Toronto Blue Jays", "neutral"),
    ],
)
def test_flair_side(flair, expected):
    assert streams.flair_side(flair, FLAIRS) == expected


def test_assign_streams_splits_only_the_mixed_subreddit():
    loaded = config.load_game()
    comments = pd.DataFrame(
        {
            "subreddit": ["baseball", "baseball", "baseball", "Dodgers", "Torontobluejays"],
            "author_flair_text": [
                ":lad: Los Angeles Dodgers",
                ":tor2: Toronto Blue Jays",
                None,
                # A rival's flair in a team subreddit still counts as that subreddit's crowd.
                ":tor2: Toronto Blue Jays",
                None,
            ],
        }
    )

    assigned = streams.assign_streams(comments, loaded["streams"], loaded["flairs"])

    assert list(assigned) == [
        "r/baseball:LAD-flair",
        "r/baseball:TOR-flair",
        "r/baseball:neutral",
        "r/Dodgers",
        "r/Torontobluejays",
    ]


def test_tag_phase_uses_first_pitch_and_final_out():
    first_pitch = pd.Timestamp("2025-10-25T00:14:00Z")
    final_out = pd.Timestamp("2025-10-25T03:27:00Z")
    created = pd.Series(
        pd.to_datetime(["2025-10-24T22:00:00Z", "2025-10-25T00:14:00Z", "2025-10-25T02:00:00Z", "2025-10-25T03:27:01Z"])
    )

    assert list(streams.tag_phase(created, first_pitch, final_out)) == ["pregame", "in-game", "in-game", "postgame"]


def test_hash_author_is_stable_and_hides_the_name():
    hashed = streams.hash_author("some_fan")

    assert hashed == streams.hash_author("some_fan")
    assert hashed != streams.hash_author("other_fan")
    assert "some_fan" not in hashed
    assert streams.hash_author("[deleted]") is None
    assert streams.hash_author(None) is None
