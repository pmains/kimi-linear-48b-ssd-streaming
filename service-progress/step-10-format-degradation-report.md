# Step 10 Report — Localize Real-Path Exact-Format Degradation

## Status

**PASS** (determination; delta attributed with CI support). STOPPED for review —
no production patch made or authorized by this step. TTFT remains deferred
(Step 10A).

## Objective

Determine why the full OpenClaw agent path degrades exact-format compliance
(P1 `PLATANOS!` → bare `PLATANOS`; P1/P2 prose-wrapped and fenced JSON)
relative to the frozen 9B/9C C1a-D reduced baseline (P1 0/3 terse bare
`PLATANOS`, P2 3/3 exact), keeping sampler effects and agent-environment
effects experimentally separate. Owner order (Pete, 2026-09-04): 9D treated as
PASS, patch retained; deliverable is a determination with retained evidence;
NO production patch; wall times are covariates only (latency = Step 10A).

## Changes

- `tools/service_step10_localize.py` (retained 16:54, REPAIRED before
  execution): line 249 carried the transit corruption this file's header warns
  about (`" pass="***"instruction"]` — unparseable). Repaired to
  `rec["instruction"]`; also aligned temp-0.0 cells with the pre-registered
  design (send `"seed": 42` for the deterministic greedy floor). Verified:
  `ast.parse` clean; preflight byte-identity vs frozen 9B C1a-D/P1 payload
  (sha16 `53a44916c9fdb2da`) MATCHES before any execution.
- `tools/service_step10_realpath.sh` (NEW, retained before execution): Leg 3
  real-path driver — 3 fresh headless sessions/probe, `step10` session
  namespace, no `--deliver`, model pinned `llama-server/kimi-linear-48b`, 64K,
  stale-gateway guard inherited from 9D.
- `tools/service_step10_analyze.py` (NEW, retained before execution): merges
  Legs 1+2 offline rows + Leg 3 real-path rows (step10 n=3 + frozen 9D n=3 →
  n=6/probe), Wilson 95% CI, Fisher exact (two-sided), layer contrasts, and
  the pre-registered H-S1/H-S2/ATTRIBUTED determination. (First run of the
  mechanical decision block mislabeled P1 as FAIL:greedy-floor-not-exact; the
  decision logic was corrected to apply the pre-registered falsification
  branch — a deterministic non-exact greedy floor that is fully reproduced
  offline is an attribution, not a separation failure.)
- Results: `benchmarks/results/service-step-10/{offline,realpath,analysis}/`.
- Report: this file. Design: `service-progress/step-10-format-degradation-design.md`.

## Results

Everything ran against the SAME live llama-server endpoint the agent uses
(`127.0.0.1:18080`, 64K, MXFP4, `--parallel 1`); llama-default sampler
(temperature 0.8 / top-p 0.95 / min-p 0.05 / top-k 40) whenever no sampler
fields were sent — the agent path sends none, so default cells are
agent-identical.

### Offline Legs 1+2 (n=10/cell; 180 calls; EXIT=0)

Environment ladder at the agent's real default sampler (exact-format rate,
Wilson 95% CI, families):

| cell | rate | CI | families |
|---|---|---|---|
| C0 P1 | 5/10 | [0.237,0.763] | terse 5, exact 5 |
| C0 P2 | 10/10 | [0.723,1.0] | exact 10 |
| C1 P1 | 0/10 | [0.0,0.278] | terse 6, **empty/directive-only 3, directive-prefixed 1** |
| C1 P2 | 8/10 | [0.49,0.943] | exact 8, directive-prefixed 1, terse 1 |
| C1a-D P1 | 0/10 | [0.0,0.278] | clarifying 6, terse 2, prose 2 |
| C1a-D P2 | 6/10 | [0.313,0.832] | exact 6, terse 4 |
| E2 P1 | 0/10 | [0.0,0.278] | terse 5, directive-prefixed 1, prose 2, clarifying 2 |
| E2 P2 | 8/10 | [0.49,0.943] | exact 8, terse 2 |
| E2T P1 | 0/10 | [0.0,0.278] | terse 6, clarifying 3, prose 1 |
| E2T P2 | 5/10 | [0.237,0.763] | exact 5, terse 3, prose 2 |

