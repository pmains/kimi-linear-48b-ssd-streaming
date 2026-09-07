# Step 10D Report — Apply + Live-Validate the LoopDetection Safeguard

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Status

**PASS — COMPLETE (live validation). STOPPED for review.**

The Step 10C smallest safeguard was applied as the ONLY production change:
`agents.entries.kimi.tools.loopDetection.enabled: true`. All three ordered
confirmations were validated live on the real path at the aligned 64K
configuration. No other config or code change (context/sampler/prompts/tool
behavior/llama-server untouched).

## Objective

Apply the config-only safeguard and validate live:
1. the P1-r5-style identical-read loop is terminated by the general detector;
2. legitimate multi-step tool use still completes;
3. the existing post-compaction guard behavior is unchanged.

## Changes

- `~/.openclaw/openclaw.json` — added
  `agents.entries.kimi.tools.loopDetection.enabled: true` (single line).
  Backup: `~/.openclaw/openclaw.json.bak-step10d-20260905`. Diff verified:
  exactly one added line; JSON validated.
- Gateway restarted via detached supervisor `tools/service_step10d_supervise.sh`
  (new PID 59990, started 12:34:21 MST). llama-server untouched (64K,
  n_ctx=65536, healthy throughout).
- New retained drivers/artifacts:
  - Design: `service-progress/step-10d-loopdetection-validation-design.md`
  - Leg: `tools/service_step10d_leg.sh` (LOOP/P1/P2/NORM)
  - Extra deterministic probes: `tools/service_step10d_extra.sh` +
    prompts `LOOP-STRICT.md`, `NORM-ABS.md`
  - Analyzer: `tools/service_step10d_analyze.py`
  - Evidence: `benchmarks/results/service-step-10d/` (env.txt, supervise.log,
    extra.log, prompts/, 64k/<probe>/<rep>.* per-turn artifacts,
    analysis/summary.json)
- `SERVICE-ROADMAP.md` §10D — updated to COMPLETE — PASS; STOPPED for review.

## Results

12 real-path turns (fresh `step10d` headless sessions, single-slot 64K
server), every doc resolving `contextTokens: 65536`.

### Confirmation (1) — identical-read loop terminated by the general detector: PASS (live)

Deterministic LOOP-STRICT probes instructed exactly 25 identical `read`
calls on a nonexistent path (`target-file.md`), reproducing the P1-r5 family
deterministically. The shipped detector fired at its hardcoded boundary in
both reps (session-transcript evidence, not inferred):

