# Fan Pulse Live: talking points

Notes for presenting the dashboard as it stood on 8 Oct 2026: the full game on the free baseline, with Jev covering the first 11%.

## How it works right now

1. **Game data** comes from MLB's free Stats API: every pitch, play and win probability, with timestamps.
2. **Fan data** is 52,872 Reddit comments from three communities: r/Dodgers, r/Torontobluejays, and r/baseball split by team flair into Dodgers fans, Blue Jays fans and neutrals.
3. **Every 5 seconds** of game time, each community's last 20 seconds of comments get a reading: mood, who they're talking about, and whether they're reacting to something.
4. **The readings shown are a free stand-in, not Jev:** RoBERTa (a small sentiment model run on a laptop CPU) for tone, and name matching for who a comment is about.
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
4. **Switch "Readings from" to Jev** and scrub to the 1st inning (about 8:25 PM ET). Every comment is tagged with a subject, including "Dodgers team", managers and umpires, not only named players.
5. **The saved Jev examples** in `reports/phase2_examples/`: Jev read r/Dodgers at −0.99 after the grand slam and r/Torontobluejays at +0.99 after Varsho's homer.
6. **The fanbase report** (`reports/fanbase_report.html`): Dodgers fans named Blake Snell 1,085 times, at sentiment −0.19; his win probability added was −17.3%.

## What Jev changes

| | Baseline (now) | Jev |
|---|---|---|
| Mood | Tone of the words only. It scores "LET'S FUCKING GO" as negative, so the grand slam barely moves the lines. | Knows the score, the play and whose fans are talking. |
| Who it's about | Only when a name is typed: 22% of comments. | Every comment, and it resolves "he" or "this guy" from the reply and the recent plays. |
| Emotion, blame | Not available. | Joy, anger, sarcasm, resignation; whether criticism targets a manager's decision. |
| Moments | Volume only. | Volume plus "are they reacting to one thing". |
| One-line summaries | None. | Written per fanbase for each moment, by a small text model. |

Cost is estimated at about $3 per game, at about half a second per call.

**One point to be straight about.** On the 114 hand-labelled comments, untuned Jev did not beat RoBERTa at plain comment sentiment: both were within one level of the labeller about 84–85% of the time, and RoBERTa matched the negative/neutral/positive side more often (61% against 51%). Jev's measured edge is elsewhere: it picked the labeller's subject for 59% of comments (89% in its top three) and agreed on "is this a moment" for 82% of windows, neither of which RoBERTa can do. Tuning the question wording is the next step. Full tables are in `reports/phase2_eval.md`.

## What is needed from Anand

- **OpenRouter credit.** About $5 finishes this game. $20–25 would also cover tuning the questions and running a live game.
- **A decision on sharing.** The page shows real Reddit comment text. An internal demo is fine; anything public or client-facing needs sign-off on Reddit and MLB data.
- **Which 2026 postseason game** to run live for Phase 8, the real-time proof.
- **YouTube:** nothing to approve. The page embeds MLB's official video with YouTube's own player, never downloads it and draws nothing over it, which is what YouTube's terms require. A client product would still need MLB's view on licensing.

## Known gaps

- Jev has read 11% of the game (pregame and most of the 1st inning). The run stopped when the OpenRouter account's free allowance ran out.
- The video is not synced to the dashboard clock until `config/anchors.csv` is filled in.
- Moment cards show mood numbers, not written summaries.
- Live mode (Phase 8) is not built yet.
