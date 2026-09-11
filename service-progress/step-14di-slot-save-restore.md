# Step 14D(i) Report — Persistent Slot Save/Restore Characterization

## Status

**PASS (characterization complete), with a decisive negative result on
warm-start reuse.** llama-server slot save/restore is mechanically fast,
correct, and now verified to survive a controlled server restart — but
restoring a snapshot does **not** give the next request a warm start for
this hybrid/recurrent model, so restore+continue currently costs ≈ cold
prefill. Measurement / characterization only: no model, sampler, context,
or OpenClaw-compaction change; no second server; sequential single-slot
requests only.

Owner-authorized 2026-09-11. Design: `SERVICE-ROADMAP.md` §14D(i).
Driver: `tools/service_step14di.py` (+ `tools/service_step14di.sh`).
Evidence: `benchmarks/results/service-step-14/14di/` (+ `14di-smoke/`).

## Objective

Determine whether llama-server *inference state* can persist
independently of OpenClaw's conversation lifecycle, using llama-server's
native slot save/load and the production `--slot-save-path`.

Architectural distinction preserved:

> the harness constructs the token sequence; llama-server owns the
> evaluated inference state.

## Changes

- `tools/service_step14di.py` — driver: deterministic `/tokenize`-sized
  prefix; `POST /slots/0?action=save|restore|erase`; `/slots` for depth;
  temperature-0 completions for reuse/correctness; controlled restart via
  `launchctl kickstart -k` with explicit wait-for-DOWN then wait-for-UP;
  crash-class invalid-state tests gated behind `DO_CRASH_TESTS` (default
  off); `STAGES` selector; merge-on-load of `results.json`.
- `tools/service_step14di.sh` — retained launcher.
- `benchmarks/results/service-step-14/14di/` — results.json, driver.log,
  run1/run2 console logs, corpus, evidence notes.
- `benchmarks/results/service-step-14/14di-smoke/` — 3K smoke run, the
  targeted reuse probe, and the truncated-snapshot crash evidence.
- `service-progress/step-14di-slot-save-restore.md` — this report.

## Results

### 1. Save / restore mechanics and correctness

| state | op | result |
|---|---|---|
| 3,011 tok | save | `n_saved=3011`, **69,221,668 B**, 0.04 s (`save_ms` 39.3) |
| 3,011 tok | restore | `n_restored=3011`, 0.01–0.04 s, slot depth restored |
| 3,011 tok | correctness | greedy continuation after restore **byte-identical** to cold re-prefill |
| 47,945 tok | save / restore | 432,288,388 B / 0.159 s; `n_restored=47945` in 0.112 s |
| 99,892 tok | save / restore | 852,020,148 B / 0.361 s; `n_restored=99892` in 0.155 s |
| 149,854 tok | save / restore | 1,255,713,108 B / 0.548 s; `n_restored=149854` in 0.250 s |

### 2. Snapshot size model

**size ≈ 45 MB + 8,080 B/token** (fits all four points):

| tokens | bytes | check |
|---|---|---|
| 3,011 | 69,221,668 | 45e6 + 3,011·8,080 = 69.3e6 ✓ |
| 47,945 | 432,288,388 | 45e6 + 47,945·8,080 = 432.3e6 ✓ |
| 99,892 | 852,020,148 | 45e6 + 99,892·8,080 = 852.1e6 ✓ |
| 149,854 | 1,255,713,108 | 45e6 + 149,854·8,080 = 1,255.7e6 ✓ |

Extrapolated **256K-token state ≈ 2.16 GB**.

### 3. Save/restore latency and throughput

0.04–0.55 s save, 0.01–0.25 s restore; **≈ 2.3–2.7 GB/s** (page-cache
dominated). Sub-second even at 1.26 GB.

### 4. Prefill rate collapses with depth

| depth | prefill rate |
|---|---|
| 3,011 | 53.9 tok/s |
| 47,945 | 39.9 tok/s |
| 99,892 | 19.4 tok/s |
| 149,854 | 14.6 tok/s |
| 47,973 (post-restart) | 36.5 tok/s |

A 150K re-prefill is ≈57 min; a 256K full re-prefill is extrapolated to
≈2–3 h.

### 5. Warm-start reuse: NEGATIVE (the central finding)

After a restore, the **first** request re-evaluates the entire prompt at
every depth tested:

| request after restore | cache_n | prompt_n | wall |
|---|---|---|---|
| direct `P + suffix` (3K) | **0** | 3,039 | 56.5 s (≈ cold) |
| after one normal prefill | 3,007 | 6 | 0.82 s |
| 48K `P + suffix` after restore | **0** | 47,973 | 1,315.6 s |