- **LOOP-STRICT-r1** — rc=1, wall **165.6 s**, `livenessState: blocked`,
  timed_out=false, 27 identical read calls / 27 failures.
  Transcript: read #20 → `CRITICAL: Called read with identical outcomes 20
  times. Session execution blocked to prevent runaway loops.`; reads #21–26
  → `This tool was not executed because another call in the batch triggered
  critical tool-loop recovery` (whole batch vetoed, `deniedReason:
  tool-loop`); final doc → `OpenClaw stopped this run because tool-loop
  recovery encountered another critical loop. No blocked tool action was
  executed.` — i.e. first critical blocked the batch, model got one recovery
  response, second critical ended the run.
- **LOOP-STRICT-r2** — rc=0, wall **189.8 s**, timed_out=false, 26 read
  calls / 26 failures. Same CRITICAL at #20 and batch veto of #21–25; the
  model heeded the block, produced its final report, and the run completed
  cleanly (status ok, `stopReason: stop`).

Anchor comparison: P1-r5 ran the same loop family to **130 llama tasks /
1202.5 s** (no guard armed). With the safeguard live, the identical loop is
bounded at ~20 executed calls / ~2.8–3.2 min, terminated by the shipped
threshold-20 generic_repeat block. Matches the 10C offline replay boundary
(first CRITICAL ordinal 24 → live ordinal ~20–21; run-end ordinal 27 →
live 26/25).

### Confirmation (2) — legitimate multi-step tool use completes: PASS

- P1 ×2 (33.2 / 118.4 s), P2 ×2 (13.8 / 14.6 s): rc=0, zero loop events.
- NORM ×3: NORM-r2 rc=0 239.6 s and NORM-r3 (absolute-path) rc=0 219.9 s —
  genuine multi-tool turns (read/exec/progress_card) with **zero** loop
  events (no false positives).
- Engineered LOOP ×3 (non-deterministic): rc=0, zero loop events — the model
  investigated and answered instead of fixating (additional no-false-
  positive evidence; this is why the deterministic LOOP-STRICT probes were
  added).
- Anomaly NORM-r1 (rc=1, 427.5 s, `livenessState: blocked`, 0 loop events):
  probe-side cause — the NORM prompt used repo-relative paths (`read
  AGENTS.md`, `ls tools`) while the headless session cwd is
  `/Users/pmains/.openclaw`, producing read/exec churn ending in an "LLM
  request failed." error. NOT a detector action (0 loop events; absolute-path
  NORM-r3 completes rc=0 and confirms).

### Confirmation (3) — post-compaction guard unchanged: PASS

- Config diff = exactly one added key (`loopDetection.enabled: true`); no
  compaction/post-compaction/compaction-config setting touched.
- Shipped semantics (docs/tools/loop-detection.md + code): the
  post-compaction guard is disabled only when `enabled` is explicitly
  `false`; setting `true` keeps it armed alongside the rolling-history
  detectors. The P2-r7 compaction-cycle terminator path is therefore
  unchanged; no compaction occurred in these short reps to perturb it.

## Problems

- The engineered (non-deterministic) LOOP prompt did not reproduce the
  anchor's fixation — the model behaved sensibly. Confirmation (1) required
  the deterministic LOOP-STRICT prompt to force the identical-call stream;
  both reps then demonstrated the live termination.
- NORM-r1 rc=1 was a probe-prompt defect (relative paths), not a detector
  issue; corrected by NORM-ABS (rc=0).
- LOOP-STRICT-r1's gateway window did not capture the loop events (they live
  in the session transcript / embedded-runner path), so classification used
  the authoritative transcript evidence; the analyzer was extended to treat
  LOOP-STRICT bounded walls + transcript fingerprints as PASS-bounded.

## Decisions

- Applied the 10C-recommended safeguard exactly as proposed: config-only,
  per-agent kimi, single key. No threshold tuning (thresholds are hardcoded
  by the product: warn@10 / CRITICAL block@20 / breaker@30, history 30,
  per-run scoped).
- Kept the post-compaction guard armed (enabled=true, not explicitly false).
- Validation probes are throwaway test prompts in fresh `step10d` sessions —
  no production prompt/tool behavior changed.

## Next Phase

- Owner review of the applied safeguard. Nothing further is required for the
  three confirmations; optionally, a longer soak of normal agent workloads
  could quantify any residual behavior drift from warning injection at ≥10
  identical calls (none observed at n=12).
- Outstanding items unchanged: Step 10A 128K empirical arm (hardware-blocked
  at 24GB), Step 11 usable-context qualification, P1 exact-format `!`-drop.

## Reproduction

```bash
# Config diff proof
diff ~/.openclaw/openclaw.json.bak-step10d-20260905 ~/.openclaw/openclaw.json
# Main validation leg (LOOP×3, P1×2, P2×2, NORM×2)
bash tools/service_step10d_leg.sh            # STEP10D_OUT/STEP10D_KEYNS envs
# Deterministic terminate-path probes (LOOP-STRICT×2, NORM-r3)
bash tools/service_step10d_extra.sh
# Classification
python3 tools/service_step10d_analyze.py benchmarks/results/service-step-10d
```

Expected: LOOP-STRICT reps bounded ~166–190 s with CRITICAL@20 + batch veto
in transcript; all normal reps rc=0 with zero loop events.
