#!/bin/bash
# Step 13G driver - Poliscopic Capacity Measurement at 256K (retained 2026-09-07).
#
# Owner go 2026-09-07 20:00 MST with an EXACT representative read-only task:
# inspect the current Poliscopic knowledge-graph state and produce a concise
# maintenance assessment (roadmap/status docs + KG/database counts + integrity
# indicators -> next highest-priority KG maintenance work). NO modifications.
#
# Capacity measurement on the LIVE 262144 Kimi contract (13D/13E production
# state) through the real agent path (headless openclaw agent turn, agent
# poliscopic, model forced to llama-server/kimi-linear-48b by the retained
# runner -> rides the local 256K Kimi contract).
#
# Roadmap 13G gate: capture the fresh Poliscopic context accounting
# (system prompt / tool schemas / bootstrap chars + assembled promptTokens +
# usage input/cacheRead/output + ctx resolved), run the representative
# maintenance task, and determine whether the larger context materially
# improves useful working-context retention vs the 64K baseline. Slow prefill
# alone is NOT a failure.
#
# Measured per turn (runner record.json/client.json): rc, wall, timed_out,
# agentMeta contextTokens/contextTokensSource/promptTokens/usage/lastCallUsage,
# systemPromptReport (chars), livenessState, executionTrace, finalPromptText.
# llama window -> prefill/TTFT/decode rates; gateway window -> per-call fetch
# timing; slots before/after -> cache state. RSS/free sampler -> envelope.
# Read-only gate: poliscopic workspace git status + KG sqlite sha256 + file
# inventory identical before/after (only sqlite sidecars may appear, benign).
#
# Usage: bash tools/service_step13g.sh
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
ROOT="$KIMI_DIR"
EVID="$ROOT/benchmarks/results/service-step-13/13g-poliscopic-capacity"
RUNNER="$ROOT/tools/service_step10a_turn.py"
PROM13G="$EVID/prompts"
BASE="$EVID/baselines"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PORT=18080
GW_PORT=18789
KIMI_DB=/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite
POLI_DB=/Users/pmains/.openclaw/agents/poliscopic/agent/openclaw-agent.sqlite
REDACT_JS=/opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js
CFG="$HOME/.openclaw/openclaw.json"
CFG_13D="$HOME/.openclaw/openclaw.json.bak-step13d-20260907"
POLI_WS=/Users/pmains/Code/openclaw/poliscopic
POLI_DBS="data/maricopa.sqlite data/analytics.sqlite data/bluesky_tracking.sqlite"
AGENT=poliscopic
TIMEOUT=2700
LOG="$EVID/driver.log"
mkdir -p "$EVID" "$PROM13G" "$BASE"

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

health_ok() { curl -s -m 5 http://127.0.0.1:$PORT/health >/dev/null 2>&1; }
gw_ok() { [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:$GW_PORT/healthz)" = "200" ]; }
slot_idle() {
  curl -s -m 5 http://127.0.0.1:$PORT/slots 2>/dev/null | python3 -c "
import json,sys
try:
    s=json.load(sys.stdin)[0]
    print('IDLE' if not s.get('is_processing') else 'BUSY')
except Exception:
    print('ERR')
"
}

# run_one <label> <agent> <promptfile> <timeout>
run_one() {
  local label="$1" agent="$2" prompt="$3" tmo="$4"
  local outdir="$EVID/$label"
  local key="agent:$agent:step13g-$label-${RUN_SUFFIX:-r1}"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label.client.json" ]; then
    log "[$label] cached"
    return 0
  fi
  log "=== $label start (agent=$agent timeout=${tmo}s) $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python3 "$RUNNER" "$outdir" "$label" "$prompt" "$key" \
    --agent "$agent" --timeout "$tmo" \
    --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
    > "$outdir/$label.turn.stdout" 2> "$outdir/$label.turn.stderr"
  local rc
  rc=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['rc'])" 2>/dev/null)
  local wall
  wall=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['wall_s'])" 2>/dev/null)
  log "[$label] rc=$rc wall=${wall}s"
}

