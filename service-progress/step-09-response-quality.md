# Step 9 Report — Qualify Response Quality

## Status (2026-09-03 — post-9A frozen-suite rerun, supersedes the 2026-09-02 baseline below)

**FAIL — RESPONSE QUALITY (agent trajectory).** After the Step 9A agent
response-path remediation (patch set frozen 2026-09-03), the complete frozen
Step 9 suite was rerun unchanged through the repaired agent path, one fresh
isolated session per probe (keys `agent:kimi:step09-r2-<label>`, manifest
re-frozen before probe 1, prompts SHA-256 identical to the frozen baseline).

Rerun outcome (frozen scorer, rubric.csv in `benchmarks/results/service-step-09/`):

| probe | rc | instruction | format | result | task | notes |
|---|---|---|---|---|---|---|
| P1 | 1 | INVALID | | INVALID | INVALID | client rc=1 (empty visible reply this run) |
| P1b | 0 | FAIL | FAIL | FAIL | FAIL | clarifying question, not the one required word |
| P2 | 0 | PASS | PASS | PASS | PASS | exact `{"ok": true}` — acceptance met |
| P3 | 0 | FAIL | FAIL | FAIL | FAIL | prose answer, not only the number |
| P4 | 0 | FAIL | FAIL | FAIL | FAIL | prose answer; first number parsed is not 18080 |
| P5 | 0 | PASS | FAIL | PASS | PASS | correct count (5) embedded in prose |
| P6 | 0 | PASS | N/A | PASS | PASS | file roundtrip verified |
| P7 | 0 | FAIL | FAIL | FAIL | FAIL | sentence contains date but final line not exactly COMPLETE |

Mechanical layer verified clean on every probe: zero `[[reply…]]`/separator
leakage, zero cloud fallback (`llama-server`/`kimi-linear-48b` everywhere),
zero post-generation stall (all probes rc=0 or client-error within 16–414 s;
no 1200 s timeouts, no 630 s stall family). P2 — the 9A acceptance probe that
previously failed with fenced JSON — now returns exactly `{"ok": true}`.

Demonstrated scope of the remaining failures: strict-format response quality
through the agent trajectory (prose/clarifying/empty replies instead of the
required bare exact outputs). Likely boundary: the agent trajectory
(commentary + tool rounds + follow-up generation), not the Kimi model (direct
llama-server controls return exact answers) and not the 9A mechanical layer.
Pre-remediation baseline (2026-09-02 FAIL run) is archived byte-identically at
`benchmarks/results/service-step-09-baseline-2026-09-02/`; 9A evidence at
`benchmarks/results/service-step-09a-verify/`; remediation report at
`service-progress/step-09a-agent-response-path.md`. Step 10 not begun.

## Baseline Status (2026-09-02 run, pre-remediation — superseded by the rerun above)

**FAIL — RESPONSE QUALITY (agent path), 2026-09-02.** The current Kimi
agent configuration (agent `kimi` → live MXFP4 Metal llama-server via the
OpenClaw agent path, model pinned to `llama-server/kimi-linear-48b`) does
NOT reliably produce responses of sufficient quality for productive use on
the strict-format tasks Step 9 requires. Two of the two completed
strict-format probes FAILED (P1 literal instruction following, P2
constrained output); two probes PASSED (P3 reasoning, P5 bounded tool
use); four probes were INVALID — the agent-path turn exceeded the 1200 s
client timeout before producing a scorable completion (P1b, P4, P6, P7).
Direct llama-server controls (attribution only) show the underlying Kimi
model answers the failed probes EXACTLY as required (`PLATANOS!` and
`{"ok": true}`) — the demonstrated failure boundary is the OpenClaw agent
response path (leaked `[[reply_to_current]]:` / `[[reply_to:]]` directive
prefixes) plus agent-path per-turn latency (Step 10 territory, not fixed
here). No remediation performed; Step 10 not begun. Stopped for review.

## Objective

Step 9 (SERVICE-ROADMAP.md): determine whether the current Kimi Linear
runtime produces responses of sufficient quality for productive use as a
real OpenClaw agent, measured through the actual agent path. Owner
approval 2026-09-02 added clarifications: exact `Respond only PLATANOS!`
→ exact `PLATANOS!` for P1; two retained drivers + fixed prompts/expected
under `benchmarks/results/service-step-09/`; agent under test = `kimi`;
fresh isolated session per probe; one authorized non-qualification smoke +
one authorized non-qualification validation turn; freeze baseline manifest
before the first probe; run the suite exactly once; preserve outputs
verbatim; direct llama-server controls only for failed/ambiguous probes;
no remediation; no Step 10; stop for review.

## Frozen Baseline (manifest.json, frozen before probe 1)

