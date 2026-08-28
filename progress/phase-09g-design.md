# Phase 9G Design — Benchmark Methodology (paired bracketed A/B)

Status: **DESIGN — protocol updated after harness pilot (2026-08-28)**

Decision (Peter, 2026-08-28): stop Phase 9 optimization subphases. The next
activity is a benchmark-methodology phase: quantify run-to-run and
session-to-session variance, establish a repeated paired/bracketed A/B
protocol, determine the bracket count needed to distinguish 5/10/20%
effects, and freeze that protocol for all subsequent phases.

Post-pilot protocol decisions (Peter, 2026-08-28; pilot report:
`progress/phase-09g-harness-pilot-report.md`):

- Pilot PASS (n=4 brackets, 12 runs); **no tuning based on pilot
  brackets** — the pilot's job was harness validation, and bracket 4's
  18.96% A spread is exactly the phenomenon the full phase must
  quantify, not an outlier to delete.
- Harness frozen after adding randomization: per bracket, the three
  labeled runs (A-before, B, A-after) execute in a seeded uniform random
  permutation (candidate placement/order randomized across brackets;
  seed recorded in `harness.json`; `SEED` env to reproduce).
- Null experiment runs the SAME machinery and labels: the middle labeled
  slot executes the frozen A config as a sham "B" (`MODE=null`); no
  special null execution path. S_i computed identically; under the null
  it should center near 1.0 and its tails bound the spurious
  "optimizations" the protocol can manufacture.
- Full experiment prioritizes three estimates: the null distribution of
  S_i, the positive-control distribution of S_i (W4 vs W1), and how both
  change across sessions — i.e., how large an observed speedup must be
  to be reliably distinguished from this machine's performance
  variability.
- Prefer **3 shorter sessions separated in time over 1 marathon** of
  equivalent N: between-session variation (including variation in the
  response to parallel I/O) is part of the phenomenon.
- Robust summaries (median/IQR, bootstrap CI) accommodate swing brackets
  without pretending they did not happen.

Companion artifacts:
- `benchmarks/results/phase-09g/variance-9d-9f.json`
- `tools/phase09g_variance_quant.py` (produces the above)

---

## 1. Why this phase exists

Phase 9F's end-to-end prediction (9E model: +11.8% tok/s over archived 9D)
was falsified: measured 0.99× at the recommended operating point. The 9F
report attributed the failure to a session confound: "the 9F session's
cached-config reads were ~55% slower than the 9D session on the IDENTICAL
frozen workers=1 path (read wall 75.9–80.7 ms/step vs ~50 ms in the 9D
ladder)".

**Re-analysis of the archived raw data (this phase's seed result) shows
that root-cause claim is wrong in its specifics.** The apples-to-oranges
comparison is now documented:

| quantity | 9D session | 9F session | delta |
|---|---|---|---|
| frozen w1 read wall (ms/step, n=6 runs each) | 73.1 (63.0–80.4) | 78.5 (75.4–80.7) | **+7%** |
| frozen w1 tok/s (n=6 runs each) | 4.97 (4.72–5.46) | 4.52 (4.34–4.86) | **−9%** |
| candidate W4 read wall (ms/step) | 42.2 | 77.4 | +83% |
| candidate W4 tok/s | 5.99 | 5.93 | −1% |
| parallel read-wall compression (frozen→W4) | 73.1→42.2 (1.73×) | 78.5→77.4 (1.01×) | compression vanished |
| paired speedup W4 (B vs bracketed w1 mean) | 1.15× | 1.31× | +14% (rel.) |

Corrected interpretation:

1. The frozen control was ~7–9% slower in the 9F session — a real but
   modest session effect, NOT the ~55% the 9F report claimed.
2. The "~50 ms" figure in the 9F report was the 9D session's *parallel
   candidate* wall (49.4 at W2), not its frozen control.
3. The dramatic cross-session difference was in **parallel-read wall
   compression**: 9D's parallel reads compressed the wall 1.5–1.7×, 9F's
   did not compress at all. The I/O environment changed *how reads
   responded to parallelism*, not just how fast sequential reads ran.
4. Candidate end-to-end tok/s was remarkably stable across sessions
   (W2: 5.69 vs 5.89; W4: 5.99 vs 5.93). The within-session speedup ratio
   moved because the *control* moved.
5. The 9E model's load-wall mechanics were confirmed in-session; its
   end-to-end expectation was falsified by control instability — but the
   instability is better described as "environment-dependent parallel-read
   benefit" than "uniformly slower I/O".

Lesson for methodology: **absolute tok/s of the control is not a stable
denominator, within a session (bracket-to-bracket CV 2.7–5.0%, min–max
spread up to ~16% on the identical frozen binary) or between sessions
(2–9%).** A 10–20% optimization gate cannot be evaluated against an
archived baseline. The 9F report's conclusion (use contemporaneous
bracketed controls) survives; its supporting evidence needs the correction
above, which this phase will also record in its final report.

## 2. Objective

Make "how much faster is B than A" trustworthy and reproducible:

- quantify the variance components (within-run, run-to-run, drift,
  session-to-session) of the frozen control path;
- validate a paired bracketed A→B→A protocol against a null control
  (A→A→A) and a positive control (W=4 pipelined vs W=1 frozen);
