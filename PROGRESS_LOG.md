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
