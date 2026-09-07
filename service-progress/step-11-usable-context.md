# Step 11 Report — Qualify Usable Agent Context

Date: 2026-09-05 · Author: Alkaline (agent) · Authorization: Pete *** MST
Repo: `github.com/pmains/kimi-linear-48b-ssd-streaming` <!-- project: github.com/pmains/kimi-linear-48b-ssd-streaming -->

## Status

**CONDITIONAL PASS (determination) — STOPPED for review. No production
change.**

Qualified usable context at the current production configuration (Kimi at
aligned 64K, `tools.loopDetection.enabled: true`) with realistic sustained
OpenClaw workloads. Context size, sampler, prompting, and liveness
configuration were NOT changed.

## Objective (roadmap §11, scoped to production config)

Determine how much of the aligned 64K window the real OpenClaw agent can use
productively under realistic sustained workloads, and whether the service
remains stable as sessions grow. Stage 8's 128K native-runtime result was not
re-tested; 256K was not attempted (production window is 64K).

## Method

30 realistic same-key multi-turn turns across three sustained sessions on the
single-slot 64K server (launchd, untouched), fresh `step11` session keys,
full per-turn instrumentation (retained `service_step10a_turn.py`: llama
window, gateway window, /slots, final doc, client.json):

- **Session R** (10 turns) — research conversation: read AGENTS.md/TOOLS.md,
  cross-turn recall questions (R2/R6/R10), exec queries, obsolete-instruction
  test (R8), synthesis. Realistic repo research with growing same-key
  context.
- **Session C** (8 turns) — coding work in
  `benchmarks/results/service-step-11/work/code/`: write `text_stats.py` +
  tests, run/fix cycles via exec, refactor, docstring/CLI, final verify.
- **Session G** (12 turns) — context growth to auto-compaction: sequential
  large-file digest turns (SERVICE-ROADMAP.md ~85 KB, service-progress
  reports, benchmarks summaries) designed to grow promptTokens past the
  compaction threshold and continue post-compaction.

Drivers: `tools/service_step11_leg.sh` (resumable), `tools/service_step11_analyze.py`.
Design: `service-progress/step-11-usable-context-design.md`.

## Results

### Context accumulation and compaction

- Same-key promptTokens grew monotonically: R 17,379 → 27,218 (10 turns);
  C 13,907 → 33,937 (C5) then auto-compaction (C6 events) → 22,664 (C7);
  G 17,554 → 31,165 (G4) then auto-compaction (G5 event: 31,165 → 17,064,
  gateway log `auto-compaction succeeded ... retrying prompt`, post-
  compaction guard armed) → recovered to 29,793 (G12).
- Auto-compaction fired successfully at ~30–34K assembled prompt tokens
  (well under the 64K window) and sessions **continued correctly after
  compaction** (G5, G10–G12, C7, C8 all rc=0).
- No technical ceiling reached in any turn: max assembled promptTokens
  ~34K; no context-overflow, no OOM, server stayed 64K for the full 3.3 h
  leg (llama-server pid unchanged).

### Session R — research conversation: 10/10 rc=0 (PASS quality)

- Walls 14.2–667.9 s (growth-driven); zero failures, zero compaction events,
  zero loop events.
- Cross-turn recall correct at 27K context (R2: AGENTS phase-completion
  report file + Reproduction section; R6/R10: earlier-turn content).
- Obsolete-instruction test (R8) handled correctly (listed topics instead of
  re-summarizing).
- R10 quality check: correctly recalled the step-10D PASS and operating
  rules; conflated the §11 milestone numbers with the three *ceiling types*
  (technical/useful/practical) — minor retrieval imprecision at 27K, not a
  functional failure.

### Session C — coding work: 6/8 rc=0 (PASS with 2 client-cap timeouts)

- Real artifacts created and compiling: `text_stats.py` +
  `test_text_stats.py` (+ supporting debug files), `py_compile` OK.
- C2 rc=1 = client timeout at the 1200 s cap on **legitimate sustained
  multi-step work**: 34 assistant turns, 33 tool calls (write/exec/read/
  edit), **0 failures**, 0 loop events, 0 compaction. The model was
  iterating correctly but exceeded the per-turn client cap — a practical
  (not correctness or loop) limit.
- C6 rc=1 = client timeout with 2 auto-compaction events mid-turn (heavy
  turn + compaction cost exceeded the cap).

### Session G — growth to compaction: 5/12 rc=0 (mechanism PASS, quality limited)

- Auto-compaction **demonstrated working** (G5: 31,165 → 17,064, rc=0
  after; post-compaction turns G10–G12 rc=0).
