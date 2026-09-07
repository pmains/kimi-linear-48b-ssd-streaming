# Step 10C Report — Localize and Bound the Runaway-Tool-Loop Liveness Family

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST)
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Status

**PASS — COMPLETE (determination). STOPPED for review. No production patch.**

No context-size, sampler, prompt, tool-behavior, or llama-server change was
made. The deliverable is the localization + the smallest-safeguard design,
validated offline by replaying both retained real anchor streams through the
actual shipped OpenClaw detector. Applying the safeguard (a config-only
enable) is deferred to owner review.

## Objective

At the aligned 64K configuration (Step 10B), determine:

1. why repeated tool calls continue in the runaway family;
2. which loop/compaction guards currently fire;
3. where a safe termination/recovery boundary belongs.

Anchors: 10A P2-r7 (882.5 s compaction-loop-guard abort) and 10B P1-r5
(1202.5 s client timeout, 130 llama tasks).

## Localization results

Both anchors are the **same loop**:

- The model emits `read` of a nonexistent `/Users…DMAP.md` path — the path
  contains a literal U+2026 ellipsis (an apparent truncation artifact).
- Every attempt is byte-identical and every failure is byte-identical
  ("File not found: /Users…DMAP.md."), so the model receives no
  discriminating feedback and re-emits the identical `(tool, args, result)`
  triple indefinitely.
- P1-r5: 129 tool calls = 117 identical `read` failures + 10 exec + 1
  create_goal + 1 progress_card. 117/117 read calls share one distinct
  args variant and one distinct result.
- P2-r7: 19 tool calls = 12 read (10× identical broken-path), 4 exec,
  2 process, 1 sessions_history; max in-window identical streak 8.

**1. Why repeated calls continue.** Nothing in the runtime counts identical
no-progress triples except the post-compaction guard, which arms **only in
the immediate aftermath of an auto-compaction**. At aligned 64K, prompt
context stays ~9–24K tokens (`shouldCompact=false` in P1-r5, budget 64K /
reserve 20K), so compaction never fires, the guard never arms, and the loop
runs to the client timeout.

**2. Which guards currently fire.** Only the post-compaction guard (active
by default when `tools.loopDetection.enabled` is not explicitly `false`). It
fired correctly in P2-r7 after auto-compaction succeeded (CRITICAL
`compaction_loop_persisted` abort after 3 identical post-compaction reads).
The shipped general rolling-history detector (`tools.loopDetection` → dist
`tool-loop-detection-*.js`, `detectToolCallLoop`) is **disabled by default**
(`DEFAULT_LOOP_DETECTION_CONFIG.enabled: false`), openclaw.json has no
`tools.loopDetection` block, and every general-detector admission path
early-returns when `ctx.loopDetection?.enabled !== true` — so it was inert in
both anchors. The idle-timeout cost-runaway breaker is paid-provider-only;
the provider fetch timeout (30 min) is a backstop, not a loop guard.

**3. Where the safe boundary belongs (shipped semantics).** With
`tools.loopDetection.enabled: true`, the rolling-history detectors watch the
last `historySize: 30` calls per run (run-scoped when a runId is present):

- warning at 10 identical calls / identical no-progress outcomes;
- CRITICAL block at 20 identical no-progress outcomes (`generic_repeat`;
  `global_circuit_breaker` at 30; `unknown_tool_repeat` at 10; ping-pong and
  known-poll detectors also active);
- first critical blocks the whole tool batch *before execution*; the model
  gets one more response with normal tools; a second critical in the same
  run ends the run. Fresh runs reset the allowance.

Config schema is zod-strict with **only** `enabled` configurable
(`agents.entries.<id>.tools.loopDetection` or global `tools.loopDetection`);
thresholds are hardcoded in `resolveLoopDetectionConfig` (confirmed by the
dead-config-keys test listing historySize/warningThreshold/detectors/
postCompactionGuard as dead keys).

## Changes

- `service-progress/step-10c-loop-liveness-design.md` — pre-registered
  design (objective, anchors, localization, proposed safeguard, offline
  validation method, acceptance criteria, STOP point).
- `tools/service_step10c_replay.mjs` — offline replay driver. Imports the
  **actual shipped dist module**
  (`/opt/homebrew/lib/node_modules/openclaw/dist/tool-loop-detection-CWrUtzrR.js`)
  and replays each retained real call stream with `{ enabled: true }`,
  mirroring the runtime admission/outcome flow (pre-call
  `detectToolCallLoop`; post-call `recordToolCall` +
  `recordToolCallOutcome` with the real observed result/error text).
- `benchmarks/results/service-step-10c/anchor-call-streams.json` — retained
  per-call streams for both anchors (tool, args, result text, isError,
  runId, timestamps) extracted from the agent session store.
