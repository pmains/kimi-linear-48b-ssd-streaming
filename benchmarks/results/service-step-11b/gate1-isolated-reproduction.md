# Step 11B — Gate 1: Isolated-Replica Reproduction Evidence

Date: 2026-09-05 ~21:36 MST (session created 1788669416612)
Status: REPRODUCTION CONFIRMED — gate question answered YES

## Gate question (Pete, 21:05:45 MST)

Does the byte-clean absolute path reproduce as a literal U+2026-corrupted path
in the isolated replica?

## Setup (isolated replica, no production changes)

- Code under test: byte-copy of the INSTALLED openclaw 2026.9.1-beta.1
  (the code the production gateway PID 59990 actually runs), at
  /tmp/oc11b-pkg.
- Isolated gateway: launch.sh (verified clean: 0 literal triple-asterisk, 0 U+2026,
  no token material — gateway reads gateway.auth.token from its cloned
  config file) -> node /tmp/oc11b-pkg/dist/index.js gateway --port 18791,
  env OPENCLAW_HOME/STATE_DIR/CONFIG_PATH -> /tmp/oc11b-env/{home,state,config}.
  Boot 1 installed plugins then exited by design ("refusing to report
  ready... restart"); boot 2 healthy: {"ok":true,"status":"live"}.
- Config: production config cloned, paths rewritten to /tmp/oc11b-env,
  gateway.port -> 18791, llama baseUrl unchanged (127.0.0.1:18080).
- Probe (retained Step 11A discriminator):
  benchmarks/results/service-step-11b/probe-clean.md
  = 126 bytes, 0 U+2026 (verified), content:
  "Please read the file at exactly this path:
  /Users<U+2026>VICE-ROADMAP.md\n\nThen reply with the
  word DONE."

## Routing (why this run is the valid isolated test)

Earlier attempts iso-1 / iso-3 (CLI with OPENCLAW_CONFIG_PATH / OPENCLAW_HOME
only) were silently served by the PRODUCTION gateway (18789): their sessions
appeared in ~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite, and the
isolated store stayed empty. iso-2 proved the CLI honors OPENCLAW_GATEWAY_URL
but refuses it without explicit credentials ("config credentials are
intentionally not reused").

iso-4 therefore used:
- OPENCLAW_GATEWAY_URL=http://127.0.0.1:18791
- OPENCLAW_GATEWAY_TOKEN read at runtime from the cloned config file via
  /tmp/oc11b-env/readtok.py (helper verified clean, 369 B, 0 triple-asterisk, 0 U+2026;
  token value never echoed, never written to a file, never in the transcript)
- runner script /tmp/oc11b-env/run-probe-iso4.sh (verified clean, 1005 B)

Routing check: session agent:kimi:step11b-iso-4 exists ONLY in the isolated
store (/tmp/oc11b-env/state/agents/kimi/agent/openclaw-agent.sqlite,
snapshot /tmp/iso4-snap/main.sqlite). Production store at 21:37 MST contains
only iso-1 and iso-3 (earlier mis-routed runs); it does NOT contain iso-4.

## Byte evidence (isolated transcript DB, session agent:kimi:step11b-iso-4)

sid = 4b0db6e4-12d3-4f17-acbd-6805733ca718, created_via=run,
created_at=1788669416612. 19 events.

- seq=0 type=session            : 0 U+2026
- seq=1 type=message len=379    : u2026chars=1, e280a6bytes=1  <-- KEY
- seq=2 type=thinking_level_change
- seq=3 type=custom
- seq=4+ : model-side churn copying the corrupted path (read / exec
  File-not-found attempts), identical to the production-path family

Stored seq=1 content (repr, U+2026 shown as escape):

  'Please read the file at exactly this path:
  /Users\u2026VICE-ROADMAP.md\n\nThen reply with the word DONE.'

Hex around the corruption:

  3a202f5573657273 e280a6 56494543452d524f41444d41502e6d64 0a0a
  i.e. ": /Users" + e2 80 a6 (U+2026) + "VICE-ROADMAP.md\n\n"

So the clean 126-byte file (full path /Users/pmains/Code/openclaw/kimi/
SERVICE-ROADMAP.md, 0 U+2026) was stored as a seq=1 user message whose path
token is head-6 "/Users" + U+2026 + tail "VICE-ROADMAP.md" — the exact 11A
signature — in a FRESH isolated environment running the identical installed
code and a clone of the production config.

## Gate answer

YES — the clean absolute path reproduces as a literal U+2026-corrupted path
in the isolated replica. The corruption therefore lives in the installed
OpenClaw code path (message ingestion -> transcript store, pre-model), not in
accumulated production gateway state, plugins, environment, or configuration
idiosyncrasies of the live gateway. Code-identical + config-cloned + fresh
state => reproduces.

## Scope note

Per the gate instruction, this turn stops here: no instrumentation, no
tracing, no fix, no production change. Next authorized phase (Step 11B
mandate) is the boundary trace to identify the first corrupting function,
then the smallest fix, then validation (clean-file repro, G-style path cases,
long-message regression), roadmap update, report, stop for review.