- Failures (G1–G3, G6, G8–G9 rc=1, `livenessState: blocked`) were **NOT
  context-capacity limits** — promptTokens were only 17.5–28.5K. Root cause
  (transcript evidence): the model repeatedly emitted **U+2026-truncated
  absolute paths in read tool calls** (same artifact family as the P1-r5/
  P2-r7 anchors), e.g. read args `{"path": "/Users…VICE-ROADMAP.md"}` and
  `/Users…vice-progress/step-10c-...md` → File-not-found churn (250+
  occurrences in session G), argument churn across read/sessions_search/exec
  → runs ended blocked / "LLM request failed" (replayInvalid, liveness
  blocked).
- Loop detector correctly did NOT fire on these (paths/args varied → not
  identical no-progress), so no false positives; but the churn is
  resource-wasteful and ended runs in error rather than a clean answer.
  This is a model tool-call reliability issue under sustained multi-file
  workloads, not a 64K capacity boundary.

### Stability and liveness

- Gateway + llama-server up for the entire 3.3 h leg; zero real
  loop-detector events on legitimate work (no false positives);
  post-compaction guard armed only after real compaction events (G5/C6).
- Zero OOM, zero precheck overflow at 64K.

### Latency / TTFT / cache

- Decode steady 6.4–8.9 tok/s (matches earlier 5–9 tok/s finding).
- TTFT (first prompt-eval time) grows with assembled context: seconds at
  small context; ~100–350 s first-eval observed at 25–31K assembled
  (prefill ≈ 43 tok/s) — the dominant per-turn latency cost at large
  context.
- Cache reuse high and growing: cacheRead 20K–920K tokens/turn; usage.input
  small relative to cacheRead on later same-key turns.

## Problems

- Session G's large-file digest workload surfaced a recurring model
  artifact: U+2026-truncated read paths → File-not-found churn → blocked
  runs. Same family as the Step 10 anchors; appears under sustained reads of
  many files with long absolute paths. Not detector-fixable (not identical
  repeats); a model reliability item.
- Client per-turn cap (1200 s) cut off legitimate long coding turns (C2,
  C6) — a practical ceiling for multi-step agent work at ~7 tok/s decode.
- Analyzer's gateway-window "loop event" grep can match content lines that
  merely mention loop topics (G4/G5/G12 counts); transcript is the
  authoritative source (real detector events: none on legit work).

## Decisions

- No production change. Scoped Step 11 to the current production config
  (aligned 64K) per owner order; did not re-run the Stage 8 128K native
  ladder and did not trigger the 256K gate (no evidence more context would
  help; binding limits are per-turn latency, the client cap, and the model
  path-truncation artifact).
- Kept same-key multi-turn sessions (real context accumulation) rather than
  fresh-session probes; instrumentation unchanged.

## Acceptance answers (roadmap §11)

1. **How much working context does the agent actually need/use?** Realistic
   sessions operated well at 17–34K assembled prompt tokens; auto-compaction
   engaged at ~30–34K and kept sessions usable beyond it.
2. **Quality as context grows?** Acceptable through ≥27K (R 10/10 with
   correct recall); coding correct through 34K; minor retrieval conflation at
   27K.
3. **Reliable retrieval/prioritization?** Yes within the research session at
   27K (cross-turn recall correct).
4. **Latency cost of larger context?** Decode flat ~7 tok/s; TTFT rises with
   assembled context (~100–350 s first-eval at 25–31K) — the dominant cost;
   1200 s client cap reached on legit long coding turns.
5. **Current useful/practical ceiling?** Useful ceiling not reached in
   research/coding workloads through ~34K assembled. Practical ceiling:
   per-turn latency + 1200 s client cap on long multi-step turns; plus the
   U+2026 path-truncation churn failure mode under sustained multi-file
   reads (model reliability, not capacity).
6. **256K value evidence?** None produced; not triggered.

**Classification: CONDITIONAL PASS** — the aligned 64K window supports
realistic sustained agent work to ≥ ~30K assembled with working
auto-compaction and stable service; residual limits are model tool-call
reliability (truncated-path churn) and the 1200 s per-turn cap, not context
capacity.

## Next Phase

Owner review. Options on approval: (a) prompt/sampler-level mitigation study
for the U+2026 path artifact (outside Step 10/11 scope, no config change
here); (b) Step 12 once the usable-context requirement is accepted; (c)
re-run G with shortened file lists or explicit "use the exact path shown"
instruction to separate artifact from workload design.

## Reproduction

```bash
# Design
service-progress/step-11-usable-context-design.md
# Run (30 turns, ~3.3 h on single-slot server)
bash tools/service_step11_leg.sh      # env: STEP11_OUT, STEP11_TIMEOUT
# Analyze
python3 tools/service_step11_analyze.py benchmarks/results/service-step-11
# Evidence
benchmarks/results/service-step-11/{env.txt,leg.log,64k/{R,C,G},analysis,work/code}
```
