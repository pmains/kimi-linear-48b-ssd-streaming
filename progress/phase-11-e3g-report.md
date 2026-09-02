# Phase 11 E3G Report — Metal Compute Deficit Premise Falsified

## Status

**STOPPED — the E3G premise is falsified; the correction is documented;
no remaining E3G measurement is needed to choose the next optimization
target; no optimization performed.**

The E3G hypothesis — "Metal route/trunk + expert compute is ~2.8× the
same-quantization CPU reference; decompose the Metal compute time into
op/kernel categories and explain why Metal is slower than CPU" — is
**invalid**. The 2.8× figure that motivated it was an artifact of E3A
comparing **Metal MXFP4 against a CPU Q4_K_M run** (the file E3A labeled
"frozen K1 CPU cached MXFP4" is, per its own retained provenance, the
30 GB Q4_K_M model), i.e. a *cross-quantization* comparison, not a
same-quantization Metal-vs-CPU one. The retained **true MXFP4 CPU
references show compute parity with the promoted MXFP4 Metal baseline**
(MXFP4 CPU route+expert ≈ 44.4 ms/token canonical [e1-cpu-ref], range
35.4–68.5 across retained runs; promoted Metal = 43.8 ms/token).
Metal compute is parity work, not a backend deficit — so an op-level
Metal profiler would hunt a deficit that does not exist. No profiler was
built (work stopped at orientation, per directive). Report + ROADMAP +
E3A/E3F reports updated so the invalid comparison is not reused.
Stopped for review.

## Objective

E3G (authorized, then reassessed per review): decompose the remaining
Metal compute time (route/trunk 23.60 + expert 20.22 = 43.82 ms/token,
48.3% of the promoted live baseline) into operation/kernel categories,
separate route/trunk vs expert paths, and explain why Metal is ~2.8×
slower than the "same-quantization CPU reference". Do not optimize.
Prefer existing Metal/ggml profiling facilities; add only minimal
diagnostic instrumentation if required. **Reassessment instruction
(2026-09-01): stop building the op-level profiler — the retained CPU
reference used in E3A/E3F is Q4_K_M, not MXFP4; the true MXFP4 CPU
reference is ~44.4 ms/token vs 43.8 ms/token Metal; the 2.8× claim is
invalid and the hypothesis falsified. Document the correction, determine
whether any remaining E3G measurement is necessary to choose the next
optimization target, update E3A/E3F/ROADMAP so the invalid comparison
is not reused, stop for review.**

## Method

No code changes and no new runs were performed (directive: stop the
profiler). The falsification is established entirely from retained
artifacts:

- E3A's CPU reference file and its provenance:
  `benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/`
  (manifest.json: `"backend": "cpu"`, llama_commit caea707b7;
  k1-model.json: `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`,
  30,061,058,720 bytes; run.log: `model : ...Q4_K_M.gguf`,
  `ftype : Q4_K - Medium`).
- Retained true MXFP4 CPU runs (same quantization, `-ngl 0`, streamed
  naive, zerocopy): `phase-k1/e1-cpu-ref` (44.41 ms route+expert),
  `phase-k1/e1b-cpu-trace` (35.38), `phase-k1/full/coding-cap4/{b6,b8,b10}-B`
  (45.79 / 59.60 / 68.51, harness-load covariates).
- Promoted MXFP4 Metal baseline: E3F live B1 soak
  (`benchmarks/results/phase-11/e3e-promotion/B1/stats.delta`, 7,554
  decode steps): route 23.60 + expert 20.22 = 43.82 ms/token; plus
  controlled Metal arms E3C W1/W4 (48.08 / 41.02) and E3E cap8192
  (37.73).

Analysis: recompute the same-quantization comparison from these retained
numbers; identify which E3A/E3F statements embed the invalid comparison;
decide whether a remaining measurement is required for target selection.
Artifact: `benchmarks/results/phase-11/e3g/quant-reference-correction.txt`.

## Results

### The invalid comparison, exactly

E3A/E3F claim: Metal per-step compute 99.8 ms vs "same-quantization CPU
reference" 36.0 ms → ~2.8× (E3A findings 4b/5; E3F findings 2/classification;
ROADMAP E3A/E3F entries).