- `benchmarks/results/service-step-10c/replay/replay-results.json` +
  `replay-run.txt` + `replay-stderr.log` — replay evidence.
- `SERVICE-ROADMAP.md` §10C — updated to COMPLETE — PASS (determination);
  STOPPED for review.
- This report.

No production file was modified (openclaw.json untouched; no gateway restart;
no llama-server change).

## Results

Offline replay of both real streams through the actual shipped detector with
`enabled: true`:

| Anchor | Observed calls | First warning | First CRITICAL | Run-end (2nd critical, per docs) |
|---|---|---|---|---|
| P1-r5 (10B) | 129 | ordinal 13, generic_repeat count=10, +122.1 s | ordinal 24, generic_repeat count=20, +235.1 s ("Called read with identical outcomes 20 times") | ordinal 27, +285.6 s |
| P2-r7 (10A) | 19 | none | none | — |

Interpretation:

- **P1-r5 (the unguarded runaway):** with the general detector enabled, the
  first CRITICAL would have fired at call 24 (~+235 s) and the run would have
  ended at the second critical (~+286 s) — vs the observed 130 calls /
  1202.5 s client timeout. The 117-call read loop is therefore bounded at the
  shipped threshold of 20 identical no-progress outcomes, exactly as
  designed, roughly 4–5× earlier than today's terminator and with a defined
  blocked outcome instead of a silent timeout.
- **P2-r7 (the compaction-cycle variant):** 0 warnings / 0 criticals — its
  in-window identical streak (≤8) stays below the critical threshold, so the
  general detector would NOT fire there. This confirms (a) the
  post-compaction guard — already active by default — is the correct
  terminator for the compaction-cycle variant, and (b) enabling the general
  detector does not disturb or duplicate that path (no false-positive risk on
  this anchor).
- The two shipped guards are complementary: post-compaction for
  compaction-cycles, rolling-history for no-compaction runaways. Today only
  the first is armed; the second is the missing boundary for the aligned-64K
  configuration.

Acceptance criteria (design doc) — met:

- P1-r5 critical `generic_repeat` fires at ordinal 24 (+235 s), well inside
  the 130-call run (expected ≈20–24; observed 24). PASS.
- P2-r7 stays below threshold (0/0), consistent with the post-compaction
  guard remaining the terminator there. PASS.

## Problems

- The general detector is disabled by default and was never enabled on this
  path; nothing in the shipped defaults would have caught P1-r5. This is a
  configuration gap, not a code bug.
- Replay fidelity note: outcome hashing used the real recorded result/error
  text; the 117 loop calls are byte-identical, so hash equality (the only
  thing the detector consumes for this loop) is reproduced exactly.
- The empirical question "would the model heed the warning at count 10 and
  stop before the count-20 block" is not answerable offline — the warning is
  model-dependent. The block at 20 is the deterministic bound regardless.

## Decisions

- **Smallest safeguard = config-only enable of the shipped general loop
  detector**, scoped to the kimi agent service:
  `agents.entries.kimi.tools.loopDetection.enabled: true` (or global
  `tools.loopDetection.enabled: true`). No code, no sampler/prompt/tool/
  contextWindow change. The post-compaction guard stays armed (it is only
  disabled by an explicit `false`).
- Not building a custom liveness loop guard: the shipped, documented
  machinery (docs/tools/loop-detection.md, recommended for smaller models)
  already implements the exact boundary needed, with hardcoded thresholds and
  run-scoped history.
- **Not applied in this step** — STOP for review per authorization.

## Next Phase

- Owner review of the proposed safeguard. If approved, apply the config-only
  change (gateway restart required, supervisor pattern from
  `tools/service_step10b_supervise.sh`), then optionally re-run a retained
  read-loop workload or the Step 10A/10B liveness leg to demonstrate the
  guard firing live.
- The residual exact-format P1 issue (`!`-drop at E2) and the Step 10A 128K
  empirical arm remain open as before; this step does not change their
  status.

## Reproduction

```bash
# 1) Extract per-call streams (requires the agent session-store copy):
#    (streams already retained in benchmarks/results/service-step-10c/anchor-call-streams.json)

# 2) Offline replay through the actual shipped detector:
node tools/service_step10c_replay.mjs \
  benchmarks/results/service-step-10c/anchor-call-streams.json \
  benchmarks/results/service-step-10c/replay/replay-results.json
```

Expected: P1-r5 first warning ordinal 13 (+122 s), first CRITICAL ordinal 24
(+235 s), second critical ordinal 27 (+286 s); P2-r7 0 warnings / 0
criticals.

Evidence: `benchmarks/results/service-step-10c/`; design:
`service-progress/step-10c-loop-liveness-design.md`.
