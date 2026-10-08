# Fan Pulse Live: talking points

Notes for presenting the dashboard as of the evening of 8 Oct 2026: the full game on Jev's readings, with the free baseline selectable for comparison.

## How it works right now

1. **Game data** comes from MLB's free Stats API: every pitch, play and win probability, with timestamps.
2. **Fan data** is 52,872 Reddit comments from three communities: r/Dodgers, r/Torontobluejays, and r/baseball split by team flair into Dodgers fans, Blue Jays fans and neutrals.
3. **Every 5 seconds** of game time, each community's last 20 seconds of comments get a reading: mood, who they're talking about, and whether they're reacting to something.
4. **The readings come from Jev**, which is told the score, the recent plays and whose fans are talking. A free stand-in (RoBERTa sentiment plus name matching) is in the "Readings from" menu for comparison. The full game cost about $1.27 of Jev calls.
5. **Everything is precomputed**, so the dashboard looks things up by clock time. That is why scrubbing and 60× speed respond instantly.

## How moments are detected

- Count each community's comments in every 5-second slice.
- Compare each slice with that community's own previous 10 minutes. A slice far above normal is a spike.
- A **moment** is when at least two communities spike at the same time. Spikes within a minute of each other merge into one.
- Each moment is linked to the play that ended in the previous 2 minutes with the biggest win-probability swing, not simply the latest play. Fans react late: we measured the reaction peaking 45–75 seconds after a play ends.

With Jev, a spike only counts if Jev also says the crowd is reacting to one specific thing (probability 0.85 or higher), so a burst of unrelated chatter does not trigger a moment.

## What to show, in order

1. **Press "Moment ▶" to Varsho's tying homer** (9:34 PM ET). All five communities react at once; win probability jumps 20 points.
2. **Jump to the grand slam** (10:33 PM ET): 96 comments in 5 seconds. Flip the tabs between r/Torontobluejays and r/Dodgers to see the same instant from both sides.
3. **"Who's being talked about":** click a player for the fans-vs-stats card. Andy Pages is a good one, at about −0.8 from Dodgers fans.
4. **Switch "Readings from" to the baseline and back** at the grand slam. On the baseline the mood lines barely move; on Jev, Blue Jays fans climb to about +0.9 and Dodgers fans fall to about −0.9 and stay there.
5. **The saved Jev examples** in `reports/phase2_examples/`: Jev read r/Dodgers at −0.99 after the grand slam and r/Torontobluejays at +0.99 after Varsho's homer.
6. **The fanbase report** (`reports/fanbase_report.html`): Dodgers fans named Blake Snell 1,085 times, at sentiment −0.19; his win probability added was −17.3%.

## What Jev changes

| | Baseline | Jev |
|---|---|---|
| Mood | Tone of the words only. It scores "LET'S FUCKING GO" as negative, so the grand slam barely moves the lines. | Knows the score, the play and whose fans are talking. |
| Who it's about | Only when a name is typed: 22% of comments. | Every comment, and it resolves "he" or "this guy" from the reply and the recent plays. |
| Emotion, blame | Not available. | Joy, anger, sarcasm, resignation; whether criticism targets a manager's decision. |
| Moments | Volume only. | Volume plus "are they reacting to one thing". |
| One-line summaries | None. | Written per fanbase for each moment, by a small text model. |

Measured cost was about $1.27 for this game, at about half a second per call, after capping what each call carries (at most 30 comments shown and 10 tagged per update).

**One point to be straight about.** On the 114 hand-labelled comments, untuned Jev did not beat RoBERTa at plain comment sentiment: both were within one level of the labeller about 84–85% of the time, and RoBERTa matched the negative/neutral/positive side more often (61% against 51%). Jev's measured edge is elsewhere: it picked the labeller's subject for 59% of comments (89% in its top three) and agreed on "is this a moment" for 82% of windows, neither of which RoBERTa can do. Tuning the question wording is the next step. Full tables are in `reports/phase2_eval.md`.

## What is needed from Anand

- **OpenRouter credit** for anything further: tuning the questions and a live game. This game is paid for.
- **A decision on sharing.** The page shows real Reddit comment text. An internal demo is fine; anything public or client-facing needs sign-off on Reddit and MLB data.
- **Which 2026 postseason game** to run live for Phase 8, the real-time proof.
- **YouTube:** nothing to approve. The page embeds MLB's official video with YouTube's own player, never downloads it and draws nothing over it, which is what YouTube's terms require. A client product would still need MLB's view on licensing.

## Known gaps

- In a burst Jev tags a sample of the new comments (up to 10 per community per 5 seconds), so about 11% of comments carry no tags.
- The video is not synced to the dashboard clock until `config/anchors.csv` is filled in.
- The moment summaries are written by a small text model from a sample of comments and can get a detail wrong.
- Live mode (Phase 8) is not built yet.
