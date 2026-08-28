# Phase 9G Harness Pilot Report

Status: **PASS** (harness validation pilot — not the 9G variance experiment)

## Objective

Validate the Phase 9G A→B→A paired-bracket harness end-to-end before the
full methodology phase:

1. correct A→B→A execution and paired-speedup calculation;
2. correctness/invariants remain green;
3. raw per-run data are retained;
4. environmental covariates are captured;
5. no obvious order/warmup/state artifact appears.

Explicitly NOT a performance experiment: n=4 brackets, no conclusions about
W4-vs-W1 drawn from the speedup numbers.

## Changes

- `tools/phase09g_run_brackets.sh` — new. Repeated A→B→A bracket runner
  (design WP5 precursor). A = frozen workers=1 path (Phase 8/9D/9F
  byte-identical control), B = candidate (default W=4 pipelined repack,
  the 9G positive control). Per-run env identical to the 9F ladder
  (`KIMI_EXPERT_CACHE_MODE=zerocopy`, `KIMI_PHASE7_INSTR=1`,
  `KIMI_PHASE9C_TRACE`, `CTX=4096`). Snapshots env covariates (memory
  pressure, vm_stat, loadavg, top CPU procs, CPU thermal level, pmset
  therm, live llama-server health) before and after every bracket.
  Retains the full Phase 4/7/9 artifact set per run.
- `tools/phase09g_analyze.py` — new. Computes paired speedup
  `S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))`,
  distribution stats with bootstrap CI, and runs the harness-validation
  checks (execution, worker counts via manifest, moe md5 vs frozen Phase 8
  baseline, retr byte-identity, Phase 7 per-step invariants, run.log
  scan, artifact presence, env covariates, position/warmup/drift
  artifact checks). Exit 0 = PILOT PASS.
- `benchmarks/results/phase-09g/pilot/` — raw data, 4 brackets ×
  (A-before, B, A-after) = 12 runs + 8 env snapshots + summaries.
  `act.bin` excluded per .gitignore (regenerable).

## Results

Run: 2026-08-28 18:14:40Z → 18:26:48Z (~12.1 min wall), coding-cap4,
N=128, seed=1, A=w1 / B=w4, cache 4096 MiB. Live llama-server (pid 21854)
healthy in all 8 env snapshots.

| bracket | A_before | B | A_after | S_i | A spread |
|---|---|---|---|---|---|
| 1 | 4.007 | 5.341 | 4.230 | 1.297 | 5.41% |
| 2 | 4.132 | 5.416 | 4.062 | 1.322 | 1.69% |
| 3 | 4.229 | 5.075 | 4.044 | 1.227 | 4.46% |
| 4 | 4.457 | 5.548 | 3.685 | 1.363 | 18.96% |

Paired speedup: median **1.309**, bootstrap 95% CI [1.227, 1.363].
A-bracket tok/s CV 5.4% (A mean 4.106). Speedup magnitude consistent
with archived paired data (9D 1.15×, 9F 1.31×) — sanity only, not a
conclusion.

Harness checks: **14/14 PASS** (overall PILOT PASS):

- bracket execution: 4 brackets × 3 runs complete; manifest confirms
  A runs read_workers=1, B runs =4;
- paired speedup computed for all 4 brackets; per-bracket arithmetic
  spot-checked (S_i = B / mean of the two A runs);
- moe.csv md5 == frozen Phase 8 baseline (`da45ab…642e5c`) in all 12 runs;
- retr.csv byte-identical across all 12 runs (deterministic routing);
- Phase 7 per-step invariants: 12/12 runs PASS (0 violations);
- run.log clean (no error/assert/abort markers);
- all 9 expected artifacts present in all 12 run dirs;
- 8 env snapshots captured and parseable (memory free 79% → ~57–59%
  after page-cache warm; loadavg 1.76 → 3.74 during ladder);
- no position bias (A_before vs A_after: mean Δ −4.45% rel, sign test
  p = 0.625);
- no warmup artifact (first bracket A mean 4.118 vs rest 4.102, +0.4%);
- no strong drift (A slope −0.25% rel/bracket across brackets).

