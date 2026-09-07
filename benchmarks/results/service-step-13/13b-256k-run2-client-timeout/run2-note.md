# 13B run 2 (2026-09-07 11:56:50-12:02:20 MST)

Stage 1: PASS at ctx 262144 (same as run 1). Stage 2 launched; client died at 302.8 s wall with 'read error: cannot read from timed out object'.

**Failure mode = client bug, not runtime failure:**
- Python http.client's SocketIO permanently raises 'cannot read from timed out object' after the FIRST socket timeout (known CPython behavior); my 30 s read-timeout loop hit it during the long silent prefill.
- Server evidence: healthy throughout; RSS peaked 13,038,848 KB (12.44 GiB) at 12:00:15, watchdog never fired (aborted=0), no OOM/assert lines, GATE4 pass; on client disconnect the server stopped prompt processing at 32,768 tokens (slot release log), i.e. it was mid-prefill.
- Rate note: first ~32,768 prompt tokens processed in ~300 s (~105+ tok/s early-chunk rate) before abort — consistent with depth-degrading prefill; full 146K prefill would take longer than 13A's 95K (34 tok/s avg).

Fix applied in tools/service_step13b.sh: blocking body read (settimeout(None)) + guardian thread that closes the connection on ABORT flag or 3 h deadline; probe-b prefill/eval greps now require >=100000 prompt tokens / >=5 decode tokens so probe-a's 14-token/2-token lines cannot masquerade as probe-b results.

Production restored & verified after this run: llama 200 / n_ctx 65536, gw 200, 11B sha 8baf474684, FK 0 (restore-verify.json).

Superseded by run 3 (13b-256k/).
