#!/bin/bash
# Stage 6E acceptance driver — prefill invalidation, LIVE.
#
# Protocol: SERVICE-ROADMAP stage 6E (service-progress/step-06e-prefill-invalidation.md).
# Orchestration-only: drives the committed `openclaw prefill` CLI + registry +
# tools/serve_kimi_local.sh. No new runtime functionality.
#
# Sequence (operator refinement 2026-08-17: restored bootstrap returns to fp A):
#   1. prefill on the current live PID -> READY(fp A, PID A)
#   2. mutate a fingerprinted bootstrap input (acp.enabled=false hides the
#      acp-router skill -> effective tool catalog changes) -> `prefill status`
#      must reconcile READY -> STALE
#   3. restore the intended bootstrap -> prefill -> READY(fp A, PID A) again
#   4. controlled server restart -> new PID B
#   5. `prefill status` must reconcile READY -> COLD (new PID)
#   6. prefill -> READY(fp A, PID B)
#   7. verify final config identical to original
#
# Env:
#   KIMI_DIR      workspace root (default /Users/pmains/Code/openclaw/kimi)
#   KIMI_PIDFILE  live server pidfile (default /tmp/kimi-llama-server.pid)
#   STAGE6E_OUT   artifact dir (default <stateDir>/stage6e-acceptance/<ts>)
set -u

KIMI_DIR="${KIMI_DIR:-/Users/pmains/Code/openclaw/kimi}"
OC_SRC="$KIMI_DIR/openclaw-src"
DEV_CFG="$KIMI_DIR/dev-openclaw/config/openclaw.json"
DEV_STATE="$KIMI_DIR/dev-openclaw/state"
REGISTRY="$DEV_STATE/warm-state/registry.json"
PIDFILE="${KIMI_PIDFILE:-/tmp/kimi-llama-server.pid}"
CLI_ENTRY="$OC_SRC/prefill-cli-live.tmp.ts"
TARGET_AGENT="caveman"
TARGET_MODEL="kimi-linear-48b"
GW_URL="${OPENCLAW_GATEWAY_URL:-http://127.0.0.1:18790}"
PREPREFILL_TIMEOUT=2700   # cold prefill budget (s)
WARM_TIMEOUT=600          # warm re-prefill budget (s)
POLL_INTERVAL=15

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="${STAGE6E_OUT:-$DEV_STATE/stage6e-acceptance/$TS}"
mkdir -p "$OUT"
DRIVER_LOG="$OUT/driver.log"
PROGRESS="$OUT/progress.json"
CONFIG_BACKUP="$OUT/openclaw.json.pre-6e"
PREfill_SUBSHELL=""

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$DRIVER_LOG"; }
progress() {
  # progress <phase> <key> <value>
  python3 - "$PROGRESS" "$1" "$2" "$3" <<'PY'
import json, sys, os, datetime
path, phase, key, value = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
d = {}
if os.path.exists(path):
    d = json.load(open(path))
d["phase"] = phase
d["lastUpdated"] = datetime.datetime.now(datetime.UTC).isoformat()
d.setdefault("facts", {})[key] = value
json.dump(d, open(path, "w"), indent=2)
PY
}

write_cli_entry() {
  cat > "$CLI_ENTRY" <<'EOF'
import { Command } from "commander";
import { registerPrefillCli } from "./src/cli/prefill-cli.js";
const program = new Command();
registerPrefillCli(program);
await program.parseAsync(process.argv.slice(2), { from: "user" });
EOF
}

# --- registry helpers ------------------------------------------------------
find_ready_on_pid() {
  # prints the fingerprint of a READY entry whose serverPid == $1, else nothing
  python3 - "$1" "$REGISTRY" <<'PY'
import json, sys
pid = int(sys.argv[1])
try:
    d = json.load(open(sys.argv[2]))
except Exception:
    sys.exit(0)
for e in d.get("entries", {}).values():
    if e.get("status") == "READY" and e.get("serverPid") == pid:
        print(e.get("bootstrapFingerprint", ""))
        sys.exit(0)
PY
}

entry_status_for_fp() {
  python3 - "$1" "$REGISTRY" <<'PY'
import json, sys
fp = sys.argv[1]
try:
    d = json.load(open(sys.argv[2]))
except Exception:
    print("MISSING")
    sys.exit(0)
for k, e in d.get("entries", {}).items():
    if e.get("bootstrapFingerprint") == fp:
        print(f"{e.get('status')} pid={e.get('serverPid')}")
        sys.exit(0)
print("MISSING")
PY
}

