# Phase 9G Report

Status: **PASS**

## Objective

Freeze a trustworthy benchmark methodology for all subsequent phases of
this project, replacing archived-baseline comparisons:

1. quantify the variance components of the frozen control path (A =
   workers=1, the Phase 8/9D/9F byte-identical path), including the
   candidate path (B = W4 pipelined repack);
2. validate a paired bracketed A→B→A protocol against a null control
   (sham B ≡ A) and a positive control (W4 vs W1);
3. determine the bracket count needed to detect 5 / 10 / 20% effects;
4. freeze the harness + analysis tooling + env capture in TOOLS.md;
5. ban archived-baseline comparisons as optimization gates;
6. record the corrected Phase 9F root-cause analysis.

Full protocol: `progress/phase-09g-design.md`; frozen protocol section:
TOOLS.md "Phase 9G Benchmark Protocol (frozen 2026-08-28)".
Analysis/reporting conventions: `progress/phase-09g-full-experiment-analysis-notes.md`.

## Changes

- `benchmarks/results/phase-09g/session-1/` — full session 1 (null +
  positive, 10 brackets each, 60 runs), seed 1787983806, run
  2026-08-28 23:11 → 2026-08-29 00:06 MST.
- `benchmarks/results/phase-09g/session-2/` — full session 2, seed
  1788018038, run 2026-08-29 08:40 → 09:36 MST.
- `benchmarks/results/phase-09g/session-3/` — full session 3, seed
  1788029761, run 2026-08-29 11:56 → 12:50 MST.
- `benchmarks/results/phase-09g/session-{1,2,3}/run_sessionN.sh` —
  per-session driver scripts (frozen harness, sequential null →
  positive, then analysis; identical template, only the seed differs).
- `progress/phase-09g-report.md` — this report.

