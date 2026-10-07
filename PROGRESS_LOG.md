# Progress log

What got done and when. Times are local (IST). Newest entries at the bottom.

## 2026-10-07 (evening)

| Time | What |
|---|---|
| ~19:00 | Phase 0 done: repo scaffolded, ingest ported from `mlb-fan-pulse`, `config/game.yaml` written, 37 tests passing. |
| ~19:30 | Phase 1 code written (stream and phase tagging, volume chart, reaction lag). MLB data pulled: 78 plays, 327 pitches. |
| 19:36–23:05 | Reddit pull blocked. Arctic Shift timed out on whole-thread searches of the three big game threads; PullPush refused bulk pulls after ~100 requests; the Hugging Face mirror stops at 2022-08; the per-subreddit torrent no longer exists. |
| ~20:10 | Jev half of Phase 2 done while waiting: client, question set, game-state timeline, state builder. First live Jev calls. |
| 23:06 | Pull fixed: read each game thread out of its subreddit in 5-minute windows, with short random retry waits. |

## 2026-10-08 (night)

| Time | What |
|---|---|
| 00:29 | Reddit pull complete: 54,359 comments fetched, 52,872 kept after cleaning. About 83 minutes once the working method was found. |
| 00:35 | Phase 1 checkpoint: counts per thread, volume chart, reaction lag measured (peak 45–75s after a play); `play_lookback_s` set to 120. |
| 00:40 | Phase 2: three real example states with Jev's answers saved in `reports/phase2_examples/`. |
| 00:55 | Labelling page built: 40 windows, 114 comments. |
| 01:59 | Hand labelling finished (about 1 hour of Atharva's time). |
| 02:03 | Phase 2 evaluation of Jev against the labels: `reports/phase2_eval.md`. |
| 02:05 | Phase 3 tick engine written; full run started. |
| 02:06 | Full Jev run stopped at about 10%: OpenRouter account has no purchased credits (free allowance ran out at $0.24). 1,162 Jev responses cached. **Needs credit to finish.** |
| 02:48 | Decision: build tonight's dashboard on the free local baseline (RoBERTa sentiment, name matching, volume spikes), run locally; swap in Jev when credit arrives. |
| 02:49 | RoBERTa scoring of all 52,872 comments started on CPU (no GPU). |
| 02:52 | Jev tick tables rebuilt from cached answers only: Jev has read about 11% of the game (pregame and most of the 1st inning). |
| 03:00 | Written and tested: baseline readings, moment detection (Phase 4), summary writer, dashboard data export, and the dashboard itself (`web/`). 107 Python tests and the dashboard helper tests pass. |
| 03:21 | RoBERTa scoring finished: 52,872 comments in 31.5 minutes on CPU. |
| 03:23 | Dashboard data built: 12 moments from the baseline, 3 from Jev's partial run. One-line summaries skipped (same credit problem). Dashboard served at http://localhost:8000. |
| 03:25 | Dashboard checked in a headless browser (light, dark, phone width, both sources). Fixed: chart library now bundled locally, name matching no longer reads "Call me a doomer" as Alex Call, mood lines smoothed. |
| 03:27 | Phase 5 fanbase report written: `reports/fanbase_report.html`. Jev vs RoBERTa comparison added to `reports/phase2_eval.md`. |
| 03:30 | README updated with status and run instructions. Everything committed and pushed. |

## How long things took

| Piece | Time |
|---|---|
| Phase 0 (scaffold, port) | about 30 min |
| Phase 1 code | about 30 min |
| Getting the Reddit data | about 5 hours end to end, of which about 3.5 hours was finding a method that worked and 83 minutes was the pull itself |
| Jev client, questions, state builder | about 40 min |
| Labelling page | about 20 min to build, 1 hour to label |
| Evaluation against labels | about 5 min |
| Tick engine | about 10 min to write; the full run is estimated at 20 min and about $3, not yet done |
| RoBERTa baseline | 31.5 min of CPU time |
| Moments, export, dashboard, fanbase report | about 1 hour 40 min |

## Still to do

- **Add OpenRouter credit, then run `ticks`, `build`, `evaluate`.** This is the only thing blocking the real Jev dashboard.
- Fill in `config/anchors.csv` to sync the video.
- Tune Jev's question wording using the disagreements listed in `reports/phase2_eval.md`.
- Phase 8 (live mode on a 2026 postseason game).