wait_for_ready() {
  # wait_for_ready <expected-pid> <timeout-s> <label> [watch-cli-subshell]
  # Watches the registry; fails fast if the CLI subshell exits without READY.
  local pid="$1" tmo="$2" label="$3" watch="${4:-}" deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local fp
    fp="$(find_ready_on_pid "$pid" "$REGISTRY")"
    if [ -n "$fp" ]; then
      log "READY on pid $pid (fp=$fp) [$label]"
      echo "$fp"
      return 0
    fi
    if [ -n "$watch" ] && ! kill -0 "$watch" 2>/dev/null; then
      # CLI subshell exited before READY -> surface its tail
      log "CLI subshell exited before READY [$label]"
      for f in "$OUT"/prefill.*.err.log; do
        [ -f "$f" ] && tail -5 "$f" | sed 's/^/    /' >> "$DRIVER_LOG"
      done
      return 1
    fi
    sleep "$POLL_INTERVAL"
  done
  log "TIMEOUT waiting for READY on pid $pid [$label]"
  return 1
}

start_prefill_cli() {
  # start_prefill_cli <label>; returns immediately; CLI runs in background.
  local label="$1"
  write_cli_entry
  log "prefill[$label] launching in background..."
  (
    cd "$OC_SRC" && \
    OPENCLAW_CONFIG_PATH="$DEV_CFG" \
    OPENCLAW_STATE_DIR="$DEV_STATE" \
    OPENCLAW_GATEWAY_URL="$GW_URL" \
    KIMI_PIDFILE="$PIDFILE" \
    node --import tsx "$CLI_ENTRY" prefill "$TARGET_AGENT" "$TARGET_MODEL" --json \
      > "$OUT/prefill.$label.out.jsonl" 2> "$OUT/prefill.$label.err.log"
  ) &
  PREfill_SUBSHELL=$!
  log "prefill[$label] bg subshell pid=$PREfill_SUBSHELL"
}

run_status_cli() {
  local label="$1"
  write_cli_entry
  (
    cd "$OC_SRC" && \
    OPENCLAW_CONFIG_PATH="$DEV_CFG" \
    OPENCLAW_STATE_DIR="$DEV_STATE" \
    OPENCLAW_GATEWAY_URL="$GW_URL" \
    KIMI_PIDFILE="$PIDFILE" \
    node --import tsx "$CLI_ENTRY" prefill status --json \
      > "$OUT/status.$label.out.json" 2> "$OUT/status.$label.err.log"
  )
  local rc=$?
  log "status[$label] exit=$rc"
  return $rc
}

snapshot_registry() {
  python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2],"w"), indent=2)' \
    "$REGISTRY" "$OUT/$1.json" 2>/dev/null || log "registry read failed ($1)"
}

# --- phase 0: baseline -----------------------------------------------------
log "=== Stage 6E driver start ($TS) ==="
log "live pidfile: $(cat "$PIDFILE" 2>/dev/null || echo '<missing>')"
snapshot_registry "registry.phase0"
cp -p "$DEV_CFG" "$CONFIG_BACKUP"
ORIG_SHA="$(shasum -a 256 "$DEV_CFG" | cut -d' ' -f1)"
log "original config sha256=$ORIG_SHA (backup: $CONFIG_BACKUP)"
progress 0 baseline configSha "$ORIG_SHA"

LIVE_PID_A="$(cat "$PIDFILE" 2>/dev/null || true)"
if [ -z "$LIVE_PID_A" ] || ! kill -0 "$LIVE_PID_A" 2>/dev/null; then
  log "FATAL: no live server on pidfile $PIDFILE"
  exit 2
fi
log "live PID A = $LIVE_PID_A"
progress 0 baseline pidA "$LIVE_PID_A"

# --- phase 1: establish READY(fp A, PID A) ---------------------------------
progress 1 prefill establishing
start_prefill_cli "establish"
FP_A="$(wait_for_ready "$LIVE_PID_A" "$PREPREFILL_TIMEOUT" "establish" "$PREfill_SUBSHELL")" || { log "FATAL: no READY on PID A"; exit 3; }
log "fp A = $FP_A"
progress 1 established fingerprintA "$FP_A"
snapshot_registry "registry.phase1"