No harness or analyzer changes this phase. Harness frozen at commit
`c40c008` ("phase-09g: freeze harness — seeded bracket-order
randomization + MODE=null (sham B)"). The full experiment ran exactly
the frozen protocol: `CONFIG=coding-cap4`, `CTX=4096`, `N=128`,
`KIMI_CACHE_MB=4096`, A=workers=1, B=workers=4 (positive) / B≡A
(null).

## Results

All 6 phase/session summaries (3 sessions × null/positive) report the
same harness checks: **15/15 PASS, analyzer exit 0** — bracket
execution, manifest worker counts (A=1, B=1 in null / B=4 in positive),
recorded-order vs execution-order verification, paired speedup, moe.md5
== frozen Phase 8 baseline (`da45ab…642e5c`) in all 180 runs, retr
byte-identity in all runs, Phase 7 per-step invariants 30/30 per run,
run.log clean, raw artifacts retained, env covariates captured and
parseable, no position bias, no warmup artifact, no strong drift.

### Null protocol (sham B ≡ A, 10 brackets per session)

Paired speedup S_i = tok/s(B_i) / mean(tok/s(A1,i), A2,i), where A1/A2
are the two labeled A controls of the bracket (randomized temporal
order; conventions doc Note 1).

| session | median S | IQR | min–max | bootstrap 95% CI | median log S | CI of median log S | sign test p |
|---|---|---|---|---|---|---|---|
| 1 | 0.9794 | 0.960–1.020 | 0.933–1.057 | [0.963, 1.018] | −0.0208 | [−0.0377, 0.0176] | 0.754 |
| 2 | 1.0091 | 0.983–1.038 | 0.807–1.072 | [0.963, 1.035] | +0.0090 | [−0.0389, 0.0348] | 0.109 |
| 3 | 0.9735 | 0.965–1.031 | 0.911–1.051 | [0.961, 1.030] | −0.0269 | [−0.0403, 0.0294] | 0.754 |

Pooled across sessions (n=30): median S = **1.0018**, IQR 0.967–1.028,
min 0.807, max 1.072 → pooled median log S = **+0.0018** (well within
the ±0.02 acceptance band). All three per-session bootstrap CIs of
median log S include 0; all sign tests non-significant.

Empirical false-positive rate (10-bracket subsamples of the 30 null
brackets, bootstrap CI of median S excluding 1.0): **2.7%** vs nominal
5% — within binomial tolerance. No null subsample's CI ever excluded
the 1.10 gate (0% false "10% wins"). Direct evidence: 0 of 3 real
sessions produced a CI excluding 1.0.

S by sham-B temporal position (position effects are part of the
phenomenon; conventions doc Note 2):

| session | first (n) | middle (n) | last (n) |
|---|---|---|---|
| 1 | 0.962 (2) | 0.979 (4) | 1.009 (4) |
| 2 | 0.985 (4) | 1.013 (3) | 1.005 (3) |
| 3 | 0.969 (3) | 0.985 (4) | 1.034 (3) |

No consistent position artifact: "last" is highest in sessions 1 and 3
but not 2; session 2's two first-position outliers (0.922, 0.807) pull
its first-position median down. The 0.807 bracket (session 2, b9) is
the largest null swing (−19%); per protocol it is data, not a deletion.

### Positive control (W4 pipelined repack vs W1 frozen)

| session | median S | IQR | min–max | bootstrap 95% CI |
|---|---|---|---|---|
| 1 | 1.2178 | 1.182–1.298 | 1.154–1.328 | [1.184, 1.296] |
| 2 | 1.1773 | 1.137–1.183 | 1.088–1.250 | [1.137, 1.184] |
| 3 | 1.1630 | 1.140–1.318 | 1.129–1.342 | [1.143, 1.318] |

Pooled (n=30): median S = **1.1798**, IQR 1.155–1.234, min 1.088, max
1.342. All 30 brackets > 1.08; all three session CIs of median S
exclude the 1.10 gate. **W4-vs-W1 is a real ~+16–22% median effect
(≈+18% pooled), reliably detected with 10 brackets where only 3 are
needed for a 10% effect.**

S by B position (positive): session 1 — first 1.188 (2), middle 1.207
(4), last 1.254 (4); session 2 — first 1.179 (4), middle 1.175 (3),
last 1.164 (3); session 3 — first 1.133 (3), middle 1.241 (4), last
1.180 (3). No consistent placement gradient.

### Variance decomposition (dedicated full-phase data)

Frozen control path (A, all 180 runs across modes; null mode provides
20 A runs + 10 sham-B runs per session, all the identical w1 path):

- within-bracket A1/A2 pair CV (mean): session 1 3.36% (max 6.8%),
  session 2 2.86% (max 6.3%), session 3 4.01% (max 9.8%);
- across-bracket A CV (analyzer `a_cv_pct`): null 2.95 / 2.85 / 4.40%,
  positive 3.80 / 4.14 / 3.68% → **A bracket CV ≈ 3–4%**;
- session-to-session A mean: 5.266 / 5.118 / 5.257 tok/s → min–max
  spread **2.8%**, sd ≈ 1.6% of mean (smaller than the ~2–9% range seen
  across the 9D/9F archived sessions);
- drift: slope −0.27% / −0.31% / +0.16% rel per bracket (null) —
  bracketed interpolation cancels this by construction;
- sd(log S): null **0.0559**, positive 0.0560 — the paired estimator's
  spread is the same under both modes.

### Power table (from measured null sd(log S) = 0.0559)

Paired test on log S, two-sided α = 0.05, β = 0.20 (z_α/2 + z_β = 2.80):

| effect | brackets needed |
|---|---|
| +5% | 11 |
| +10% | 3 |
| +20% | 1 |

Prior design estimate (~8 / 3 / 1–2) is updated to the measured 11 / 3
/ 1. At the frozen run length (128 tok), a 10% gate needs 3 brackets; a
5% gate needs 11.

### Env covariates

120 snapshots (20 per session-mode phase): memory free 69–81%;
loadavg 1-min 0.92–8.35 (one spike to 8.35 in session-2 positive
b2-after, recorded as a covariate; no run invalidated); live llama-server
healthy in all 120 snapshots; thermal reading empty (known limitation:
no thermal sysctl without powermetrics/sudo; `pmset -g therm`
captured).

### Acceptance criteria

1. **Variance components quantified with CIs from dedicated runs,
   including the candidate path** — PASS (within-bracket pair CV,
   across-bracket CV, session effect, drift, sd(log S), bootstrap CIs
   above).
2. **Null validated: median log S within ±0.02 of 0; FPR at α = 0.05
   within binomial tolerance (n ≥ 10)** — PASS. Pooled median log S =
   +0.0018. Two of three sessions sit marginally below 0 (−0.021,
   −0.027) but both CIs include 0 and sign tests are non-significant;
   this asymmetry is reported, not hidden. Empirical FPR 2.7% (nominal
   5%); 0/3 sessions false-positive.
3. **Positive control detected reliably at the recommended bracket
   count** — PASS. All three session CIs exclude 1.10; pooled median
   1.180.
4. **Power table produced from measured σ_s** — PASS (11 / 3 / 1 for
   5 / 10 / 20%).
5. **Protocol frozen in TOOLS.md + committed harness; archived-baseline
   gating banned** — PASS. Harness commit `c40c008`; TOOLS.md section
   frozen 2026-08-28; archived-baseline comparisons explicitly banned
   as optimization gates.
6. **Corrected 9F root-cause analysis recorded** — below.

### Corrected Phase 9F root-cause analysis (design §1, now in-project record)

The 9F report attributed its 9E-model falsification to "9F session's
cached-config reads ~55% slower than 9D on the identical frozen w1
path". Re-analysis of the archived raw data (9G seed result) shows the
root-cause claim was wrong in its specifics:

| quantity | 9D session | 9F session | delta |
|---|---|---|---|
| frozen w1 read wall (ms/step, n=6) | 73.1 (63.0–80.4) | 78.5 (75.4–80.7) | **+7%** |
| frozen w1 tok/s (n=6) | 4.97 (4.72–5.46) | 4.52 (4.34–4.86) | **−9%** |
| candidate W4 read wall (ms/step) | 42.2 | 77.4 | +83% |
| candidate W4 tok/s | 5.99 | 5.93 | −1% |
| parallel read-wall compression (frozen→W4) | 73.1→42.2 (1.73×) | 78.5→77.4 (1.01×) | vanished |
| paired speedup W4 vs bracketed w1 mean | 1.15× | 1.31× | +14% rel |

Corrected interpretation:

1. The frozen control was ~7–9% slower in the 9F session — real but
   modest, NOT the ~55% claimed.
2. The "~50 ms" figure in the 9F report was 9D's *parallel candidate*
   wall (49.4 at W2), not its frozen control.
3. The dramatic cross-session difference was **parallel-read wall
   compression** (9D 1.5–1.7×, 9F none) — the I/O environment changed
   *how reads responded to parallelism*, not just sequential read
   speed.
4. Candidate end-to-end tok/s was stable across sessions (W2 5.69 vs
   5.89; W4 5.99 vs 5.93). The within-session speedup ratio moved
   because the *control* moved.
5. The 9E model's load-wall mechanics were confirmed; its end-to-end
   expectation was falsified by control instability — better described
   as "environment-dependent parallel-read benefit" than "uniformly
   slower I/O".

Lesson (now protocol law): **absolute control tok/s is not a stable
denominator** — within-session bracket CV 3–4%, min–max up to ~10%,
between-session 2–9%. A 10–20% optimization gate cannot be evaluated
against an archived baseline; the 9G bracketed protocol with
contemporaneous controls is required. The 9F report's conclusion
(use contemporaneous bracketed controls) survives; this report is the
corrected supporting evidence. 9F history is not rewritten.

## Problems

1. **Session-2 null b9 swing**: S = 0.807 (−19%), the largest null
   outlier (sham B slower). Per protocol it is retained as data; it
   drives session-2's slightly wider null CI and its first-position
   median. No external event was logged for it; loadavg was normal
   (2.25 → 2.53).
2. **Per-session null centering asymmetry**: sessions 1 and 3 median
   log S = −0.021 / −0.027, marginally outside the ±0.02 band (pooled
   +0.0018, CIs include 0). A small negative sham-B bias cannot be
   ruled out; the bootstrap CIs and sign tests say it is not
   distinguishable from 0 at n=10.
3. **Loadavg spike 8.35** (session-2 positive b2-after): transient
   external load, captured as a covariate; the affected bracket's
   speedup (1.164) is mid-range, consistent with the paired design
   containing it.
4. **Warmup artifact in session-3 null**: first bracket +7.1% A mean vs
   rest (5.590 vs 5.220 tok/s) — the analyzer's warmup flag remained
   false (threshold not crossed), but it is the largest first-bracket
   effect observed; bracketing absorbs it.
5. **Thermal covariate unavailable** without powermetrics/sudo (known
   from pilot; `pmset -g therm` captured instead).
6. **Watcher fix provenance (required by conventions doc)**: the
   `9g-idle-window-watcher` cron trigger failed with "code mode module
   access is disabled" (Node `require('child_process')` disallowed in
   the trigger sandbox) and was rewritten to the documented
   `tools.call('exec', ...)` API with IDENTICAL thresholds, verified
   fire:false outside the idle window, and confirmed by Peter
   (2026-08-28 18:09 MST) as infrastructure repair, not experimental
   tuning. This occurred BEFORE Session 1 and before any full-experiment
   data collection, so it cannot have been motivated by these results.