## Problems

1. Bracket 4's A_after (3.685) is a clear within-session outlier
   (18.96% A spread vs 1.7–5.4% elsewhere). This is exactly the
   bracket-to-bracket noise the full 9G phase must decompose (σ_iid vs
   drift); it does not invalidate the harness — the paired design
   contains it by construction, and the noise disclosure flags it.
2. Wall time 12.1 min vs ~10 min target: 12 runs at ~50–55 s each.
   Acceptable for the pilot; the full phase's run-length choice (128 vs
   256 vs 512 tok) will trade σ against wall cost explicitly.
3. `machdep.xcpm.cpu_thermal_level` returns empty on this machine;
   `pmset -g therm` is captured instead (no thermal sysctl available
   without powermetrics/sudo). Note for the frozen protocol.
4. Fixed A→B→A order for the pilot; the design's cross-bracket
   randomization of candidate placement/order is a full-phase decision
   point, not yet implemented.

## Decisions

- Built the WP5 harness as the vehicle for this pilot (it did not exist).
- A = frozen w1, B = W4 pipelined (design's positive control), config
  coding-cap4 only, N=128, seed=1 — matches 9D/9F conventions so results
  are comparable.
- Paired speedup denominator = mean of the two bracketing A runs (per
  design); A spread reported as mandatory noise disclosure.
- Pilot run executed during daytime per explicit request; live-server
  load recorded as a covariate (design §5 idle-window rule applies to the
  full phase).
- Raw data committed per prior-phase convention (all artifacts except
  gitignored act.bin).

## Post-pilot changes (2026-08-28, per Peter)

Implemented and committed BEFORE the full phase (no re-tuning from the
four pilot brackets):

- **Randomized within-bracket execution order** — the three labeled runs
  (A-before, B, A-after) are now executed in a seeded uniform random
  permutation per bracket (`SEED` env, default epoch-s, recorded in
  `harness.json` as `bracket_orders`; pass `SEED` to reproduce). The
  candidate's temporal placement/order is therefore randomized across
  brackets per design §3. Labels stay attached to roles; the estimator is
  unchanged.
- **`MODE=null` (sham B)** — the middle labeled slot runs the frozen A
  config under identical machinery and labels; there is no special null
  execution path. S_i is computed identically; the analyzer reports null
  diagnostics (median log S, bootstrap CI, sign test, centered flag) as
  findings, not exit gates.
- **Analyzer additions** — mode-aware worker expectations, recorded-order
  vs driver-log-mtime verification (check 1b), S by candidate position
  (first/middle/last), `pilot` flag selecting summary filename
  (`phase-09g-pilot-summary.json` vs `phase-09g-summary.json`).
- Full phase (variance decomposition, ≥10 null brackets, positive
  control, power table) is scheduled for idle windows per design §5;
  three shorter sessions are preferred over one marathon.

## Next Phase

Full 9G (design `progress/phase-09g-design.md`) — requires an idle
window per design §5:

- variance decomposition (8–10× A back-to-back, 2–3 sessions);
- null protocol validation (10× A→A→A; S centered ≈ 1.0);
- positive control at the recommended bracket count;
- power table from measured σ_s;
- freeze harness in TOOLS.md + final 9G report (with corrected 9F
  root-cause analysis).
- Add cross-bracket randomization before freezing.

## Reproduction

    # pilot (12 runs, ~12 min; PILOT=true keeps the pilot summary filename)
    CONFIG=coding-cap4 PILOT=true SEED=1 tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/pilot 4 128

    # null-mode bracket (sham B; same machinery and labels)
    CONFIG=coding-cap4 MODE=null tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<dir> 10 128

    # positive-control bracket (B = W4 candidate)
    CONFIG=coding-cap4 MODE=positive tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<dir> 10 128

    # analysis + harness validation (exit 0 = PILOT PASS)
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/pilot

    # raw per-run invariant re-check (any run dir)
    python3 tools/phase07_summarize.py \
        benchmarks/results/phase-09g/pilot/coding-cap4/b1-B
