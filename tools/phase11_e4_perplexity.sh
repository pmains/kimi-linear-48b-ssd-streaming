#!/bin/bash
# Phase 11 E4 quality gate: wikitext-2-raw perplexity, streamed path,
# MXFP4 CPU (reference) vs MXFP4 Metal (frozen E2 path).
#
# Methodology mirrors the accepted K1 gate (tools/phasek1_perplexity.sh):
#   same corpus, 32 x 512-token chunks, ctx=512, -b 2048 -ub 512,
#   same streamed path (KIMI_STREAM_EXPERTS=naive, zerocopy cache),
#   same 4096 MiB expert cache, same llama-perplexity binary.
#
# ARM=cpu:    -ngl 0 (accepted MXFP4 CPU reference path)
# ARM=metal:  -ngl 999 + KIMI_STREAM_METAL_STAGE=1 + KIMI_STREAM_E2_DIRECT_PLACE=1
#             (the frozen E2 Metal path; E2 gate requires the E1 stage gate)
# ARM=metal-sub: metal arm + KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1 (layer-0 Q/K/V
#             projection routed through the CPU backend; env-gated diagnostic,
#             default off, no kernel changes). E4 cause discriminator.
#
# Usage: tools/phase11_e4_perplexity.sh cpu|metal|metal-sub OUTDIR [CHUNKS] [CTX]
# Env: KIMI_EXPERT_CACHE_MB (default 4096), KIMI_CLI, KIMI_PPL_CORPUS
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARM="${1:?arm: cpu|metal}"
OUTDIR="${2:?outdir}"
CHUNKS="${3:-32}"
CTX="${4:-512}"
export CTX
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_EXPERT_CACHE_MB="${KIMI_EXPERT_CACHE_MB:-4096}"
export KIMI_STREAM_EXPERTS=naive
CORPUS="${KIMI_PPL_CORPUS:-$REPO_ROOT/benchmarks/data/wikitext-2-raw-test.txt}"
CLI="${KIMI_CLI:-$REPO_ROOT/llama.cpp/build-metal/bin/llama-perplexity}"
MODEL="$REPO_ROOT/models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-MXFP4_MOE.gguf"

case "$ARM" in
    cpu)   NGL=0; export -n KIMI_STREAM_METAL_STAGE KIMI_STREAM_E2_DIRECT_PLACE KIMI_STREAM_LOCALIZE_SUB_QKV_CPU 2>/dev/null || true ;;
    metal) NGL=999; export KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1; export -n KIMI_STREAM_LOCALIZE_SUB_QKV_CPU 2>/dev/null || true ;;
    metal-sub) NGL=999; export KIMI_STREAM_METAL_STAGE=1 KIMI_STREAM_E2_DIRECT_PLACE=1 KIMI_STREAM_LOCALIZE_SUB_QKV_CPU=1 ;;
    *) echo "ERROR: arm must be cpu|metal|metal-sub" >&2; exit 2 ;;
esac

mkdir -p "$OUTDIR"
if [ ! -f "$CORPUS" ]; then
    echo "ERROR: corpus missing: $CORPUS" >&2
    exit 2
fi

# covariates (9G convention)
{ date -u +%FT%TZ; uptime; vm_stat | head -5; } > "$OUTDIR/covariates-before.txt"

echo "=== E4 perplexity ARM=$ARM $(basename "$MODEL") chunks=$CHUNKS ctx=$CTX cache=${KIMI_EXPERT_CACHE_MB}MiB ngl=$NGL ==="
"$CLI" -m "$MODEL" -ngl "$NGL" -c "$CTX" -b 2048 -ub 512 --chunks "$CHUNKS" -f "$CORPUS" \
    > "$OUTDIR/ppl.log" 2>&1 || { echo "ERROR: perplexity failed (arm=$ARM)" >&2; tail -25 "$OUTDIR/ppl.log"; exit 1; }

{ date -u +%FT%TZ; uptime; vm_stat | head -5; } > "$OUTDIR/covariates-after.txt"

python3 - "$ARM" "$OUTDIR" "$CHUNKS" "$CTX" "$CORPUS" "$CLI" "$(git -C "$REPO_ROOT/llama.cpp" rev-parse HEAD 2>/dev/null || echo unknown)" "$MODEL" <<'PY'
import json, os, re, sys
arm, outdir, chunks, ctx, corpus, cli, commit, model = sys.argv[1:]
log = open(os.path.join(outdir, "ppl.log"), errors="replace").read()
final = None
for line in log.splitlines():
    m = re.search(r"Final estimate: PPL\s*=\s*([\d.]+)", line)
    if m:
        final = float(m.group(1))
pairs = [(int(m.group(1)), float(m.group(2))) for m in re.finditer(r"\[(\d+)\]([\d.]+)", log)]
pairs.sort()
ppl_vals = [v for _, v in pairs]
envs = {}
for k in ("KIMI_STREAM_METAL_STAGE", "KIMI_STREAM_E2_DIRECT_PLACE", "KIMI_STREAM_LOCALIZE_SUB_QKV_CPU",
          "KIMI_STREAM_EXPERTS", "KIMI_EXPERT_CACHE_MODE", "KIMI_EXPERT_CACHE_MB"):
    envs[k] = os.environ.get(k)
with open(os.path.join(outdir, "ppl-result.json"), "w") as f:
    json.dump({
        "arm": arm,
        "model": model,
        "model_basename": os.path.basename(model),
        "chunks": int(chunks),
        "ctx": int(ctx),
        "corpus": corpus,
        "cli": cli,
        "llama_commit": commit,
        "ngl": 0 if arm == "cpu" else 999,
        "env": envs,
        "final_ppl": final,
        "n_ppl_values": len(ppl_vals),
        "all_ppl_values": ppl_vals,
    }, f, indent=2)
print("ARM=%s final PPL = %s (chunks=%s)" % (arm, final, chunks))
PY
