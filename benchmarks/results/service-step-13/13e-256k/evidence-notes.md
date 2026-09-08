# Step 13E evidence notes (2026-09-07)

## Watchdog free_pct column caveat
leg4/leg5 watchdog.tsv free_pct values are 0.0 throughout: the sampler's
original vm_stat free-page computation used a 4096-byte page size on this
16KB-page Mac and only counted free pages, so it always printed ~0. The
abort DECISION never used that value after the run-1 false abort: both
watchdog blocks recomputed host free% via `memory_pressure -Q`
(13B-proven) before the <=3% check, and the RSS >=18 GiB check is
independent. Run 2 (the passing runs, legs 4+5) therefore ran with a
correct abort guard. Driver fixed post-run so future watchdog.tsv logs
record the memory_pressure value (see tools/service_step13e.sh).

## Run 1 false abort (preserved)
leg4-beyond64k-run1-watchdog-false-abort/ holds the full evidence of the
driver-bug abort: watchdog.tsv single sample rss_gib=8.73 free_pct=0.0,
ABORT file, note.md. Not a runtime failure; llama-server never restarted
(pid 7021 constant through the whole stage, n_ctx 262144).

## Session label note
Runner sessions were created as agent:kimi:step13e-<leg>-r1 with a fresh
session per leg (headless `openclaw agent`), per service_step10a_turn.py
conventions. Leg 3 ran as agent poliscopic (real production agent).
