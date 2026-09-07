#!/bin/bash
# Step 13D driver — promote the OpenClaw production context contract to 256K.
#
# Owner decision 2026-09-07 (13C): select 256K (ctx 262144) as the production
# target. This driver makes the change real and verifies it through the real
# agent path, per SERVICE-ROADMAP §13D:
#
#   llama-server context:           65536 -> 262144  (launchd plist KIMI_CTX)
#   OpenClaw model contextWindow:   65536 -> 262144  (kimi-local AND
#                                                     llama-server provider
#                                                     entries for
#                                                     kimi-linear-48b)
#
# Scope (owner: "no optimization work yet; just make llama-server and OpenClaw
# consistently understand the selected 262,144-token ceiling, verify
# configuration resolution, and preserve rollback to the qualified 64K
# production state"):
#   - changes ONLY KIMI_CTX in the launchd plist (repo + installed copy) and
#     contextWindow on the two kimi-linear-48b provider entries in openclaw.json
#   - verifies the resolved runtime value through the REAL agent path
#     (headless openclaw agent turn -> agentMeta.contextTokens == 262144)
#   - retains rollback artifacts for the qualified 64K production state
#   - restores 64K automatically if any gate fails (never leaves production
#     half-aligned)
#
# Usage: bash tools/service_step13d.sh
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
EVID="$KIMI_DIR/benchmarks/results/service-step-13/13d-256k"
ROLLBACK="$EVID/rollback-64k"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
mkdir -p "$EVID" "$ROLLBACK"
LOG="$EVID/driver.log"
PLIST_REPO="$KIMI_DIR/tools/com.openclaw.kimi-llama-server.plist"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist"
LABEL=com.openclaw.kimi-llama-server
UID_N="$(id -u)"
PORT=18080
CTX=262144
OC_JSON="$HOME/.openclaw/openclaw.json"
OC_BAK="$HOME/.openclaw/openclaw.json.bak-step13d-20260907"
SRVLOG=/tmp/kimi-llama-server.launchd.log
HEALTH_URL="http://127.0.0.1:$PORT/health"
PROPS_URL="http://127.0.0.1:$PORT/props"
CHAT_URL="http://127.0.0.1:$PORT/v1/chat/completions"
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PROMOTED=0

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

wait_healthy() {
  local tmo="${1:-600}"
  local deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -s -m 2 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}

wait_port_free() {
  local tmo="${1:-60}"
  local deadline=$(( $(date +%s) + tmo ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

n_ctx_now() {
  curl -s -m 3 "$PROPS_URL" 2>/dev/null | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])" 2>/dev/null || echo 0
}

# ---- restore qualified 64K production state (plist + openclaw.json) --------
restore_64k() {
  log "ROLLBACK: restoring qualified 64K production state"
  # plist: KIMI_CTX back to 65536 (installed + repo)
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" "$PLIST_INSTALLED" 2>>"$LOG" || true
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" "$PLIST_REPO" 2>>"$LOG" || true
  launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
  sleep 2
  wait_port_free 30 || true
  launchctl bootstrap "gui/$UID_N" "$PLIST_INSTALLED" 2>>"$LOG" || true
  # openclaw.json: restore backup
  if [ -f "$OC_BAK" ]; then
    cp "$OC_BAK" "$OC_JSON"
    log "ROLLBACK: openclaw.json restored from $OC_BAK"
  fi
  wait_healthy 300 && log "ROLLBACK: llama healthy, n_ctx=$(n_ctx_now)" || log "ROLLBACK WARN: llama not healthy after restore"
}
trap 'if [ "$PROMOTED" != 1 ]; then log "EXIT trap fired before promotion completed"; restore_64k; fi' EXIT

log "=== Step 13D: promote production context contract to 262144 ($TS) ==="

# --- phase 0: preflight ------------------------------------------------------
log "--- phase 0 preflight ---"
GW_CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:18789/healthz)"
CTX_NOW="$(n_ctx_now)"
log "gateway healthz=$GW_CODE  llama n_ctx=$CTX_NOW"
if [ "$GW_CODE" != "200" ] || [ "$CTX_NOW" != "65536" ]; then
  log "ABORT: preflight failed (expected gateway 200 + llama ctx 65536)"
  exit 3
fi
SHA_BEFORE="$(shasum -a 256 "$OC_JSON" | cut -d' ' -f1)"
log "openclaw.json sha256(before)=$SHA_BEFORE"

# backup qualified 64K artifacts
cp "$PLIST_INSTALLED" "$ROLLBACK/plist-installed-64k-20260907.plist"
cp "$PLIST_REPO"      "$ROLLBACK/plist-repo-64k-20260907.plist"
cp "$OC_JSON" "$OC_BAK" && chmod 600 "$OC_BAK"
cat > "$ROLLBACK/README.md" <<EOF
# Rollback to the qualified 64K production state (Step 13D)

