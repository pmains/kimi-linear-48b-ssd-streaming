# norm pass 1 (client timeout) - 2026-09-07 ~18:47-18:57 MST

NORM-ABS multi-step tool probe hit the driver's 600s client cap mid-turn
(rc=1, timed_out, doc_status=timeout, liveness=paused). NOT a tool-calling
failure: executionTrace winnerProvider=llama-server, attempts success, no
fallback; llama healthy throughout; no loop-detector event (the agent was
making progress - 7 slot launches in the window, gateway llama fetches 200).
At 256K the agent path is slower per round trip than the 64K baseline
(NORM-ABS at 64K ~220-240s), so the bounded control needs a larger client
budget. Driver patched: norm leg now runs with 1500s client timeout.
Rerun as norm (RUN_SUFFIX=r2).