**Mechanism** (`llama.cpp` @ `a895f6826`, `tools/server/server-context.cpp`):

- `SLOT_RESTORE` (≈2492) restores KV via `llama_state_seq_load_file` and
  sets `slot->prompt.tokens`, but **not `slot->prompt.checkpoints`**.
- On the next request the server computes
  `n_past = slot.prompt.tokens.get_common_prefix(input_tokens)` (3112);
  for **hybrid/recurrent memory** (Kimi-Linear KDA) reuse then requires a
  context checkpoint. With an empty checkpoint list it takes `do_reset`
  (≈3276) — *"forcing full prompt re-processing due to lack of cache data
  (likely due to SWA or hybrid/recurrent memory)"* — and sets `n_past=0`.
- After a normal prefill the server has built checkpoints, so subsequent
  requests reuse normally (hence 3,007/6 above).

**Consequence:** restore + continue costs ≈ cold prefill. The state that
makes a completed prefill cheaply resumable is not entirely inside the
slot-save artifact.

### 6. Controlled restart: PASS

- `launchctl kickstart -k gui/501/com.openclaw.kimi-llama-server`
  → **down=True, up=True in 15.4 s**, pid 95558 → 39781, n_ctx 262144.
- Fresh slot was empty after restart.
- Restore of the retained 48K snapshot → `n_restored=47945` in 0.163 s.
- Continuation after restart+restore produced ` SUFFIX-OK-7412`, matching
  the pre-restart expected value → **correctness across a restart PASS**.
- The continuation still required a full 47,973-token re-prefill
  (1,315.6 s), consistent with §5.

### 7. Invalid / incompatible state

| input | result |
|---|---|
| nonexistent filename | **HTTP 400** `Unable to restore slot: No available space in KV cache or invalid slot save file` |
| invalid filename (path separator) | **HTTP 400** `Invalid filename` (fs_validate_filename works) |
| truncated snapshot | **HARD ABORT** (`ggml_abort` in `llama_context::state_seq_load_file`); launchd restarted the server (pid 7021 → 90079 on 2026-09-10) |

The snapshot header carries magic, version and counts (token count
visible) but **no model path, model hash or n_ctx** — i.e. no identity
check; validation is structural only (`restored.validate(ctx_tgt)` plus
size vs `n_ctx`).

### 8. External SSD

Not available (`/Volumes` = `Macintosh HD` only) — sub-test not performed.

## Problems

- **Restore does not warm-start** (§5) — the negative result that matters.
- **Truncated/corrupt snapshot restore hard-aborts the server** (§7) —
  a robustness defect; it is a crash, *not* a silent wrong-state restore.
- **No model/context identity in the artifact** (§7) — incompatible
  state is not rejected on identity.
- **Driver defects found and fixed** (2026-09-11): the restart stage
  checked health immediately after `kickstart -k` and read `200` from the
  *dying* process (never observed the restart); the restart stage's
  results key collided with `48k` and overwrote that record (preserved as
  `restart-48k-BROKEN-20260910`, real numbers in `run1-console.log`);
  `results.json` is now merge-on-load.
- `/slots` returned null at two points right around restore (endpoint
  contention while the slot was busy) — cosmetic; `n_restored` is the
  authoritative figure.

## Decisions

- Crash-class invalid-state tests are **gated off by default**
  (`DO_CRASH_TESTS=0`) so a characterization run cannot take production
  down; the smoke run's evidence is retained.
- The 100k/150k legs were **size/latency only** (no forward pass) to bound
  wall time; the reuse mechanism is depth-independent and was proven with
  a forward pass at 3K and 48K.
- No session-tiering / orchestration implementation was started.
- No OpenClaw compaction change; no model/sampler/context change.

## Next Phase

For inference state to persist as a *warm start* across the server
lifecycle on this model, the missing piece is **persisting / rebuilding
`slot.prompt.checkpoints`** (or a checkpoint-carrying state artifact).
That is a llama.cpp-side design change and is out of scope for 14D(i);
it is the prerequisite for any SSD/RAM session-tiering work.

## Reproduction

    # full characterization
    DO_RESTART=1 bash tools/service_step14di.sh
    # restart + invalid only (snapshots retained)
    STAGES=restart,invalid DO_RESTART=1 python3 tools/service_step14di.py
    # cheap flow validation
    SMOKE=1 DO_RESTART=0 python3 tools/service_step14di.py

Env: production llama-server, `--ctx-size 262144`, `--parallel 1`,
`--slot-save-path runtime/state/slot-cache`. Live contract at run time:
11B(i) redaction sha b54b13f1d7, llama ctx 262144. NOTE: the restart leg
intentionally restarts the production server (live sessions cold-prefill
on their next turn).