# ---------------- preflight ----------------
log "--- preflight ---"
for _ in 1 2 3 4 5 6; do health_ok && gw_ok && break; sleep 5; done
python3 - "$EVID" "$PORT" "$GW_PORT" "$KIMI_DB" "$POLI_DB" "$REDACT_JS" "$CFG" "$CFG_13D" "$POLI_WS" <<'PY'
import json, os, subprocess, sys
evid, port, gw, kdb, pdb, redact, cfg, cfg13, ws = sys.argv[1:]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception: return "?"
def leaves(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from leaves(v, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, o
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None
old, new = load(cfg13), load(cfg)
drift = []
if old is not None and new is not None:
    a, b = dict(leaves(old)), dict(leaves(new))
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            ok = (k == "/models/providers/kimi-local/models[0]/contextWindow" and a.get(k) == 65536 and b.get(k) == 262144) or \
                 (k == "/models/providers/llama-server/models[0]/contextWindow" and a.get(k) == 65536 and b.get(k) == 262144)
            if not ok:
                drift.append({"path": k, "old": a.get(k), "new": b.get(k)})
rec = {
  "step": "13G-preflight",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "kimi_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{kdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "poliscopic_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{pdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "slot_idle": sh(f"curl -s -m 5 http://127.0.0.1:{port}/slots | python3 -c \"import json,sys; s=json.load(sys.stdin)[0]; print(not s.get('is_processing'))\""),
  "config_drift_vs_13D": drift,
  "compaction_config_keys": sh(f"python3 -c \"import json; d=json.load(open('{cfg}')); print([k for k in d.get('context',{{}}) if 'compact' in k.lower()] + list(d.get('context',{{}}).keys())[:8])\""),
}
json.dump(rec, open(f"{evid}/preflight.json", "w"), indent=2)
print("PREFLIGHT:", json.dumps(rec))
PY
log "--- preflight recorded ---"

# ---------------- workload idle gate (pre-turn) ----------------
log "--- workload idle gate ---"
IDLE="$(slot_idle)"
log "slot_idle=$IDLE"
OTHER=$(pgrep -f "openclaw agent|service_step10a_turn" | grep -v $$ | head -3 | tr '\n' ' ')
log "other_agent_procs=$OTHER"
if [ "$IDLE" != "IDLE" ] || [ -n "$OTHER" ]; then
  log "13G NOT STARTED: active Kimi inference workload (slot=$IDLE other=$OTHER)"
  exit 30
fi
log "idle gate PASS: no other active Kimi workload"

# ---------------- read-only baselines (poliscopic workspace) ----------------
log "--- read-only baselines ---"
( cd "$POLI_WS" && git status --porcelain | sort ) > "$BASE/git-before.txt" 2>&1
( cd "$POLI_WS" && for f in $POLI_DBS; do echo "$f $(shasum -a 256 "$f" 2>/dev/null | cut -d' ' -f1)"; done ) > "$BASE/db-before.sha" 2>&1
( cd "$POLI_WS" && find data snapshots -type f 2>/dev/null | sort ) > "$BASE/files-before.txt" 2>&1
log "baselines recorded (git lines=$(wc -l < "$BASE/git-before.txt"), db shas=$(wc -l < "$BASE/db-before.sha"), files=$(wc -l < "$BASE/files-before.txt"))"

# ---------------- prompt (EXACT owner-specified task, read-only) ----------------
cat > "$PROM13G/kg-maintenance.txt" <<'EOF'
Inspect the current Poliscopic knowledge-graph state and produce a concise maintenance assessment. Read the relevant roadmap/status documentation and inspect the current KG/database counts and integrity indicators needed to identify the next highest-priority knowledge-graph maintenance work. Do not modify code, data, schemas, configuration, or documentation.
EOF
log "prompt written: $(wc -w < "$PROM13G/kg-maintenance.txt") words"

# ---------------- measured turn ----------------
# RSS/free sampler (evidence only; bounded single turn ~9-10 GiB envelope,
# far below the 18 GiB / 3% abort thresholds retained from 13E; client-side
# timeout is the run cap - consistent with 13F which ran no watchdog).
(
  LPID="$(pgrep -f 'llama-server.*18080' | head -1)"
  while [ -n "$LPID" ] && kill -0 "$LPID" 2>/dev/null; do
    RSS_KB=$(ps -o rss= -p "$LPID" 2>/dev/null | tr -d ' ')
    FREE_PCT=$(memory_pressure -Q 2>/dev/null | awk '/percentage/ {print $NF}' | tr -d '%')
    echo "$(date +%s) ${RSS_KB:-?} ${FREE_PCT:-?}"
    sleep 10
  done
) > "$EVID/watchdog.tsv" &
SAMPLER=$!

run_one kg-maintenance "$AGENT" "$PROM13G/kg-maintenance.txt" "$TIMEOUT"
kill "$SAMPLER" 2>/dev/null; wait "$SAMPLER" 2>/dev/null

# ---------------- post-turn read-only check ----------------
log "--- post-turn read-only check ---"
( cd "$POLI_WS" && git status --porcelain | sort ) > "$BASE/git-after.txt" 2>&1
( cd "$POLI_WS" && for f in $POLI_DBS; do echo "$f $(shasum -a 256 "$f" 2>/dev/null | cut -d' ' -f1)"; done ) > "$BASE/db-after.sha" 2>&1
( cd "$POLI_WS" && find data snapshots -type f 2>/dev/null | sort ) > "$BASE/files-after.txt" 2>&1

# ---------------- gates ----------------
log "--- gates ---"
python3 - "$EVID" "$BASE" "$POLI_WS" <<'PY'
import json, os, sys
evid, base, ws = sys.argv[1:]
label = "kg-maintenance"
results = {}

def rd():
    try:
        return json.load(open(f"{evid}/{label}/{label}.record.json"))
    except Exception:
        return None
def cl():
    try:
        return json.load(open(f"{evid}/{label}/{label}.client.json"))
    except Exception:
        return None
def reply():
    try:
        with open(f"{evid}/{label}/{label}.reply.txt", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

r, c = rd(), cl()
am = (r or {}).get("agentMeta", {})
spr = (r or {}).get("systemPromptReport", {}) or {}
rc = (c or {}).get("rc")
wall = (c or {}).get("wall_s")
timed_out = bool(((r or {}).get("summary") or {}).get("timed_out", False))
rep = reply()

# GATE ctx: resolved to the live 262144 contract
ctx_ok = am.get("contextTokens") == 262144 and am.get("contextTokensSource") == "resolved"
# GATE liveness: working (not blocked / no runaway)
liveness = (r or {}).get("livenessState")
# GATE rc + not client timeout
rc_ok = rc == 0 and not timed_out
# GATE tool behavior: real llama-server route, no fallback
et = (r or {}).get("executionTrace", {}) or {}
route_ok = et.get("winnerProvider") == "llama-server" and not et.get("fallbackUsed", False)
# GATE read-only: poliscopic workspace unchanged (git set equal, KG sqlite
# sha equal; sqlite sidecar files may legitimately appear - benign, recorded)
try:
    git_b = set(open(f"{base}/git-before.txt").read().splitlines())
    git_a = set(open(f"{base}/git-after.txt").read().splitlines())
    db_b = dict(l.strip().split(None, 1) for l in open(f"{base}/db-before.sha") if l.strip())
    db_a = dict(l.strip().split(None, 1) for l in open(f"{base}/db-after.sha") if l.strip())
    fb = set(open(f"{base}/files-before.txt").read().splitlines())
    fa = set(open(f"{base}/files-after.txt").read().splitlines())
    new_files = sorted(fa - fb)
    sidecars = [f for f in new_files if f.endswith((".sqlite-shm", ".sqlite-wal", ".sqlite-journal"))]
    real_new = [f for f in new_files if f not in sidecars]
    read_only_ok = (git_b == git_a) and (db_b == db_a) and not real_new
except Exception as ex:
    read_only_ok, new_files, sidecars = False, [], []
    results["readonly_error"] = str(ex)
# GATE reply: substantive maintenance assessment (non-empty)
results["reply"] = {"chars": len(rep), "head": rep[:400]}

# assembled/usage accounting (recorded; semantics documented in evidence notes)
usage = am.get("usage", {}) or {}
results["context"] = {
  "contextTokens": am.get("contextTokens"),
  "contextTokensSource": am.get("contextTokensSource"),
  "promptTokens": am.get("promptTokens"),
  "usage_input": usage.get("input"),
  "usage_output": usage.get("output"),
  "usage_cacheRead": usage.get("cacheRead"),
  "usage_total": usage.get("total"),
  "systemPromptChars": spr.get("systemPromptChars"),
  "projectContextChars": spr.get("projectContextChars"),
  "toolsSchemaChars": spr.get("toolsSchemaChars"),
  "toolsListChars": spr.get("toolsListChars"),
  "skillsPromptChars": spr.get("skillsPromptChars"),
  "bootstrapMaxChars": spr.get("bootstrapMaxChars"),
  "bootstrapTotalMaxChars": spr.get("bootstrapTotalMaxChars"),
}

results["kg-maintenance"] = {
  "pass": bool(ctx_ok and rc_ok and liveness == "working" and route_ok and read_only_ok and len(rep) > 0),
  "rc": rc, "wall_s": wall, "timed_out": timed_out,
  "liveness": liveness,
  "ctx_resolved_262144": bool(ctx_ok),
  "winner_llama_server": route_ok,
  "read_only_preserved": bool(read_only_ok),
  "new_files": new_files,
  "sidecars_only": sidecars,
  "reply_chars": len(rep),
}
json.dump(results, open(f"{evid}/gates.json", "w"), indent=2)
print(json.dumps(results, indent=1))
PY
GATE_RC=$?
[ $GATE_RC -eq 0 ] || { log "gates evaluator failed"; exit 20; }

# ---------------- final verify ----------------
sleep 2
log "--- final verify ---"
python3 - "$EVID" "$PORT" "$GW_PORT" "$KIMI_DB" "$POLI_DB" "$REDACT_JS" <<'PY'
import json, subprocess, sys
evid, port, gw, kdb, pdb, redact = sys.argv[1:]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception: return "?"
rec = {
  "step": "13G-final",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "kimi_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{kdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "poliscopic_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{pdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
}
json.dump(rec, open(f"{evid}/final-verify.json", "w"), indent=2)
print("FINAL:", json.dumps(rec))
PY

# ---------------- summary + exit ----------------
python3 - "$EVID" <<'PY'
import json, sys
evid = sys.argv[1]
g = json.load(open(f"{evid}/gates.json"))
p = json.load(open(f"{evid}/preflight.json"))
f = json.load(open(f"{evid}/final-verify.json"))
leg = g.get("kg-maintenance", {})
pid_stable = (p.get("llama_pid") == f.get("llama_pid") and
              p.get("gateway_pid") == f.get("gateway_pid") and
              bool(p.get("llama_pid")))
verify_ok = (f.get("llama_health") == "200" and f.get("gateway_healthz") == "200" and
             f.get("resolved_n_ctx") == "262144" and
             f.get("kimi_fk_violations") == "0" and
             f.get("poliscopic_fk_violations") == "0" and
             f.get("step11b_sha") == "8baf474684")
no_drift = not p.get("config_drift_vs_13D")
ok = bool(leg.get("pass")) and pid_stable and verify_ok and no_drift
summ = {
  "gates_pass": bool(ok),
  "leg": "kg-maintenance",
  "pid_stable_across_window": pid_stable,
  "final_verify_ok": verify_ok,
  "no_config_drift": no_drift,
}
json.dump(summ, open(f"{evid}/summary.json", "w"), indent=2)
print("13G SUMMARY:", json.dumps(summ))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
PY
RC=$?
log "=== 13G driver exit $RC ==="
exit $RC