- determine how many brackets are needed to detect 5%, 10%, 20% effects
  (paired test, α = 0.05, β = 0.20);
- freeze the protocol (harness + analysis tooling + env capture) for all
  subsequent phases;
- ban archived-baseline comparisons as optimization gates.

## 3. Protocol under design (to be validated, not assumed)

Experimental unit = one bracket:

    A_before → B → A_after

contemporaneous (tight timing, same session, same config), candidate
placement and order randomized across brackets: the three labeled runs
are executed in a seeded uniform random permutation per bracket (labels
stay attached to roles; the estimator is unchanged; seed recorded in
`harness.json`). Paired speedup:

    S_i = tok/s(B_i) / mean(tok/s(A_before,i), tok/s(A_after,i))

Report the **distribution** of S_i over brackets (median, IQR, bootstrap
CI), plus the A-bracket spread as a mandatory noise disclosure. A gate
passes only if the bootstrap CI of median S_i excludes the gate threshold
(e.g. 1.10) AND the bracketed A spread is reported.

Design points to validate:

- **Run length**: 128 tokens vs 256 vs 512 — measure how σ shrinks with
  run length vs wall-clock cost (runs are ~2–3 min at 128 tok).
- **Drift vs iid noise**: repeated A→A→A→A sequences decompose
  bracket-to-bracket noise into drift (linear-in-time component, which
  A→B→A interpolation cancels) and iid noise (which it does not).
- **Bracket count**: power analysis from measured σ_s = sd(log S_i) on the
  paired estimator. Prior estimate from archived data (σ_A ≈ 4% CV):
  ~8 brackets for a 5% effect, ~3 for 10%, 1–2 for 20% — to be replaced
  by measured values.
- **Env capture (mandatory covariates)**: memory pressure / page-cache
  state (`memory_pressure`, `vm_stat`), SSD thermal/state if readable,
  background load (top processes), time of day, and the live llama-server
  state (the production server shares this machine; see §5).
- **Metric choice**: tok/s (end-to-end) vs load-wall ms/step (mechanism).
  Load wall is more stable (it excludes dense-trunk compute noise) but
  tok/s is the user-facing quantity. Protocol should gate on tok/s and
  report load wall as mechanism evidence.

## 4. Work packages

1. **Variance decomposition** (new runs): 8–10× A (frozen w1, coding-cap4)
   back-to-back in one session; repeat across 2–3 sessions (ideally
   different ambient states: idle vs after-reboot vs high-memory-pressure).
   Output: σ_iid, drift rate, σ_session, and the same for the candidate
   (W=4) path.
2. **Null protocol validation**: 10× (A→A→A) brackets where B ≡ A,
   implemented as a sham — the middle labeled slot runs the frozen A
   config under identical machinery and labels (`MODE=null`); no special
   null path. Output: empirical S distribution; must be centered ≈ 1.0;
   false positive rate at α = 0.05 must be within binomial tolerance;
   check for position bias (is the middle run systematically different?)
   — reported as S by the sham's temporal position.
3. **Positive control**: W=4 vs W=1 frozen, same-session, ~6 brackets.
   Expected S ≈ 1.15–1.35 from archived paired data; must be detected at
   the recommended bracket count with p < 0.05.
4. **Power table**: measured σ_s → n_brackets for 5/10/20% effects;
   validate empirically where feasible (e.g., subsample the null brackets
   to confirm nominal false-positive rate).
5. **Freeze**: commit `tools/phase09g_run_brackets.sh` +
   `tools/phase09g_analyze.py`; document protocol in `TOOLS.md`;
   amend the Phase 9F report's root-cause claim via the 9G report (do not
   rewrite 9F history); record the corrected variance story. Harness
   frozen 2026-08-28 (randomization + `MODE=null` sham-B), pending the
   full-phase runs in idle windows.

## 5. Machine-time and live-server constraint

The live production server (`runtime/live`, port 18080, serving
`kimi-local/kimi-linear-48b` to agents kimi/poliscopic/aristotle) shares
this MacBook Air. Ladder runs peg CPU at 84–99% and will degrade agent
latency. **Benchmark runs for this phase must be scheduled when the live
service is idle** (e.g., overnight), and the protocol should record
whether the live server is under load as a covariate.

## 6. Acceptance criteria (phase PASS requires all)

1. Variance components quantified with CIs from dedicated runs (not only
   archived data), including the candidate path.
2. Null protocol validated: median log S within ±0.02 of 0; false-positive
   rate at α=0.05 within binomial tolerance of nominal (n ≥ 10 brackets).
3. Positive control (W4 vs W1) detected reliably at the recommended
   bracket count.
4. Power table produced from measured σ_s: brackets needed for 5/10/20%
   effects, with the run length chosen in (1).
5. Protocol frozen in TOOLS.md + committed harness; archived-baseline
   gating explicitly banned for optimization decisions (roadmap note).
6. `progress/phase-09g-report.md` written with the corrected 9F root-cause
   analysis included.

## 7. Explicit non-goals (scope)

- No optimization work of any kind (no prefetch, policy, layout, kernel,
  MXFP4, I/O tuning). The positive control reuses the existing W=4 path.
- No investigation of *why* parallel compression vanished in 9F beyond
  recording env covariates (that is a 9G+ decision if ever pursued).
- No changes to the live runtime.
