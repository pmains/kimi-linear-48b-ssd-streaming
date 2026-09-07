# Step 9D Report — Conditional Reply-Directive Instructions (Production Fix + Real-Path Verification)

## Status

PASS (Step 9D objective). The production fix was implemented exactly as ordered —
the two reply-directive instruction lines are emitted only when the run has an
actual messaging delivery surface — and verified through the real OpenClaw path
with a post-restart gateway running the patched dist. The directive-tag/empty
family the fix targets is eliminated through the real path (0/6 reps; the frozen
9A r2 real-path P1 was INVALID rc=1 with an empty visible reply from that
family). Residual strict-format families (P1 `!`-drop, prose commentary, P2
fenced JSON) persist unchanged and are attributed out of scope (sampler +
tool-catalog/assistant-style behavior per 9B/9C). No sampler, tool, prose, or
Step 10 changes were made.

## Objective

Pete's order (2026-09-04): implement the smallest production fix supported by
9B/9C — make reply-directive instruction lines conditional on an actual
delivery surface. Then rerun the affected P1/P2/directive-family probes through
the real OpenClaw path and compare against the frozen 9B/9C baseline. Do not
change sampler settings, tool behavior, prose handling, or Step 10. Record the
patch and verification in a Step 9D report, then stop for review.

## Changes

Production dist patch (2 files; worker.mjs duplicate copies NOT patched — not on
the live embedded path, 9A precedent; worker copies are not live for probes):

1. `system-prompt-params-7goAPY5o.js` (2 edits)
   - `buildAssistantOutputDirectivesSection` (def 366–390, three branches):
     the two reply-directive lines at 381–382 are now spread from a gate:
     `...(params.hasDeliverySurface === false ? [] : ["- Native reply starts with ...", "- Directives stripped before render ..."])`
   - Section call site (~910): passes `hasDeliverySurface: params.hasDeliverySurface`
     (a missing-comma syntax error after `runtimeChannel` at ~913 was repaired).
2. `builtin-openclaw-nX1NfpmQ.js` (3 edits)
   - `prepareEmbeddedAttemptSystemPrompt` (~18897): computes
     `const hasDeliverySurface = runtimeChannel != null && runtimeChannel !== "webchat" || attempt.currentMessageId != null || attempt.currentChannelId != null || attempt.currentMessagingTarget != null;`
   - Embedded-prompt bag literal (~19068): adds `hasDeliverySurface,`
   - `buildEmbeddedSystemPrompt` explicit param list (~17680): forwards
     `hasDeliverySurface: params.hasDeliverySurface,`

Data flow verified end-to-end (bag → embeddedSystemPrompt → buildAttemptSystemPrompt
→ buildEmbeddedSystemPrompt → buildConfiguredAgentSystemPrompt →
buildAgentSystemPrompt → section call site → gated section). Only one live copy
of the two lines exists in dist. Other callers of the section
(`commands-system-prompt-D8qYBSNr.js`, `helpers-71byl0vs.js`) do not thread the
flag → param undefined → lines present = default preserves current behavior.

Semantics of the gate:
- Real Slack runs: `runtimeChannel` is a non-webchat channel → lines present.
- Real Control-UI/webchat runs with a delivery surface: keep lines via
  `currentMessageId`/`currentChannelId`/`currentMessagingTarget` (option1-restore
  regression precedent: webchat alone must NOT be treated as headless).
- Headless probe runs (fresh `openclaw agent` sessions, no `--deliver`, no ids):
  `hasDeliverySurface = false` → the two lines are omitted = the C1a-D behavior
  9B/9C measured.

No sampler/tool/policy/prose/Step-10 changes. All edits validated with
`node --check` + dynamic-import smoke tests; dist restored pristine between
tests; backups hash-verified before each edit.

