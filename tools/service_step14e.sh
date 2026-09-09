#!/bin/bash
# Step 14E driver - Production Qualification vs Step 13G (retained 2026-09-08).
#
# Owner authorization 2026-09-08 20:47 MST per SERVICE-ROADMAP.md 14E.
# MEASUREMENT ONLY: no production changes, no llama.cpp/OpenClaw patches, no
# config/tool/prompt changes, no gateway/llama restart. Live 262144 contract,
# llama pid expected 7021. Single-llama discipline: never restart llama.
#
# Roadmap 14E decisive benchmark: large accumulated context + small suffix,
# ideally ~150K existing context + ~1K new tokens. The retained 13G
# poliscopic session (agent:poliscopic:step13g-kg-maintenance-r1, ~48K
# assembled, 43 transcript events) is the real-workload anchor: its cold
# run measured promptTokens 47,626 / usage.input 46,780 / wall 1,410.7s.
#
# Design (real agent path, same session key throughout):
#   B1..B14 build legs: read the large poliscopic workspace docs IN FULL
#     (each < 50KB, within the read-tool cap), reply exactly B<n>-DONE.
#     Each tool result appends ~5-12K tokens to the transcript, growing the
#     assembled prompt from ~48K toward ~150K. Same-session prefix reuse
#     means each leg's prefill cost is only the newly appended file tokens.
#   S1 decisive suffix: a read-free user turn embedding ~0.6-1K tokens of
#     new reference text + an exact-reply instruction -> measures whether
#     the system evaluates the ~1K suffix instead of re-prefilling ~150K.
#   TTFT proxy: llama prompt-eval ms of the decisive task (decode starts
#     after prefill). New-token proof: usage.input / llama n_tokens new vs
#     assembled; cacheRead = assembled - input.
#
# Run detached (setsid) so exec-session reaping cannot kill it; legs cache
# their client.json so an interrupted run resumes where it stopped.
#
# Usage: bash tools/service_step14e.sh [RUN_SUFFIX]
set -u

ROOT=/Users/pmains/Code/openclaw/kimi
EVID="$ROOT/benchmarks/results/service-step-14/14e"
RUNNER="$ROOT/tools/service_step10a_turn.py"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PORT=18080
GW_PORT=18789
KIMI_DB=/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite
POLI_DB=/Users/pmains/.openclaw/agents/poliscopic/agent/openclaw-agent.sqlite
REDACT_JS=/opt/homebrew/lib/node_modules/openclaw/dist/redact-DMnNBHXb.mjs
CFG="$HOME/.openclaw/openclaw.json"
CFG_13D="$HOME/.openclaw/openclaw.json.bak-step13d-20260907"
SUFFIX="${1:-r1}"
LOG="$EVID/driver.log"
POLI_WS=/Users/pmains/Code/openclaw/poliscopic
AGENT=poliscopic
SESSION=agent:poliscopic:step13g-kg-maintenance-r1
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

# run_turn <outdir> <label> <promptfile> <timeout>
run_turn() {
  local outdir="$1" label="$2" prompt="$3" tmo="$4"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label.client.json" ]; then
    log "[$label] cached"
    return 0
  fi
  log "=== $label start (agent=$AGENT key=$SESSION timeout=${tmo}s) ==="
  python3 "$RUNNER" "$outdir" "$label" "$prompt" "$SESSION" \
    --agent "$AGENT" --timeout "$tmo" \
    --llama-log "$LLAMA_LOG" --gateway-log "$GATEWAY_LOG" \
    > "$outdir/$label.turn.stdout" 2> "$outdir/$label.turn.stderr"
  local rc wall
  rc=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['rc'])" 2>/dev/null)
  wall=$(python3 -c "import json;print(json.load(open('$outdir/$label.client.json'))['wall_s'])" 2>/dev/null)
  log "[$label] rc=$rc wall=${wall}s"
}

# ---------------- preflight ----------------
log "--- preflight (14E $SUFFIX) ---"
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
  "step": "14E-preflight",
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
  "loadavg": sh("sysctl -n vm.loadavg"),
  "other_agent_procs": sh("pgrep -f 'openclaw agent|service_step10a_turn' | grep -v $$ | tr '\\n' ' '"),
  "session_transcript_events": sh(f"python3 -c \"import sqlite3; c=sqlite3.connect('{pdb}'); r=c.execute(\\\"SELECT current_session_id FROM session_nodes WHERE session_key='agent:poliscopic:step13g-kg-maintenance-r1'\\\").fetchone(); print(c.execute('SELECT COUNT(*) FROM transcript_events WHERE session_id=?',(r[0],)).fetchone()[0]) if r else print('MISSING')\""),
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
  log "14E NOT STARTED: active Kimi workload (slot=$IDLE other=$OTHER)"
  exit 30
fi
log "idle gate PASS"

# ---------------- prompts: 14 build legs (real poliscopic docs, <50KB) ----------------
P="$EVID/prompts"
declare -a FILES=(
  "docs/KG-ROADMAP.md"
  "docs/kg-progress/STAGE-0.md"
  "docs/entities/PIPELINE.md"
  "docs/roadmaps/ENTITY_RELATIONSHIPS.md"
  "docs/ARCHITECTURE.md"
  "docs/INFO_LAYER.md"
  "docs/WORKFLOWS.md"
  "docs/ops/OPS.md"
  "docs/roadmaps/GRAPH_BUILDER_FIXES.md"
  "docs/archive/entity-feedback-loop.md"
  "data/june2026_report_body.md"
  "data/reviews/newsletter-writer-role.md"
  "data/entity-model-improvement-plan.md"
  "drafts/lessons-learned-paper.md"
)
for i in "${!FILES[@]}"; do
  n=$((i+1))
  rel="${FILES[$i]}"
  abs="$POLI_WS/$rel"
  printf 'Use the read tool to read the file at exactly this path in full (no line limit):\n%s\n\nThen reply with exactly: B%d-DONE\n' "$abs" "$n" > "$P/B$n.txt"