- Model: `moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`
  (27,205,376,672 bytes)
- Runtime: `runtime/live/COMMIT` = `a895f6826` (binary unchanged since E3C)
- OpenClaw: `2026.9.1-beta.1`
- Live env (launchd wrapper): naive stream, `KIMI_EXPERT_CACHE_MB=8192`,
  `KIMI_EXPERT_READ_WORKERS=4`, zerocopy, `KIMI_STREAM_METAL_STAGE=1`,
  `KIMI_STREAM_E2_DIRECT_PLACE=1`, ctx 65536, `-ngl 999`, port 18080
- Agent under test: `kimi`; **model pin: `llama-server/kimi-linear-48b`**
  (allow-listed id for the live endpoint `http://127.0.0.1:18080/v1`;
  agent's default primary is `deepseek/deepseek-v4-flash`, so the pin is
  required — unpinned turns are served by cloud and would be INVALID)
- Invocation: `openclaw agent --agent kimi --model llama-server/kimi-linear-48b
  --session-key agent:kimi:step09-<label> --message-file <prompt> --json
  --timeout 1200` (no `--deliver`), via `tools/service_step09_turn.py`
- Server health at freeze: `{"status":"ok"}`; covariates recorded
  (`cov-before.txt`, `cov-after.txt`)

## Pre-qualification diagnostics (NON-QUALIFICATION, excluded)

1. Smoke #1 (unpinned): harness mechanics OK (`OK` reply) but served by
   **deepseek-v4-flash** (cloud) — agent kimi's default primary is cloud,
   proving the model pin is mandatory.
2. Smoke #2 (pinned `kimi-local/kimi-linear-48b`): policy-rejected by
   `agents.defaults.modelPolicy.allow` (id not allow-listed) — routing
   evidence retained in `smoke/`.
3. Validation turn (pinned `llama-server/kimi-linear-48b`, fresh session
   `agent:kimi:step09-validation`): rc=0, reply `OK`, recorded
   provider/model `llama-server`/`kimi-linear-48b`, llama-server log delta
   +28 lines attributable, zero cloud references → Kimi exercised; harness
   validated. Evidence in `validation/`.

## Test Matrix and Per-Probe Results

Suite: P1, P1b, P2, P3, P4, P5, P6, P7 — one fresh isolated session per
probe (`agent:kimi:step09-<label>`), run exactly once, 2026-09-02
16:48–18:37 UTC.

| probe | category | rc | wall s | fallback | result |
|---|---|---|---|---|---|
| P1 | literal instruction following | 0 | 1214.7 | no | **FAIL** |
| P1b | literal (negative constraint) | 1 | 1231.4 | no | INVALID (gateway timeout) |
| P2 | constrained output | 0 | 46.5 | no | **FAIL** |
| P3 | simple reasoning | 0 | 117.4 | no | **PASS** |
| P4 | repository comprehension | 1 | 1231.1 | no | INVALID (gateway timeout) |
| P5 | bounded tool use | 0 | 264.2 | no | **PASS** |
| P6 | multi-step engineering | 1 | 1231.2 | no | INVALID (gateway timeout) |
| P7 | final-answer behavior | 1 | 1203.6 | no | INVALID (gateway timeout) |

Every probe exercised the live Kimi server (fallback_suspected=false on
all; recorded provider/model `llama-server`/`kimi-linear-48b` on all
completed turns). No cloud fallback or model substitution occurred on any
probe.

### P1 — literal instruction following: FAIL (verbatim)