Qualified 64K state recorded 2026-09-07 (see
benchmarks/results/service-step-13/baseline-64k-20260907.json):
llama-server ctx 65536 (launchd job $LABEL), OpenClaw kimi-local +
llama-server provider contextWindow 65536, RSS ~8.8 GiB.

Artifacts in this dir:
- plist-installed-64k-20260907.plist  (installed launchd plist, KIMI_CTX=65536)
- plist-repo-64k-20260907.plist       (repo tools/ plist, KIMI_CTX=65536)
- openclaw.json backup: $OC_BAK (sha256 $SHA_BEFORE)

Rollback procedure:
1. /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" \\
     $PLIST_INSTALLED
   /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" \\
     $PLIST_REPO
2. cp $OC_BAK $OC_JSON
3. launchctl bootout gui/$UID_N/$LABEL; sleep 2
4. launchctl bootstrap gui/$UID_N $PLIST_INSTALLED
5. Verify: curl http://127.0.0.1:18080/props -> n_ctx 65536; gateway
   healthz 200; a short agent turn reports contextTokens 65536 (resolved).
EOF
log "backups retained: $ROLLBACK + $OC_BAK"

# --- phase 1: llama-server promotion (launchd plist) ------------------------
log "--- phase 1: plist KIMI_CTX -> $CTX ---"
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX $CTX" "$PLIST_INSTALLED"
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX $CTX" "$PLIST_REPO"
log "plist KIMI_CTX now: $(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:KIMI_CTX' "$PLIST_INSTALLED")"

log "relaunching launchd job at ctx $CTX"
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
sleep 2
wait_port_free 30 || log "warn: port busy after bootout"
launchctl bootstrap "gui/$UID_N" "$PLIST_INSTALLED" 2>>"$LOG" || { log "FAIL: bootstrap rc=$?"; exit 4; }

if ! wait_healthy 600; then
  log "GATE1 FAIL: server not healthy at $CTX within 600s"
  # fallback: warm slot cache may be ctx-incompatible; move aside and retry once
  if ls "$KIMI_DIR/runtime/state/slot-cache/"*.bin >/dev/null 2>&1; then
    log "retrying once with slot cache moved aside"
    mkdir -p "$ROLLBACK/slot-cache-64k"
    mv "$KIMI_DIR/runtime/state/slot-cache/"*.bin "$ROLLBACK/slot-cache-64k/" 2>/dev/null || true
    launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
    sleep 2
    launchctl bootstrap "gui/$UID_N" "$PLIST_INSTALLED" 2>>"$LOG" || true
    wait_healthy 600 || { log "GATE1 FAIL after retry"; exit 4; }
  else
    exit 4
  fi
fi
log "GATE1 pass: *** healthy"
sleep 2
RESOLVED_CTX="$(n_ctx_now)"
log "resolved n_ctx=$RESOLVED_CTX (expected $CTX)"
[ "$RESOLVED_CTX" = "$CTX" ] || { log "GATE1 FAIL: resolved n_ctx=$RESOLVED_CTX"; exit 4; }

# startup allocation highlights from the launchd server log
python3 - "$SRVLOG" "$EVID/startup.mem.txt" <<'PY'
import sys
src, out = sys.argv[1], sys.argv[2]
keep = []
try:
    lines = open(src, errors="replace").read().splitlines()
except OSError:
    lines = []
for ln in lines:
    if any(k in ln for k in ("llama_context: n_ctx", "KV buffer size", "llama_kv_cache: size",
                             "llama_memory_recurrent", "expert cache armed",
                             "ggml_metal_init:", "found device", "srv    load_model",
                             "new slot, n_ctx", "slot   load_model")):
        keep.append(ln)
open(out, "w").write("\n".join(keep[-25:]) + "\n")
print("startup highlights ->", out)
PY
grep -E "KV buffer size|expert cache armed|llama_context: n_ctx" "$EVID/startup.mem.txt" | tail -5