done
# decisive suffix: ~0.6-1K new tokens of read-free reference text + exact reply
cat > "$P/S1.txt" <<'EOF'
Below is a short new reference note for the maintenance assessment. Do not run any tool.

REFERENCE-NOTE-14E: The Poliscopic knowledge graph merges entity records from Maricopa planning documents, county assessor feeds, and Bluesky community tracking. Integrity indicators are the dangling-edge count, the duplicate-entity ratio, and the per-source citation coverage in the analytics database. The roadmap treats KG maintenance as a queue: STAGE-0 backfills missing parcels, PIPELINE repairs entity resolution conflicts, and ENTITY_RELATIONSHIPS audits edge directionality. Recent archive notes flag that entity-feedback-loop corrections are not yet replayed into the canonical store, and the lessons-learned draft recommends a weekly integrity sweep that compares maricopa.sqlite counts against analytics.sqlite aggregates. OPS runbooks require the sweep to be read-only: it must never mutate schemas or data, only report counts and anomalies for the next maintenance ticket.

Using only the reference note above and your earlier KG assessment in this session, reply with exactly: 14E-SUFFIX-OK
EOF

# ---------------- build legs ----------------
for i in "${!FILES[@]}"; do
  n=$((i+1))
  run_turn "$EVID/legB" "B$n-$SUFFIX" "$P/B$n.txt" 1500
done

# ---------------- decisive suffix leg ----------------
run_turn "$EVID/legS" "S1-$SUFFIX" "$P/S1.txt" 1200

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
    progress=1.00), cached, reuse, prefill ms (ttft proxy), rate, decode."""
    tasks = {}
    cur = None
    for ln in w.splitlines():
        m = re.search(r'task (\d+) \| (.*)', ln)
        if not m: continue
        tid, rest = m.group(1), m.group(2)
        if 'processing task' in rest:
            cur = tid; tasks.setdefault(tid, {})
        elif 'prompt processing, n_tokens' in rest:
            mm = re.search(r'n_tokens =\s*(\d+), progress = 1\.00', rest)
            if mm and cur:
                t = tasks.setdefault(cur, {})
                t["new"] = int(mm.group(1))
                tm = re.search(r't =\s*([\d.]+) s / ([\d.]+) tokens per second', rest)
                if tm: t["pt_s"], t["rate"] = float(tm.group(1)), float(tm.group(2))
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
    # TTFT proxy: prefill ms of the LAST fully-processed task (decode starts after prefill)
    ttft_ms = None
    for tk in reversed(tasks):
        if tk.get("pt_s") is not None:
            ttft_ms = int(tk["pt_s"] * 1000)
            break
    return {
        "label": label, "rc": c.get("rc"), "wall_s": c.get("wall_s"),
        "timed_out": c.get("timed_out"),
        "contextTokens": am.get("contextTokens"), "contextTokensSource": am.get("contextTokensSource"),
        "promptTokens": am.get("promptTokens"),
        "usage": am.get("usage"), "lastCallUsage": am.get("lastCallUsage"),
        "winnerProvider": ((r.get("executionTrace") or {}).get("winnerProvider")),
        "liveness": r.get("livenessState"),
        "llama_tasks": tasks, "compaction_matches": comp, "ttft_proxy_ms": ttft_ms,
        "slots_before": (c.get("slots_before") or {}), "slots_after": (c.get("slots_after") or {}),
    }

for leg, n in (("legB", 14), ("legS", 1)):
    labels = [f"B{i}-{sfx}" for i in range(1, n+1)] if leg == "legB" else [f"S1-{sfx}"]
    results["legs"][leg] = {l: turn_stats(leg, l) for l in labels}

# assemble a compact table: last task per leg (assembled/new/cached/reuse/wall)
table = []
for leg, turns in results["legs"].items():
    for label, t in turns.items():
        if not t: continue
        lt = (t.get("llama_tasks") or [{}])[-1]
        u = t.get("usage") or {}
        table.append({
            "label": label, "rc": t.get("rc"), "wall_s": round(t.get("wall_s") or 0, 1),
            "promptTokens": t.get("promptTokens"),
            "usage_input": u.get("input"), "usage_cacheRead": u.get("cacheRead"),
            "lastTask_new": lt.get("new"), "lastTask_assembled": lt.get("assembled"),
            "lastTask_cached": lt.get("cached"),
            "lastTask_reuse": None if lt.get("reuse") is None else round(lt["reuse"], 4),
            "ttft_proxy_ms": t.get("ttft_proxy_ms"), "compaction": t.get("compaction_matches"),
        })
results["summary_table"] = table

# 13G cold reference (retained gates.json)
try:
    g = json.load(open("/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-13/13g-poliscopic-capacity/gates.json"))
    results["reference_13g"] = {
        "promptTokens": g.get("promptTokens"), "usage_input": g.get("usage_input"),
        "usage_cacheRead": g.get("usage_cacheRead"), "wall_s": g.get("wall_s"),
    }
except Exception as e:
    results["reference_13g"] = {"error": str(e)}

json.dump(results, open(f"{evid}/analysis.json", "w"), indent=1)
print("ANALYSIS WRITTEN")
for row in table:
    print(row)
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
  "step": "14E-final",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "kimi_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{kdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "poliscopic_fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{pdb}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "loadavg": sh("sysctl -n vm.loadavg"),
}
json.dump(rec, open(f"{evid}/final-verify.json", "w"), indent=2)
print("FINAL:", json.dumps(rec))
PY
log "--- 14E driver done ---"
exit 0
