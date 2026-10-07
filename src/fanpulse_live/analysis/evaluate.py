"""Scores Jev against the hand labels: one Jev call per labelled window, then agreement per question."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fanpulse_live.jev import client, questions

NEW_COMMENT = re.compile(r"^\[(\d+)\]\*")
TEAM_NAMES = {"LAD": "Dodgers", "TOR": "Blue Jays"}


def ask_item(item: dict, subjects: dict[str, str | None]) -> dict[str, dict]:
    """Jev's answers for one sampled window, asked exactly as the tick engine would ask."""
    new_numbers = [int(m.group(1)) for text in item["state"]["comments"] if (m := NEW_COMMENT.match(text))]
    answers: dict[str, dict] = {}
    for batch in questions.question_batches(TEAM_NAMES.get(item["team"]), subjects, new_numbers):
        answers.update(client.ask(item["state"], batch)["answers"])
    return answers


def level(answer: dict) -> int:
    """A five-level score answer as the label scale -2..+2."""
    return round(answer["score"]) - 2


def top_choices(answer: dict, n: int) -> list[str]:
    ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
    return [option for option, _ in ranked[:n]]


def sign(value: float) -> int:
    return (value > 0) - (value < 0)


@dataclass
class Tally:
    """Agreement counts for one question."""

    rows: list[dict] = field(default_factory=list)

    def add(self, **row) -> None:
        self.rows.append(row)

    def rate(self, key: str) -> float:
        return sum(bool(r[key]) for r in self.rows) / len(self.rows) if self.rows else float("nan")

    def mean(self, key: str) -> float:
        return sum(r[key] for r in self.rows) / len(self.rows) if self.rows else float("nan")

    def __len__(self) -> int:
        return len(self.rows)


def compare(items: list[dict], labels: dict[str, dict], answers: dict[str, dict[str, dict]]) -> dict[str, Tally]:
    """One tally per question, each row a (human, Jev) pair with its agreement flags."""
    tallies = {name: Tally() for name in ("mood", "target", "moment", "subject", "sentiment")}
    for item in items:
        human, jev = labels[item["id"]], answers[item["id"]]
        where = {"id": item["id"], "stream": item["stream"], "kind": item["kind"]}

        if item["team"] and human.get("mood") is not None:
            got = level(jev["mood"])
            tallies["mood"].add(
                **where, human=human["mood"], jev=got, score=questions.score_to_unit(jev["mood"]),
                exact=got == human["mood"], within_one=abs(got - human["mood"]) <= 1,
                same_side=sign(got) == sign(human["mood"]), error=abs(got - human["mood"]),
            )  # fmt: skip
        tallies["target"].add(
            **where, human=human["target"], jev=jev["target"]["choice"],
            exact=jev["target"]["choice"] == human["target"], top3=human["target"] in top_choices(jev["target"], 3),
        )  # fmt: skip
        tallies["moment"].add(
            **where, human=human["moment"], jev=jev["moment"]["noul"],
            exact=(jev["moment"]["noul"] >= 0.5) == human["moment"],
        )  # fmt: skip

        for k in item["label_comments"]:
            label = human["comments"][str(k)]
            text = item["state"]["comments"][k - 1]
            subject, sentiment = jev[f"subj_{k}"], jev[f"sent_{k}"]
            tallies["subject"].add(
                **where, k=k, text=text, human=label["subject"], jev=subject["choice"],
                exact=subject["choice"] == label["subject"], top3=label["subject"] in top_choices(subject, 3),
            )  # fmt: skip
            got = level(sentiment)
            tallies["sentiment"].add(
                **where, k=k, text=text, human=label["sentiment"], jev=got,
                score=questions.score_to_unit(sentiment), exact=got == label["sentiment"],
                within_one=abs(got - label["sentiment"]) <= 1, same_side=sign(got) == sign(label["sentiment"]),
                error=abs(got - label["sentiment"]),
            )  # fmt: skip
    return tallies


def compare_baseline(items: list[dict], labels: dict[str, dict], scores: dict[tuple[str, int], float]) -> Tally:
    """The RoBERTa baseline against the same comment sentiment labels.

    ``scores`` maps (window id, comment number) to the baseline's -1..+1
    sentiment, put on the five-level scale the same way Jev's score is.
    """
    tally = Tally()
    for item in items:
        for k in item["label_comments"]:
            if (item["id"], k) not in scores:
                continue
            human = labels[item["id"]]["comments"][str(k)]["sentiment"]
            unit = scores[(item["id"], k)]
            got = round(unit * 2)
            tally.add(
                id=item["id"], stream=item["stream"], kind=item["kind"], k=k, text=item["state"]["comments"][k - 1],
                human=human, jev=got, score=unit, exact=got == human, within_one=abs(got - human) <= 1,
                same_side=sign(got) == sign(human), error=abs(got - human),
            )  # fmt: skip
    return tally


