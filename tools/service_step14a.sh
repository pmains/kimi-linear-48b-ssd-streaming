#!/bin/bash
# Step 14A driver - Measure Existing Reuse Behavior (retained 2026-09-07).
#
# Owner authorization 2026-09-07 20:42 MST per SERVICE-ROADMAP.md Step 14A.
# MEASUREMENT ONLY: no production changes, no llama.cpp/OpenClaw patches, no
# config/tool/prompt changes. Live 262144 contract, llama pid expected 7021.
#
# Characterizes prefix/KV/cache reuse through the REAL agent path using
# controlled consecutive turns in the SAME agent session (same session key,
# successive runner invocations) plus fresh-session controls, per roadmap:
#   1. small established context + small suffix            -> legA t1/t2
#   2. representative medium accumulated ctx + small suffix -> legB t1/t2
#   3. large accumulated context + small suffix (13G poly session, 47.6K)
#                                                            -> legC
#   4. repeated/stable-tool-schema agent turn (eng repeat) -> legE t1/t2
#   5. fresh-session comparison where appropriate          -> legD
# Per-turn records: assembled prompt tokens, cacheRead, newly evaluated
# (llama per-task prompt processing at progress=1.00 vs release n_tokens),
# prompt-eval duration/tok/s, TTFT, decode, wall, PID, slot state,
# compaction events, material-change detection, reuse outcome.
#
# Usage: bash tools/service_step14a.sh [RUN_SUFFIX]
set -u

ROOT=/Users/pmains/Code/openclaw/kimi
EVID="$ROOT/benchmarks/results/service-step-14/14a"
RUNNER="$ROOT/tools/service_step10a_turn.py"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PORT=18080
GW_PORT=18789
KIMI_DB=/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite
POLI_DB=/Users/pmains/.openclaw/agents/poliscopic/agent/openclaw-agent.sqlite
REDACT_JS=/opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js
CFG="$HOME/.openclaw/openclaw.json"
CFG_13D="$HOME/.openclaw/openclaw.json.bak-step13d-20260907"
SUFFIX="${1:-r1}"
LOG="$EVID/driver.log"
mkdir -p "$EVID/prompts" "$EVID/baselines"

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

# run_turn <outdir> <label> <agent> <session-key> <promptfile> <timeout>
run_turn() {
  local outdir="$1" label="$2" agent="$3" key="$4" prompt="$5" tmo="$6"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label.client.json" ]; then
    log "[$label] cached"
    return 0
  fi
  log "=== $label start (agent=$agent key=$key timeout=${tmo}s) ==="
  python3 "$RUNNER" "$outdir" "$label" "$prompt" "$key" \
    --agent "$agent" --timeout "$tmo" \
    --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
    > "$outdir/$label.turn.stdout" 2> "$outdir/$label.turn.stderr"
  local rc wall
  rc=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['rc'])" 2>/dev/null)
  wall=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['wall_s'])" 2>/dev/null)
  log "[$label] rc=$rc wall=${wall}s"
}

# ---------------- preflight ----------------
log "--- preflight (14A $SUFFIX) ---"
for _ in 1 2 3 4 5 6; do health_ok && gw_ok && break; sleep 5; done
python3 - "$EVID" "$PORT" "$GW_PORT" "$KIMI_DB" "$POLI_DB" "$REDACT_JS" "$CFG" "$CFG_13D" <<'PY'
import json, os, subprocess, sys
evid, port, gw, kdb, pdb, redact, cfg, cfg13 = sys.argv[1:]
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
  "step": "14A-preflight",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "kimi_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{kdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "poliscopic_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{pdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "slot": sh(f"curl -s -m 5 http://127.0.0.1:{port}/slots | python3 -c \"import json,sys; s=json.load(sys.stdin)[0]; print({{'proc': s.get('is_processing'), 'n_prompt': s.get('n_prompt_tokens'), 'cache': s.get('n_prompt_tokens_cache'), 'task': s.get('id_task')}})\""),
  "config_drift_vs_13D": drift,
  "other_agent_procs": sh("pgrep -f 'openclaw agent|service_step10a_turn' | grep -v $$ | tr '\\n' ' '"),
}
json.dump(rec, open(f"{evid}/preflight.json", "w"), indent=2)
print("PREFLIGHT:", json.dumps(rec))
PY
log "--- preflight recorded ---"

