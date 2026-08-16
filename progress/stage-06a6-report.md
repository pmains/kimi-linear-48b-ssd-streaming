# Stage 6A.6 Report

## Status

PASS (rerun 2026-08-16 on the fixed binary — see
`progress/stage-06a6-temp-ctx-pool-fix-report.md`)

## Objective

Run the canonical invariant-prefix prefill to completion, then create a
brand-new ordinary Caveman session on the same fresh llama-server PID and
measure whether the ordinary turn substantively reuses the invariant prefix.

## Changes

- `openclaw-src/scripts/dev/stage6a6-acceptance-harness.ts` dispatches the
  Stage 6A.6 turns through the lower-level `agent` RPC with `expectFinal:
  false`, while preserving durable completion monitoring.
- `SERVICE-ROADMAP.md`,
  `service-progress/step-06a6-invariant-prefix-cache-acceptance.md`,
  and this report record the `sessions_send` admission issue as a separate
  integration boundary.
- llama.cpp `0a6b2df63`: fixed the ggml compute-pool shortfall that blocked
  the previous run (streamer temp-context pool recycle-on-overflow; see the
  dedicated fix report for the full characterization).

## Results (rerun, 2026-08-16, fixed binary 0a6b2df63)

- Prefill turn (runId `cfe69649-e171-4688-bca0-442d3104c462`): 22,409-token
  cold eval completed — `prompt eval time = 1348704.81 ms / 22409 tokens
  (16.62 t/s)` — with **zero aborts**. This is the exact scenario that
  SIGABRT'd the previous run (`ggml_new_object: not enough space ... needed
  1049168, available 1048944`, 224 B short).
- Ordinary followup turn (runId `48aae132-8e6b-41ec-993c-e601946cab67`):
  usage `input: 86, output: 2, cacheRead: 22410` — prefix reuse confirmed.
- Acceptance criteria:
  1. `cacheRead > 0` on the ordinary turn: **22,410** ✓
  2. llama-server telemetry corroborates prefix reuse: followup server-side
     eval 86 tokens / 14,456.88 ms vs the prefill's 22,409 tokens /
     1,348,704.81 ms ✓
  3. Ordinary prompt-eval materially lower than the uncached ~27k baseline:
     ~14.5 s vs 1,882,854.65 ms ✓
- Records: `dev-openclaw/state/stage6a6-acceptance/2026-08-16T21-41-41-660Z/`
  (manifest.json, results.json, summary.md), `/tmp/kimi-llama-server.log`.
- The fix is committed (`0a6b2df63`) and promoted to the live runtime
  (`runtime/live/COMMIT`), which also closes the live server's own recurring
  SIGABRTs from the same defect (BUG-001; two observed in
  `/tmp/kimi-llama-server.lifecycle.log`, exit 134).

## Problems

- Resolved: the compute-pool shortfall blocker at large prompt sizes
  (grow-only temp context; ~15 objects/step; the 2851st object needed 368 B
  with 144 B free — 224 B short). See the dedicated fix report for
  characterization, unit test, and regression evidence.
- Remaining: the separate `sessions_send(..., timeoutSeconds: 0)` admission
  issue, recorded as a harness/OpenClaw integration boundary (this harness
  dispatches via the lower-level `agent` RPC and is unaffected).

## Decisions

- The ggml compute-pool shortfall was a defect in the expert-streaming
  temp-context pool management, fixed with recycle-on-overflow — no caching,
  KDA/KV, prefix-construction, dispatch, or acceptance-semantics changes.
- Fix committed (`0a6b2df63`) and promoted to the live runtime; see
  `progress/stage-06a6-temp-ctx-pool-fix-report.md`.

## Next Phase

- Stage 6A.6 acceptance is now PASS. Next boundaries per the service
  roadmap: warm-state registry (6B) and anything downstream that depends on
  the established prefix boundary.

## Reproduction

```bash
# dev gateway first (token = gateway.auth.token from ~/.openclaw/openclaw.json)
cd openclaw-src
OPENCLAW_GATEWAY_PORT=18790 OPENCLAW_SERVICE_KIND=gateway \
OPENCLAW_GATEWAY_TOKEN="<token>" node openclaw.mjs gateway --port 18790

# acceptance rerun (fixed binary; harness starts the server itself)
OPENCLAW_HOME=$PWD/../dev-openclaw/home \
OPENCLAW_STATE_DIR=$PWD/../dev-openclaw/state \
OPENCLAW_CONFIG_PATH=$PWD/../dev-openclaw/config/openclaw.json \
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790 \
KIMI_BIN=$PWD/../llama.cpp/build-metal/bin/llama-server \
pnpm exec tsx scripts/dev/stage6a6-acceptance-harness.ts
```

Measured cache-reuse artifact (rerun):

```bash
python3 - <<'EOF'
import json
r = json.load(open('dev-openclaw/state/stage6a6-acceptance/2026-08-16T21-41-41-660Z/results.json'))
for c in r['captures']:
    print(c['label'], (c['toolResult'] or {}).get('runId'))
EOF
```

Supporting comparison artifacts (baseline):

```bash
sed -n '1,40p' dev-openclaw/state/stage6a4-reduction/baseline_300s.body.txt
sed -n '1,40p' dev-openclaw/state/stage6a4-reduction/results.tsv
sed -n '1,40p' dev-openclaw/state/stage6a4-token-prefix-compare.json
```
