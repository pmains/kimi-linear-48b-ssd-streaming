# Stage 6A.6 Report

## Status

PARTIAL (acceptance) — preflight PASS (2026-08-16); acceptance run FAIL / incomplete (2026-08-16)

## Update (2026-08-16): full acceptance run executed — FAIL / incomplete

- Full acceptance experiment run 12:51–13:11 MST on a fresh llama-server PID
  (58186) with the dev gateway (diagnostics.enabled=false) and the SQLite
  live-run monitor. Details:
  `service-progress/step-06a6-acceptance-run-2026-08-16.md`.
- Prefill (runId `eac84423-...`): auto-compacted (compactionCount 1); model
  call 1 = 331-token prompt (19.5 s eval); model call 2 = 22,241-token prompt,
  COLD eval (1088.14 s, no cached_tokens).
- llama-server crashed right after the 22,241-token eval:
  `ggml_new_object: not enough space in the context's memory pool (needed
  1049168, available 1048944)` -> `GGML_ASSERT(obj_new)` -> SIGABRT. The
  compute pool was short by 224 bytes for the post-eval graph.
- Followup (runId `856bb37c-...`) errored in 3 s: no live server (gateway
  localService auto-spawn of the frozen bundle fails on
  `@rpath/libllama-server-impl.dylib`); never reached a model call, so
  `cacheRead` is unmeasurable.
- Monitor verified working: followup live marker found in SQLite
  `trajectory_runtime_events` (seq 70, waitedMs 3204). Prefill marker pending
  explained: the SQLite store flushes at run finalization (created_at = event
  timestamp), so rows appear at finalize, not mid-run.
- Acceptance criteria: cacheRead>0 NOT MEASURABLE; no telemetry corroboration
  (all large evals cold); no material speedup. Recorded as an incomplete
  cache-acceptance observation. llama.cpp NOT patched (roadmap STOP point).
- Next boundary: expert-streamer/ggml compute-buffer accounting crash at large
  prompt sizes.

---

## Original report

## Update (2026-08-16): monitor gap closed, preflight PASS

- The Stage 6A.6 harness monitor now reads the authoritative SQLite
  `trajectory_runtime_events` table in the per-agent state database
  (`<OPENCLAW_STATE_DIR>/agents/<agentId>/agent/openclaw-agent.sqlite`) via
  `node:sqlite`, matching on the accepted `run_id`; `session.started` /
  `prompt.submitted` are the live marker, `trace.artifacts` / `session.ended`
  the terminal state. `.trajectory.jsonl` scanning remains only as fallback.
- Dev gateway runs with `diagnostics.enabled=false` (the supported
  stuck-session-recovery gate) and the provider `timeoutSeconds` removal
  already in effect.
- Stage 6A.6 preflight PASSED the integration check (dev gateway -> accepted
  runId `eb4e791b-...` -> llama-server received request -> SQLite observed
  live runtime events for the runId while live). Details:
  `service-progress/step-06a6-sqlite-live-monitor-preflight.md` and
  `dev-openclaw/state/stage6a6-preflight/2026-08-16T19-33-25-138Z/preflight-evidence.json`.
- The run terminated after the observations when the dev gateway received an
  external SIGTERM; recorded as post-observation, not stuck-session recovery.
- Full Stage 6A.6 acceptance (canonical cold prefill -> warm ordinary turn on
  the same fresh llama-server PID) remains the next step; not run per
  directive.

---

## Original report

## Objective

Run the canonical 27,554-token invariant-prefix prefill to completion, then
create a brand-new ordinary Caveman session on the same fresh llama-server PID
and measure whether the ordinary turn substantively reuses the invariant prefix.

## Changes

- `openclaw-src/scripts/dev/stage6a6-acceptance-harness.ts` now dispatches the
  Stage 6A.6 turns through the lower-level `agent` RPC with `expectFinal:
  false`, while preserving durable completion monitoring.
- `SERVICE-ROADMAP.md` and the progress reports now record the separate
  `sessions_send` admission issue as an integration boundary.

## Results

- The harness now loads the isolated dev config explicitly via
  `OPENCLAW_CONFIG_PATH=/Users/pmains/Code/openclaw/kimi/dev-openclaw/config/openclaw.json`
  and `OPENCLAW_STATE_DIR=/Users/pmains/Code/openclaw/kimi/dev-openclaw/state`.
- Manual trajectory inspection already proves durable live-run observability:
  the active session `.trajectory.jsonl` contains `session.started` and
  `prompt.submitted` for the dispatched run before `model.completed`.
- The harness still returns `pending` for that same live marker, so the
  polling path remains unresolved and should not be read as a cache miss.
- The same-server ordinary replay artifact shows substantive cache reuse:
  `cached_tokens=24576`, `prompt_n=3149`, and `prompt_ms=211371.516`, versus
  the uncached 27,809-token prefill at `prompt eval time = 1882854.65 ms`.
- The comparison trail is anchored by
  `dev-openclaw/state/stage6a4-reduction/baseline_300s.body.txt`,
  `dev-openclaw/state/stage6a4-reduction/results.tsv`, and
  `dev-openclaw/state/stage6a4-token-prefix-compare.json`.
- The preflight smoke still started a fresh temporary `llama-server` on port
  18080, confirmed `/health`, and proved the direct `agent` dispatch path
  returns an accepted run id.
- The smoke also proved the provider request reaches `llama-server` PID 27194
  before shutdown.
- No canonical acceptance prefill was run.

## Problems

- The previous user-level config-validation failure is no longer the blocker.
- The remaining blocker is the automated durable-monitor prerequisite: the
  harness can prove accepted dispatch and provider reach, but still cannot
  positively identify the live run before completion.
- The separate `sessions_send(..., timeoutSeconds: 0)` admission issue remains
  recorded as a harness/OpenClaw integration boundary.

## Decisions

- Do not treat the harness monitor gap as evidence against canonical prefix
  reuse.
- Preserve the server-state cleanup boundary and the durable-monitor gap as the
  authoritative stop point for this run.
- Keep Stage 6A.6 harness-only and leave runtime/cache behavior unchanged.
- Treat the cache-reuse signal as established and the live-marker monitor as
  the remaining harness gap.

## Next Phase

- Add or identify an early durable run-id monitor that can observe the live run
  before completion, or accept that the preflight cannot be upgraded to PASS
  without waiting for terminal trace artifacts.
- Once that monitor exists, rerun Stage 6A.6 from a genuinely fresh
  `llama-server` PID and collect the terminal prefill/follow-up telemetry.

## Reproduction

```bash
pnpm -C openclaw-src exec tsx scripts/dev/stage6a6-acceptance-harness.ts
```
