# Phase 10 Report

Status: **PASS** (release packaging + local clean-room gate)
Gate status: independent external reproduction **OPEN** (pending
publication of the fork/repo and a separate machine — see Problems).

## Objective

Turn the frozen Kimi Linear storage-backed inference implementation into
a reproducible public artifact that another technically competent user
can install, run, benchmark, and validate without knowledge of the
project's development history.

Phase 9 remains the frozen scientific baseline. This phase packages that
baseline for external use; it does not change the experimental result.

Baseline selected (Peter, 2026-08-29): llama.cpp commit
`caea707b7d216c8685cf85db1a2682e26deb47d9` ("phase-09f: per-kind
pipelined repack") — the tree validated by the Phase 9G full experiment.
The pre-existing `runtime/live/` bundle (`cad716035`) was explicitly
NOT treated as authoritative: it predates the completed 9G validation of
the W4 pipelined path. It is preserved untouched for operational
continuity.

## Changes

- `README.md` — new. Clean path clone → build/install → obtain model →
  run → verify → benchmark, written from the process that actually
  succeeded (see Results). Documents supported hardware/OS, RAM/disk
  requirements, known limitations, expected performance, configuration
  knobs, troubleshooting, model setup (incl. sha256), and provenance.
- `tools/phase10_verify.sh` — new. Reproducibility test asserting:
  (1) model loads and run completes; (2) storage-backed expert execution
  is active (retr.csv emitted, pread column present); (3) routing/
  retrieval invariants hold (Phase 7 analyzer, 0 violations); (4)
  generated output is valid; (5) expected instrumentation is produced
  (retr/stats/moe/act/manifest/mem/cache_layers). Exit 0 = PASS.
  CLI resolution: `KIMI_CLI` → `runtime/release-9f/bin/llama-cli` →
  `llama.cpp/build-release/bin` → `llama.cpp/build-metal/bin`.
- `tools/freeze_live_runtime.sh` — parameterized: `KIMI_FREEZE_SRC` /
  `KIMI_FREEZE_DST` / `KIMI_FREEZE_COMMIT` (defaults unchanged =
  runtime/live). Now also bundles `llama-cli` + `libllama-cli-impl.dylib`
  (the benchmark/verify path uses the CLI). **rpath bug fixed**: the
  historical code wrote the literal `$DST` (repo-root-relative) as the
  rpath, which only resolved when launched from the repo root; it now
  writes `@loader_path` (cwd-independent, as the comment always claimed).
- `tools/phase04_run_streamed.sh` — hardcoded `/Users/pmains/...` paths
  replaced with repo-relative defaults + env overrides (`KIMI_MODEL`,
  `KIMI_CLI`, `KIMI_LLAMA_GIT`, `KIMI_REPO`). CLI default kept as
  `llama.cpp/build-metal/bin/llama-cli` — the frozen 9G harness samples
  CPU via `pgrep 'build-metal/bin/llama-cli'`; a different default would
  silently break that covariate.
- `tools/phase07_summarize.py` — committed the Phase 9F `hidden_us`
  overlap accounting (col 32) that existed only in the working tree. The
  committed analyzer (1249ea9) predates 9F and flags the legitimate
  read/repack overlap as a violation. See Results/Problems.
- `.gitignore` — `runtime/release-9f/bin/` ignored (bundle binaries
  regenerable via freeze tooling; provenance tracked in
  `runtime/release-9f/COMMIT`), mirroring the `runtime/live/bin/` rule.
- `runtime/release-9f/` — frozen 9F baseline bundle: 14 files, ~21 MB,
  `@loader_path` rpath, `COMMIT` records `caea707b7` + subject. Built
  from `llama.cpp/build-release` (clean out-of-tree build at the release
  commit) via the freeze tooling with overrides.
- `runtime/live/` — untouched (still `cad716035`).

## Results

### Clean build from the selected source revision

Fresh out-of-tree build (`build-release`) at exactly `caea707b7`,
configured to match the validated `build-metal` configuration
(`GGML_CPU_REPACK=ON`, `GGML_BACKEND_DL=ON`, `GGML_CPU_ALL_VARIANTS=ON`,
`GGML_METAL=ON`, `GGML_BLAS=ON` Apple, `GGML_OPENMP=ON`, Release):
**configure OK, build exit 0, ~2 min on 10 cores.**

Dependencies actually required on a clean machine (recorded, not
assumed):

- macOS + Xcode Command Line Tools (Apple clang 21.0.0);
- cmake 4.4.2 (any recent 3.x expected to work);
- Homebrew `openssl@3` — `llama-server` and `llama-cli` link
  `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib` +
  `libcrypto.3.dylib` (not bundled; same for the pre-existing live
  bundle);
- no runtime `libomp` dependency (OpenMP is compiled into the CPU
  backend plugin).

### Release bundle

`runtime/release-9f/` frozen from the clean build. Verified
self-contained: `@loader_path` rpath on both binaries; `llama-cli
--version` works from `/tmp` (foreign cwd). The historical live bundle
had a repo-root-relative rpath and only worked from the repo root — a
latent bug for a public artifact, fixed in the freeze tooling.

### Smoke/validation runs (release bundle, Q4_K_M, ctx 4096, cache
4096 MiB, zerocopy, workers=4, seed 7)