# ---------------- workload idle gate (before measured turns) ----------------
log "--- workload idle gate ---"
IDLE="$(slot_idle)"
log "slot_idle=$IDLE"
OTHER=$(pgrep -f "openclaw agent|service_step10a_turn" | grep -v $$ | head -3 | tr '\n' ' ')
log "other_agent_procs=$OTHER"
if [ "$IDLE" != "IDLE" ] || [ -n "$OTHER" ]; then
  log "14A NOT STARTED: active Kimi workload (slot=$IDLE other=$OTHER)"
  exit 30
fi
log "idle gate PASS"

# ---------------- prompts ----------------
P="$EVID/prompts"
printf 'Reply with exactly: PROBE-1\n' > "$P/legA-t1.txt"
printf 'Reply with exactly: PROBE-2\n' > "$P/legA-t2.txt"
printf 'Use the read tool to read service-progress/step-13-context-capacity.md (lines 1-400), then reply with exactly: MED-1\n' > "$P/legB-t1.txt"
printf 'Reply with exactly: MED-SUFFIX\n' > "$P/legB-t2.txt"
printf 'Without re-running any inspection or tool, reply with only the single highest-priority KG maintenance item name from your earlier assessment.\n' > "$P/legC.txt"
printf 'Now reply with exactly: REUSE-CONFIRMED\n' > "$P/legC2.txt"
printf 'Reply with exactly: PROBE-2\n' > "$P/legD.txt"
printf 'Use the read tool to read tools/service_step10a_turn.py and reply with exactly: E-1\n' > "$P/legE-t1.txt"
printf 'Use the read tool to read tools/service_step10a_turn.py and reply with exactly: E-1\n' > "$P/legE-t2.txt"

# ---------------- legs ----------------
# legA: same fresh kimi session, two consecutive tiny turns
run_turn "$EVID/legA" "legA-t1-$SUFFIX" kimi "agent:kimi:step14a-legA-$SUFFIX" "$P/legA-t1.txt" 900
run_turn "$EVID/legA" "legA-t2-$SUFFIX" kimi "agent:kimi:step14a-legA-$SUFFIX" "$P/legA-t2.txt" 900

# legB: same fresh kimi session, medium tool ctx then small suffix
run_turn "$EVID/legB" "legB-t1-$SUFFIX" kimi "agent:kimi:step14a-legB-$SUFFIX" "$P/legB-t1.txt" 1500
run_turn "$EVID/legB" "legB-t2-$SUFFIX" kimi "agent:kimi:step14a-legB-$SUFFIX" "$P/legB-t2.txt" 900

# legC: CONTINUE the retained 13G poliscopic session (47.6K established ctx)
# NOTE: slot KV for the 13G prompt was evicted by earlier kimi-agent legs in
# this window -> legC measures the eviction-miss (full re-prefill of ~48K);
# legC2 (immediately after) measures the SAME large context reused WARM.
run_turn "$EVID/legC" "legC-$SUFFIX" poliscopic "agent:poliscopic:step13g-kg-maintenance-r1" "$P/legC.txt" 2400
run_turn "$EVID/legC" "legC2-$SUFFIX" poliscopic "agent:poliscopic:step13g-kg-maintenance-r1" "$P/legC2.txt" 1200

# legD: fresh-session control for the small suffix (no accumulated session)
run_turn "$EVID/legD" "legD-$SUFFIX" kimi "agent:kimi:step14a-legD-$SUFFIX" "$P/legD.txt" 900

# legE: same fresh kimi session, IDENTICAL engineering tool turns x2
run_turn "$EVID/legE" "legE-t1-$SUFFIX" kimi "agent:kimi:step14a-legE-$SUFFIX" "$P/legE-t1.txt" 1200
run_turn "$EVID/legE" "legE-t2-$SUFFIX" kimi "agent:kimi:step14a-legE-$SUFFIX" "$P/legE-t2.txt" 1200

# ---------------- analysis + gates ----------------
log "--- analysis + gates ---"
python3 - "$EVID" "$SUFFIX" <<'PY'
import json, os, re, sys
evid, sfx = sys.argv[1:]
results = {"legs": {}}

def rd(leg, label):
    try: return json.load(open(f"{evid}/{leg}/{label}.record.json"))
    except Exception: return None
def cl(leg, label):
    try: return json.load(open(f"{evid}/{leg}/{label}.client.json"))
    except Exception: return None
def wnd(leg, label, kind):
    try: return open(f"{evid}/{leg}/{label}.{kind}.window.log", encoding="utf-8").read()
    except Exception: return ""

