# Step 10C Design — Localize and Bound the Runaway-Tool-Loop Liveness Family

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Objective

At the aligned 64K configuration (Step 10B), localize why repeated tool calls
continue in the runaway family, determine which loop/compaction guards fire,
and determine where a safe termination/recovery boundary belongs.

Explicit constraints (from authorization): **do NOT change context size,
sampler, prompts, or normal tool behavior.** Design + test the *smallest*
liveness safeguard, retain before/after evidence, update SERVICE-ROADMAP.md
(Step 10C), write the report, and **STOP for review before any production
patch.**

## Anchors (retained evidence)

| Anchor | Where | Shape | Terminator today |
|---|---|---|---|
| P2-r7 | `benchmarks/results/service-step-10a/64k/P2/P2-r7.*` | 19 tool calls: 12 read (10x byte-identical `/Users…DMAP.md` failures), 4 exec, 2 process, 1 sessions_history; max in-window identical streak 8 | 882.5s auto-compaction → post-compaction guard abort (`compaction_loop_persisted`, 17 llama tasks) |
| P1-r5 | `benchmarks/results/service-step-10b/64k/P1/P1-r5.*` | 129 tool calls: 117x byte-identical `read` of nonexistent `/Users…DMAP.md` (literal U+2026 in path), 10 exec, 1 create_goal, 1 progress_card; 117 identical "File not found" errors | 1202.5s client timeout (130 llama tasks; `doc_status: timeout`, liveness paused) |

Per-call streams retained: `benchmarks/results/service-step-10c/anchor-call-streams.json`.

## Localization (completed before this design)

1. **Why repeated tool calls continue.** The model fixates on a malformed,
   nonexistent path (`/Users…DMAP.md` — contains a literal U+2026 ellipsis, an
   apparent truncation artifact). Every attempt returns a byte-identical
   "File not found" error, so the model receives no discriminating feedback
   and re-emits the identical `(tool, args, result)` triple indefinitely.
   Nothing in the runtime counts identical no-progress triples *except* the
   post-compaction guard, which arms only in the immediate aftermath of an
   auto-compaction. At aligned 64K the context stays ~9–24K tokens
   (`shouldCompact=false` in P1-r5), so compaction never fires, the guard
   never arms, and the loop runs to the client/provider timeout.

2. **Which guards currently fire.** On this path only the post-compaction
   guard is active (default-on when `tools.loopDetection.enabled` is not
   explicitly `false`). It fired correctly in P2-r7 after auto-compaction.
   The general rolling-history detector (`tools.loopDetection`, shipped in
   dist as `tool-loop-detection-*.js`, `detectToolCallLoop`) is **disabled by
   default** and openclaw.json has no `tools.loopDetection` block → it is
   inert in both anchors. The idle-timeout cost-runaway breaker is
   paid-provider-only; provider fetch timeout (30 min) is a backstop, not a
   loop guard.

3. **Where the safe termination boundary belongs (shipped semantics).**
   With `tools.loopDetection.enabled: true` the rolling-history detectors
   watch the last `historySize: 30` calls per run:
   - warning at 10 identical calls / identical no-progress outcomes;
   - **critical block at 20** identical no-progress outcomes
     (`generic_repeat`, threshold hardcoded; also `global_circuit_breaker`
     at 30; `unknown_tool_repeat` at 10);
   - first critical blocks the whole tool batch *before* execution; the model
     gets one more response with normal tools; a second critical in the same
     run ends the run. Fresh runs reset the allowance.
   In P1-r5 this boundary would fire at ~call 20–24 (wall ≈ 2–3 min per
   retained fetch timing) instead of 130 calls / 1202.5s. P2-r7's terminator
   (post-compaction guard) is preserved because that guard stays armed when
   `enabled` is `true`.

   Config schema is deliberately minimal: `tools.loopDetection.enabled`
   (boolean, zod-strict) at global `tools.loopDetection` or per-agent
   `agents.entries.<id>.tools.loopDetection`. Thresholds are hardcoded in
   `resolveLoopDetectionConfig` (dead-config-keys test confirms
   historySize/warningThreshold/detectors are not configurable).

## Proposed smallest safeguard (NOT applied — for review)

Config-only enable of the shipped general loop detector, scoped to the kimi
agent service (production change deferred to Pete's authorization):

```json5
agents: {
  entries: {
    kimi: {
      tools: {
        loopDetection: { enabled: true },
      },
    },
  },
},
```

No code change, no sampler/prompt/tool-behavior/contextWindow change. It arms
the rolling-history detectors; the post-compaction guard remains armed.

## Validation method (offline, no production change)

Replay both retained real call streams through the **actual shipped detector
module** (`/opt/homebrew/lib/node_modules/openclaw/dist/tool-loop-detection-CWrUtzrR.js`,
importable in node; exports `detectToolCallLoop`/`recordToolCall`/
`recordToolCallOutcome`) with `{ enabled: true }` and the real per-run scoping.

Driver: `tools/service_step10c_replay.mjs`

For each call in stream order, mirror the runtime admission/outcome flow:
1. Pre-call: `detectToolCallLoop(state, name, args, {enabled:true}, {runId})`
   → record first warning / first critical firing point.
2. Post-call: `recordToolCall` + `recordToolCallOutcome` with the real
   observed result/error text (byte-identical across the loop → identical
   outcome hashes → identical to what the runtime would count).

Acceptance:
- P1-r5: detector reports a critical `generic_repeat` block at an ordinal
  well inside the 130-call run (expected ≈ call 20–24, matching the
  hardcoded threshold 20 within a 30-call window), i.e. a bounded wall of
  roughly 2–3 min vs the observed 1202.5s.
- P2-r7: detector behavior consistent — its in-window identical streak (≤8
  pre-compaction) stays below the critical threshold, confirming the
  post-compaction guard (already active) is the correct terminator for the
  compaction-cycle variant and that enabling the general detector does not
  disturb that path.
- Evidence retained under `benchmarks/results/service-step-10c/replay/`.

## STOP point

After replay + report + SERVICE-ROADMAP.md update: **STOP for review. No
production patch** (no openclaw.json change, no gateway restart, no
llama-server change).