# short probe (gate: generation ok at new ctx)
PROBE_START=$(python3 -c 'import time; print(time.time())')
RESP=$(curl -s -m 120 "$CHAT_URL" -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":8}' 2>/dev/null)
PROBE_END=$(python3 -c 'import time; print(time.time())')
python3 - "$RESP" "$PROBE_START" "$PROBE_END" "$EVID/probe-a.json" <<'PY'
import json, sys
resp, t0, t1, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
ok = False
text = ""
try:
    d = json.loads(resp)
    text = d["choices"][0]["message"]["content"]
    ok = text.strip().upper().startswith("OK")
except Exception as e:
    d = {"parse_error": str(e)}
json.dump({"ok": ok, "wall_s": round(t1 - t0, 3), "text": text, "raw": d if isinstance(d, dict) else resp[:200]},
          open(out, "w"), indent=2)
print("probe ok:", ok, "wall_s:", round(t1 - t0, 3), "text:", repr(text[:40]))
PY
python3 -c "import json,sys; sys.exit(0 if json.load(open('$EVID/probe-a.json'))['ok'] else 1)" || { log "GATE2 FAIL: short probe"; exit 4; }
log "GATE2 pass: *** probe ok at ctx $CTX"

# --- phase 2: OpenClaw config alignment --------------------------------------
log "--- phase 2: openclaw.json contextWindow -> $CTX (kimi-local + llama-server entries) ---"
python3 - "$OC_JSON" "$CTX" <<'PY'
import json, sys, os, tempfile
path, ctx = sys.argv[1], int(sys.argv[2])
with open(path) as f:
    cfg = json.load(f)
providers = cfg.get("models", {}).get("providers", {})
changed = []
for pname in ("kimi-local", "llama-server"):
    for m in providers.get(pname, {}).get("models", []):
        if m.get("id") == "kimi-linear-48b":
            old = m.get("contextWindow")
            if old != ctx:
                m["contextWindow"] = ctx
                changed.append((pname, old, ctx))
            # maxTokens is an output-token cap, NOT a context ceiling; unchanged
if not changed:
    print("WARN: no kimi-linear-48b contextWindow updated")
else:
    for pname, old, new in changed:
        print(f"updated {pname}/kimi-linear-48b contextWindow {old} -> {new}")
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("written:", path)
PY
python3 -m json.tool "$OC_JSON" >/dev/null && log "openclaw.json valid JSON" || { log "FAIL: invalid JSON after edit"; exit 5; }
SHA_AFTER="$(shasum -a 256 "$OC_JSON" | cut -d' ' -f1)"
log "openclaw.json sha256(after)=$SHA_AFTER (backup at $OC_BAK)"

# wait for gateway hybrid reload (models hot-apply; no gateway restart expected)
sleep 12
tail -100 "$GATEWAY_LOG" 2>/dev/null | grep -i "reload" | tail -3 >> "$LOG" || log "no reload line seen in gateway log tail"

# --- phase 3: real-agent-path resolution verification ------------------------
log "--- phase 3: real-path resolution check (agentMeta.contextTokens) ---"
mkdir -p "$EVID/realpath"
printf 'Reply with exactly: OK\n' > "$EVID/realpath/prompt.txt"
python3 "$KIMI_DIR/tools/service_step10a_turn.py" \
  "$EVID/realpath" resolve-262144 "$EVID/realpath/prompt.txt" step13d-resolve \
  --agent kimi --timeout 900 \
  --llama-log "$SRVLOG" --gateway-log "$GATEWAY_LOG" > "$EVID/realpath/turn.stdout" 2>&1
echo "turn rc=$?" >> "$LOG"
python3 - "$EVID/realpath/resolve-262144.record.json" <<'PY'
import json, sys
try:
    rec = json.load(open(sys.argv[1]))
    am = rec.get("agentMeta", {})
    print("contextTokens:", am.get("contextTokens"), "source:", am.get("contextTokensSource"),
          "promptTokens:", am.get("promptTokens"))
    print("doc_status:", rec.get("doc_status"), "rc:", rec.get("summary", {}).get("rc"))
    sys.exit(0 if am.get("contextTokens") == 262144 and am.get("contextTokensSource") == "resolved" else 1)
except Exception as e:
    print("parse error:", e)
    sys.exit(1)
PY
RP_RC=$?
[ "$RP_RC" = 0 ] || { log "GATE3 FAIL: real-path contextTokens != 262144 (resolved)"; exit 6; }
log "GATE3 pass: *** agent path resolves contextTokens=262144"

# --- phase 4: final state + evidence ------------------------------------------
PROMOTED=1
log "--- phase 4: final verify + evidence ---"
sleep 2
python3 - "$EVID" "$TS" "$SHA_BEFORE" "$SHA_AFTER" <<'PY'
import json, subprocess, sys
evid, ts, sha_b, sha_a = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"
rec = {
  "step": "13D",
  "decision": "13C: select 256K (ctx 262144) as production target (owner 2026-09-07)",
  "ts": ts,
  "llama_health": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
  "resolved_n_ctx": sh("curl -s http://127.0.0.1:18080/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18789/healthz"),
  "openclaw_sha_before": sha_b,
  "openclaw_sha_after": sha_a,
  "openclaw_backup": "/Users/pmains/.openclaw/openclaw.json.bak-step13d-20260907",
  "step11b_sha": sh("shasum -a 256 /opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js | cut -c1-10"),
  "fk_violations": sh("python3 -c \"import sqlite3; print(len(sqlite3.connect('/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "rollback_dir": "/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-13/13d-256k/rollback-64k",
}
json.dump(rec, open(f"{evid}/results.json", "w"), indent=2)
print("RESULTS:", json.dumps(rec))
PY
echo "$TS" > "$EVID/window-ts.txt"
touch "$EVID/DONE"
log "=== 13D driver complete: production promoted to ctx $CTX ==="
exit 0