Sampler ladder at C0 and E2 (exact rate 0.0 / default 0.8 / 1.6):

| cell | t0.0 | default | t1.6 |
|---|---|---|---|
| C0 P1 | 0/10 | 5/10 | 2/10 |
| C0 P2 | 10/10 | 10/10 | 10/10 |
| E2 P1 | 0/10 | 0/10 | 0/10 |
| E2 P2 | 10/10 | 8/10 | 6/10 |

Determinism assertion (temp-0 cells, seed 42): **10/10 byte-identical in all
four temp-0 cells** — E2/P1 = `PLATANOS` (10/10), E2/P2 = `{"ok": true}`
(10/10), C0/P1 = `PLATANOS`, C0/P2 = `{"ok": true}`.

### Real-path Leg 3 (n=3 fresh headless sessions/probe, step10 namespace)

| probe | step10 (this run) | frozen 9D | merged n=6 |
|---|---|---|---|
| P1 | 0/3 (terse 2, clarifying 1) | 0/3 (terse 2, prose 1) | **0/6** [0.0,0.39] |
| P2 | 3/3 exact | 1/3 (exact 1, prose 1, terse 1) | **4/6** [0.3,0.903] |

Walls (covariates): P1 75.2/398.6/75.7 s; P2 48.5/373.5/96.8 s. Frozen 9D
walls: P1 158.9/532.1/54.2 s; P2 41.6/371.3/34.7 s.

### Real path vs offline default-temp cells (acceptance Q2)

Real-path rate falls INSIDE the offline Wilson CI for every comparison cell on
both probes; Fisher exact supports indistinguishability:

- P1: real 0/6 inside C1a-D/E2/E2T [0.0,0.278] (rate equal 0; p=1.0 all).
- P2: real 0.667 inside C1a-D [0.313,0.832] (p=1.0), E2 [0.49,0.943]
  (p=0.604), E2T [0.237,0.763] (p=0.633).

### Acceptance questions

1. **Greedy floor at E2 (env-equivalent payload):** P2 YES — 10/10 exact and
   identical. P1 NO — 10/10 identical but all `PLATANOS` (missing `!`). The P1
   `!`-drop is **deterministic at the greedy floor** under the headless-
   equivalent prompt, so it is not a sampler-variance artifact.
2. **Offline default-temp compliance and real-path containment:** C1a-D/E2/E2T
   P1 0/10 [0.0,0.278]; P2 6/10 [0.313,0.832], 8/10 [0.49,0.943], 5/10
   [0.237,0.763]. The real-path n=6 distribution falls inside these intervals
   on both probes.
3. **Layer contributions:** (a) the two 9D-gated lines (C1→E2) remove the
   directive-leak family offline (P1: 3 empty/directive-only + 1
   directive-prefixed → 1 directive-prefixed; P2: 1 → 0) with no exact-rate
   change at P2 (8→8) — confirms the 9D gate semantics; (b) surviving
   directive-section lines (E2→C1a-D) shift P1 families (terse → clarifying)
   and P2 8→6; (c) tool catalog (E2→E2T) lowers P2 8→5 with overlapping CIs,
   P1 unchanged; (d) sampler mode: P2 exact rate falls monotonically with
   temperature at E2 (10→8→6), P1 is flat 0/10 at every temperature under E2
   (but 5/10 at C0 default — the system text, not the sampler, suppresses the
   `!` under E2).
4. **Real-vs-reduced delta:** sampling noise at n=3 plus a sampler-mode effect
   for P2 — NOT an unmodeled agent-environment layer. The reduced-baseline P2
   3/3 exact was a favorable small-n draw; at n=10 the same offline cell
   (C1a-D) is 6/10 and the real path (4/6) is statistically indistinguishable
   from offline E2/E2T at the agent's default sampler.

### Determination

- **P1: ATTRIBUTED — model+prompt at the E2 payload.** The `!`-drop is
  deterministic at greedy (10/10 identical `PLATANOS`), and the real path
  (0/6) is statistically identical to offline E2/E2T (0/10, p=1.0). The
  failure is fully reproduced offline at the environment-equivalent payload;
  no unmodeled agent-environment contribution; a sampler configuration would
  NOT fix P1 (flat 0 across temperatures under E2).
