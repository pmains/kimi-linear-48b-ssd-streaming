# Step 13 Report — Context Capacity Qualification and Production Promotion

## Status

**13A PASS, 13B PASS, 13C: 256K SELECTED, 13D PASS (2026-09-07)**. Production
context contract promoted to 262144 and verified through the real agent path
(agentMeta.contextTokens 262144, resolved). 64K rollback artifacts retained.
Stopped at the 13D gate awaiting owner go for 13E (real OpenClaw agent
qualification at 256K).

## Objective

Step 13 (owner-authored 2026-09-07, §13–13G) determines whether 128K or 256K
is the appropriate production context window for Kimi Linear, then promotes
and qualifies only the selected target through the real OpenClaw agent path.

13A specifically: reproduce the previously validated 128K capability
(ctx 131072) using the **current** runtime (frozen live binary a895f6826,
MXFP4 GGUF, E3E operating env) under controlled conditions — and record the
TTFT/memory tradeoff data the owner asked to track ("just so long as you
track what the tradeoffs are in terms of TTFT, we can decide on 256k or 128k
later. Right now, we are testing and exploring.").

## Changes

- `SERVICE-ROADMAP.md` — committed Pete's §13 text as `641b5f2`; this report
  updates §13 Status: PENDING → IN PROGRESS with the 13A PASS record.
- `tools/service_step13a.sh` — new retained 13A driver: single-llama-server
  swap protocol (bootout launchd 64K job → manual ctx-131072 server on the
  live port → gates/probes → unconditional restore of the launchd 64K job).
  Revision note: first version died mid-swap on a bash `set -u` bug
  (`local tmo=... deadline=$((...tmo...))` single-line form expands before
  assignment); production was restored manually and verified, and the driver
  was rewritten with split `local` statements, a preflight abort guard, and
  streamed TTFT capture.
- `benchmarks/results/service-step-13/` — baseline-64k JSON + `13a-128k/`
  evidence (preflight, probe-a, probe-b payload/stream, server log, startup
  mem lines, RSS samples/peak, results.json, needle). Telemetry CSVs
  (stats/retr/mem/cache_layers) are env-redirected into
  `13a-128k/telemetry/` and retained locally only (retr.csv ~1.8 GB — not
  committed, consistent with repo telemetry policy).
- This file: `service-progress/step-13-context-capacity.md`.

## Baseline (recorded 2026-09-07, before the swap)

64K qualified production state (see `baseline-64k-20260907.json`):

- llama-server: ctx 65536, MXFP4_MOE.gguf, `runtime/live` (a895f6826),
  ngl 999, no-mmap, parallel 1, port 18080; launchd job
  `com.openclaw.kimi-llama-server`; RSS ~8.8 GB at 64K.
- OpenClaw: kimi-local provider models[0] contextWindow 65536
  (unchanged during 13A, per roadmap).
- E3E env: naive stream, KIMI_EXPERT_CACHE_MB=8192, READ_WORKERS=4,
  zerocopy, KIMI_STREAM_METAL_STAGE=1, KIMI_STREAM_E2_DIRECT_PLACE=1.
- 11B: active pathname `/opt/homebrew/lib/node_modules/openclaw/dist/
  redact-CquADQ9-.js`, sha `8baf474684…`.
- Gateway PID 56719 healthz 200; llama 200; FK violations 0.
- GGUF metadata: `kimi-linear.context_length = 1048576` (1M trained ctx —
  both 131072 and 262144 are inside the trained range; rope freq_base
  10000, dim 64). 13A/B are therefore memory/Metal residency questions, not
  rope/out-of-range questions.

## Method (13A)

Single-llama-server swap window (owner-confirmed protocol 2026-09-07: "There
can be only one llama-server"):

1. Preflight record + abort guard (llama/gw must be 200 before swap).
2. `launchctl bootout` of the 64K launchd job; port-free wait.
3. Manual server start: identical binary/model/env, only `--ctx-size`
   131072; `-lv 4` observability; telemetry env vars redirected to the
   13A evidence dir; `--slot-save-path` omitted (stage-8 rung parity; avoids
   touching the production warm-state slot cache).
4. Gates: G1 allocation/startup healthy + KV/recurrent/Metal lines;
   G2 boundary prompt admitted with tokens > 65536; G3 short probe ok AND
   needle (embedded at 90% char depth ≈ 85.5K-token position — beyond the
   64K boundary) retrieved; G4 server healthy after.
5. Restore: kill test server → `launchctl bootstrap` the 64K plist → verify
   health, ctx 65536, FK 0, 11B sha unchanged.

## Results (13A, 2026-09-07)

Classification: **PASS** (`results.json`, window 10:43:27–11:30:22 MST).

| item | value |
|---|---|
| configured ctx | 131072 (server resolved n_ctx = 131072) |
| KV allocation | 1008.00 MiB (131072 cells, 7 layers, K f16) |
| KDA/recurrent | 42.81 MiB (27 layers, R 2.81 + S 40.00 MiB) |
| expert cache | armed 8192 MiB, zero-copy mode |
| Metal | ggml_metal_init OK (Apple M5); MTL0 KV buffer 1008 MiB |
| probe-a (short "ok") | ok=true; streamed TTFT 0.011 s |
| probe-b prompt | **95,004 tokens admitted** (gate2 pass) |
| needle | NEEDLE-13A-7394 retrieved exactly (position ≈ 85,504) |
| prefill | 2,797,977.72 ms / 95,004 tokens = **33.95 tok/s** |
| TTFT (long prompt) | ≈ prefill wall ≈ 2,798 s (~46.6 min) |
| decode | 1,868.04 ms / 10 tokens = **5.35 tok/s** at 95K ctx |
| total wall | 2,799.92 s (~46.7 min) |
| RSS | steady ~10.85 GiB; peak during prefill **11.80 GiB** |
| host free | 26% after run (24 GiB machine) — safe envelope |
| gate4 | server healthy after long-context use: pass |
| gates | G1 pass, G2 pass, G3 pass, G4 pass |

### TTFT tradeoff data (for the 128K-vs-256K decision)

- Short-prompt TTFT: 64K ≈ 0.103 s (baseline probe), 128K ≈ 0.011 s —
  no material change; short TTFT is not the cost of a bigger window.
- Long-prompt TTFT at 128K ≈ the full prefill (2,798 s for 95K tokens at
  33.95 tok/s). Prefill throughput at depth is the dominant cost and is
  expected to be the dominant differentiator at 256K.
- Decode degrades with context depth: ~9.3 tok/s at 14–21K ctx (Step 12
  eng-c1s12), ~7.4 at ~30K (Step 12 long-g1), **5.35 tok/s at ~95K ctx**
  (this run). A bigger window only matters where deep context is actually
  used; normal short/medium turns will decode at their usual depth.
- Memory headroom at 128K: peak 11.80 GiB of 24 GiB (26% free) with the
  8 GiB expert cache and 1008 MiB KV. 256K adds ~1 GiB KV (K f16 linear)
  plus prefill buffers — allocation feasibility is 13B's question.

## Problems

- **Driver v1 bash bug (recovered):** first 13A attempt died mid-swap on a
  `set -u` bug (single-line `local tmo=... deadline=$((...tmo...))` expands
  the arithmetic before `tmo` is assigned). The launchd job had already been
  booted out; production llama was down ~2 min until manual restore
  (`launchctl bootstrap`). Verified healthy (llama 200, ctx 65536, gw 200,
  sha `8baf4746`, FK 0) before re-running. Root cause fixed in the rewritten
  driver (split `local` statements + preflight abort guard); second run
  clean end-to-end with automatic restore verified.
- **Streamed TTFT metric caveat:** the first SSE frame arrives immediately
  (role/metadata frame), so the recorded streamed "TTFT" (0.073 s) is not
  time-to-first-content-token. True TTFT to first content ≈ prefill wall
  (2,798 s); reported as such above.
- Telemetry CSVs (1.8 GB retr.csv) kept locally, not committed (repo
  telemetry policy). All curated evidence is small and committed.

## Decisions

- Single-llama-server swap protocol for all Step 13 testing (owner order
  2026-09-07), restoring the launchd 64K job after every window so the
  validated 64K baseline is never left in an intermediate state.
- 13A uses the production model/env exactly; only ctx differs. GGUF trained
  ctx (1,048,576) recorded so 13B's 262144 ask is framed correctly.
- 13A classified PASS on the stage-8 invariant gates; no failure boundary
  to classify, no optimization opened.

## Next Phase

13B (probe 256K feasibility: allocation at 262144, then a 140–160K-token
prompt with a retrieval target beyond 128K) — same swap protocol, new
window. Requires owner go at the 13A gate (roadmap: 13A pass → proceed to
13B; owner said testing/exploring and decision on 128K vs 256K comes later).

## Reproduction

```bash
# 13A (swap window; restores 64K launchd job on exit regardless of outcome)
bash tools/service_step13a.sh
# evidence: benchmarks/results/service-step-13/13a-128k/{results.json,
#   preflight-*.json, probe-a.json, probe-b.json, probe-b.stream.json,
#   server-131072.log, startup.mem.txt, rss-samples.tsv, rss-peak.txt}
# baseline: benchmarks/results/service-step-13/baseline-64k-20260907.json
```

---

# 13B Addendum — 256K Feasibility Probe

## Status

PASS — classification **`256K FEASIBLE`** (2026-09-07, window 12:37:34–14:08:21
MST, owner order 11:40 MST). 64K launchd baseline restored and verified after
the window. No production context selected or promoted; STOPPED at the 13B gate
per owner order. 13C (select target) awaits the owner's 128K-vs-256K decision.

## Objective

Probe whether the current runtime (frozen live binary a895f6826, MXFP4 GGUF,
E3E operating env — 8 GiB expert cache, 4 read workers, unchanged settings)
can safely provide ctx 262144: Stage 1 allocation/startup at `--ctx-size
262144`; if healthy, Stage 2 exercise an actual 140K–160K-token prompt with a
deterministic retrieval target located unambiguously beyond token 131,072.
Do not fill the 256K window; do not optimize/alter expert-cache or runtime
settings to make 256K fit; restore and verify the qualified 64K baseline after
the window.

## Method

Same single-llama-server swap protocol as 13A (driver `tools/service_step13b.sh`):
bootout launchd 64K job → manual ctx-262144 server on live port 18080 →
Stage-1 gates → Stage-2 needle probe (blocking SSE read, watchdog armed:
RSS ≥ 18 GiB or host free ≤ 3% → abort) → unconditional restore of the 64K
launchd job → restore verification (llama 200 / n_ctx 65536 / gw 200 / 11B
sha `8baf474684` / FK 0 / no leftover test servers).

Three earlier attempts aborted and were archived with notes; none were runtime
failures: `13b-256k-run1-stage1/` (11:54, driver OOM-gate false positive on a
benign llama.cpp `fit params … abort` warning; Stage 2 not run — fixed to
fatal-only patterns), `13b-256k-run2-client-timeout/` (11:56, client died at
302.8 s: CPython http.client SocketIO permanently raises "cannot read from
timed out object" after the first socket read timeout — fixed to blocking read
+ guardian thread), `13b-256k-run3-harness-timeout/` (12:05, exec harness
SIGTERM at 30 min — driver launch lacked an explicit timeout; EXIT trap
restored production correctly; relaunched with 4 h timeout). Stage 1 passed
in all four windows; Stage 2 completed on run 4.

## Results (run 4)

Stage 1 — allocation/startup at ctx 262144 (all four windows healthy):
n_ctx resolved 262144; KV buffer 2016.00 MiB (262,144 cells, 7 MLA layers,
K f16); KDA recurrent RS 42.81 MiB (27 layers); expert cache armed 8192 MiB
zero-copy; Metal init OK (Apple M5, fusion/concurrency/graph-optimize true);
short probe ok (14 tok / 1,096 ms, TTFT 0.013 s); steady RSS 10.37 GiB;
host free 25% at handoff to Stage 2.

Stage 2 — beyond-128K needle probe (task 5):
- actual prompt tokens: **145,824** (content 145,806; template/system ~18)
  — inside the mandated 140–160K band; 256K window not filled
- needle `NEEDLE-13B-6937` embedded at 94% char depth; measured via live
  `/tokenize` + `/detokenize` binary search: content token **137,037**,
  prompt position ~137,039 — **5,967 tokens beyond 131,072**
- prompt admitted; response `NEEDLE-13B-6937` (exact match); no truncation
  (`done: true`, no `truncated`); no Metal/runtime failure
- prefill: 5,417,942.64 ms / 145,824 tokens = **26.92 tok/s**
- decode: 10 tokens / 2,769.47 ms = **3.61 tok/s** (Step-12-consistent
  formula; at 146K ctx depth)
- long-prompt TTFT (streamed first content frame): **5,420.7 s ≈ 90.3 min**
  ≈ prefill wall + ~3 s (13A caveat applies: for deep prompts first-frame
  TTFT ≈ prefill wall)
- KV usage at prompt depth: 1,121 MiB of the 2,016 MiB allocation
- peak RSS: **13,354,640 KB (12.73 GiB)** at 13:58:21 (13.5 GB / 24 GB,
  ~56%); steady ~12.6 GiB through prefill; post-run RSS 12.5 GiB; host
  free 21% after; watchdog never fired (5,304 samples, aborted=0)
- GATE4: server healthy after run; restore + verify clean

Memory comparison vs 13A (95K ctx run): idle/steady RSS comparable (~10.4
vs ~10.85 GiB — expert-cache-dominated); prefill working set ~12.6 GiB at
146K vs ~11 GiB at 95K; peak 12.73 GiB vs 11.80 GiB. KV scales linearly
(2016 MiB @ 262144 vs 1008 MiB @ 131072); recurrent/KDA and expert cache
unchanged (42.81 MiB / 8192 MiB).

## Problems

- Three driver-side abort attempts before the clean run (false-positive OOM
  grep; Python http.client read-timeout socket poisoning; exec-harness
  30-min default timeout on a backgrounded launch). None indicated a runtime
  or memory problem; each was fixed and the failure preserved as archived
  evidence with notes.
- Driver gap: `results.json` left `decode_tok_s` null (post-processed to
  3.61 in the retained artifact).
- Operability at depth is real: prefill 26.9 tok/s at 146K (vs 33.95 at
  95K, 13A) and decode 3.61 tok/s at 146K (vs 5.35 at 95K, 13A). A full
  ~146K prompt costs ~90 min prefill before the first token. This is the
  operating cost the 13C 128K-vs-256K decision must weigh (tracked per
  owner: TTFT tradeoffs).

## Decisions

- Classified **`256K FEASIBLE`**: allocation reliably healthy across four
  windows; useful >128K operation demonstrated (retrieval at ~137K);
  memory safely within the 24 GB envelope (peak 12.73 GiB, host free 21%);
  short-context behavior unchanged (probe-a TTFT 0.012–0.013 s, same as
  64K/13A). The depth-throughput cost is documented, not disqualifying —
  "slow prefill alone is not a Step 13 failure unless operationally
  unusable", and the FEASIBLE-WITH-LIMIT/NOT-PRACTICAL buckets are for
  allocation/memory/operability failures, none of which occurred.
- Did NOT select or promote a production context (owner order: STOP at the
  13B gate). 128K (13A) remains the demonstrated production candidate until
  the owner chooses at 13C.

## Next Phase

13C: owner selects 128K or 256K using this evidence (allocation stability,
peak/steady memory, KV cost, prefill cost, operational stability, usefulness
for real workloads). If 256K is selected, 13D aligns the OpenClaw production
context contract (llama 262144 + OpenClaw contextWindow 262144) and verifies
the resolved runtime value through the real agent path.

## Reproduction

```bash
# 13B (swap window; restores 64K launchd job on exit regardless of outcome)
bash tools/service_step13b.sh
# evidence: benchmarks/results/service-step-13/13b-256k/{results.json,
#   stage1.json, calibrate.json, probe-a.json, probe-b.json,
#   probe-b.stream.json, probe-b.prefill, probe-b.eval, needle.txt,
#   rss-peak.txt, rss-samples.tsv, watchdog.tsv, server-262144.log,
#   startup.mem.txt, restore-verify.json}
# aborted attempts: 13b-256k-run1-stage1/ (OOM-gate false positive),
#   13b-256k-run2-client-timeout/ (client bug), 13b-256k-run3-harness-timeout/
#   (exec timeout) — each with a runN-note.md

---

# 13C/13D Addendum — 256K SELECTED; Production Contract Aligned (2026-09-07)

## Status

**13C: SELECT 256K (owner decision, 14:23 MST). 13D: PASS (14:40 MST).**
Production context contract promoted to **262144** on both sides and verified
through the real OpenClaw agent path: `agentMeta.contextTokens = 262144`
(`contextTokensSource: resolved`). 64K rollback artifacts retained. Stopped at
the 13D gate for owner go on 13E.

## Objective

13C: owner chooses 128K or 256K from the 13A/13B evidence. 13D: promote only
the selected target — llama-server ctx 262144 AND OpenClaw contextWindow
262144; identify every OpenClaw setting that could silently retain the former
64K ceiling; verify the resolved runtime value through the real agent path
(not config text alone); preserve rollback to the qualified 64K production
state.

## Changes

- `SERVICE-ROADMAP.md` — §13 Status updated; 13C decision block added
  (owner 2026-09-07 14:23: select 256K; rationale: safe allocation 4/4
  windows, short-context unchanged, >128K retrieval proven, peak 12.73 GiB;
  128K would not remove the prefill cost, only cap usable context).
- `tools/service_step13d.sh` — new retained 13D driver: preflight →
  plist KIMI_CTX 65536→262144 (repo + installed) → launchd relaunch →
  gate (healthy, resolved n_ctx 262144, short probe OK) → openclaw.json
  contextWindow 65536→262144 (kimi-local AND llama-server provider entries
  for kimi-linear-48b; maxTokens untouched) → gateway hot-reload wait →
  real-path resolution turn → final verify + evidence. EXIT trap restores
  64K automatically if any gate fails (never leaves production half-aligned).
- `tools/com.openclaw.kimi-llama-server.plist` — KIMI_CTX 65536 → 262144
  (repo copy; installed copy updated by driver).
- `~/.openclaw/openclaw.json` — production config, outside repo:
  contextWindow 262144 on both kimi-linear-48b provider entries; backup
  `~/.openclaw/openclaw.json.bak-step13d-20260907` (sha before
  `efe1f7a2…`, after `1d5c5afc…`). Gateway hot-reloaded
  (`[reload] config hot reload applied (models.providers.kimi-local.models,
  models.providers.llama-server.models)`) — no gateway restart needed.
- `benchmarks/results/service-step-13/13d-256k/` — driver.log, results.json,
  probe-a.json, startup.mem.txt, realpath/ (resolve-262144.* incl. final
  doc record), rollback-64k/ (plists + README with exact rollback commands).

## Results

| item | value |
|---|---|
| configured ctx (llama) | 262144 — launchd job `com.openclaw.kimi-llama-server` pid 7012 (14:36:18 START, ctx=262144 cache=8192) |
| resolved n_ctx | 262144 (server /props) |
| OpenClaw contextWindow | 262144 (kimi-local + llama-server → kimi-linear-48b) |
| gateway hot reload | applied 14:36:29 (models.*.kimi-linear-48b), gateway healthz 200 |
| short probe | OK, wall 1.57 s |
| real-path resolution | headless agent turn (`openclaw agent --agent kimi --model llama-server/kimi-linear-48b`): **contextTokens 262144, contextTokensSource "resolved"**, promptTokens 12,726, doc ok, rc 0, server prefill 64.65 tok/s @12.7K |
| 11B sha | 8baf474684 (unchanged) |
| FK violations | 0 |
| telemetry | e3e-promotion CSVs unchanged by this step (repo policy: not committed) |

## Problems

None. Driver ran clean on the first pass (contrast 13A/13B's aborted
attempts). startup.mem.txt captures the launchd log, which is less verbose
than the -lv 4 test-server logs, so KV-allocation lines from this boot are
not in the highlight file — KV 2016 MiB @ 262144 was already measured in 13B
(4/4 windows) and is unchanged by this promotion (same binary/model/ctx).

## Decisions

- 13C: **256K** selected by owner. Deep-context prefill cost (~26.9 tok/s at
  146K) accepted as the operating cost of the chosen ceiling; not an
  optimization target in this phase.
- 13D changes are production-promotion, not swap-window: the launchd plist
  itself now runs ctx 262144 permanently, and OpenClaw's provider config
  declares 262144, so client admission/compaction resolves against the real
  server window. Rollback to the qualified 64K state is a retained
  procedure, not the routine restore path (13A/B drivers' restore step would
  now restore the 262144 plist — future test windows must re-swap explicitly).
- Verification is through the real agent path (contextTokens 262144 resolved
  in an actual agent turn), per §13D "Do not rely only on configuration
  text."

## Next Phase

13E — real OpenClaw agent qualification at 256K (short control, eng/tool
turn, Poliscopic turn, >64K agent turn, and per 13E item 5 a >131072-token
real-agent prompt). Requires owner go at the 13D gate.

## Reproduction

```bash
# 13D promotion driver (idempotent; restores 64K on any gate failure)
bash tools/service_step13d.sh
# evidence: benchmarks/results/service-step-13/13d-256k/{results.json,
#   driver.log, probe-a.json, startup.mem.txt, realpath/, rollback-64k/}
# 64K rollback: procedure + artifacts in
#   benchmarks/results/service-step-13/13d-256k/rollback-64k/README.md
#   (openclaw.json backup: ~/.openclaw/openclaw.json.bak-step13d-20260907)
