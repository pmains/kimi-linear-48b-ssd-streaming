#!/bin/bash
# Freeze the current llama.cpp build into runtime/live/ as the standalone
# live-serving bundle. Run this AFTER building and validating a new
# llama.cpp revision, before pointing OpenClaw agents at it.
#
#   llama.cpp/  (dev tree, rebuild freely)  ->  runtime/live/  (frozen)
#
# The live bundle is self-contained (binary + dylibs + backend .so
# plugins + rpath rewritten to @loader_path), so dev rebuilds of
# llama.cpp/build-metal can never change what agents are served.
set -euo pipefail

SRC_BIN="${KIMI_FREEZE_SRC:-llama.cpp/build-metal/bin}"
DST="${KIMI_FREEZE_DST:-runtime/live/bin}"
COMMIT_FILE="${KIMI_FREEZE_COMMIT:-runtime/live/COMMIT}"

mkdir -p "$DST"

# binary + link dylibs + backend plugins
cp -f "$SRC_BIN/llama-server" "$DST/"
# CLI + impl dylib: the reproducibility/benchmark path uses llama-cli
cp -f "$SRC_BIN/llama-cli" "$DST/" 2>/dev/null || true
cp -f "$SRC_BIN/libllama-cli-impl.dylib" "$DST/" 2>/dev/null || true
for d in libllama-server-impl.dylib libllama-common.0.dylib libmtmd.0.dylib \
         libllama.0.dylib libggml.0.dylib libggml-base.0.dylib \
         libggml-cpu.0.dylib libggml-metal.0.dylib libggml-blas.0.dylib; do
    cp -f "$SRC_BIN/$d" "$DST/" 2>/dev/null || true
done
cp -f "$SRC_BIN"/*.so "$DST/" 2>/dev/null || true

# rewrite rpath so the bundle is self-contained: absolute build path ->
# @loader_path (resolves relative to the executable's own directory, so the
# bundle works from any cwd). NOTE: the historical behavior wrote the
# literal $DST (a repo-root-relative path), which only resolved when the
# process was launched from the repo root; @loader_path is cwd-independent.
ABS_SRC="$(cd "$SRC_BIN" && pwd)"
for bin in llama-server llama-cli; do
    if [ -f "$DST/$bin" ]; then
        install_name_tool -rpath "$ABS_SRC" "@loader_path" "$DST/$bin" 2>/dev/null || true
    fi
done

# record provenance
git -C llama.cpp rev-parse HEAD > "$COMMIT_FILE"
git -C llama.cpp log -1 --format=%s >> "$COMMIT_FILE"
echo "frozen llama.cpp $(head -1 "$COMMIT_FILE") into $(dirname "$DST")/"
echo "bundle: $(ls "$DST" | wc -l | tr -d ' ') files, $(du -sh "$(dirname "$DST")" | cut -f1)"
