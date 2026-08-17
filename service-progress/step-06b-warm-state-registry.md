# Stage 6B Report — Warm-State Registry

## Status

PASS (implementation + unit tests). End-to-end CLI acceptance (6C/6D) is next;
the registry itself is implemented, wired into the canonical prefill seam, and
fully unit-tested.

## Objective

Give OpenClaw a durable answer to "what does it believe is warm?" for the
caveman/Kimi bootstrap, keyed by `(agent_id, model_id, bootstrap_fingerprint)`:

    (caveman, kimi-linear-48b, bootstrap-fingerprint)
      -> status        COLD | PREFILLING | READY | STALE | FAILED
      -> server_pid    llama-server PID that produced READY
      -> cached_tokens cache-read tokens observed at completion
      -> warmed_at     epoch ms when READY was recorded

State machine (per `SERVICE-ROADMAP.md` stage 6B):

    COLD
     ↓
    PREFILLING
     ↓
    READY

    READY -> STALE when the bootstrap fingerprint changes
    READY -> COLD  when the inference-server PID changes
    PREFILLING -> FAILED
    FAILED -> PREFILLING on explicit retry

The PID rule is load-bearing: stage 5B established that restart persistence is
unsupported in this configuration, so a server restart must invalidate READY.
Similarly, READY must be tied to the *actual* llama-server PID — new PID → COLD.

## Changes

`openclaw-src` (dev tree; commit `c9c6ff96d93`):

- **`src/agents/warm-state-registry.ts`** (new) — the registry itself:
  - `computeBootstrapFingerprint({ systemPrompt, tools })` — sha256 over a
    stable serialization (sorted keys, undefined dropped) of the provider-ready
    system prompt plus the effective tool catalog. Any bootstrap change flips
    the fingerprint → READY becomes STALE.
  - `resolveLiveServerPid(env)` — reads `KIMI_PIDFILE` (default
    `/tmp/kimi-llama-server.pid`), parses the PID, and verifies liveness with
    `process.kill(pid, 0)`. Missing/unparsable/dead → `undefined` (no live
    server can validate READY).
  - `beginPrefill` / `completePrefill` / `failPrefill` — transition functions
    with validation (complete/fail require PREFILLING; begin is allowed from
    COLD/FAILED/STALE/READY/PREFILLING).
  - `resolveWarmState(...)` — lazy invalidation on read: READY with a changed
    fingerprint → STALE (persisted); READY whose serverPid differs from the
    live PID → COLD (persisted); READY with no live server → COLD; READY under
    a different fingerprint than requested → STALE.
  - Persistence — versioned JSON at `<stateDir>/warm-state/registry.json`
    (`version: 1`, atomic tmp+rename writes, corrupted file → empty registry).
    `stateDir` defaults to `resolveStateDir(process.env)` and is overridable
    per call, which keeps tests hermetic.

- **`src/agents/simple-completion-runtime.ts`** — `prefillWithStableBootstrapForAgent`
  now records the prefill lifecycle: compute fingerprint from the prepared
  stable bootstrap, `beginPrefill` before dispatch, `completePrefill` on
  success (with `resolveLiveServerPid(process.env)` and
  `assistant.usage.cacheRead`), `failPrefill` on error.
  - Opt-in: recording only happens when `OPENCLAW_WARM_STATE_REGISTRY=1` or an
    explicit `warmStateDir` is passed. Ordinary callers and unit tests are
    byte-unchanged (inert by default).
  - Registry bookkeeping errors are caught and logged/surfaced, never allowed
    to fail the prefill itself (the prefill's job is warming the server; the
    registry is bookkeeping).

- **`src/agents/warm-state-registry.test.ts`** (new) — 17 unit tests in the
  unit-fast shard: fingerprint determinism + sensitivity, stable serialization,
  PID resolution (live / missing / unparsable / dead), every state transition,
  both invalidation rules, the retry path, persistence round-trip, and
  corrupted-file tolerance.