- Short 24-token coding run: valid Python output; streamed path active
  (retr.csv 17,521 rows; stats.csv pread columns); Phase 7 invariants
  PASS (0 violations); decode ≈ 6.8–7.0 tok/s.
- `tools/phase10_verify.sh` (64 tokens, coding prompt): **5/5 PASS** —
  loads, storage-backed active, invariants PASS, valid output,
  instrumentation complete. Run summary: decode 7.0 tok/s, cache hit
  rate 0.476 all-steps / 0.624 decode-steady, SSD ≈ 322 MB/token,
  prefill peak phys 6.3 GB, decode steady 5.6 GB — consistent with the
  frozen Phase 8 results (this is a validation, not a new benchmark).

### Model artifact facts (for the README)

- `moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`, 30,061,058,720
  bytes = 28.00 GiB; sha256
  `a1a7d865370652221f937163f7e94c99e1f114861335ba4f8666606843f1620f`
  (computed from the reference artifact).
- GGUF v3, arch `kimi-linear`, 610 tensors, 26 MoE layers × 256 experts
  (6,656 routed), Q4_K gate/up + Q6_K down per expert (per-expert
  stride: down 1,935,360 B; gate/up 1,327,104 B).
- Weights are NOT redistributed; README documents acquisition + checksum
  verification.

### Clean-room gate (local environment)

Per Peter (2026-08-29): a fresh local user/environment is acceptable for
initial Phase 10 development; a genuinely separate machine/operator
remains the final preferred external validation. This run is therefore a
**local clean-room**, NOT independent reproduction.

Procedure (4th attempt = final, all from pristine `git clone`, clean
`env -i` environment, fresh HOME, no workspace state):

1. `git clone` parent repo + `git clone` llama.cpp fork; checkout
   `caea707b7` — pristine committed tree;
2. cmake configure + build from clean source (exit 0, ~2 min);
3. model provided (symlink to the reference GGUF — local constraint; a
   remote user would download per README and verify sha256);
4. `tools/phase10_verify.sh` with **source build, no bundle**:
   **5/5 PASS** (auto-detected `llama.cpp/build-release/bin/llama-cli`).

The clean-room gate caught two real release defects, both fixed and
committed (see Problems): the stale committed analyzer, and the verify
tool's bundle-only assumption.

## Problems

1. **Repo has no origin; `llama.cpp/` is not part of the parent tree.**
   `git remote -v` is empty and `llama.cpp/` is a nested gitignored
   checkout (0 tracked files). A `git clone` of the parent contains NO
   fork source, so the README's clone step is not yet executable from a
   public URL. **The fork must be published (or the release tree
   vendored) before the external reproduction gate can close.** The
   local clean-room clones used local paths for this step.
2. **Stale committed analyzer.** `tools/phase07_summarize.py` at the
   last commit (`1249ea9`) predates Phase 9F: it flagged the legitimate
   read/repack overlap ("component sum > total") on the 9F baseline.
   The working tree carried the fix (`hidden_us` col 32; identity
   components + other == total + hidden); it was never committed. The
   clean-room gate exposed this: verify FAILED against the committed
   tree while passing against the working tree. Fixed in `f03629c`;
   re-verified 5/5. Only this analyzer had the stale invariant
   (`phase09g_analyze.py` unaffected).
3. **Verify tool bundle-only assumption.** `phase10_verify.sh` v1 hard-
   required `runtime/release-9f/bin/llama-cli`; a source-only clean room
   (no frozen bundle) could not verify. Fixed (`46ad8aa`): CLI resolution
   falls back to `build-release` then `build-metal`; the resolved path is
   passed to the runner explicitly.
