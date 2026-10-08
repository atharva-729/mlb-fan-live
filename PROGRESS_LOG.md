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

## 2026-10-08 (evening)

| Time | What |
|---|---|
| ~20:00 | New OpenRouter key in place. Budget set at $2 for the full Jev run. |
| 20:27 | Jev calls made about half the size: at most 30 comments shown and 10 tagged per update, subject list cut from 59 options to about 19, main subject derived from tags. Estimate $1.27 from 30 calibration calls; accuracy against the labels unchanged. |
| 20:29 | Full Jev run started. Stopped at 20:33 on a provider "system overloaded" error; client changed to retry it; resumed 20:34. |
| 20:43 | Full Jev run complete: 12,175 readings, 40,354 comments tagged, 14 minutes of run time. Jev spend: $1.15 on the key in total by OpenRouter's count (run, test calls and summaries); the local log adds up to $1.27. |
| 20:46 | Dashboard rebuilt on Jev: 12 moments, each with one-line fanbase summaries. Jev is now the default source. |

## 2026-10-09

| Time | What |
|---|---|
| (8 Oct, ~21:10) | Dashboard published at https://atharva-729.github.io/mlb-fan-live/ through GitHub Pages. |
| (8 Oct, ~21:25) | Video sync helper page added (`anchors.html`). |
| 00:27 | Atharva marked the first pitch of all 17 half-innings. |
| ~00:50 | Anchors checked against the game clock: 15 consistent; the two 6th-inning marks were not, and mid-inning pitching changes turned out to be cut from the video too. Sync points extended to pitching changes; four points estimated. Video now synced and published. |

## How long things took

| Piece | Time |
|---|---|
| Phase 0 (scaffold, port) | about 30 min |
| Phase 1 code | about 30 min |
| Getting the Reddit data | about 5 hours end to end, of which about 3.5 hours was finding a method that worked and 83 minutes was the pull itself |
| Jev client, questions, state builder | about 40 min |
| Labelling page | about 20 min to build, 1 hour to label |
| Evaluation against labels | about 5 min |
| Tick engine | about 10 min to write; the full run took 14 min and about $1.20 after the call format was slimmed |
| RoBERTa baseline | 31.5 min of CPU time |
| Moments, export, dashboard, fanbase report | about 1 hour 40 min |

## Still to do

- Check the four estimated video anchors (top of the 6th, and the three mid-inning pitching changes) on `anchors.html`.
- Tune Jev's question wording using the disagreements listed in `reports/phase2_eval.md`.
- Phase 8 (live mode on a 2026 postseason game).
