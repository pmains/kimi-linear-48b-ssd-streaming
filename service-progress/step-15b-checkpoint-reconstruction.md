# Step 15B — Implement the Minimum Persistent-Resume Mechanism

## Status

**STOPPED again at the 15B verification gate — but this time the 15A mechanism
is RUNTIME-CONFIRMED.**

The owner authorised **Option B**: one short controlled restart of the
production llama-server under an otherwise-identical *instrumented* build
(commit `a895f6826`), diagnostic only, then immediate restoration of the
qualified production binary.

- **No production patch applied.** The checkpoint-reconstruction change is
  still NOT implemented.
- The qualified production binary is restored and verified.
- The instrumentation was reverted; the llama.cpp tree is clean.
- 15C not begun.

## Diagnostic outcome (decisive)

Instrumented values captured for the first continuation request after restoring
the retained ~48K snapshot (`benchmarks/results/service-step-15/15b/diagnostic-log-window.txt`):

```
hybrid seq_pos_min: seq=0 attn=0 recr=47944 -> 47944
reuse-branch: slot=0 n_past=47944 pos_next=47944 n_swa=0
              pos_min=47944 pos_min_thold=47944 has_new_tokens=1
              ckpts=0 prompt_tokens=47945 task_tokens=47973
              checkpoint_search_will_run=1
checkpoint search executed: found=0 ckpts=0 do_reset=1
do_reset FIRES: no usable checkpoint; forcing full reprocess, n_past -> 0
reuse-result: n_past=0 task_tokens=47973 prompt_tokens=47945
```

Read against the required chain:

| quantity | observed |
|---|---|
| restored depth `D` (prompt tokens) | 47945 |
| `n_past` (LCP) | **47944** |
| `pos_next` | 47944 |
| `n_swa` | 0 |
| attention-side `seq_pos_min` | **0** |
| recurrent-side `seq_pos_min` | **47944** |
| final `llama_memory_seq_pos_min` | **47944** |
| `pos_min_thold` | **47944** |
| `pos_min >= pos_min_thold` | **TRUE** |
| checkpoint count | **0** |
| checkpoint search executes | **YES** |
| `do_reset` executes | **YES** |
| resulting `n_past` | **0** → full re-prefill |