## Decisions

- **Harness and protocol frozen** — no changes to
  `tools/phase09g_run_brackets.sh`, `tools/phase09g_analyze.py`, or the
  TOOLS.md protocol after the pilot. Sessions 1–3 ran exactly as
  frozen.
- **Three sessions separated in time** (overnight 23:11, morning 08:40,
  midday 11:56) per design §3 — between-session variation is a studied
  component, not noise to aggregate away.
- **Run length stayed at 128 tok** (frozen convention, matches 9D/9F);
  the measured sd(log S) = 0.0559 at this length feeds the power table.
  A future run-length study would be a new methodology phase.
- **S by session and by B position reported** (conventions Notes 1–2);
  no pooling of brackets across sessions into a single IID sample.
- **Null asymmetry reported, not gated away** — per-session medians
  outside ±0.02 are disclosed; the pooled estimate and CIs carry the
  acceptance decision.
- **Gate semantics fixed**: an optimization passes only if the
  bootstrap CI of the median S excludes the gate threshold AND the
  bracketed A spread is reported (mandatory noise disclosure).

## Next Phase

Phase 9G is a methodology phase; there is no automatic next
optimization phase (AGENTS.md STOP point after the Memory Ladder/
Phase 8 streaming experiment stands). Any future optimization work must
be selected from the measured results of that experiment and must use
the frozen 9G protocol:

