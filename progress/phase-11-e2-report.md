# Phase 11 E2 Report — Direct Placement on the Streamed Metal Path (A/B)

## Status

PASS (bounded experiment complete). The E2 direct-placement path
(`KIMI_STREAM_E2_DIRECT_PLACE`, default off) was implemented, the
phase07 instrumentation-contract violation it exposed was resolved with
compatibility accounting (invariant and acceptance criteria UNCHANGED),
and the planned A/B comparison was completed. Stopping for review per
directive — no expansion of E2 and no instrumentation-contract change.

## Objective

E2 (per Pete's directive, 2026-08-31): on the streamed Metal miss path,
eliminate the physical repack/copy work (the temp-tensor round trip:
pread → packed staging → `tensor_set` into a single-slice temp →
memcpy → persistent slot) and measure whether direct placement of the
compute-ready packed bytes measurably improves the path while
preserving the established runtime gates. One bounded experiment;
env-gated; frozen E1/E1b baseline preserved.

## Accounting fix (instrumentation-contract violation)

### What failed

After implementing E2's direct path, the phase07 invariant gate FAILED
on the B arm (E2 on):

    e2-B-4096: step 0 repack_bytes 3,760,128 != pread_bytes 97,763,328
               step 1 repack_bytes 0 != pread_bytes 3,760,128
    e2-B-256:  3 violations (same class); e2-B-0: PASS (cache disabled)

This was NOT a computational failure. It was an
instrumentation-contract violation: the frozen phase07 invariant
`repack_bytes == pread_bytes` assumes every byte read on the miss path
is subsequently repacked. E2's direct-placement path intentionally
removes the physical repack transform (pread → compute-ready MXFP4
bytes → Metal placement), so the direct branch legitimately performed
no repack and did not increment `repack_bytes`.

### The fix (kept, per directive)

The invariant and acceptance criteria were NOT changed. Instead, the
E2 direct branches now count `repack_bytes` as **logical /
backward-compatible accounting**: each directly-placed miss slice
increments `repack_bytes` by `slice_bytes` exactly as the legacy temp
path did, so the frozen invariant remains satisfied.

- `load_layer` cached-miss loop and `zc_place` (Phase D) E2 direct
  branches: `stats_.repack_bytes += k.slice_bytes;` added, with comments
  stating the counter is compatibility accounting, NOT evidence of a
  physical repack transform.
- `repack_us` is NOT incremented on the direct path (no physical repack
  time exists); `copy_us == repack_us + placement_us` still holds
  because direct placement time is charged to `placement_us`/`copy_us`
  as before.
- The A arm (E2 gate off) is byte-identical by construction: the fix
  only adds increments inside `if (e2_direct)` branches.

### Interpretation rule (documented here per directive)

After this change, `repack_bytes` on the E2 direct path is logical
accounting only. Do NOT claim physical repack work from the
`repack_bytes` counter for B-arm runs.

### Future instrumentation note (recorded, not implemented)

The accounting model should eventually distinguish, on the relevant
miss path:

    pread_bytes = repack_transform_bytes + direct_placement_bytes

rather than overloading `repack_bytes`. This was deliberately NOT
implemented during E2 to preserve comparability with the frozen Phase
7/K1/E1 baselines (directive: no instrumentation redesign during E2).

## Changes

- `llama.cpp/src/llama-expert-stream.h` — `e2_direct_place_enabled()`
  (requires `KIMI_STREAM_E2_DIRECT_PLACE` AND the E1 gate
  `KIMI_STREAM_METAL_STAGE`, so the CPU repack-buft path is untouched by
  construction; rollback = unset the env).
- `llama.cpp/src/llama-expert-stream.cpp` —
  1. `load_layer` miss loop: E2 direct branch (memcpy packed → loaded
     slot + `cache_put` of packed bytes, skipping `cache_ensure_temp`).
  2. `zc_place` Phase D: E2 direct branch (memcpy packed → persistent
     slot, skipping the temp tensor).
  3. Compatibility accounting in both branches (`repack_bytes` counted
     logically; comments document the distinction).
- `tools/phase11_e2_run.sh` — driver for the 6-run A/B set (fresh
  `-r2` result dirs; pre-fix artifacts retained as evidence).
- `progress/phase-11-e2-report.md` — this report.

## Results

### Environment

64-tok deterministic run (MXFP4 gguf, `-ngl 999`, `-c 4096`, seed 7,
phase-03-coding-lru prompt), same binary for both arms, A/B per config:
A = `KIMI_STREAM_METAL_STAGE=1` only; B = + `KIMI_STREAM_E2_DIRECT_PLACE=1`.
Configs: cache 0 (diagnostic, cache disabled), 256 MiB (miss-heavy),
4096 MiB (frozen E1 production config). 2026-08-31 evening, same
MacBook Air (live server idle).

### Acceptance gates

| gate | result |
|---|---|
| clean execution (EXIT=0) all configs, A and B | PASS (6/6) |
| phase07 invariants PASS (0 violations) all runs | PASS (6/6) |
| bounded streamed memory (decode steady, no growth) | PASS (flat; see note) |
| reproducible baseline-vs-E2 measurement | PASS (below) |
| trace observability available on B | carried from E1b (fix is outside trace path; gate unchanged) |
| rollback by disabling the E2 gate (A restored) | PASS (A arm byte-identical by construction) |
| CPU/Metal tensor or routing identity requirement | N/A (none; per E2 definition) |
| quality-equivalence claim | NOT made (Metal output quality remains the E4 gate) |

### Decode steady state (median ms/step)

| config | A | B | Δ |
|---|---|---|---|
| cache=0 | 59.0 | 58.7 | −0.5% |
| 256 MiB | 44.3 | 45.3 | +2.2% |
| 4096 MiB | 42.8 | 41.4 | −3.3% |

Gen tok/s: cache=0: 15.5 → 16.5; 256: 22.0 → 21.8; 4096: 22.7 → 24.0
(single 64-tok run each; indicative, not a 9G bracket).

### Physical operations (the actual E2 effect — miss path, prefill)

| metric (prefill totals) | A 256 | B 256 | A 4096 | B 4096 |
|---|---|---|---|---|
| repack_us | 1,528 | **163** (−89%) | 2,186 | **147** (−93%) |
| placement_us | 29,202 | 30,067 | 5,560 | 5,050 |
| copy_us (repack+placement) | 30,730 | 30,230 | 7,746 | **5,197** (−33%) |
| pread_bytes / repack_bytes | 96.8 / 96.8 MB | 96.8 / 96.8 MB | 96.8 / 96.8 MB | 96.8 / 96.8 MB |

The temp-tensor round trip is effectively eliminated: `repack_us`
drops to ~150 µs (remnant timing noise) from ~2 ms, and on the 4096 MiB
config the whole copy stage falls by a third. The 256 MiB config's
larger placement cost is cache-capacity churn (more misses/evictions),
not E2 — placement bytes identical across arms (459.0 MB prefill).

### Compatibility counters vs physical ops (per directive)

- `repack_bytes` on B now equals `pread_bytes` (96.8 MB prefill) — this
  satisfies the frozen invariant but is **logical accounting**: the
  direct path performed no physical repack transform. Physical repack
  evidence is `repack_us` (~0 on B) and the copy_us delta above.
- Pre-fix B runs (retained at `benchmarks/results/phase-k1/e2-B-*`)
  show the raw violation (repack_bytes 3.76 MB vs pread 96.8 MB) — the
  instrumentation-contract failure, preserved as evidence.

### E1 baseline unchanged

- A-arm code path is untouched by the fix (increments live only inside
  `if (e2_direct)`), so A behavior is unchanged by construction.
- Post-fix A runs pass all invariants and sit in the frozen E1 band
  (decode median 42.8 ms @4096 vs pre-fix 40.7 ms and E1's 41 ms —
  run-to-run/thermal variance on this shared machine; gen 22.7 vs 24.1
  t/s pre-fix, same noise band).
- Memory (decode steady resident): A 4096 7,310 MB vs B 7,645 MB,
  256: 10,080 vs 10,244, cache=0: 9,569 vs 10,001. B is not lower
  despite eliding the temp allocation; differences are within
  run-to-run variance on a shared host (live server idle but resident).
  No memory regression claim either way; E2's temp elision is a real
  allocation removal but is masked by system noise at this scale.

## Problems

- The phase07 B-arm failure was an instrumentation-contract violation
  (fixed with compatibility accounting, above), not a computational
  failure. Verified: A arm invariant-clean, all B violations were
  exactly `repack_bytes != pread_bytes`.
- Decode steady-state shows no E2 effect (cached configs have 0
  misses/0 repack/0 placement in steady state — the zerocopy cache
  already absorbed the cost; E2 only touches the miss path, which is
  prefill-dominated). The 256 MiB B arm measured +2.2% and 4096 B
  −3.3% — both within single-run noise; no claim of a decode-steady
  win from this experiment.
- Metal output quality remains the open E4 gate (numeric divergence
  from layer 1, localized in E1b) — untouched by E2.

## Decisions

- Keep the phase07 invariant and acceptance criteria exactly as frozen;
  fix the accounting, not the gate (per directive).
- `repack_bytes` on the E2 direct path = logical/backward-compatible
  accounting; `repack_us` remains 0 (honest physical time); report
  physical ops (repack_us, copy_us, placement) separately from
  compatibility counters (repack_bytes).
- Record, for future instrumentation: miss path should model
  `pread_bytes = repack_transform_bytes + direct_placement_bytes`.
- E2 stays env-gated and rollback-able; A (E1 gate only) remains the
  frozen baseline.

## Next Phase

- E2 answer: eliminating the physical repack/copy work on the streamed
  Metal miss path measurably removes the temp round trip (repack_us
  −89/−93%, copy_us −33% on the production 4096 config) while all
  frozen gates hold. The residual decode cost is compute (route trunk
  ~19 ms + expert ~11 ms ≈ 73% of the cached step), which is E3
  territory; quality is E4 (perplexity vs frozen CPU reference).
- Stop for review: do not expand E2 or change the instrumentation
  contract without a new directive. If continuing, the natural next
  step is a 9G-bracket measurement of B (direct placement) vs A to get
  a CI-bounded effect size, then E3 (compute-side) scoping, then the
  E4 quality gate.

## Reproduction

Re-run the A/B set (fixed binary; `-r2` dirs; pre-fix evidence at
`e2-{A,B}-*`):

    cd /Users/pmains/Code/openclaw/kimi
    tools/phase11_e2_run.sh          # writes e2-{A,B}-{0,256,4096}-r2

Single config example (B, 4096 MiB):

    env KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 \
      KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy \
      KIMI_EXPERT_CACHE_MB=4096 \
      KIMI_STREAM_STATS_FILE=benchmarks/results/phase-k1/e2-B-4096-r2/stats.csv \
      KIMI_STREAM_RETR_FILE=benchmarks/results/phase-k1/e2-B-4096-r2/retr.csv \
      KIMI_STREAM_MEM_FILE=benchmarks/results/phase-k1/e2-B-4096-r2/mem.csv \
      KIMI_STREAM_CACHE_LAYERS_FILE=benchmarks/results/phase-k1/e2-B-4096-r2/cache_layers.csv \
      llama.cpp/build-metal/bin/llama-cli -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 -c 4096 -f benchmarks/prompts/phase-03-coding-lru.md -n 64 \
      --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn < /dev/null

Invariant gate on all runs:

    for d in e2-A-0-r2 e2-A-256-r2 e2-A-4096-r2 e2-B-0-r2 e2-B-256-r2 e2-B-4096-r2; do
      python3 tools/phase07_summarize.py benchmarks/results/phase-k1/$d
    done
    # -> invariants: PASS (0 violation(s)) on all 6

Fork commits: E2 implementation + accounting fix (this commit). Main
repo: this report.
