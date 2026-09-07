# 13B run 3 (2026-09-07 12:05:32-12:35:32 MST)

Stage 1: PASS at ctx 262144 (same healthy numbers: KV 2016 MiB, RSS 10.25 GiB, host free 26%). Stage 2 launched; driver SIGTERM'd by the exec harness at exactly 30 min (12:35:32) — launch omitted an explicit timeoutSeconds (default ~1800 s).

**Not a runtime failure.** Server healthy throughout: no OOM/assert, watchdog never fired (no ABORT, no WATCHDOG_KILLED), RSS steady ~12.6 GiB during prefill, peak 13,166,752 KB (12.55 GiB) — memory envelope safe. Driver EXIT trap fired on SIGTERM and restored the launchd 64K job (bootstrap 12:35:34); restore-verify step was cut short, completed manually: llama 200 / n_ctx 65536, gw 200, 11B sha 8baf474684, FK 0, no 256K leftover.

RSS trajectory during the 30-min prefill (every 200th sample): 12:05:47 10.29 GiB -> 12:09:12 12.26 GiB -> 12:16:02 12.07 GiB -> 12:22:51 11.98 GiB -> 12:33:05 12.11 GiB. Prefill working set adds ~2.3 GiB over the 10.25 GiB idle baseline.

Superseded by run 4 (13b-256k/) launched with an explicit 4 h timeout.
