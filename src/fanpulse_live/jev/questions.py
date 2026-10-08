"""The fixed question set asked of Jev on every update.

Window-level questions are answered about the whole window of comments.
Comment-level questions are asked once per comment, when it is new. Jev's
three question types are ``noul`` (yes/no, returns P(yes)), ``choice`` (one
option, returns a probability per option) and ``score`` (a position on an
ordered list of levels, 0 = first level).
"""

from __future__ import annotations

from fanpulse_live.gamestate import GameTimeline, Player

# The README's budget per call. The API accepted 65 in a test, so this is our
# cap, not an enforced limit.
MAX_QUESTIONS = 64
QUESTIONS_PER_COMMENT = 2

MOOD_LEVELS = [
    "Despair: fans think the game is lost or are miserable about their team",
    "Unhappy: frustrated, worried or annoyed with how it is going for their team",
    "Neutral or mixed: no clear feeling either way about how the game is going",
    "Pleased: encouraged, things are going their team's way",
    "Elation: celebrating, thrilled with their team",
]

EMOTIONS = {
    "joy": "Celebration, delight or relief.",
    "anger": "Fury, blame or disgust.",
    "anxiety": "Nervousness or dread about what happens next.",
    "disbelief": "Shock or amazement at what just happened, good or bad.",
    "sarcasm": "Sarcasm or gallows humor: joking about how badly it is going.",
    "resignation": "Giving up: the game is lost, nothing to be done.",
    "boredom": "Disengaged, off-topic or killing time.",
}

SENTIMENT_LEVELS = [
    "Very negative: furious, disgusted or despairing",
    "Negative: critical, annoyed or worried",
    "Neutral, factual or unclear",
    "Positive: approving, hopeful or pleased",
    "Very positive: thrilled, celebrating or full of praise",
]

SARCASM_NOTE = "Read sarcasm for what it means, not what it literally says."


def subject_options(timeline: GameTimeline, players: list[Player] | None = None) -> dict[str, None]:
    """Who a comment can be about: players, then the non-player subjects.

    The option list is repeated in every subject question, so it is kept as
    small as it can be. Options are bare names with no descriptions: what each
    means is said once, in the state's ``subject_guide``, and teams are in its
    ``players``. With ``players`` only those are offered, plus a catch-all per
    team for anyone else on a roster; without it, every player on both rosters.
    """
    if players is None:
        players = sorted(timeline.players.values(), key=lambda p: (p.team, p.name))
        catch_alls = []
    else:
        catch_alls = [f"another {timeline.away_name} player", f"another {timeline.home_name} player"]
    names = [player.name for player in players] + catch_alls + [
        f"{timeline.away_name} manager",
        f"{timeline.home_name} manager",
        "umpire",
        f"{timeline.away_name} team",
        f"{timeline.home_name} team",
        "broadcast",
        "other",
    ]
    return dict.fromkeys(names)


def subject_guide(timeline: GameTimeline) -> str:
    """What the subject options mean, said once per call instead of inside every question."""
    return (
        "For questions asking who or what a comment is about: choose a listed player if the comment is about him, "
        "using the recent plays and any reply context to work out who 'he' or 'this guy' means. Choose 'another "
        f"{timeline.away_name} player' or 'another {timeline.home_name} player' for a player who is not listed. "
        "Choose a team's 'manager' for the manager or coaches: pitching changes, lineups, in-game decisions. Choose "
        "'umpire' for the umpires or a specific call. Choose a 'team' for the team as a whole, its bullpen or its "
        "offense rather than one player. Choose 'broadcast' for the announcers, the TV broadcast, ads or the stream. "
        "Choose 'other' for anything else: general chatter, other teams, the fans themselves, or unclear."
    )


def window_questions(team_name: str | None) -> dict[str, dict]:
    """The questions answered about the whole window.

    ``team_name`` is None for a neutral crowd, which gets no mood question:
    mood is a fanbase's feeling about its own team, and these commenters have
    no team in the game. The window's main subject is not asked: it is the
    most common subject among the comment tags, which costs nothing.
    """
    mood = {}
    if team_name:
        mood["mood"] = {
            "type": "score",
            "instructions": f"Taking the comments together, how do these {team_name} fans feel about how the game "
            f"is going for the {team_name} right now? {SARCASM_NOTE}",
            "criteria": MOOD_LEVELS,
        }
    return {
        **mood,
        "emotion": {
            "type": "choice",
            "instructions": "What is the dominant emotion across the comments?",
            "criteria": EMOTIONS,
        },
        "moment": {
            "type": "noul",
            "instructions": "Are the comments reacting to one specific, notable thing that just happened in the game?",
            "criteria": {
                "true": "Several comments react to the same recent play, call or decision.",
                "false": "Ordinary chatter, scattered topics, or reactions to nothing in particular.",
            },
        },
        "blame": {
            "type": "noul",
            "instructions": "Are the comments criticizing a decision by a manager or the front office?",
            "criteria": {
                "true": "Comments fault a pitching change, a lineup choice, leaving a pitcher in, or a roster move.",
                "false": "No criticism, or criticism aimed at players, umpires or luck.",
            },
        },
    }


def comment_questions(k: int, subjects: dict[str, str | None]) -> dict[str, dict]:
    """The two questions asked once about comment ``[k]``."""
    return {
        f"subj_{k}": {
            "type": "choice",
            "instructions": f"Who or what is comment [{k}] mainly about?",
            "criteria": subjects,
        },
        f"sent_{k}": {
            "type": "score",
            "instructions": f"How does the author of comment [{k}] feel about what they are commenting on? "
            f"{SARCASM_NOTE}",
            "criteria": SENTIMENT_LEVELS,
        },
    }


def question_batches(
    team_name: str | None, subjects: dict[str, str | None], new_comments: list[int]
) -> list[dict[str, dict]]:
    """Question sets for one update, one per Jev call.

    The first call carries the window questions plus as many new comments as
    fit; a burst of new comments spills into extra calls.
    """
    batches = [window_questions(team_name)]
    for k in new_comments:
        if len(batches[-1]) + QUESTIONS_PER_COMMENT > MAX_QUESTIONS:
            batches.append({})
        batches[-1].update(comment_questions(k, subjects))
    return batches


def score_to_unit(answer: dict) -> float:
    """A score answer mapped from its 0..n-1 levels onto -1..+1."""
    top = len(answer["legend"]) - 1
    return round(answer["score"] / top * 2 - 1, 3)
