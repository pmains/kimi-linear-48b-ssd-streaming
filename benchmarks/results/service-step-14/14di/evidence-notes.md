# 14D(i) evidence notes — Persistent Slot Save/Restore Characterization

Owner-authorized 2026-09-10. Measurement / characterization only.
Driver `tools/service_step14di.py` (+ launcher `tools/service_step14di.sh`),
retained. Design: SERVICE-ROADMAP.md §14D(i).

Environment: production llama-server, `--ctx-size 262144`,
`--slot-save-path runtime/state/slot-cache`, `--parallel 1` (single slot).
No model/sampler/context change; no OpenClaw compaction change; no second
server; sequential requests only.

---

## Status: IN PROGRESS (full run launched 2026-09-10 10:56 MST)

Two prior runs are complete and are themselves evidence:

1. **Smoke run** (`14di-smoke/`, 3K-token state, no restart) — validated the
   whole flow.
2. **Targeted reuse probe** (`14di-smoke/probe-restore-reuse.py` /
   `probe-restore-reuse.json`) — isolated the reuse behaviour.

The full run (48k full protocol + 100k/150k size-latency legs + controlled
restart at 48k) is executing under `14di/`.

---

## Findings so far (high confidence)

### F1. Slot save/restore is mechanically fast and correct

3,011-token state:

| op | result |
|---|---|
| save | `n_saved=3011`, **69,221,668 bytes**, 0.04 s wall (`save_ms` 39.3) |
| erase | slot depth 0 |
| restore | `n_restored=3011`, 0.01–0.04 s wall, slot depth back to 3011 |
| correctness | greedy continuation after restore **byte-identical** to cold re-prefill |

Snapshot header (first 32 B):
`7173676702000000c70b0000ffffffff01000000c30b0000c4050000c4050000`
= magic `0x67677371`, version 2, then counts incl. token count 3011
(`c30b0000`). **No model path, model hash, or n_ctx field** appears in the
header — i.e. the file does not self-identify model/runtime/context.

### F2. Restore does NOT give warm-start prefix reuse on the next request

Measured (probe, 3,011-token state):

| request after restore | cache_n | prompt_n | wall |
|---|---|---|---|
| **direct** `P + suffix` | **0** | 3039 | 56.5 s (≈ cold) |
| exact `P` (re-prefill, then suffix) | 0 | 3011 | 38.4 s |
| `P + suffix` *after* that | **3007** | **6** | **0.82 s** |

i.e. the **first** request after a restore is forced to re-evaluate the
entire prompt; only once a normal prefill has run does reuse work again.
The restore itself is fast (0.04 s) but the following prefill is not, so
**restore + continue currently costs ≈ cold prefill** — no prefill savings.

**Mechanism (source-grounded, `llama.cpp` @ `a895f6826`,
`tools/server/server-context.cpp`):**
- `SLOT_RESTORE` (≈2492) does `slot->prompt.clear();
  slot->prompt.tokens = restored;` and loads KV via
  `llama_state_seq_load_file`, but **does not populate
  `slot->prompt.checkpoints`**.
- On the next request the server computes `n_past = get_common_prefix(...)`
  (3112) and, for hybrid/recurrent memory (Kimi-Linear KDA), then requires a
  **context checkpoint** to reuse it; with an empty checkpoint list it takes
  `do_reset` (≈3276) and logs *"forcing full prompt re-processing due to lack
  of cache data (likely due to SWA or hybrid/recurrent memory)"* →
  `n_past = 0` → full re-prefill.
- After a normal prefill the server has built checkpoints, so subsequent
  requests reuse normally (hence the 3007/6 reuse in the last row above, and
  the earlier 14A/14D/14E warm-suffix results).

This is the architectural answer to the 14D(i) question: for this
hybrid/recurrent model, a completed prefill's *reusable* state is not
entirely contained in the slot-save artifact, so it does not yet survive
as a warm start independent of the OpenClaw lifecycle.

### F3. Invalid snapshots are rejected — one class by hard crash

| input | result |
|---|---|
| nonexistent filename | clean HTTP 400 `Unable to restore slot: No available space in KV cache or invalid slot save file` |
| **truncated snapshot** | **hard abort** of the server (`ggml_abort` in `llama_context::state_seq_load_file`); launchd restarted it (pid 7021 → 90079) |

So incompatible/truncated state is *not* silently restored — but the failure
mode is a process crash, not a returned error. Evidence:
`14di-smoke/crash-truncated-restore.txt` (backtrace + lifecycle log) and
`14di-smoke/results.json`. Corrupt-header / zero-length restore were not
reached in that run (server already down); the full run gates the whole
crash class behind `DO_CRASH_TESTS=0` to protect production.

### F4. External SSD

None mounted (`/Volumes` = `Macintosh HD` only) — the external-SSD path test
is **not available** on this host; documented as a limitation.

---

## Production note (disclosed)

The smoke run's truncated-snapshot test aborted the production llama-server;
launchd KeepAlive restarted it (pid 7021 → 90079) within ~1 min. Health
verified 200 post-restart; n_ctx 262144. Any live OpenClaw session warm
state was lost by that restart (cold re-prefill on next turn). The
crash-inducing tests are disabled for the full run.
