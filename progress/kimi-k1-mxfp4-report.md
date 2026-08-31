# Phase K1 Report — MXFP4 vs Q4_K_M on Kimi-Linear-48B-A3B (Streamed, Bounded Cache)

## Status

PASS (with documented limitations — see Problems)

## Objective

Test the original K1 hypothesis on the streamed runtime:

> MXFP4 quantization → smaller expert tensors → higher expert-cache density
> → more cache hits → less SSD reload traffic → faster decode, without
> unacceptable quality loss.

Measured against Q4_K_M (A) as the frozen baseline under identical
streaming machinery (`KIMI_STREAM_EXPERTS=naive`, 4096 MiB expert cache,
zerocopy mode, `-ngl 0` CPU path — the same configuration as the frozen
9G harness). A second configuration (cache=0, "uncached") isolates the
compute-path effect from the cache effect. Quality is assessed with a
bounded wikitext-2-raw perplexity run through the same streamed runtime.

Acceptance criteria are unchanged from the K1 plan; no new experiments
were added.

## Changes

- `tools/phasek1_perplexity.sh` — **fixed a parser bug** (see Problems):
  the result extractor searched for `PPL\s+([\d.]+)` but llama-perplexity
  prints `Final estimate: PPL = 6.6681` (with `=`), so every run wrote
  `final_ppl: null`. The regexes now accept `PPL = X` and parse the
  per-chunk `[n]value` lines. No PPL compute was wasted: both runs had
  completed successfully, so the real values were re-extracted from the
  existing `ppl.log` files and written into the result JSONs.
- `benchmarks/results/phase-k1/ppl/q4km/ppl-result.json` — corrected
  `final_ppl` 6.6681, `n_ppl_values` 32.
- `benchmarks/results/phase-k1/ppl/mxfp4/ppl-result.json` — corrected
  `final_ppl` 6.7596, `n_ppl_values` 32.
- `benchmarks/results/phase-k1/metal-smoke.log` — full-offload Metal
  attempt (failed, see Problems).
- `benchmarks/results/phase-k1/metal-smoke-streamed.log`,
  `benchmarks/results/phase-k1/metal-smoke-streamed-nonzc.log` —
  streamed Metal attempts (failed, see Problems).
- `progress/kimi-k1-mxfp4-report.md` — this report.

## Results

### Perplexity (quality) — streamed runtime, `-ngl 0`, 4096 MiB cache

| model | final PPL (chunks=32, ctx=512) | vs Q4_K_M |
|---|---|---|
| Q4_K_M | **6.6681** | — |
| MXFP4 | **6.7596** | **+1.37%** |

Both runs completed with the expert streamer active (cache armed, 4096
MiB, zero-copy; Q4_K_M 38..39 slots/layer, MXFP4 43..44 slots/layer).
MXFP4's perplexity cost is small (+1.37% relative), consistent with
"without unacceptable quality loss".

### Cached benchmark (coding-cap4, 4096 MiB cache) — `benchmarks/results/phase-k1/full/phase-k1-coding-cap4-summary.json`

| metric | A (Q4_K_M) | B (MXFP4) | delta |
|---|---|---|---|
| median S (B/A per-bracket time) | — | **1.479** | +48% |
| bootstrap95 CI of median S | — | **[1.311, 1.564]** | — |
| decode tok/s | 5.080 | 7.516 | +48% |
| SSD traffic MB/tok | 286.4 | 267.3 | **−6.7%** |
| decode expert hit rate | 0.6642 | 0.6416 | −2.3 pts |
| resident decode MB | 5584 | 5998 | +7.4% |
| A moe md5 vs frozen baseline | `da45ab777b0fdd99be62f6c46a642e5c` | — | identical |
| phase07 per-step invariants | 30/30 PASS | 30/30 PASS | — |
| analyzer checks | 14/14 PASS | | |

### Uncached benchmark (cache=0) — `benchmarks/results/phase-k1/uncached/phase-k1-uncached-summary.json`

| metric | A (Q4_K_M) | B (MXFP4) | delta |
|---|---|---|---|
| median S (B/A per-bracket time) | — | **1.871** | +87% |
| bootstrap95 CI of median S | — | **[1.468, 2.077]** | — |
| decode tok/s | 2.449 | 4.689 | +91% |
| SSD traffic MB/tok | 850.1 | 745.9 | **−12.3%** |
| decode expert hit rate | n/a (cache=0) | n/a (cache=0) | — |
| resident decode MB | 1540 | 1957 | +27% |
| A retr byte-identity | identical | — | — |
| phase07 per-step invariants | 30/30 PASS | 30/30 PASS | — |
| warmup disclosure flag | **TRUE** (first bracket 2.592 vs rest 2.234 tok/s, +16.0% rel) | | |

