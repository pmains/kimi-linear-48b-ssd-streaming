# Step 11A Forensics Summary — U+2026 Path-Truncation Localization

Date: 2026-09-05 · Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming`
Companion: design `service-progress/step-11a-u2026-path-truncation-design.md`; report `service-progress/step-11a-u2026-path-truncation-report.md`.

## Question

A byte-valid absolute path (e.g. `/Users…VICE-ROADMAP.md`)
sometimes becomes a U+2026 middle-truncated form (e.g. `/Users…VICE-ROADMAP.md`)
in real agent runs. Where does the corruption enter? (model output · OpenClaw
tool-call handling · message/transcript layer)

## Controls and results

### 1. Input files are byte-clean (authoring hypothesis REFUTED)
All step-11/10d/10c/09 prompt files contain 0 U+2026 (byte-accurate python
`"\u2026" in s` scan + `od` spot checks). `G1.md` and `probe-clean.md` were
od-verified: full valid path present, no U+2026. Only the assistant's own
report/design docs contain U+2026 (quoted artifact examples).

### 2. Raw model output is clean on clean input (model-emission REFUTED for clean inputs)
8 direct llama-server `/v1/chat/completions` trials, tool-call schema, user
message with the full clean path. All 8 returned
`{"path": "/Users…VICE-ROADMAP.md"}` — byte-clean,
zero U+2026 (retained: `raw-control-{0..7}.json` + `.request.json`).
→ The model does not corrupt clean input; a corrupted path in the transcript is
therefore not model-invented from clean text at this schema.

### 3. Clean-probe reproduction: corruption exists in the stored transcript BEFORE the model runs
Driver: `openclaw agent --message-file probe-clean.md` (same turn driver as the
G/10D legs), fresh session `agent:kimi:step11a-probe-64k`, single-slot 64K server.
- `probe-clean.md` on disk: byte-clean (od).
- Live gateway DB (`~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite`),
  `transcript_events` seq=1 user message content, byte dump:
  `b'Please read the file at exactly this path: /Users\xe2\x80\xa6VICE-ROADMAP.md\n\nThen reply with the wor'`
  → real U+2026 present at message-store time. seq=1 timestamp == session
  creation; model snapshot at seq=3 (+0.8s); first assistant output seq=4 (+20s).
- Model then emitted byte-identical corrupted read args
  (`{"path": "/Users…VICE-ROADMAP.md"}`), executed reads failed
  File-not-found on the corrupted path (the clean-path file exists, so a clean
  read would have succeeded), churn until the 200s client cap.
→ Corruption is present in OpenClaw's stored user-message content pre-model; the
model faithfully copies it (matches the G-session and 10C/10D anchor churn).

### 4. finalPromptText vs stored content discrepancy
`probe.record.json` records `finalPromptText` CLEAN (full valid path) while the
stored transcript content is corrupted. The doc/monitor's resolved "prompt text"
(marker machinery: markTranscriptPromptText / restoreTranscriptPromptText /
projectTranscriptPromptMessages in dist) can differ from message content. The
executed reads used the corrupted path, so the assembled model context carried
the corrupted content; the clean finalPromptText is a separate (marked/clean)
copy, not proof the model saw clean text.

### 5. Not every long path corrupts (open question)
Live-DB comparisons, all same gateway process (PID 59990 since 12:34):
- `step10d-64k-loop-strict-r1` seq=1: 88-char path
  `/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-10d/target-file.md`
  stored CLEAN (U2026=False).
- `step11-64k-research` / `step11-64k-code` seq=1: paths stored CLEAN.
- `step11-64k-growth` (G1) seq=1: `/Users…VICE-ROADMAP.md` CORRUPTED.
- `step11a-probe-64k` seq=1: CORRUPTED.
→ Not a pure "token length" rule; the triggering condition is unresolved
(candidate: a normalization/compaction pass that fires on specific content
shape/route). 128k-OOM sessions are inconclusive (their user messages contain no
`/Users` token at all).

### 6. Dist search: no exact-match truncator found
Exhaustive scan of dist for U+2026 + slice/substring helpers: many exist
(coerceDisplayValue 79/80@160, compactRawCommand half/half@120,
compactProgressLineDetail 45%/rest, maskLifecycleIdentifier 4+4, redactSessionKey
6+6, shortId 8+4/12+4, truncateTitle, event-store truncateUtf8 tail-only, etc.).
None reproduces the observed token-level signature
(`/Users` + `…` + `VICE-ROADMAP.md`; head 6 + tail 15; surrounding sentence
byte-identical) with a fixed rule. Corrupted tails vary across samples
(VICE-ROADMAP.md 15 chars; DMAP.md 7 chars) → consistent with a code path that is
not a single fixed-window truncator, or with truncation at different layers over
time. Exact function NOT conclusively identified.

## Classification

- Authoring-layer artifact (prompts corrupted before run): **REFUTED** (files byte-clean).
- Model emits U+2026 from clean input: **REFUTED** for the exercised schema/input (8/8 clean).
- OpenClaw-side corruption in the message/transcript layer, PRE-model: **SUPPORTED**
  (byte-clean file → corrupted stored user message; model then copies it; executed reads fail on corrupted path).
- Exact transform + trigger condition: **NOT isolated** (open question; candidates listed in report).

## Retained evidence (this directory)
probe-clean.md · probe.client.json · probe.record.json · probe.out ·
probe.gateway.window.log · probe.llama.window.log · probe.slots.json ·
raw-control-{0..7}.json(+.request.json) · raw-trial-{0..4}.json ·
11a-forensics-summary.md (this file)
DB copies: /tmp/11a-agent.sqlite (live agent DB snapshot), /tmp/11a-probe.sqlite.
Live DB: ~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite (session
agent:kimi:step11a-probe-64k, seq=1 corrupted bytes confirmed).
