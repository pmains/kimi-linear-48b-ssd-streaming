# Stage 6B Report — Warm-State Registry

## Status

`PASS` (registry implemented and verified; full prefill-loop acceptance is
Stage 6D)

## Objective

Give OpenClaw an authoritative, machine-readable answer to "is Caveman warm?"
instead of relying on experiments. Implement the registry keyed by
`(agent_id, model_id, bootstrap_fingerprint)` with the exact state machine
from SERVICE-ROADMAP.md Stage 6B:

    COLD -> PREFILLING -> READY
    READY      -> STALE when the bootstrap fingerprint changes
    READY      -> COLD  when the inference-server PID changes
    PREFILLING -> FAILED
    FAILED     -> PREFILLING on explicit retry

The PID rule is load-bearing: Stage 5B proved restart persistence is
unsupported in this configuration, so a server restart (new PID) must
invalidate prior READY state.

## Environment

- Repo: `/Users/pmains/Code/openclaw/kimi`
- Live server: PID `64991`, frozen bundle `runtime/live/bin/llama-server`,
  `runtime/live/COMMIT` = `0a6b2df63`, port 18080, `/health` ok
  (read-only probe; server not restarted or modified)
- Fingerprint source: `dev-openclaw/state/stage6a4-request-body.json` — the
  captured exact ordinary Caveman request body (system prompt + 53 tool
  declarations + `tool_choice: auto`)
- Tool: `tools/warm_state.py` (Python 3.14, stdlib only)
- Store: `runtime/state/warm-state.json` (schema `warm-state/v1`)

## Changes

- Added `tools/warm_state.py`:
  - `status` — print registry entry plus live probe info
  - `probe` — discover live `llama-server` PID (pidfile, then pgrep of the
    frozen live bundle) and apply the state machine transitions, then write
    the store
  - `begin-prefill` — explicit transition to PREFILLING (also the retry path
    from FAILED); binds `server_pid` to the live server
  - `ready --pid N --cached-tokens N` — PREFILLING -> READY, records
    `server_pid`, `cached_tokens`, `warmed_at`
  - `fail --reason` — PREFILLING -> FAILED
  - `reset` — any -> COLD
  - `fingerprint` — compute the current bootstrap fingerprint
  - `--json` for machine-readable output; `--registry` to operate on an
    alternate store; `--fingerprint-source` to point at a different
    canonical bootstrap body
  - Bounded transition history (last 20, with timestamps and reasons) is
    kept on each entry for observability
- Seeded `runtime/state/warm-state.json` via `probe` (no server interaction
  beyond read-only PID discovery):
  `(caveman, kimi-linear-48b, sha256:360e19a7...)` -> COLD, `live_pid` 64991.
- SERVICE-ROADMAP.md Stage 6B updated with the implementation note.

## Tests

All transition tests ran against a scratch store
(`/tmp/warm-state-test.json`); the real store stayed seeded COLD.

1. COLD -> PREFILLING: `begin-prefill` — PASS
2. PREFILLING -> READY: `ready --pid 64991 --cached-tokens 22410` — PASS
   (records `server_pid` 64991, `warmed_at`, history)
3. READY stable: `probe` with live PID 64991 and unchanged fingerprint —
   PASS (no transitions, status stays READY)
4. READY -> COLD on PID change: bound READY to pid 12345, `probe` with live
   64991 — PASS ("inference-server PID changed 12345 -> 64991; READY
   invalidated")
5. READY -> STALE on bootstrap change: `--fingerprint-source` pointing at a
   modified copy of the request body (appended marker to system prompt),
   `probe` — PASS ("bootstrap fingerprint changed; READY marked STALE",
   `fingerprint_match: false`)
6. PREFILLING -> FAILED: `fail --reason "test failure"` — PASS
7. FAILED -> PREFILLING on retry: `begin-prefill` — PASS
8. `status --json` on the real store — PASS: status COLD, `live_pid` 64991,
   fingerprint match true

## Measurements

- Bootstrap fingerprint (current canonical body):
  `sha256:360e19a74470e12fc5d86367bc755b0e1fd5648c45ef1b021eb83cf6353ef6de`
- Real store state after seeding: status COLD, `server_pid` null,
  `cached_tokens` null, `warmed_at` null, `live_pid` 64991
- No server restart, no configuration change, no inference run.

## Results

- The registry exists, is seeded from live state, and is queryable by any
  OpenClaw agent: `python3 tools/warm_state.py status --json`.
- The full state machine is implemented and verified, including both
  invalidation rules Pete called out: new PID -> COLD, changed bootstrap ->
  STALE.
- Honest initial state: COLD. No prefill has been recorded for the current
  live PID, so the registry correctly reports "not warm" rather than
  assuming warmth from earlier experiments.

## Problems

- None blocking. Minor CLI note: `--json` must precede the subcommand
  (e.g. `warm_state.py --json status`) since it is a global flag.

## Decisions

- Fingerprint = sha256 over the canonical ordinary-request bootstrap body
  (system prompt + tools + `tool_choice`). This is the same serialization
  shape Stage 6A.4/6A.5 proved is the invariant prefix source. If that body
  changes, STALE is the correct outcome.
- Live PID discovery is deliberately read-only (pidfile + `pgrep -f
  runtime/live/bin/llama-server`); no `/health` call is required for
  transition logic, keeping probe side-effect free.
- PREFILLING -> FAILED is also applied by `probe` if the recorded prefill
  target PID is no longer the live server (server died mid-prefill).

## Next Steps

- Stage 6C: CLI/observability — `openclaw prefill ...` commands backed by
  this registry, plus real inference-progress reporting.
- Stage 6D: acceptance — restart server, `openclaw prefill caveman
  kimi-linear-48b`, verify READY + PID match, new Caveman session reuses
  prefix (`cacheRead > 0`), no fallback/abort/restart.
- Stage 6E: invalidation tests — bootstrap mutation -> STALE, server restart
  -> COLD, manual prefill -> READY.

## Reproduction

    python3 tools/warm_state.py fingerprint
    python3 tools/warm_state.py status --json
    python3 tools/warm_state.py probe
    # transition demo on a scratch store:
    T=/tmp/warm-state-test.json
    python3 tools/warm_state.py --registry $T begin-prefill
    python3 tools/warm_state.py --registry $T ready --pid 64991 --cached-tokens 22410
    python3 tools/warm_state.py --registry $T probe

## Artifacts

- `tools/warm_state.py`
- `runtime/state/warm-state.json`
- `SERVICE-ROADMAP.md` (Stage 6B implementation note)