def baseline_section(jev: Tally, roberta: Tally) -> str:
    """Markdown comparing the two models on comment sentiment, the only question both answer."""
    by_key = {(r["id"], r["k"]): r for r in jev.rows}
    pairs = [(by_key[(r["id"], r["k"])], r) for r in roberta.rows if (r["id"], r["k"]) in by_key]
    jev_only = [(j, r) for j, r in pairs if j["same_side"] and not r["same_side"]]
    roberta_only = [(j, r) for j, r in pairs if r["same_side"] and not j["same_side"]]
    lines = [
        "## Jev against the RoBERTa baseline",
        "",
        "Comment sentiment is the only question both models answer. RoBERTa reads the comment text alone; "
        "Jev also sees the game, the community and the comment being replied to.",
        "",
        "| model | n | exact level | within one level | same side (neg/neutral/pos) | mean error (levels) |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Jev | {len(jev)} | {jev.rate('exact'):.0%} | {jev.rate('within_one'):.0%} | {jev.rate('same_side'):.0%} | {jev.mean('error'):.2f} |",
        f"| RoBERTa | {len(roberta)} | {roberta.rate('exact'):.0%} | {roberta.rate('within_one'):.0%} "
        f"| {roberta.rate('same_side'):.0%} | {roberta.mean('error'):.2f} |",
        "",
        f"On the same comments, Jev is on the labeller's side where RoBERTa is not {len(jev_only)} times; "
        f"RoBERTa is where Jev is not {len(roberta_only)} times.",
        "",
        "### Jev right, RoBERTa wrong (side)",
        "",
    ]
    for j, r in jev_only:
        lines.append(f"- human {j['human']:+d}, Jev {j['jev']:+d}, RoBERTa {r['jev']:+d}: {j['text'][:160]}")
    lines += ["", "### RoBERTa right, Jev wrong (side)", ""]
    for j, r in roberta_only:
        lines.append(f"- human {j['human']:+d}, Jev {j['jev']:+d}, RoBERTa {r['jev']:+d}: {j['text'][:160]}")
    return "\n".join(lines) + "\n"


def report(tallies: dict[str, Tally], usage: dict[str, float]) -> str:
    """The evaluation as Markdown: a summary table, then where Jev and the labeller disagree."""
    lines = [
        "# Phase 2 evaluation: Jev against hand labels",
        "",
        "One labeller, 40 windows (8 per stream) and 114 comments. Agreement with that labeller, not ground truth.",
        "",
        "| question | n | exact | lenient | notes |",
        "|---|---:|---:|---:|---|",
    ]
    mood, sentiment = tallies["mood"], tallies["sentiment"]
    lines += [
        f"| window mood (5 levels) | {len(mood)} | {mood.rate('exact'):.0%} | {mood.rate('within_one'):.0%} within one level "
        f"| same side (neg/neutral/pos) {mood.rate('same_side'):.0%}; mean error {mood.mean('error'):.2f} levels |",
        f"| window main subject (59 options) | {len(tallies['target'])} | {tallies['target'].rate('exact'):.0%} "
        f"| {tallies['target'].rate('top3'):.0%} in Jev's top 3 | |",
        f"| window moment (yes/no) | {len(tallies['moment'])} | {tallies['moment'].rate('exact'):.0%} | | threshold P >= 0.5 |",
        f"| comment subject (59 options) | {len(tallies['subject'])} | {tallies['subject'].rate('exact'):.0%} "
        f"| {tallies['subject'].rate('top3'):.0%} in Jev's top 3 | |",
        f"| comment sentiment (5 levels) | {len(sentiment)} | {sentiment.rate('exact'):.0%} "
        f"| {sentiment.rate('within_one'):.0%} within one level "
        f"| same side {sentiment.rate('same_side'):.0%}; mean error {sentiment.mean('error'):.2f} levels |",
        "",
        f"Jev usage for these windows: {usage['calls']} calls, {usage['input_tokens']:,} input tokens, "
        f"${usage['cost']:.4f}, median latency {usage['median_latency_s']:.2f}s.",
        "",
        "## Where they disagree",
        "",
        "### Window main subject",
        "",
    ]
    for row in tallies["target"].rows:
        if not row["exact"]:
            lines.append(f"- {row['id']} {row['stream']}: human **{row['human']}**, Jev **{row['jev']}**")
    lines += ["", "### Window moment", ""]
    for row in tallies["moment"].rows:
        if not row["exact"]:
            lines.append(f"- {row['id']} {row['stream']} ({row['kind']}): human {'yes' if row['human'] else 'no'}, Jev P(yes) {row['jev']:.2f}")
    lines += ["", "### Window mood, off by two levels or more", ""]
    for row in mood.rows:
        if row["error"] >= 2:
            lines.append(f"- {row['id']} {row['stream']}: human {row['human']:+d}, Jev {row['jev']:+d} (score {row['score']:+.2f})")
    lines += ["", "### Comment subject", ""]
    for row in tallies["subject"].rows:
        if not row["exact"]:
            lines.append(f"- {row['id']} {row['stream']}: human **{row['human']}**, Jev **{row['jev']}**: {row['text'][:160]}")
    lines += ["", "### Comment sentiment, off by two levels or more", ""]
    for row in sentiment.rows:
        if row["error"] >= 2:
            lines.append(f"- {row['id']} {row['stream']}: human {row['human']:+d}, Jev {row['jev']:+d}: {row['text'][:160]}")
    return "\n".join(lines) + "\n"
