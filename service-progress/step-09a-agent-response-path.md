# Step 9A Report — Agent Response-Path Remediation

## Status

**COMPLETE (remediation) — 2026-09-03.** Patch set frozen. Remaining P1
exact-output failure classified as **Step 9 response-quality evidence**, not an
unresolved 9A stripping/finalization defect (owner decision 2026-09-03). Frozen
Step 9 suite rerun through the repaired agent path: see
`service-progress/step-09-response-quality.md` and
`benchmarks/results/service-step-09/`.

## Objective

Repair and validate the OpenClaw agent response path before completing Step 9
response-quality qualification (SERVICE-ROADMAP.md §9A). Scope: the
demonstrated agent-path defects only — reply-directive/separator leakage and the
post-generation stall — while preserving the frozen Kimi model, llama-server
behavior, sampler, prompts, context configuration, and the frozen Step 9 suite.

## Changes (dist patch set, frozen 2026-09-03)

All live in the installed gateway dist
(`/opt/homebrew/lib/node_modules/openclaw/dist/`); per-file diffs retained in
`service-progress/step-09a-dist-diffs/`:

1. `directive-tags-D4YR8I_B.js.diff` — directive-tag parsing/strip fixes
   (strip tag + optional separators; nothing removes trailing punctuation).
2. `embedded-agent-l7T_r8nK.js.diff` — final visible/raw text run through
   `stripInlineDirectiveTagsForDisplay`.
3. `system-prompt-params-7goAPY5o.js.diff` — original gate (omit reply
   directive lines on http/cli channels).
4. `system-prompt-params-7goAPY5o.js.option1-restore.diff` — **live state**:
   the two unconditional reply-directive lines restored (the http/cli gate
   removed) because removing the directive anchor caused a separate
   response-quality regression (P2 fenced JSON; P1 clarifying question).
5. `agent-command-BHLpevuF.js.diff` + `agent-runner.runtime-DegKfVtB.js.diff` —
   post-generation memory-flush maintenance gated on having a
   delivery/channel target (skipped for headless `--json` runs) — the 630 s
   stall fix.
6. `agent-runner-memory-CHicSjNz.js.diff` — maintenance run capped at 120 s.

Dist SHA-256 at freeze (2026-09-03):
`agent-command-BHLpevuF.js 8ea7e6f7…`, `agent-runner.runtime-DegKfVtB.js
d8199cdf…`, `agent-runner-memory-CHicSjNz.js e5acc5be…`,
`directive-tags-D4YR8I_B.js cfae5243…`, `embedded-agent-l7T_r8nK.js 5e507572…`,
`system-prompt-params-7goAPY5o.js 1f72fb06…`.

Driver note: `tools/service_step09_qualify.sh` gained an optional
`STEP09_KEYSUFFIX` env knob (empty default = byte-identical to the retained
2026-09-02 driver, backup `.pre-2026-09-03.bak`; scorer heredoc untouched). The
rerun uses a fresh session-key namespace (`agent:kimi:step09-r2-<label>`)
because reusing the original `step09-<label>` keys would resume the Sep-2
sessions rather than fork fresh (empirically verified).

## Verified 9A remediation results

- Reply-directive leakage: **fixed** — zero `[[reply…]]`/separator leakage in
  every post-fix probe (`P1-opt1-1`, `P2-opt1-1`, gated rounds).
- Post-generation 630 s stall: **fixed** — all probes rc=0, `timed_out=false`,
  wall 45–356 s (pre-fix every probe died at the 630 s gateway timeout).
- Cloud fallback: **none** — every probe `winnerProvider: llama-server`,
  `winnerModel: kimi-linear-48b`, `fallbackUsed: false`, runner embedded.
- Restored directive instructions: **retained** (Option-1 restore is the live
  state; removing them caused a separate response-quality regression).
- P2 constrained output: **PASS exact** — delivered exactly `{"ok": true}`
  (bare, no fence) in `P2-opt1-1`.
- P1 literal instruction: **still fails exact output** — delivered `PLATANOS`
  (missing `!`) in `P1-opt1-1`, for the reason below.

## P1 localization result (owner-accepted 2026-09-03)

Verdict: **hypothesis 2 — the generation itself was already `PLATANOS`**; no
OpenClaw stage transforms `PLATANOS!` into `PLATANOS`. Full evidence:
`benchmarks/results/service-step-09a-verify/P1-opt1-1-localization.md`.

- Earliest post-generation capture (session transcript, written from the
  provider response before any strip) = exactly `"text":"PLATANOS"`; trajectory
  `model.completed` agrees (`assistantTexts: ["PLATANOS"]`, stopReason `stop`).
- Token accounting: `PLATANOS` = 4 tokens; `PLATANOS!` = 5; agent-path final
  call output = 5 tokens (4 content + EOS) — no room for a `!` token. Direct
  llama-server control returns `PLATANOS!` (retained
  `P1-opt1-1-direct-control.json`).
- Trajectory cause: the run made an unnecessary `update_goal` tool call after
  commentary; the tool failed (`goal not found` in the fresh probe session), and
  the follow-up generation answered tersely `PLATANOS` without the `!`.
- Dist code contains no path that strips trailing punctuation.

## Classification (owner decision)

The remaining P1 failure is **Step 9 response-quality evidence** (agent
trajectory generates `PLATANOS` after an unnecessary failed `update_goal` tool
round), **not** a 9A mechanical defect. No patch/tuning of prompt, tool policy,
sampler, model, or response path to make P1 pass.

## Wall-time note (preserved for Step 10)

P1-opt1-1 wall time 355.6 s (rc=0, not a timeout) is preserved for Step 10 and
was deliberately not investigated during Step 9A.

## Evidence

- `benchmarks/results/service-step-09a-verify/` — P1/P2 gated + Option-1
  probes, direct control, localization note, suite-rerun console log.
- `benchmarks/results/service-step-09/` — frozen Step 9 suite rerun through the
  repaired agent path (fresh sessions), manifest frozen before probe 1,
  rubric.csv scored by the frozen scorer.
- `benchmarks/results/service-step-09-baseline-2026-09-02/` — byte-verified
  archive of the pre-remediation (FAIL) suite evidence.

## Reproduction

    # suite rerun through repaired agent path, fresh sessions:
    STEP09_KEYSUFFIX=r2 tools/service_step09_qualify.sh
    # per-probe turn (fresh session, pinned model):
    python3 tools/service_step09_turn.py <outdir> <label> <promptfile> \
        agent:kimi:step09-r2-<label> --agent kimi --timeout 1200
    # direct llama-server control (attribution only):
    curl -s http://127.0.0.1:18080/v1/chat/completions -H 'Content-Type: application/json' \
      -d '{"model":"models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf",
           "messages":[{"role":"user","content":"Respond only PLATANOS!"}],
           "temperature":0,"n_predict":32}'

## Next Phase (completed 2026-09-03)

Frozen Step 9 suite rerun through the repaired agent path completed (fresh
sessions `agent:kimi:step09-r2-<label>`, frozen prompts byte-identical, frozen
scorer untouched); Step 9 determination recorded in
`service-progress/step-09-response-quality.md` and SERVICE-ROADMAP.md Step 9/9A
status blocks: **FAIL — RESPONSE QUALITY (agent trajectory)** — mechanical
layer clean (zero leakage/fallback/stall), residual failures are strict-format
response quality through the agent trajectory. Stopped for review; Step 10 not
begun.