4. **Runner default vs frozen 9G harness.** `phase04_run_streamed.sh`
   initially defaulted to the release-bundle CLI; the frozen 9G harness
   samples CPU via `pgrep 'build-metal/bin/llama-cli'` and would
   silently lose that covariate. Default reverted to build-metal; the
   release path passes `KIMI_CLI` explicitly. No 9G harness change.
5. **Freeze-tool rpath bug.** Historical freeze wrote the literal
   relative `$DST` as the rpath (cwd-dependent); fixed to `@loader_path`
   for both binaries. The pre-existing `runtime/live/` bundle retains
   the old behavior (operational, launched from repo root).
6. **External reproduction not yet performed.** Requires (a) publishing
   the fork + release tree, (b) a separate machine/operator. This is the
   remaining open gate; per Peter, not required to complete the initial
   Phase 10 development.
7. **Model delivery in the local clean room** used a symlink (28 GiB
   download impractical locally); a remote user follows README
   acquisition + sha256. The symlink is disclosed, not hidden.

## Decisions

- **Release baseline = `caea707b7`** (Phase 9F tree), per Peter. The
  deployed `runtime/live/` bundle is preserved but is NOT the scientific
  baseline (predates 9G validation of the W4 pipelined path).
- **Bundle regenerated from a clean build** at the release commit via the
  existing freeze machinery (parameterized), rather than trusting any
  pre-existing binaries.
- **README written from the process that succeeded** — every command in
  it was executed in this phase (clean build, freeze, verify, clean-room
  clone/build/verify), including the exact cmake option set and the
  OpenSSL dependency.
- **Verify = thin wrapper over the frozen Phase 4/7 tooling**, not new
  instrumentation; the historical Phase 1–9 workflow is not required.
- **9G harness untouched**; the runner default stays build-metal so the
  frozen protocol's covariate capture is byte-unchanged.
- **Clean-room gate executed against the committed tree**, which is what
  exposed the uncommitted-analyzer defect — the gate's purpose.
- **No independent-reproduction claim** is made for the local clean-room
  run.

## Next Phase

Phase 10's remaining open item is the **external reproduction gate**:
publish the llama.cpp fork (23 commits on `caea707b7`) and the release
tree with a public origin, then obtain an independent clean-room install
from the public README on a separate machine/operator. Failures from that
run become reproducibility findings and fixes to the installation
process, not coaching.

Phase 11 (Cross-Architecture Validation) may begin once Phase 10
establishes the reproducible path; the local clean-room gate satisfies
the "reproducible installation, runtime, and benchmark path"
precondition for internal work, with external validation to follow.

## Reproduction

    # clean build at the release baseline (~2 min, 10 cores)
    cd llama.cpp && git checkout caea707b7d216c8685cf85db1a2682e26deb47d9
    cmake -B build-release -DCMAKE_BUILD_TYPE=Release \
        -DGGML_ACCELERATE=ON -DGGML_BACKEND_DL=ON \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=Apple \
        -DGGML_CPU=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_CPU_REPACK=ON \
        -DGGML_METAL=ON -DGGML_NATIVE=OFF -DGGML_OPENMP=ON \
        -DLLAMA_BUILD_APP=ON -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_EXAMPLES=ON \
        -DLLAMA_BUILD_IS_DEV=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=ON \
        -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_UI=ON
    cmake --build build-release -j $(sysctl -n hw.ncpu)

    # freeze the release bundle (defaults target runtime/live; overrides
    # target the release path)
    KIMI_FREEZE_SRC=llama.cpp/build-release/bin \
    KIMI_FREEZE_DST=runtime/release-9f/bin \
    KIMI_FREEZE_COMMIT=runtime/release-9f/COMMIT \
    bash tools/freeze_live_runtime.sh

    # reproducibility test (5/5 = PASS; bundle, build-release, or
    # build-metal auto-detected; KIMI_CLI overrides)
    tools/phase10_verify.sh

    # model checksum
    shasum -a 256 models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf
    # a1a7d865370652221f937163f7e94c99e1f114861335ba4f8666606843f1620f

    # local clean-room gate (what was run)
    git clone <release-tree> kimi && git clone <fork> kimi/llama.cpp
    cd kimi/llama.cpp && git checkout caea707b7d216c8685cf85db1a2682e26deb47d9
    # ... build as above ... then from a clean env:
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin \
        bash tools/phase10_verify.sh /tmp/verify-out