# --- phase 2: mutation -> STALE --------------------------------------------
progress 2 mutate acp.enabled=false
python3 - "$DEV_CFG" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["acp"] = {"enabled": False}
json.dump(d, open(p, "w"), indent=2)
PY
MUT_SHA="$(shasum -a 256 "$DEV_CFG" | cut -d' ' -f1)"
log "mutated config sha256=$MUT_SHA (acp.enabled=false)"
run_status_cli "mutated"
STATUS_AFTER_MUT="$(entry_status_for_fp "$FP_A" "$REGISTRY")"
log "entry after mutation: $STATUS_AFTER_MUT"
case "$STATUS_AFTER_MUT" in
  STALE*) log "PASS: READY -> STALE on bootstrap mutation";;
  *) log "FAIL: expected STALE, got '$STATUS_AFTER_MUT'"; exit 4;;
esac
progress 2 stale "confirmed"
snapshot_registry "registry.phase2"

# --- phase 3: restore bootstrap -> READY(fp A, PID A) -----------------------
progress 3 restore prefill
cp -p "$CONFIG_BACKUP" "$DEV_CFG"
REST_SHA="$(shasum -a 256 "$DEV_CFG" | cut -d' ' -f1)"
if [ "$REST_SHA" = "$ORIG_SHA" ]; then
  log "config restored byte-identical"
else
  log "WARN: restored sha differs ($REST_SHA vs $ORIG_SHA)"
fi
start_prefill_cli "restore"
FP_A2="$(wait_for_ready "$LIVE_PID_A" "$WARM_TIMEOUT" "restore" "$PREfill_SUBSHELL")" || { log "FATAL: no READY on PID A after restore"; exit 5; }
if [ "$FP_A2" = "$FP_A" ]; then
  log "PASS: fingerprint returned to A ($FP_A2)"
else
  log "FAIL: fp changed ($FP_A2 != $FP_A)"
  exit 5
fi
progress 3 ready fingerprintA2 "$FP_A2"
snapshot_registry "registry.phase3"

# --- phase 4: controlled restart -> PID B -----------------------------------
progress 4 restart server
log "stopping server (PID A=$LIVE_PID_A)..."
( cd "$KIMI_DIR" && tools/serve_kimi_local.sh stop ) || true
sleep 3
log "starting server..."
( cd "$KIMI_DIR" && tools/serve_kimi_local.sh start ) || { log "FATAL: server start failed"; exit 6; }
PID_B=""
for i in $(seq 1 60); do
  PID_B="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$PID_B" ] && kill -0 "$PID_B" 2>/dev/null && break
  sleep 5
done
if [ -z "$PID_B" ] || ! kill -0 "$PID_B" 2>/dev/null; then
  log "FATAL: no live PID B after restart"
  exit 6
fi
log "PID B = $PID_B (old PID A = $LIVE_PID_A)"
if [ "$PID_B" != "$LIVE_PID_A" ]; then
  log "PASS: PID changed"
else
  log "FAIL: PID unchanged"
  exit 6
fi
progress 4 restarted pidB "$PID_B"

# --- phase 5: status reconciles READY -> COLD on new PID --------------------
progress 5 status-cold
run_status_cli "restarted"
STATUS_AFTER_RESTART="$(entry_status_for_fp "$FP_A" "$REGISTRY")"
log "entry after restart: $STATUS_AFTER_RESTART"
case "$STATUS_AFTER_RESTART" in
  COLD*) log "PASS: READY -> COLD on server restart";;
  *) log "FAIL: expected COLD, got '$STATUS_AFTER_RESTART'"; exit 7;;
esac
progress 5 cold "confirmed"
snapshot_registry "registry.phase5"

# --- phase 6: prefill -> READY(fp A, PID B) ---------------------------------
progress 6 prefill-final
start_prefill_cli "final"
FP_A3="$(wait_for_ready "$PID_B" "$PREPREFILL_TIMEOUT" "final" "$PREfill_SUBSHELL")" || { log "FATAL: no READY on PID B"; exit 8; }
if [ "$FP_A3" = "$FP_A" ]; then
  log "PASS: fingerprint A on PID B ($FP_A3)"
else
  log "FAIL: fp differs ($FP_A3 != $FP_A)"
  exit 8
fi
progress 6 ready fingerprintA3 "$FP_A3"
snapshot_registry "registry.phase6"

# --- phase 7: verify final configuration -------------------------------------
FINAL_SHA="$(shasum -a 256 "$DEV_CFG" | cut -d' ' -f1)"
if [ "$FINAL_SHA" = "$ORIG_SHA" ]; then
  log "PASS: final config byte-identical to original"
  progress 7 verify configRestored "yes"
else
  log "FAIL: final config differs (expected $ORIG_SHA, got $FINAL_SHA)"
  cp -p "$CONFIG_BACKUP" "$DEV_CFG"
  progress 7 verify configRestored "restored-from-backup"
fi

log "=== Stage 6E driver complete: PASS ==="
progress done done "PASS"
exit 0