def parse_llama(w):
    """Per task: assembled (release n_tokens), new (prompt processing at
    progress=1.00), cached, reuse fraction, prefill t/rate, decode."""
    tasks = {}
    cur = None
    for ln in w.splitlines():
        m = re.search(r'task (\d+) \| (.*)', ln)
        if not m: continue
        tid, rest = m.group(1), m.group(2)
        if 'processing task' in rest:
            cur = tid; tasks.setdefault(tid, {"new": None, "assembled": None, "pt": None, "rate": None, "gen": None, "tg": None})
        elif 'prompt processing, n_tokens' in rest:
            mm = re.search(r'n_tokens =\s*(\d+), progress = 1\.00', rest)
            if mm and cur:
                t = tasks.setdefault(cur, {"new": None, "assembled": None, "pt": None, "rate": None, "gen": None, "tg": None})
                t["new"] = int(mm.group(1))
                tm = re.search(r't =\s*([\d.]+) s / ([\d.]+) tokens per second', rest)
                if tm: t["pt"], t["rate"] = float(tm.group(1)), float(tm.group(2))
        elif 'stop processing: n_tokens' in rest:
            mm = re.search(r'stop processing: n_tokens = (\d+), truncated = (\d+)', rest)
            if mm and cur:
                tasks.setdefault(cur, {})["assembled"] = int(mm.group(1))
        elif 'n_gen' in rest:
            mm = re.search(r'n_gen =\s*(\d+), tg =\s*([\d.]+) t/s', rest)
            if mm and cur:
                t = tasks.setdefault(cur, {})
                t["gen"] = int(mm.group(1)); t["tg"] = float(mm.group(2))
    out = []
    for tid, t in sorted(tasks.items(), key=lambda kv: int(kv[0])):
        a = t.get("assembled"); n = t.get("new")
        t["cached"] = (a - n) if (a is not None and n is not None) else None
        t["reuse"] = (t["cached"] / a) if (a and t["cached"] is not None) else None
        t["task"] = tid
        out.append(t)
    return out

def turn_stats(leg, label):
    r, c = rd(leg, label), cl(leg, label)
    if not r or not c: return None
    am = r.get("agentMeta", {}) or {}
    spr = r.get("systemPromptReport", {}) or {}
    w = wnd(leg, label, "llama")
    tasks = parse_llama(w)
    comp = len(re.findall(r'(?i)compact|context shift', w))
    trunc = sum(1 for t in tasks if t.get("assembled") and False)
    return {
        "label": label, "rc": c.get("rc"), "wall_s": c.get("wall_s"),
        "timed_out": c.get("timed_out"),
        "contextTokens": am.get("contextTokens"), "contextTokensSource": am.get("contextTokensSource"),
        "promptTokens": am.get("promptTokens"),
        "usage": am.get("usage"), "lastCallUsage": am.get("lastCallUsage"),
        "liveness": r.get("livenessState"), "winnerProvider": ((r.get("executionTrace") or {}).get("winnerProvider")),
        "sysPromptChars": spr.get("systemPromptChars"), "projChars": spr.get("projectContextChars"),
        "toolsSchemaChars": spr.get("toolsSchemaChars"), "skillsChars": spr.get("skillsPromptChars"),
        "llama_tasks": tasks, "compaction_matches": comp,
        "slots_before": (c.get("slots_before") or {}), "slots_after": (c.get("slots_after") or {}),
    }

for leg, labels in [("legA", [f"legA-t1-{sfx}", f"legA-t2-{sfx}"]),
                    ("legB", [f"legB-t1-{sfx}", f"legB-t2-{sfx}"]),
                    ("legC", [f"legC-{sfx}", f"legC2-{sfx}"]),
                    ("legD", [f"legD-{sfx}"]),
                    ("legE", [f"legE-t1-{sfx}", f"legE-t2-{sfx}"])]:
    results["legs"][leg] = {l: turn_stats(leg, l) for l in labels}

json.dump(results, open(f"{evid}/analysis.json", "w"), indent=1)
print("ANALYSIS WRITTEN", {k: {l: ((v or {}).get("rc"), (v or {}).get("wall_s")) for l, v in d.items()} for k, d in results["legs"].items()})
PY
RC=$?
[ $RC -eq 0 ] || { log "analysis failed rc=$RC"; exit 20; }

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
  "step": "14A-final",
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
log "--- 14A driver done ---"
exit 0
