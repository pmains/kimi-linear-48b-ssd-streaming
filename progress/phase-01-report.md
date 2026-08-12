# Phase 01 Report

## Status

PASS

## Objective

Validate whether current upstream `llama.cpp` is a suitable base for Kimi Linear / KDA support, and determine whether Metal residency means the streaming problem still exists even when weights are file-backed or mmapped.

## Changes

- Added this report as the Phase 1 deliverable.
- Ran local reconnaissance only; no implementation code was changed.
- Updated after review: corrected the Metal-residency conclusion wording, recorded the local clone state (`kimi-k3-in-c` pinned at `ff11dce`, `llama.cpp` clone in progress), and added the Phase 2 memory equation and representative context sizes (8K/32K/128K).

## Results

- Current upstream `llama.cpp` source explicitly recognizes Kimi Linear / KDA:
  - `src/llama-arch.h` defines Kimi Linear KDA tensor kinds such as `LLM_TENSOR_SSM_CONV1D_Q`, `LLM_TENSOR_SSM_CONV1D_K`, `LLM_TENSOR_SSM_CONV1D_V`, `LLM_TENSOR_SSM_F_A`, `LLM_TENSOR_SSM_F_B`, `LLM_TENSOR_SSM_BETA`, `LLM_TENSOR_SSM_G_A`, and `LLM_TENSOR_SSM_G_B`.
  - `gguf-py/gguf/constants.py` includes `MODEL_ARCH.KIMI_LINEAR`.
  - `gguf-py/gguf/tensor_mapping.py` maps the Kimi Linear tensor names to those tensor kinds.
- Public upstream evidence also shows Kimi Linear-specific CI activity and runtime logs for `Kimi-Linear-48B-A3B-Instruct-jp-imatrix.Q4_K_M.gguf`, so the model is not a hypothetical fit for the runtime.
- Metal residency cannot be assumed away by mmap and must be measured in the actual llama.cpp execution path.
  - The local probe showed that a file-backed `mmap` mostly changed virtual address space first, then increased file-backed/external pages when touched.
  - A Metal `MTLResourceStorageModeShared` buffer increased resident memory and internal pages separately from the file-backed mapping.
  - That means "the model is mmapped" does not mean "the memory problem is already solved."
  - Fresh-run probe output is saved in `progress/phase-01-metal-probe.txt`.
- Hardware facts recorded directly from the machine:
  - MacBook Air
  - Apple M5
  - 24 GB unified memory
  - model identifier: `Mac17,3`
  - internal SSD: `APPLE SSD AP2048Z`
  - capacity: 2 TB
  - free space: about 1.79 TB
  - TRIM: yes
  - protocol: Apple Fabric
- Local clones:
  - `llama.cpp`: clone in progress at `llama.cpp/` at the time of writing (working tree not yet checked out); the upstream commit will be recorded once the clone completes.
  - `kimi-k3-in-c`: local pinned checkout at `kimi-k3-in-c/`, commit `ff11dce` (Release v1.0.0), matching the release pin verified from public source. Source-code reference only; no Kimi K3 model weights are being downloaded. The docs set currently points at commit `b4a7b3f`.
- Checkpoint search:
  - No local `moonshotai/Kimi-Linear-48B-A3B-Instruct` checkpoint or obvious Kimi Linear GGUF was found in the common Hugging Face cache paths or nearby local model directories.

## Problems

- The initial reconnaissance could not clone GitHub repositories (DNS resolution failed from the shell). This was later resolved: `kimi-k3-in-c` is cloned and pinned at `ff11dce`; the `llama.cpp` clone was still in progress when this report was updated and must be verified once complete.
- The Kimi Linear checkpoint is not present locally, so the next phase still needs an actual supported Q4 GGUF or another locally available model artifact before model-size inventory can be measured.

## Decisions

- Proceed with `llama.cpp`.
- Use the existing Kimi Linear architecture support in upstream as the base, rather than selecting a different runtime.
- Treat `mmap` as only one part of the memory story; measure resident memory, file-backed pages, and Metal/shared-buffer residency separately.
- Keep the memory ladder conditional on measured Phase 2 results.

## Next Phase

- Phase 2 should inventory the model tensors and separate trunk, shared, routed-expert, and runtime-resident components.
- Before that, obtain a supported ~4-bit Kimi Linear GGUF if one is available for the chosen runtime.
- Once a local model artifact exists, measure actual resident footprint and determine feasible cache rungs.

### Phase 2 memory equation

Phase 2 must determine the terms of the memory budget explicitly:

    trunk + runtime/state + context/KV + expert working set + safety margin <= 24 GB

where:

- `trunk` is the non-routed resident component (embeddings, attention, dense weights);
- `runtime/state` is unavoidable runtime memory (graph buffers, Metal buffers, tokenizer, etc.);
- `context/KV` is the KV/state cache at a chosen context size;
- `expert working set` is the resident routed-expert portion (cache);
- `safety margin` covers the OS and other processes.

### Context sizes

Measure the equation at representative context sizes:

- 8K
- 32K
- 128K

subject to what Kimi Linear/llama.cpp actually allocates. A cache budget that only works at an impractically small coding context is not a success.

## Reproduction

Machine facts:

```bash
system_profiler SPHardwareDataType SPStorageDataType SPNVMeDataType
df -h /
diskutil info / | egrep 'Device Name|Protocol|Solid State|Internal|Volume Name|Disk Size|Volume Total Space|Volume Free Space|TRIM Support|Medium Type'
```

Checkpoint search:

```bash
find ~/.cache/huggingface ~/.local/share /Users/Shared /Volumes ~/Downloads -maxdepth 4 \
  \( -iname '*Kimi*Linear*' -o -iname '*kimi*linear*' -o -iname '*Kimi-Linear*' -o -iname '*.gguf' \)

find ~ -maxdepth 3 \( -iname '*Kimi*Linear*' -o -iname '*kimi*linear*' -o -iname '*Kimi-Linear*' -o -iname '*moonshotai*' -o -iname '*Kimi*48B*' \)
```

Metal residency probe:

```bash
clang -fobjc-arc -framework Foundation -framework Metal -framework IOKit -framework CoreFoundation \
  -x objective-c tools/metal_mem_probe.m -o /tmp/metal_mem_probe
/tmp/metal_mem_probe
```