- **P2: H-S1 — sampler-primary.** Greedy floor exact (10/10); offline E2/E2T
  at default (8/10, 5/10) is low and indistinguishable from the real path
  (4/6). Lever: per-model sampler configuration (recommendation only).
- **Classification: PASS** — delta attributed with CI support.

## Problems

1. **Retained offline driver was corrupted** (transit corruption at line 249,
   `" pass="***"instruction"]` — the exact failure class the file header
   warns about). Repaired before execution; smoke-validated; no data
   contamination (driver had never executed).
2. **Real-path context-overflow client failures.** 2 of the first 6 Leg-3
   turns (P1-r2, P2-r1) and a fresh-key retry of P1-r2 returned rc=1 with the
   CLI's `Context overflow: prompt too large for the model (precheck)` after
   long walls (581.8/715.3/823.9 s) — a multi-round trajectory exceeding the
   64K precheck, the same class as 9A-era INVALID/long-wall observations.
   Preserved verbatim under `benchmarks/results/service-step-10/realpath/failures/`.
   A third fresh-session attempt for P1-r2 succeeded (rc=0, 398.6 s,
   `PLATANOS`). Classified real-path rows are all rc=0, so the n=6 merge holds.
   This is a liveness/latency issue for Step 10A, not a format-compliance
   data point.
3. **Analyzer's first mechanical decision run mislabeled P1 as FAIL** (rule
   required greedy-floor exactness before attributing). Corrected to apply the
   pre-registered falsification branch: deterministic non-exact greedy floor +
   real path indistinguishable from offline E2/E2T ⇒ attributed to
   model+prompt at the payload, not a separation failure.

## Decisions

- No production patch (owner order; Step 10 stop rule).
- Deterministic-greedy finding takes precedence over the H-S1 assumption for
  P1: because temp-0 at E2 is 10/10 identical AND 0/10 exact, the `!`-drop is
  model+prompt behavior at the environment-equivalent payload, reproduced
  offline; sampler configuration is not the P1 lever.
- Retried the two context-overflow real-path reps in fresh session keys
  (`STEP10_KEYSUFFIX=retry`, then `r2c`) to satisfy the pre-registered
  n=3-valid/probe merge; failures retained as evidence rather than hidden.
- Default-temp offline cells at E2/E2T are the correct experimental bridge for
  the real path; C1a-D (whole-section removal) is NOT the current headless
  prompt and its 3/3 exact baseline was small-n luck.

## Next Phase

- **Step 10A (TTFT):** real-path walls 34.7–532.1 s (9D) and 48.5–398.6 s
  (step10) plus the three context-overflow client failures (581.8–823.9 s)
  are the latency/liveness evidence to attribute there.
- **Recommendations for owner (not applied):** (1) per-model sampler
  configuration for constrained-output probes — the P2 greedy floor is 10/10
  and the agent path currently runs llama defaults with no sampler fields;
  (2) P1 `!`-drop needs a prompt-level look (system text suppresses `!` after
  `PLATANOS` even at greedy; C0 default reaches 5/10 exact) — outside Step 10
  scope; (3) 64K context-overflow on long multi-round trajectories belongs in
  Step 10A/liveness work.
- Step 11 (Qualify Usable Agent Context) is the next roadmap step after owner
  review.

## Reproduction

```sh
cd /Users/pmains/Code/openclaw/kimi
# offline legs (temp sweep + env ladder at llama defaults; n=10/cell):
python3 tools/service_step10_localize.py --reps 10
# real-path leg (3 fresh headless sessions per probe, step10 namespace):
bash tools/service_step10_realpath.sh
# analysis (merge + Wilson CI + Fisher + determination):
python3 tools/service_step10_analyze.py
```

Artifacts: `benchmarks/results/service-step-10/offline/`
(offline-classified.csv, per-cell rep JSONs, offline-summary.txt, manifest.json,
offline-run.log), `benchmarks/results/service-step-10/realpath/`
(classified.csv, summary.txt, env.txt, failures/),
`benchmarks/results/service-step-10/analysis/` (analysis.json,
analysis-summary.txt). Design: `service-progress/step-10-format-degradation-design.md`.