Retained:
- `service-progress/step-09d-dist-diffs/`: `system-prompt-params-7goAPY5o.js.diff`,
  `builtin-openclaw-nX1NfpmQ.js.diff`, `MANIFEST.sha256` (pristine + patched
  SHAs), `patch_9d.py` (reproducible patcher).
- `tools/service_step09d_verify.sh`: post-restart real-path driver with a
  stale-gateway guard (gateway process start time vs patched-dist mtimes;
  `STEP09D_ALLOW_STALE=1` bypass) — probes run gateway-side with daemon-cached
  modules, so the guard is required for the run to be meaningful.
- `tools/service_step09d_classify.py`: frozen Step 9 rubric + 9B family
  vocabulary (empty/directive-only, directive-prefixed, terse-wrong,
  prose-wrapped, prose-content-ok, clarifying, exact), plus a directive-leak
  counter.

## Results

### Environment (env.txt, captured 2026-09-04T22:39:14Z)
- gateway PID 10951, started Fri Sep 4 11:55:08 2026 (AFTER the 11:33 dist
  patch writes → stale-gateway guard passed; daemon restarted between turns)
- patched SHA-256 `system-prompt-params` 53a37d08… == MANIFEST patched SHA
- patched SHA-256 `builtin-openclaw` 68baff05… == MANIFEST patched SHA
- llama-server health `{"status":"ok"}` (64K pinned instance)

### Real-path rerun (P1/P2 x 3 fresh sessions, no --deliver; 2026-09-04 22:39-23:00Z)

| rep | rc | wall_s | family | reply |
|---|---|---|---|---|
| P1-r1 | 0 | 158.9 | terse-wrong | `PLATANOS` |
| P1-r2 | 0 | 532.1 | prose-wrapped | "I'll respond only with the word "PLATANOS" as requested." |
| P1-r3 | 0 | 54.2 | terse-wrong | `PLATANOS` |
| P2-r1 | 0 | 41.6 | exact (PASS) | `{"ok": true}` |
| P2-r2 | 0 | 371.3 | prose-wrapped | "I'll respond with exactly one JSON object as requested.  ```json {"ok": true} ```" |
| P2-r3 | 0 | 34.7 | terse-wrong | ```json {"ok": true} ``` |

All six: rc=0, timed_out=False, leaky=0. Directive-family count across P1+P2:
0/6 (zero empty/directive-only, zero directive-prefixed, zero lone-tag, zero
`NO_REPLY`). grep over all turn logs and replies: no `reply_to_current`, no
`[[reply`, no `MEDIA:` leakage anywhere.

### Comparison vs frozen baselines

| dimension | frozen 9A r2 real path (pre-patch) | frozen 9B/9C C1a-D offline | 9D real path (post-patch) |
|---|---|---|---|
| directive/empty family | P1 INVALID rc=1 empty visible reply (directive family) | eliminated (0/3 P1, 0/3 P2) | eliminated 0/6 ✓ |
| P1 reply shape | empty/lone-tag | 3 terse bare `PLATANOS` | 2 terse `PLATANOS` + 1 prose-wrapped |
| P2 strict exact | exact PASS (n=1) | 3 exact | 1 exact + 1 prose + 1 fenced |
| rc / timeout | P1 rc=1 | n/a (offline) | rc=0 all, no timeouts |

The measured fix candidate from 9B/9C (gate the reply-directive lines on an
actual delivery surface) is confirmed through the real path: the specific
failure it targets is gone. The remaining shortfalls are the frozen
response-quality families 9B/9C attributed to the sampler (P1 `!`-drop: bare
`PLATANOS` without `!`) and to tool-catalog/assistant-style behavior (prose
commentary, fenced JSON) — all explicitly out of 9D scope and unchanged.

## Problems

- P1-r2 (532.1 s) returned only the commentary sentence, no bare token — the
  known first-call commentary family from the 9A localization (commentary +
  optional `update_goal` round before the terse final call). Wall time is a
  Step 10 observation, preserved, not investigated.
