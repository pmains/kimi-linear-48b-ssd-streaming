# 13G evidence notes (2026-09-07)

- Measured turn: kg-maintenance, agent poliscopic, model forced
  llama-server/kimi-linear-48b by the retained runner -> rides the live
  262144 Kimi contract (same path as 13E leg 3). No swap window; the
  production 256K launchd job was the state under test.
- Idle gate: llama slot IDLE + no other agent/turn processes before launch
  (20:10:00 MST); nothing was terminated or interfered with.
- rc 0, wall 1410.7 s (23.5 min), NOT a client timeout (2700 s budget),
  liveness working, doc_status ok. replayInvalid=true is the same metadata
  field observed on successful TOOL-USING turns in 13F (norm, p5) - it
  flags tool-call replay non-reproducibility, not a run failure; rc 0,
  doc ok, single llama-server attempt, no fallback.
- contextTokens 262144 / source "resolved" (the live contract; poliscopic's
  stale 65536 agent-store copy again did NOT impose a ceiling).
- Assembled at deepest call: promptTokens 47,626; llama slot n_prompt
  48,054 (slot count incl. tokenizer rounding), truncated=0 on every task.
  Available remaining = 262144 - ~48,054 = ~214,090 tokens.
- Fixed/bootstrap material (systemPromptReport): system 41,770 chars +
  project/workspace 24,942 + tools schema 44,651 + skills 4,995 + user task
  content. ~116K chars of fixed context (~29K tokens at ~4 ch/tok): under a
  64K window this fixed material alone would consume ~45% of budget; under
  262144 it leaves the working headroom above. Measured material
  improvement in usable working context vs the former 64K baseline.
- usage (cumulative across 58 gateway model-fetches / 17 llama tasks):
  input 46,780, output 1,291, cacheRead 540,732 (multi-call prompt-cache
  reuse; last call cacheRead 47,482 of ~48,054 = prompt cache working).
- Prefill/TTFT: tool-round prefills dominated by KV reuse; llama window
  shows per-task prompt processing; final release n_tokens 48,054 at task
  4554. Decode: ~5.7-5.8 tok/s sustained at depth (task 4554 n_gen 419+),
  consistent with 13E decode-at-depth cost; not a Step 13 failure
  (capacity measurement, no optimization authorized).
- Compaction: 0 events in llama + gateway windows; no truncation anywhere.
- Read-only preserved: poliscopic workspace git status identical before/
  after (54 lines both), KG sqlite sha256 identical (3 DBs), file
  inventory identical (3,475 files), no new files, no sqlite sidecars.
- The agent's reply (2,151 chars) is a real KG maintenance assessment with
  concrete counts (entities 99,022; mentions 273,699; relationships
  11,447; meeting events 39,132; participants 402,514) + integrity
  indicators + next-priority recommendation (relationship provenance
  resolution). Content evidence only - read-only gate is the objective
  compliance check.
- Health envelope: watchdog peak llama RSS 9.85 GiB (limit 18 GiB), host
  free 15-21%; final verify llama 200 / n_ctx 262144 / gw 200 / 11B sha
  8baf474684 / kimi FK 0 / poliscopic FK 0 / pids 7021+56719 constant /
  zero config drift vs the 13D snapshot.