**Interpretation (owner's criterion 1):** `pos_min >= pos_min_thold`, the
checkpoint search executes, the checkpoint list is empty, and that causes
`do_reset`. **The 15A proposed reconstruction mechanism is therefore
runtime-confirmed.**

## Why the 15A arithmetic *appeared* wrong (and wasn't)

The static reading assumed `n_past = D = 47945`, giving
`pos_min_thold = 47945 > pos_min = 47944` ⇒ branch skipped. In reality the
LCP was **one token short**: `n_past = 47944` (the 47945-token restored prompt
matched 47944 of the 47973-token request), so `pos_next = pos_min_thold =
47944`. The **recurrent side reports the last processed position**
(`recr = 47944`, i.e. `D-1`), and `max(attn=0, recr=47944) = 47944`, so
`pos_min == pos_min_thold` exactly — the branch fires.

So the boundary was a one-token coincidence in the threshold, not a different
code path. The empty-`checkpoints` invariant *is* the trigger.

**Corroboration from live traffic in the same window.** The captured window also
contains a normal live request (`hybrid seq_pos_min: attn=0 recr=2047 -> 2047`).
It shows the identical branch geometry — the recurrent side reports the last
processed position, so `pos_min` equals the request's `pos_next`. Live requests
do **not** fall into `do_reset` because their prefill *created* checkpoints, so
the search succeeds. After `SLOT_RESTORE` the list is empty, so the same search
fails. This is direct runtime support for the fix: supply one endpoint
checkpoint and the search succeeds.

## What this means for the fix (not implemented)

Reconstructing one endpoint checkpoint after `SLOT_RESTORE` makes the search
succeed instead of falling through to `do_reset`; `n_past` should then be set
from the checkpoint (`≈ 47944`) and only the suffix evaluated. The planned
change (from 15A) is unchanged: `n_tokens = restored depth`, `pos_min = 0`,
`pos_max = restored depth - 1`, PARTIAL_ONLY capture via the existing
`update_tgt` / `update_dft`, exactly one endpoint checkpoint pushed onto
`slot.prompt.checkpoints`.

## Protocol executed (Option B)

1. Instrumented an otherwise-identical build from `a895f6826`
   (`src/llama-memory-hybrid.cpp`: `seq_pos_min`/`seq_pos_max` split into
   attn/recr + log; `tools/server/server-context.cpp`: reuse-branch,
   search-result, `do_reset`, and final `n_past` logs). Build `BUILD_RC=0`.
2. Archived the qualified bundle:
   `runtime/live/bin.qualified-880f1637-20260911T120046` (+ `.SHA256`,
   `.COMMIT`) — `llama-server` sha `880f1637…`, `libllama-server-impl.dylib`
   sha `64dc2c91…`, `libllama.0.dylib` sha `312255fa…`.
3. Idle-gated (slot idle).
4. Froze the instrumented build into `runtime/live` and restarted via
   `launchctl kickstart -k`.
5. Verified new pid 52348, `n_ctx=262144`.
6. Restored the retained 48K snapshot: `n_restored=47945` in 0.154 s.
7. Submitted the same small append-only continuation as 14D(i).
8. Captured the diagnostic values (above).
9. No reconstructed checkpoint tested; 15C not started.
10. Restored the qualified bundle and restarted; the instrumented child
    survived the first `kickstart -k` (wrapper duplicate guard), so it was
    explicitly stopped and the job restarted → **pid 52529**.
11. Final state verified (below).

## Final state verification

| check | result |
|---|---|
| qualified `llama-server` sha restored | `880f1637…` ✓ |
| running dylib sha | `64dc2c91…` (qualified) ✓ |
| instrumentation strings in shipped bundle | 0 ✓ |
| `[15B-DIAG]` lines after final restart | 0 ✓ |
| llama pid | 52529 (health 200) ✓ |
| `n_ctx` | 262144 ✓ |
| gateway health | 200 ✓ |
| kimi FK violations | 0 ✓ |
| poliscopic FK violations | 0 ✓ |
| Step 11B(i) redaction sha | `b54b13f1d79cb98a` ✓ |
| plist (`KIMI_CTX`, `KIMI_BIN`) | unchanged ✓ |
| llama.cpp worktree | clean (instrumentation reverted) ✓ |

Note: `build-metal` currently contains the **instrumented** objects from this
diagnostic (dev tree only; it cannot affect what agents are served once
`runtime/live` is frozen). Rebuild with
`cmake --build llama.cpp/build-metal -j` to return it to a clean build.

## Problems

- The instrumented child process survived the first `launchctl kickstart -k`
  (the launchd wrapper refuses a duplicate start while a matching process
  exists). Resolved by explicitly stopping the child before restarting the job.
  Worth knowing for any future controlled restart.
- The diagnostic client was cancelled immediately after the values were captured
  (to restore production promptly, per protocol step 10), so
  `diagnostic-request.json` was not written. The authoritative record is the
  server-side log window `diagnostic-log-window.txt` plus the restore result
  (`n_restored=47945`, 0.154 s) printed by the driver.

## Decisions

- Ran the diagnostic as authorised (Option B); production binary restored
  immediately after the observation.
- Did not apply the checkpoint-reconstruction patch.
- Did not test a reconstructed checkpoint; 15C not begun.

## Next Phase

The 15A determination is now runtime-confirmed. Awaiting authorisation to
implement the bounded reconstruction in the `SLOT_RESTORE` path (15B), then
15C live verification at 48K.

## Reproduction

- Instrumentation: `src/llama-memory-hybrid.cpp` (seq_pos_min/max),
  `tools/server/server-context.cpp` (reuse branch); reverted after the run.
- Diagnostic driver: `benchmarks/results/service-step-15/15b/15b-diagnostic.py`.
- Captured values: `benchmarks/results/service-step-15/15b/diagnostic-log-window.txt`,
  `diagnostic-request.json`.
- Pre-implementation boundary analysis:
  `benchmarks/results/service-step-15/15b/verification-boundary.md`.
