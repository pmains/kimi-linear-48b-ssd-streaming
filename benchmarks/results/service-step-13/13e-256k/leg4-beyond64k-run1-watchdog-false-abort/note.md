# leg4 run 1 — watchdog false abort (driver bug, 2026-09-07 16:00:53 MST)

Stage-2 launch 1 died instantly at leg-4 start (rc=143, runner SIGTERM'd by the
watchdog before prefill began). NOT a runtime failure: llama-server stayed
healthy (n_ctx 262144, RSS 8.73 GiB idle) and no prefill ever started.

Root cause (driver bug, fixed): the watchdog's host-free% python computed
vm_stat free+purgeable pages with a hardcoded 4096 B page size, but this Mac
reports 16384 B pages and macOS keeps "free" pages near zero (available memory
lives in inactive/speculative). Result: free_pct=0.0 → the "host free <= 3%"
branch fired → touched ABORT + pkill -f step13e-leg4-beyond64k killed the
runner. watchdog.tsv (preserved here) shows the single sample: rss_gib=8.73
free_pct=0.0, then "ABORT: host free <= 3%".

Fix: both watchdog blocks now read host free% from `memory_pressure -Q`
(the 13B-proven metric — 13B used the same and never false-fired). Driver
relaunched as leg4-beyond64k run 2.