### Routing / output divergence

- `route_divergence_fraction` = **0.9184** in every bracket of both
  configurations: 91.84% of routed-expert selection rows (the
  per-position top-8 expert ids in moe.csv) DIFFER between A and B.
  The fraction is identical across all 10 brackets in both sessions
  (routing is deterministic at temp 0), so this is a fixed
  quant-induced routing change, not run-to-run noise.
- Generated text for the coding prompt (128 tokens, temp 0): the two
  streams are byte-identical for the first 649 chars — the model's
  re-echo of the prompt's opening requirements — and diverge at the
  start of the substantive answer ("...fine-grained locking to
  minimize contention. Here's the complete implementation:" vs
  "...fine-grained locking. This design balances performance with
  simplicity."). Same divergence position in both configs. Token
  identity is therefore NOT preserved (the two models emit different
  code), which is why the wikitext PPL comparison above — not token
  identity — is the quantitative quality gate. Identical text for the
  first ~650 chars despite 92% routing divergence shows the greedy
  argmax is robust to the perturbation until activation drift
  accumulates.

## Mechanism interpretation (required framing)

- **Cache/storage-density mechanism: MODESTLY SUPPORTED.** MXFP4 packs
  more expert slots per cache MiB (43..44 vs 38..39 slots/layer, ~+12%
  density) and cuts SSD traffic (cached −6.7%, uncached −12.3% MB/tok).
  But the decode hit rate did NOT improve (0.6416 vs 0.6642 — it went
  slightly *down*), so the original "more hits → less reload" causal
  chain did not materialize. Density gains were largely offset by the
  same routing pattern requesting the same experts.
- **The unexpected measured mechanism is the MXFP4 compute path itself:**
  direct 4-bit dot products (4.25 bits/elem) make every decode step
  cheaper regardless of cache behavior. That explains why the speedup is
  *larger* when the cache is disabled (+87% uncached vs +48% cached):
  with no cache, compute is the dominant cost, and MXFP4's cheaper
  math wins big; with a warm cache, SSD-wait already hides much of the
  compute difference.
- Net: the K1 hypothesis is refuted as stated (hit-rate link), but the
  project's goal — meaningfully faster productive decode from MXFP4
  under a bounded memory budget — is confirmed, with quality cost
  measured at +1.37% PPL.

## Problems

1. **Harness driver killed twice mid-run** during the K1 bench runs
   (truncated runs at 99/49 decode rows that still passed the stats.csv
   resume check). Both were caught by the analyzer's decode-row-count
   verification and the affected runs re-executed. Recorded here so the
   artifact set is understood to contain re-runs.
2. **Analyzer `moe_divergence` parse bug** — moe.csv files carry a
   comment header; the parser mis-handled it. Fixed 2026-08-30.
3. **Analyzer final-print `None` crash on cache=0** — the summary
   printer assumed a hit-rate field existed; uncached runs have none.
   Fixed.
4. **Uncached warmup disclosure flag** — first bracket is +16.0% rel
   faster than the rest (2.592 vs 2.234 tok/s). Brackets 3/5/6/7 show
   load spikes (Brave/NordVPN). Flagged, not hidden; the median S and CI
   are computed over all brackets and the position-bias check passes
   (mean delta −0.99% rel, sign test p=0.945).
5. **PPL result parser bug (found and fixed this session)** — the
   extractor regex did not match llama-perplexity's `PPL = X` output, so
   both PPL runs originally produced `final_ppl: null`. Fixed in
   `tools/phasek1_perplexity.sh`; real values re-extracted from the
   completed logs (no compute rerun required).
6. **Metal MXFP4 smoke test FAILS** — all three attempted variants
   failed, and the failure is structural:
   - Full offload `-ngl 999` (no streaming env): `llama-cli` exits 0
     but decode never runs — `ggml_metal_synchronize: error: command
     buffer 0 failed with status 5` /
     `kIOGPUCommandBufferCallbackErrorOutOfMemory` at the first ubatch.
     Cause: the 25 GB MXFP4 GGUF + KV cache + compute buffers cannot fit
     in 24 GB unified memory when fully offloaded. No generated text, no
     `offloaded` line, 0.0 tok/s.
   - Streamed on Metal (`KIMI_STREAM_EXPERTS=naive`, zerocopy,
     `-ngl 999`): SIGABRT (exit 134),
     `ggml-metal-context.m:359: GGML_ASSERT(buf_dst) failed` inside
     `ggml_metal_cpy_tensor_async`, called from
     `process_ubatch_streamed` → `llama_decode` ret -3.
   - Streamed on Metal, default (non-zerocopy) cache mode: identical
     SIGABRT at the same assert.
   - Root cause: the expert-streamer's loaded/temp tensor path is not
     Metal-buffer compatible — the streamed ubatch path issues a Metal
     `cpy` whose destination buffer is null. **All K1 measurements in
     this phase (decode tok/s, SSD MB/tok, resident MB, PPL) were taken
     on the `-ngl 0` CPU path**, which is the path the frozen benchmark
     harness uses (`tools/phase04_run_streamed.sh` invokes `-ngl 0`).
     Metal + expert streaming is therefore NOT yet validated and is the
     primary open work item.

## Decisions

- Keep the K1 acceptance criteria as planned; do not re-define success
  to fit the mechanism that actually showed up.
- Report the MXFP4 win as a compute-path effect with a modest
  storage-density assist, not as validation of the original hit-rate
  hypothesis.
- Do not treat exit code 0 from `llama-cli` as smoke-test success —
  verified generated text, offload markers, and decode completion
  instead (the full-offload run exits 0 while producing nothing).
- Fix forward in `tools/phasek1_perplexity.sh` (parser) rather than
  rerunning ~27 minutes of PPL compute; logs are the durable record and
  were complete.
- Record the Metal failure explicitly instead of silently falling back:
  no hidden fallbacks.

## Next Phase

- Metal + expert streaming is the critical gap. The streamer must
  allocate/copy expert tensors through a Metal-aware path (the
  `ggml_metal_cpy_tensor_async` `buf_dst` assert in
  `process_ubatch_streamed` is the entry point to investigate), or the
  streamed path must pin those tensors to a CPU buffer and copy into
  Metal on load. Until then every K1 number remains CPU-only.
- Any future MXFP4 work should target the compute path (direct 4-bit
  dot products), not cache-density tuning; hit-rate-driven caching
  showed no gain.
- Perplexity pipeline is now trustworthy (`final_ppl` parses correctly);
  reuse it as the standing quality gate.

## Reproduction

Perplexity (streamed, CPU, 4096 MiB cache, wikitext-2-raw, 32 chunks):

    cd /Users/pmains/Code/openclaw/kimi
    tools/phasek1_perplexity.sh models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf benchmarks/results/phase-k1/ppl/q4km 32 512
    tools/phasek1_perplexity.sh models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf benchmarks/results/phase-k1/ppl/mxfp4 32 512
    # results: benchmarks/results/phase-k1/ppl/{q4km,mxfp4}/ppl-result.json

Cached A/B benchmark (10 brackets x 3 runs, 128 tokens, seed 20260829):

    cd /Users/pmains/Code/openclaw/kimi
    CONFIG=coding-cap4 WORKERS=1 SEED=20260829 \
      tools/phasek1_run_brackets.sh benchmarks/results/phase-k1/full 10 128

Uncached A/B benchmark:

    cd /Users/pmains/Code/openclaw/kimi
    CONFIG=uncached WORKERS=1 SEED=20260830 \
      tools/phasek1_run_brackets.sh benchmarks/results/phase-k1/uncached 10 128

(A_MODEL/B_MODEL default to the repo models/kimi-linear/... GGUFs; the
script signature is OUTDIR N_BRACKETS N_TOKENS — model paths are env
vars, not positional args.)

Analysis:

    python3 tools/phasek1_analyze.py benchmarks/results/phase-k1/full
    python3 tools/phasek1_analyze.py benchmarks/results/phase-k1/uncached

Metal smoke (known failing — reproduced):

    cd /Users/pmains/Code/openclaw/kimi
    llama.cpp/build-metal/bin/llama-cli \
      -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 -c 4096 -p "$(cat benchmarks/prompts/phase-03-coding-lru.md)" \
      -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn \
      < /dev/null   # -> Metal OOM (command buffer status 5)
    env KIMI_STREAM_EXPERTS=naive KIMI_EXPERT_CACHE_MODE=zerocopy KIMI_EXPERT_CACHE_MB=4096 KIMI_PHASE7_INSTR=1 \
      llama.cpp/build-metal/bin/llama-cli \
      -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf \
      -ngl 999 -c 4096 -p "$(cat benchmarks/prompts/phase-03-coding-lru.md)" \
      -n 64 --temp 0 --seed 7 --no-display-prompt --no-conversation --single-turn \
      < /dev/null   # -> SIGABRT, GGML_ASSERT(buf_dst) in ggml_metal_cpy_tensor_async
