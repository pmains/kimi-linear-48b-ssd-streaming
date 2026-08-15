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

SRC_BIN="llama.cpp/build-metal/bin"
DST="runtime/live/bin"
COMMIT_FILE="runtime/live/COMMIT"

mkdir -p "$DST"

# binary + link dylibs + backend plugins
cp -f "$SRC_BIN/llama-server" "$DST/"
for d in libllama-server-impl.dylib libllama-common.0.dylib libmtmd.0.dylib \
         libllama.0.dylib libggml.0.dylib libggml-base.0.dylib \
         libggml-cpu.0.dylib libggml-metal.0.dylib libggml-blas.0.dylib; do
    cp -f "$SRC_BIN/$d" "$DST/" 2>/dev/null || true
done
cp -f "$SRC_BIN"/*.so "$DST/" 2>/dev/null || true

# rewrite rpath so the bundle is self-contained (absolute build path -> @loader_path)
install_name_tool -rpath "$(cd "$SRC_BIN" && pwd)" "$DST" "$DST/llama-server" 2>/dev/null || true

# record provenance
git -C llama.cpp rev-parse HEAD > "$COMMIT_FILE"
git -C llama.cpp log -1 --format=%s >> "$COMMIT_FILE"
echo "frozen llama.cpp $(cat "$COMMIT_FILE" | head -1) into runtime/live/"
echo "bundle: $(ls "$DST" | wc -l | tr -d ' ') files, $(du -sh runtime/live | cut -f1)"