- 10% gates: ≥ 3 brackets; 5% gates: ≥ 11 brackets (measured power
  table; sd(log S) = 0.056, A bracket CV ≈ 3–4%);
- archived-baseline comparisons are banned as optimization gates;
- if W4 pipelined repack is ever revisited as a gate, its measured
  effect is median ≈ 1.18 pooled (session range 1.16–1.22), CI
  [1.137, 1.318] — comfortably above a 1.10 gate at 10 brackets.

Candidate next activities (all requiring new methodology authorization
if pursued): run-length study (128 vs 256 vs 512), per-session
replication of the null asymmetry, or the post-Phase-8 optimization
selection.

## Reproduction

    # full session (60 runs, ~55 min; SEED overrides epoch-s)
    SEED=<s> bash benchmarks/results/phase-09g/session-3/run_session3.sh

    # or phase-by-phase, frozen harness:
    CONFIG=coding-cap4 MODE=null SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<dir>/null 10 128
    CONFIG=coding-cap4 MODE=positive SEED=<s> tools/phase09g_run_brackets.sh \
        benchmarks/results/phase-09g/<dir>/positive 10 128

    # analysis + harness validation (exit 0 = PASS)
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/<dir>/null
    python3 tools/phase09g_analyze.py benchmarks/results/phase-09g/<dir>/positive

    # cross-session aggregation (report tables above)
    python3 tools/phase09g_variance_quant.py   # archived 9D/9F variance

Sessions used seeds 1787983806 (1), 1788018038 (2), 1788029761 (3);
recorded orders verified against driver-log mtimes by the analyzer
(check "recorded bracket order matches execution order", 10/10 per
phase). All raw artifacts retained per run dir (act.bin gitignored,
regenerable).