- Prompt: `Respond only PLATANOS!`
- Expected: exactly `PLATANOS!` (owner clarification #1)
- Actual final assistant text (agent JSON): `[[reply_to_current]]: PLATANOS`
- Extracted reply: `: PLATANOS`
- Verdict: instruction FAIL / format FAIL / result FAIL. Missing the `!`;
  leaked `[[reply_to_current]]:` directive prefix.
- **Direct llama-server control (attribution): `PLATANOS!` — exact.**
  → Deficit is in the agent response path, not the model.

### P1b — literal instruction following (negative): INVALID

- Prompt: `Reply with exactly one word. That word must not be 'model'.`
- Client rc=1: `gateway timeout after 1230000ms` — no scorable completion.
- Verdict: INVALID (not measured; timeout is agent-path latency, Step 10
  territory). Not counted as pass or fail.

### P2 — constrained output: FAIL (verbatim)

- Prompt: `Respond with exactly one JSON object: {"ok": true}. No other text.`
- Expected: exactly `{"ok": true}`
- Actual final assistant text: `[[reply_to:]]{"ok": true}`
- Verdict: format FAIL — JSON correct but a leaked `[[reply_to:]]` directive
  prefix makes the reply not exactly the required JSON.
- **Direct llama-server control (attribution): `{"ok": true}` — exact.**
  → Deficit is in the agent response path, not the model.

### P3 — simple reasoning: PASS

- Prompt: `A train covers 60 miles in 1.5 hours. ... Answer with only the number.`
- Reply: `40` → PASS (instruction/format/result).

### P4 — repository comprehension: INVALID

- Prompt: read `TOOLS.md`; reply only with the live Kimi server port (18080).
- Client rc=1: gateway timeout (~1231 s) — no scorable completion. INVALID.

### P5 — bounded tool use: PASS

- Prompt: list `benchmarks/prompts/` e5 files; reply with only the count.
- Reply: `5` (ground truth 5) → PASS.

### P6 — multi-step engineering: INVALID

- Prompt: create `scratch/probe.txt` with `hello step9`, read back, reply.
- Client rc=1: gateway timeout — no scorable completion. INVALID.

### P7 — final-answer behavior: INVALID

- Prompt: get UTC date via tool; sentence + final line `COMPLETE`.
- Client rc=1: gateway timeout; captured fragment `⚠️ 📖 Read failed`.
  INVALID.

## Diagnostic Controls (attribution only, per owner directive)

Run directly against the live llama-server OpenAI-compatible endpoint
(`/v1/chat/completions`, model
`models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf`,
temperature 0), bypassing OpenClaw entirely:

- P1 prompt → `PLATANOS!` (exact)
- P2 prompt → `{"ok": true}` (exact)

Both failed probes pass at the model level. Conclusion: the failures are
not a Kimi model/runtime defect; they are produced by the OpenClaw agent
path (response-format directive leakage: `[[reply_to_current]]:`,
`[[reply_to:]]` prefixes appearing in the delivered assistant text) and by
agent-path per-turn latency (4 probes exceeded the 1200 s client cap —
overhead beyond the ~10–120 s of model compute, consistent with Step 10
TTFT concerns).

## Classification

**FAIL — RESPONSE QUALITY (agent path).**

- Demonstrated scope: the current OpenClaw agent configuration fails the
  two strict-format Step 9 categories it could complete (literal
  instruction following P1, constrained output P2) and could not complete
  half the suite within the 1200 s per-turn cap (P1b, P4, P6, P7 INVALID).
- Likely boundary: OpenClaw agent response formatting (leaked
  `[[reply_to_current]]` / `[[reply_to:]]` directive prefixes) and
  agent-path latency. The Kimi model/runtime itself is exonerated by
  direct llama-server controls on both failed probes.
- No cloud fallback observed; not BLOCKED/INVALID on fallback grounds.
- No remediation performed; Step 10 (TTFT) not begun.

## Evidence Retained

- `benchmarks/results/service-step-09/manifest.json` (frozen baseline w/
  model pin), `cov-before.txt`, `cov-after.txt`
- `benchmarks/results/service-step-09/prompts/*.md` + `expected/*.json`
- Per-probe dirs `P1/ … P7/`: `<label>.timed.out`, `<label>.out`,
  `<label>.err`, `<label>.reply.txt`, `<label>.client.json`,
  `srvlog.delta`, `summary.json` (all verbatim)
- `benchmarks/results/service-step-09/rubric.csv` (machine-readable scores)
- Non-qualification: `smoke/` (routing diagnostics), `validation/`
  (validated harness + live-Kimi evidence), plus direct llama-server
  control outputs recorded above
- Drivers: `tools/service_step09_turn.py`, `tools/service_step09_qualify.sh`

## Reproduction

    # full suite (once):
    tools/service_step09_qualify.sh

    # per-probe turn (fresh session, pinned model):
    python3 tools/service_step09_turn.py <outdir> <label> <promptfile> \
        agent:kimi:step09-<label> --agent kimi --timeout 1200

    # direct llama-server control (attribution only):
    curl -s http://127.0.0.1:18080/v1/chat/completions -H 'Content-Type: application/json' \
      -d '{"model":"models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf",
           "messages":[{"role":"user","content":"Respond only PLATANOS!"}],
           "temperature":0,"n_predict":32}'

## Next Phase

- Step 9 = FAIL — RESPONSE QUALITY (agent path). Do not proceed to Step 10
  without owner decision. Candidate (unapproved) directions, in evidence
  order: (1) investigate the agent-path directive-prefix leakage
  (`[[reply_to_current]]:` / `[[reply_to:]]`) — the two failures trace to
  it, not to the model; (2) agent-path per-turn latency (4 INVALID probes
  > 1200 s) belongs to Step 10 TTFT work once Step 9 is resolved.
- This report supersedes any earlier Step 9 summary. ROADMAP:
  SERVICE-ROADMAP.md updated with this status pointer only.