The CPU file E3A used (`b8-A-after`, cited in E3A's Method and fed to
`tools/phase11_e3a_analyze.py`) is, by its own retained provenance, the
**Q4_K_M** GGUF — a *different quantization* from the Metal run's MXFP4.
The "2.8× same-quantization" framing is therefore false on its face:
it compared Metal-MXFP4 (99.8 ms) against CPU-Q4_K_M (36.0 ms).

### The correct same-quantization comparison (MXFP4 CPU vs MXFP4 Metal)

| arm | backend | quant | route ms/tok | expert ms/tok | total ms/tok |
|---|---:|---:|---:|---:|---:|
| e1-cpu-ref (E1 CPU ref, 4096 MiB) | CPU | MXFP4 | 26.09 | 18.32 | **44.41** |
| e1b-cpu-trace (trace-instrumented) | CPU | MXFP4 | 21.11 | 14.27 | **35.38** |
| K1 b6-B / b8-B / b10-B (harness) | CPU | MXFP4 | 27–39 | 19–29 | 45.79–68.51 |
| E3C W1 (4 GiB) | Metal | MXFP4 | 26.27 | 21.81 | **48.08** |
| E3C W4 (4 GiB) | Metal | MXFP4 | 22.14 | 18.88 | **41.02** |
| E3E cap8192 (8 GiB) | Metal | MXFP4 | 20.60 | 17.13 | **37.73** |
| **E3F promoted live (W4/8 GiB, B1 soak)** | Metal | MXFP4 | 23.60 | 20.22 | **43.82** |

MXFP4 CPU route+expert compute: **35.4–68.5 ms/token** (canonical clean
reference 44.41). MXFP4 Metal route+expert compute across controlled
arms: **37.7–48.1 ms/token** (43.82 at the promoted live point). The
ranges overlap; the canonical CPU reference (44.41) is within 1.3% of
the promoted Metal baseline (43.82). **Compute is at parity — Metal is
not 2.8× slower than CPU at the same quantization.**

Two side observations, both consistent with parity:

1. The E3A-era Metal number (99.8 ms/token) is not representative of
   the controlled compute arms (37.7–48.1): it was measured on the E5
   live server under workers=1 / the pre-E3C read path and pooled across
   cold+warm live runs. Controlled Metal compute (E3C/E3E/E3F) is
   37.7–48.1 regardless.
2. MXFP4-on-CPU is itself slower than Q4_K_M-on-CPU (44.4 vs 36.0
   ms/token) — which is why the mislabeled comparison looked so large.
   The CPU backend's relative slowness on MXFP4 is a CPU-vec_dot
   property, not a Metal property.

### Is any remaining E3G measurement necessary to choose the next target?

**No.** The evidence needed to re-rank targets is already retained and
was not affected by the false premise:

- E3F's live profile of the promoted baseline (90.8 ms/step) is a
  measurement of the Metal path itself — valid regardless of any
  CPU comparison. Component ranking: expert reads 33.04 ms (36.4%) >
  route/trunk compute 23.60 (26.0%) > expert compute 20.22 (22.3%) >
  other (ids readback etc.) 8.15 (9.0%) > placement 3.58 > build 2.21.
- The compute bucket (43.82 ms) is *real Metal wall time*, but with the
  deficit premise removed it is **parity work** — the same cost the CPU
  pays at the same quantization. It is not a Metal-vs-CPU inefficiency
  to hunt at the kernel level, so an op-level Metal profiler (the
  original E3G deliverable) is no longer warranted.
- The largest remaining *reducible* component is unchanged and is not
  compute: **expert reads, 33.04 ms/token = 36.4% of the step**,
  per-miss-latency-dominated (0.78 ms/miss × 39 misses; fixed overhead
  only 7.9%). That is the next optimization target, and E3F already
  quantified it. Second: the "other" bucket (8.15 ms, 9.0%), ~5.4 ms of
  which is the 26× per-layer routing-ids readback.

No new run is required to make that choice; a fresh op-level CPU-vs-Metal
compute A/B would only re-confirm parity and is not needed for target
selection (and would risk disturbing the live 8 GiB instance per the E3F
OOM note). Any future compute-side work (e.g. general kernel/fusion
efficiency, quantization) would be a *baseline-wide* optimization, not a
Metal-deficit fix, and would need separate roadmap authorization.

## Problems / limitations