- P2 strict exact is 1/3 through the real path (1 prose-wrapped, 1 fenced).
  The C1a-D offline cell showed 3/3 exact, but the real path carries the full
  agent environment (tool catalog, trajectory), and P2's real-path exactness has
  documented run-to-run variance (2026-09-02 drift → 9A r2 exact n=1 → here 1/3).
  Removing the directive lines was the C1a-D direction that IMPROVED P2 offline,
  so the residuals are not attributable to this patch.
- Direct sent-prompt extraction from the trajectory store was not possible this
  run: `~/.openclaw/openclaw.sqlite` is 0 bytes / has no tables at capture time
  (state DB lives elsewhere and was not queried live per the standing rule). The
  verification chain instead rests on: stale-gateway guard (gateway restarted
  11:55:08 after the 11:33 patch writes), env.txt patched SHAs == MANIFEST
  patched SHAs, and the behavioral outcome (directive family gone where 9A r2
  showed it).
- The literal `NO_REPLY` token seen once at C1a-D/P1 128K (9C rep3) did not
  recur in any 9D rep.

## Decisions

- Gate predicate computed at prompt-prep time in
  `prepareEmbeddedAttemptSystemPrompt` from the attempt-bag delivery context;
  threaded via the embeddedSystemPrompt bag → explicit param list → section call
  site. Two-file change; other section callers default to lines-present
  (behavior preserved when the signal is absent).
- `runtimeChannel == "webchat"` alone is NOT treated as headless (Control-UI has
  a delivery surface; option1-restore regression precedent). Headless probe
  sessions are identified by webchat + no message/channel/messaging-target ids.
- Worker.mjs duplicate copies not patched (9A precedent; not live for probes).
- Gateway restart between turns (launchctl kickstart) required for the patched
  dist to load — daemon caches modules at start (crash-test proven). Stale
  gateway guard embedded in the verification driver.
- Verification driver + classifier retained in `tools/` before execution;
  `STEP09D_REPS` defaults to 3.

## Next Phase

- Step 10 untouched (no wall-time/TTFT analysis; the 532.1 s and 371.3 s reps
  are preserved as Step 10 evidence).
- The remaining Step 9 response-quality families are unchanged: P1 `!`-drop
  (sampler), prose commentary + fenced JSON (tool catalog/assistant-style).
  Fixing those would require sampler/tool/prose changes — outside 9D scope and
  not supported by 9B/9C as a single measured fix.
- Real delivery surfaces keep the directive lines: Slack runs (non-webchat
  runtimeChannel) and webchat runs with message ids → normal directive behavior
  preserved; headless/API-style runs lose only the two reply-directive lines.
- Open question for review: whether to accept Step 9 close-out with the frozen
  FAIL — RESPONSE QUALITY determination (now with the directive family resolved
  in production), or authorize sampler/tool-catalog experiments as a new step.

## Reproduction

```sh
# 1. Patch (already applied + retained; reproducible from the retained patcher):
cd /opt/homebrew/lib/node_modules/openclaw
python3 service-progress/step-09d-dist-diffs/patch_9d.py
# (pristine backups + SHAs: service-progress/step-09d-dist-diffs/MANIFEST.sha256)

# 2. Restart the gateway so the patched dist loads (daemon caches modules at start):
launchctl kickstart -k gui/501/ai.openclaw.gateway

# 3. Real-path verification (stale-gateway guard + P1/P2 x3 fresh sessions,
#    64K pinned, no --deliver, then frozen-rubric classification):
cd /Users/pmains/Code/openclaw/kimi
bash tools/service_step09d_verify.sh
# outputs: benchmarks/results/service-step-09d-verify/{env.txt,classified.csv,
#          summary.txt,P1/,P2/}
```

Evidence retained at `benchmarks/results/service-step-09d-verify/`; patch
artifacts at `service-progress/step-09d-dist-diffs/`. Stopped for review; Step
10 not begun.
