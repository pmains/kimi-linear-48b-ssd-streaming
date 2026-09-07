# Step 11A Report — Localize the U+2026 Path-Truncation Failure

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** order)
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming`
Design: `service-progress/step-11a-u2026-path-truncation-design.md`
Forensics: `benchmarks/results/service-step-11a/11a-forensics-summary.md`
Status: **COMPLETE — determination delivered, STOPPED for review. No patch, no
config/prompt/sampler change.**

---

## Status

**PASS (determination).** Classification target reached: corruption is
OpenClaw-side, pre-model, in the message ingestion → transcript-store path —
not model-emitted, not authoring-layer, not sampler/llama-server. The exact
dist function was not conclusively isolated (see Problems); the corruption
locus and mechanism are established with byte-level evidence.

## Objective

Determine exactly where a valid absolute path (e.g.
`/Users…VICE-ROADMAP.md`) becomes the
U+2026-truncated forms seen in evidence (e.g. `/Users…VICE-ROADMAP.md`,
`/Users…DMAP.md`) — from raw model output through OpenClaw tool-call
handling to the stored transcript. Reproduce with focused controls, using the
same files/schemas plus successful full-path calls for comparison.

## Changes

- `benchmarks/results/service-step-11a/11a-forensics-summary.md` — retained
  byte-level evidence summary (new).
- `benchmarks/results/service-step-11a/` — probe + raw-control artifacts
  (retained earlier this step: `probe-clean.md`, `probe.client.json`,
  `probe.record.json`, `probe.out`, `probe.gateway.window.log`,
  `probe.llama.window.log`, `raw-control-{0..7}.json`(+`.request.json`),
  `raw-trial-*.json`).
- `SERVICE-ROADMAP.md` — §11A status (this report).
- No production config, prompt, sampler, tool, or code change.

## Method and evidence

### 1. Authoring-layer hypothesis — REFUTED (byte-accurate inventory)
Corrected byte-level U+2026 inventory (python `"\u2026" in s`, `od` spot
checks) across step-11 R/C/G prompts, step-10/10D/11 prompt files, smoke
prompts, LOOP/LOOP-STRICT/NORM prompts: **all 0 U+2026**. `G1.md` and
`probe-clean.md` od-verified byte-clean with the full valid path. Only the
assistant's own report/design docs contain U+2026 (quoted artifact examples).
The earlier "prompts contain U+2026" claim was a defective zsh `$'\u2026'`
grep artifact; the corrected scan clears every prompt file.

### 2. Raw-model hypothesis — REFUTED for clean input (8/8 control)
8 direct llama-server `/v1/chat/completions` trials (retained
`raw-control-{0..7}.json` + requests): same read-tool schema, user message
containing the full clean path. All 8 responses emit
`{"path": "/Users…VICE-ROADMAP.md"}` —
**zero U+2026**. The model does not corrupt clean input at the exercised
schema/temperatures.

### 3. Clean-file probe — corruption exists in the stored transcript PRE-MODEL
Byte-clean message file (`probe-clean.md`, od-verified) → real agent run
(`openclaw agent --message-file`, session `agent:kimi:step11a-probe-64k`,
single-slot 64K llama-server):

- Live gateway DB (`~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite`),
  `transcript_events` seq=1 (the user message, written at session start
  `00:36:37.304Z`, before the model snapshot at seq=3 and before the first
  assistant output at seq=4, +20s):
  `content = "Please read the file at exactly this path: /Users…VICE-ROADMAP.md\n\nThen reply with the word DONE."`
  — **real U+2026 bytes present at store time**. Byte-verified in the LIVE DB,
  not only in /tmp copies.
- The model then emitted byte-identical corrupted read args
  (`{"path":"/Users…VICE-ROADMAP.md"}`), executed reads failed
  File-not-found on the corrupted path (the clean-path file exists), and the
  run churned to the 200s client cap (15 calls, 13 failures, read +
  memory_search/get).
- `probe.record.json` `finalPromptText` = **clean** full path, while the
  stored transcript content is corrupted → OpenClaw keeps a clean copy in the
  doc/prompt-text layer (markTranscriptPromptText-style separation) but the
  stored message content used for prompt assembly carries the U+2026 form;
  the executed reads prove the model's context contained the corrupted form.

→ The corruption enters in OpenClaw's message-file ingestion → transcript
serialization path, pre-model. The model is a faithful copier (byte-identical
repeats), not the origin.

### 4. Comparison controls (live DB, same gateway process PID 59990)
- `step10d-64k-loop-strict-r1` seq=1: 88-char path
  `/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-10d/target-file.md`
  stored **CLEAN**.
- `step11-64k-research`, `step11-64k-code` seq=1: paths stored **CLEAN**.
- `step11-64k-growth` (G1) seq=1: `/Users…VICE-ROADMAP.md` — **CORRUPTED**
  (matches the original G-session failure).
- `step11a-probe-64k` seq=1: **CORRUPTED**.
- 128k-OOM sessions: inconclusive (user messages contain no `/Users` token).

→ Corruption is not a pure "path length" rule (88-char clean vs 51-char
corrupted); the trigger condition is unresolved (open question, below). The
corrupted-vs-clean boundary across sessions suggests the corrupting pass is
conditional (content shape, message route, or a specific serialization
branch), not a blanket store transform.

### 5. Dist search for the corrupting function
Exhaustive scan of installed dist (recursive, minified-tolerant, literal and
escaped U+2026) for middle-truncate helpers: found
`coerceDisplayValue` (79+`…`+80 @160), `compactRawCommand` (half+`…`+half
@120), `compactProgressLineDetail` (45%+`…`+rest), `maskLifecycleIdentifier`
(4+`…`+4), `redactSessionKey` (6+`…`+6), `shortId` (8+`…`+4),
`formatApiKeyPreview`, `truncateTitle`, event-store/session-catalog
tail-`…` truncators, and ASCII-`...` helpers (`truncateMiddle`,
`middleTruncatePath`). **None reproduces the observed token-level signature**
(surrounding sentence byte-identical, only the long path token shortened to
head-6 `…` tail-7..15). Corrupted tails differ across samples (15 chars in
`VICE-ROADMAP.md`, 7 in `DMAP.md`) → not one fixed-window truncator; the
producing code path remains unidentified (see Problems).

## Classification

- (A) Authoring-layer artifact (prompts corrupted before run): **REFUTED** —
  all prompt files byte-clean; clean-file probe still corrupts.
- (B) Model emits U+2026 from clean input: **REFUTED** for the exercised
  schema/input (8/8 raw control clean; model is a byte-identical copier of
  whatever path text is in its context).
- (C) OpenClaw truncates message content at ingestion/transcript store,
  pre-model: **SUPPORTED** — primary finding. Byte-clean file → corrupted
  stored user message (live-DB bytes) before the model's first output; model
  then copies corrupted path into read args (matches G-session mechanism).
- (D) Copy-back loop: supported as an amplifier (corrupted path in tool
  results/compaction summaries re-enters context), but not the origin.

**Determination:** the U+2026 path-truncation is introduced by OpenClaw in
the message ingestion → transcript-store path for message-file-driven agent
runs (pre-model, pre-prompt-assembly). The kimi model faithfully reproduces
the corrupted content it is given; it does not originate the corruption from
clean input. The Step-11 "model tool-call reliability issue" label is thereby
refined: the *symptom* is model-side copying, the *root* is an OpenClaw-side
content rewrite upstream of the model.

## Problems / open questions

1. **Exact dist function not isolated.** Exhaustive static search found no
   helper matching the observed geometry. The corrupted strings' shape varies
   across samples (tail 7 vs 15 chars) and no candidate call-site was found
   that applies a U+2026 middle-truncate to user-message content or tool-call
   arguments. Possible reasons: minified indirect calls, a helper outside the
   scanned surface, or a sequence of two compactions. Finding it likely
   requires a runtime trace (intercept the message object at the steer →
   store boundary), which is a follow-up experiment, not done here.
2. **Trigger condition unexplained.** 88-char and 75-char paths stored clean
   while 51-char paths corrupted; same driver, same gateway, same store.
   Not length, not session type (both corrupted sessions were
   message-file-driven; so were clean ones). Candidates: message content
   shape, specific serialization branch, or a compaction pass that fires
   under a condition not yet identified.
3. **finalPromptText clean vs stored content corrupted.** Consistent with a
   prompt-text/content separation in the transcript layer, but the precise
   write path (steer vs store serialization) is unproven.
4. The 128k-OOM sessions (model never ran) could not serve as the
   "no-model" control for ingest-time corruption because their user messages
   contain no `/Users` tokens; a dedicated no-model ingest probe would be
   needed to fully close ingest-vs-store-timing.

## Decisions

- Keep Step-11A scoped to localization (authorization). No patch; no
  config/prompt/sampler change; llama-server untouched.
- Accept "OpenClaw-side pre-model content rewrite; model copies faithfully"
  as the determination despite the unidentified exact function, because the
  byte evidence (clean file → corrupted stored message before first model
  output; clean raw-model control) is decisive for the classification.
- Recommend (for later authorization, not applied):
  * smallest OpenClaw-side fix: locate and remove/guard the U+2026 token
    compaction applied to message content in the steer/transcript-store path
    (message content must round-trip byte-identical from accepted text to
    provider prompt);
  * service-side mitigation already in effect: prefer repo-relative paths in
    workload prompts (the standing practice from the 2026-09-02 memory note),
    which sidesteps absolute-path display/compaction entirely;
  * detector-side: loop detection does not catch this family (varied args),
    so it stays a content-layer issue, not a liveness one.

## Next phase

Step 12 (qualify decode throughput) can proceed from the prior §11
(CONDITIONAL PASS) state. §11A adds a standing caveat: absolute-path prompts
can be corrupted by the OpenClaw message layer before the model runs; treat
any future absolute-path read churn as this same family until the exact
function is traced at runtime.

## Reproduction

```bash
# 1) byte-clean input (od shows full path, no e2 80 a6):
od -c benchmarks/results/service-step-11a/probe-clean.md
# 2) run through the real agent path (driver retained pattern):
openclaw agent --agent kimi \
  --session-key agent:kimi:step11a-probe-64k \
  --message-file benchmarks/results/service-step-11a/probe-clean.md
# 3) inspect stored transcript user message (live DB):
#    ~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite
#    transcript_events seq=1 for session agent:kimi:step11a-probe-64k:
#    content contains /Users\xe2\x80\xa6VICE-ROADMAP.md
# 4) raw-model control (model clean on clean input):
python3 tools/service_step11a_raw_control.py
# 5) forensics summary:
#    benchmarks/results/service-step-11a/11a-forensics-summary.md
```