- The two retained MXFP4 CPU references differ from each other
  (e1-cpu-ref 44.41 vs e1b-cpu-trace 35.38): e1b-cpu-trace carried
  trace instrumentation and the K1 B-arms ran under the 9G harness with
  covariate load, so 44.41 (e1-cpu-ref, the phase-11 E1 CPU reference)
  is the canonical clean number. The spread does not change the
  conclusion: every retained MXFP4 CPU number is far below the 2.8×
  ratio, and the controlled Metal arms sit inside the CPU range.
- The Metal side of E3A (99.8 ms) vs the controlled Metal arms (37.7–
  48.1) is a discrepancy worth remembering: E3A's compute figures came
  from the live-server W1-era capture and should not be reused as
  representative Metal compute numbers (E3C/E3E/E3F supersede them).
- No new measurements were taken; every claim rests on retained
  artifacts (provenance files + stats.csv), which is the appropriate
  evidentiary basis for a falsification.

## Decisions

- **E3G hypothesis FALSIFIED**: no Metal-vs-CPU compute deficit exists
  at same quantization; the op-level Metal profiler is not built.
- The invalid comparison is flagged and superseded in: this report;
  `benchmarks/results/phase-11/e3g/quant-reference-correction.txt`;
  correction notes added to `progress/phase-11-e3a-report.md` and
  `progress/phase-11-e3f-report.md`; ROADMAP E3A/E3F entries annotated
  and a new E3G entry added.
- Next optimization target (for a future authorized phase): **expert
  read per-miss latency** (E3F: 33.04 ms = 36.4%, 0.78 ms/miss × 39
  misses) — read-side territory, not compute. Secondary: per-layer ids
  readback (~5.4 ms of "other"). Compute efficiency is deferred as a
  baseline-wide (not Metal-specific) concern requiring separate
  authorization.
- No optimization performed; no source changes; stopped for review.

## Next Phase

Information for the next phase:

- Do NOT reuse "Metal compute ~2.8× CPU (same quantization)" — it is
  cross-quantization (MXFP4-Metal vs Q4_K_M-CPU) and invalid. Use the
  retained same-quantization table above (MXFP4 CPU 35.4–68.5 vs MXFP4
  Metal 37.7–48.1 ms/token; parity).
- The E3F live profile remains the authoritative step attribution at the
  promoted point; its compute bucket is real wall time but parity work.
- Candidate next experiment (read-side, in E3C/E3E territory): reduce
  the 0.78 ms/miss per-miss read latency (queue depth, worker-pool
  scheduling vs per-layer serialization, read batching) and/or the
  remaining 39 misses/step. E3F/E3E already flattened the capacity
  curve past ~6 GiB, so the lever is per-miss latency, not capacity.
- E3F report's "Next Phase" candidate #1 (Metal compute efficiency via
  the 2.8× gap) is withdrawn by this falsification.

## Reproduction

No runs to reproduce (falsification from retained artifacts). To verify
the provenance claims:

    # 1. the file E3A called "frozen K1 CPU cached MXFP4" is Q4_K_M:
    cat benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/k1-model.json
    grep -m1 "model" benchmarks/results/phase-k1/full/coding-cap4/b8-A-after/run.log

    # 2. true MXFP4 CPU reference (canonical) — route+expert = 44.41 ms:
    python3 - <<'PY'
    import csv, statistics as st
    rows = list(csv.DictReader(open("benchmarks/results/phase-k1/e1-cpu-ref/stats.csv")))
    dec = [r for r in rows if r["phase"] == "decode"]
    for k in ("route_compute_us", "expert_compute_us"):
        print(k, round(st.mean(float(r[k]) for r in dec) / 1000, 2), "ms/token")
    PY

    # 3. promoted Metal baseline — route+expert = 43.82 ms/token (E3F B1):
    #    see benchmarks/results/phase-11/e3f/live-baseline-profile.txt and
    #    benchmarks/results/phase-11/e3e-promotion/B1/stats.delta

Artifacts: `benchmarks/results/phase-11/e3g/quant-reference-correction.txt`
(this report's evidence table + conclusion). Corrections applied to:
`progress/phase-11-e3a-report.md`, `progress/phase-11-e3f-report.md`,
`ROADMAP.md` (E3A/E3F entries annotated; E3G entry added). Report: this
file. Stopped for review.
