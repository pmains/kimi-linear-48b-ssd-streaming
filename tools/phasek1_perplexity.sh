#!/bin/bash
# Phase K1 quality evaluation: bounded wikitext-2-raw perplexity through
# the streamed runtime.
#
# Runs llama-perplexity under KIMI_STREAM_EXPERTS=1 (the streamed path is
# library-level, so any client gets expert paging) with the same CPU
# (-ngl 0) and cache configuration as the frozen 9G benchmark harness.
# Both models pay identical machinery; the difference is the quant.
#
# Usage:
#   tools/phasek1_perplexity.sh MODEL_GGUF OUTDIR [CHUNKS] [CTX]
# Env: KIMI_EXPERT_CACHE_MB (default 4096), KIMI_CLI, KIMI_LLAMA_GIT
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${1:?model gguf}"
OUTDIR="${2:?outdir}"
CHUNKS="${3:-32}"
CTX="${4:-512}"
export CTX="${CTX}"
export KIMI_EXPERT_CACHE_MODE=zerocopy
export KIMI_EXPERT_CACHE_MB="${KIMI_EXPERT_CACHE_MB:-4096}"
export KIMI_STREAM_EXPERTS=naive
CORPUS="${KIMI_PPL_CORPUS:-$REPO_ROOT/benchmarks/data/wikitext-2-raw-test.txt}"
CLI="${KIMI_CLI:-$REPO_ROOT/llama.cpp/build-metal/bin/llama-perplexity}"

mkdir -p "$OUTDIR"
if [ ! -f "$CORPUS" ]; then
    echo "ERROR: corpus missing: $CORPUS" >&2
    exit 2
fi

echo "=== K1 perplexity: $(basename "$MODEL") chunks=$CHUNKS ctx=$CTX cache=${KIMI_EXPERT_CACHE_MB}MiB ==="
"$CLI" -m "$MODEL" -ngl 0 -c "$CTX" -b 2048 -ub 512 --chunks "$CHUNKS" -f "$CORPUS" \
    > "$OUTDIR/ppl.log" 2>&1 || { echo "ERROR: perplexity failed" >&2; tail -20 "$OUTDIR/ppl.log"; exit 1; }

python3 - "$MODEL" "$OUTDIR" "$CHUNKS" "$CTX" "$CORPUS" "$CLI" "$(git -C "$REPO_ROOT/llama.cpp" rev-parse HEAD 2>/dev/null || echo unknown)" <<'PY'
import json, os, re, sys
model, outdir, chunks, ctx, corpus, cli, commit = sys.argv[1:]
log = open(os.path.join(outdir, "ppl.log"), errors="replace").read()
# last "Final estimate" / per-chunk lines carry PPL
final = None
for line in log.splitlines():
    m = re.search(r"Final estimate: PPL\s*=\s*([\d.]+)", line)
    if m:
        final = float(m.group(1))
# per-chunk lines look like: [1]4.7142,[2]6.3026, ...
pairs = [(int(m.group(1)), float(m.group(2))) for m in re.finditer(r"\[(\d+)\]([\d.]+)", log)]
pairs.sort()
ppl_vals = [v for _, v in pairs]
with open(os.path.join(outdir, "ppl-result.json"), "w") as f:
    json.dump({
        "model": model,
        "model_basename": os.path.basename(model),
        "chunks": int(chunks),
        "ctx": int(ctx),
        "corpus": corpus,
        "cli": cli,
        "llama_commit": commit,
        "final_ppl": final,
        "n_ppl_values": len(ppl_vals),
        "last_ppl_values": ppl_vals[-10:],
    }, f, indent=2)
print("final PPL = %s (chunks=%s)" % (final, chunks))
PY