## Results

- `node scripts/run-vitest.mjs run --config test/vitest/vitest.unit-fast.config.ts src/agents/warm-state-registry.test.ts`
  → **17/17 passed** (236ms).
- Existing prefill-seam tests (regression):
  `node scripts/run-vitest.mjs run --config vitest.config.ts src/agents/simple-completion-runtime.test.ts`
  → **54/54 passed** (both agents-core and agents-support project shards).
- `tsc --noEmit` clean for the touched files.

## Problems

- The repo's unit-fast test shard discovers candidates from `git ls-files` and
  content-classifies them; a new untracked test file is invisible until staged,
  and direct `node:fs` imports disqualify a file from the fast shard. Resolved
  by staging the new files and using the existing `privateFileStore`/
  `withTempDir` helpers instead of raw `node:fs` in the test.

## Decisions

- **One entry per (agent, model, fingerprint)** — the registry key includes the
  fingerprint, so a changed bootstrap is a *different* key whose resolution
  marks the old READY sibling STALE. No destructive overwrite of history.
- **Inert by default** — the seam only touches the registry under
  `OPENCLAW_WARM_STATE_REGISTRY=1` or an explicit `warmStateDir`, preserving
  established runtime behavior for all existing callers (project rule).
- **Registry never fails the prefill** — bookkeeping errors are caught and
  surfaced, not thrown into the inference path.
- **JSON file, not SQLite** — this is small, rarely-written, mostly-read state;
  a versioned JSON document with atomic writes is the minimal mechanism.
  Migration to the state DB can happen later if a query surface demands it.

## Next Steps

- **6C — CLI and observability**: `openclaw prefill <agent> [<model>]`,
  `openclaw prefill status [<agent>]`, `--json`. The CLI drives
  `prefillWithStableBootstrapForAgent` with the registry enabled and reads
  status through `resolveWarmState` (which already applies the PID/fingerprint
  invalidation rules on read). Progress should come from actual inference
  progress; the registry provides `status`, `server_pid`, `cached_tokens`,
  `warmed_at` for the status surface.
- **6D — acceptance**: restart server → `openclaw prefill caveman
  kimi-linear-48b` → READY with matching PID → new ordinary Caveman session →
  prefix reuse (`cacheRead > 0`), small suffix, latency well below cold.
- **6E — invalidation tests**: mutate a fingerprinted input (STALE) and
  restart the server (COLD), then re-prefill back to READY.

## Reproduction

```bash
cd openclaw-src
node scripts/run-vitest.mjs run --config test/vitest/vitest.unit-fast.config.ts \
  src/agents/warm-state-registry.test.ts
node scripts/run-vitest.mjs run --config vitest.config.ts \
  src/agents/simple-completion-runtime.test.ts
```

Registry file (when enabled):

    <stateDir>/warm-state/registry.json
    # dev harness: dev-openclaw/state/warm-state/registry.json

## Follow-up (2026-08-16, after seam commit)

The 6B follow-up seam WIP (`attempt-*.ts` family, `stable-bootstrap-context.ts`,
`addGenerationPrompt` transport plumbing) is committed as `a2b7c96075a`
(openclaw-src). `tsgo:core` clean; 74/74 seam-affected unit tests pass. This
makes the tree self-consistent: `cf4f6bde255` (6C) imports
`attempt-stable-bootstrap-prefill`/`stable-bootstrap-context`, which were
untracked before this commit. The only remaining uncommitted dev-tree changes
are the 6A.4 boundary-trace instrumentation (14 files, diagnostic only).

## Artifacts

- `openclaw-src/src/agents/warm-state-registry.ts`
- `openclaw-src/src/agents/warm-state-registry.test.ts`
- `openclaw-src/src/agents/simple-completion-runtime.ts` (seam wiring)
- Commit: `c9c6ff96d93` (openclaw-src dev tree); seam completion
  `a2b7c96075a`
