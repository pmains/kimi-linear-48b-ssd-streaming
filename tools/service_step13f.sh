#!/bin/bash
# Step 13F driver - Bounded Regression Gates at 256K (retained 2026-09-07).
#
# Owner go 2026-09-07 18:27 MST. Production is the 262144 contract (13D/13E).
# Roadmap 13F: verify the context promotion preserved the production
# properties established through Steps 9-12 using BOUNDED CONTROLS (no full
# re-qualification): normal interaction, tool calling, exact-format /
# instruction-following, Step 11B redaction/storage, FK integrity, loop
# detection, no runaway, gateway/llama health, no unrelated config drift.
#
# Legs (fresh isolated session each, real agent path, live 262144 server):
#   smoke    "Respond only OK"                        (normal interaction)
#   p2       exact JSON {"ok": true}                  (format - baseline PASS)
#   p3       number-only reasoning (40)               (informational canary;
#            baseline FAIL-class = prose w/ 40; must stay same class)
#   p5       bounded tool use, count of e5 files (5)  (tool+format - baseline PASS)
#   norm     NORM-ABS multi-step tools (read/exec/progress_card)
#   loop     LOOP-STRICT identical-read loop          (detector must fire)
#   storage  11B redaction/storage probe              (byte-level, sqlite)
#
# Per-leg record via retained service_step10a_turn.py (client.json,
# record.json with agentMeta/contextTokens/livenessState, llama+gateway
# windows, slots). Gates + preflight/final verify + driver.log. Exit 0 iff
# all required gates pass AND final verify matches preflight (no restarts,
# health, sha, FK, n_ctx) AND no config drift vs the 13D snapshot.
#
# Usage: bash tools/service_step13f.sh
set -u

KIMI_DIR=/Users/pmains/Code/openclaw/kimi
ROOT="$KIMI_DIR"
EVID="$ROOT/benchmarks/results/service-step-13/13f-256k"
RUNNER="$ROOT/tools/service_step10a_turn.py"
PROM09="$ROOT/benchmarks/results/service-step-09/prompts"
PROM10D="$ROOT/benchmarks/results/service-step-10d/prompts"
PROM13F="$EVID/prompts"
LLAMA_LOG=/tmp/kimi-llama-server.launchd.log
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"
PORT=18080
GW_PORT=18789
DB=/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite
REDACT_JS=/opt/homebrew/lib/node_modules/openclaw/dist/redact-CquADQ9-.js
CFG="$HOME/.openclaw/openclaw.json"
CFG_13D="$HOME/.openclaw/openclaw.json.bak-step13d-20260907"
AGENT=kimi
TIMEOUT=600
LOG="$EVID/driver.log"
mkdir -p "$EVID" "$PROM13F"

log() { echo "[$(date '+%F %T %z')] $*" | tee -a "$LOG"; }

health_ok() { curl -s -m 5 http://127.0.0.1:$PORT/health >/dev/null 2>&1; }
gw_ok() { [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:$GW_PORT/healthz)" = "200" ]; }

# run_one <label> <promptfile> [timeout]
run_one() {
  local label="$1" prompt="$2" tmo="${3:-$TIMEOUT}"
  local outdir="$EVID/$label"
  local key="agent:$AGENT:step13f-$label-r1"
  mkdir -p "$outdir"
  if [ -f "$outdir/$label.client.json" ]; then
    log "[$label] cached"
    return 0
  fi
  log "=== $label start (agent=$AGENT timeout=${tmo}s) $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  python3 "$RUNNER" "$outdir" "$label" "$prompt" "$key" \
    --agent "$AGENT" --timeout "$tmo" \
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
python3 - "$EVID" "$PORT" "$GW_PORT" "$DB" "$REDACT_JS" "$CFG" "$CFG_13D" <<'PY'
import json, os, subprocess, sys
evid, port, gw, db, redact, cfg, cfg13 = sys.argv[1:]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"

# semantic config drift vs the pre-13D snapshot: the ONLY allowed delta is the
# two kimi-provider contextWindow promotions 65536 -> 262144. Everything else
# is drift (compare parsed values, so formatting/pretty-print is ignored).
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
drift_paths = []
if old is not None and new is not None:
    a, b = dict(leaves(old)), dict(leaves(new))
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            ok = (k == "/models/providers/kimi-local/models[0]/contextWindow" and a.get(k) == 65536 and b.get(k) == 262144) or \
                 (k == "/models/providers/llama-server/models[0]/contextWindow" and a.get(k) == 65536 and b.get(k) == 262144)
            if not ok:
                drift_paths.append({"path": k, "old": a.get(k), "new": b.get(k)})

rec = {
  "step": "13F-preflight",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{db}').execute('PRAGMA foreign_key_check').fetchall()))\""),
  "config_context_windows": sh(f"python3 -c \"import json,os; d=json.load(open('{cfg}')); out=[]; [out.append((p,[m.get('contextWindow') for m in (c.get('models') or [])])) for p,c in d.get('models',{{}}).get('providers',{{}}).items() if 'kimi' in p.lower() or 'llama' in p.lower()]; print(out)\""),
  "plist_ctx": sh("grep -A2 'KIMI_CTX' ~/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist | grep string | head -1 | sed 's/<\\/string>//;s/<string>//' | tr -d ' '"),
  "config_drift_vs_13D": drift_paths,
}
json.dump(rec, open(f"{evid}/preflight.json", "w"), indent=2)
print("PREFLIGHT:", json.dumps(rec))
PY
log "--- preflight recorded ---"

# ---------------- prompts ----------------
# storage probe (11B redaction/storage, byte-level):
#  - benign long path containing a 40+ contiguous [A-Za-z0-9] run (the old
#    false-positive shape) must persist VERBATIM (11B fix live)
#  - labeled AWS secret (public AWS-docs example value, not a real
#    credential) must still be MASKED in the STORED transcript
cat > "$PROM13F/storage.md" <<'EOF'
Reply with exactly: STORAGE-OK

Do not use any tool. Do not mention or repeat any of the text below.

Context (for reference only):
path=/Users/pmains/Code/openclaw/kimi/benchmarks/results/service-step-13/13f-256k/storage/this-is-a-very-long-benign-file-name-segment-1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef.txt
aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY
EOF

# ---------------- legs ----------------
run_one smoke "$PROM09/smoke.md"
run_one p2 "$PROM09/P2.md"
run_one p3 "$PROM09/P3.md"
run_one p5 "$PROM09/P5.md"
run_one norm "$PROM10D/NORM-ABS.md"
run_one loop "$PROM10D/LOOP-STRICT.md"
run_one storage "$PROM13F/storage.md"

# ---------------- gates ----------------
log "--- gates ---"
python3 - "$EVID" "$DB" <<'PY'
import json, os, re, sqlite3, sys
evid, db = sys.argv[1:]
results = {}

def rd(label):
    try:
        return json.load(open(f"{evid}/{label}/{label}.record.json"))
    except Exception:
        return None
def cl(label):
    try:
        return json.load(open(f"{evid}/{label}/{label}.client.json"))
    except Exception:
        return None
def reply(label):
    r = rd(label)
    if not r: return ""
    return (r.get("summary") or {}).get("reply", "") or ""
def wall(label):
    c = cl(label)
    return (c or {}).get("wall_s", -1)
def ctx(label):
    r = rd(label)
    return ((r or {}).get("agentMeta") or {}).get("contextTokens")
def liveness(label):
    r = rd(label)
    return ((r or {}).get("livenessState"))
def timed_out(label):
    r = rd(label) or {}
    s = r.get("summary") or {}
    return bool(s.get("timed_out", False))

def session_text(session_key):
    """Pull every stored transcript event's text for a session key."""
    out = []
    try:
        c = sqlite3.connect(db)
        row = c.execute("SELECT current_session_id FROM session_nodes WHERE session_key=?", (session_key,)).fetchone()
        if row:
            sid = row[0]
            for seq, e in c.execute("SELECT seq, event_json FROM transcript_events WHERE session_id=? ORDER BY seq", (sid,)).fetchall():
                try:
                    out.append(json.dumps(json.loads(e)))
                except Exception:
                    out.append(str(e))
        c.close()
    except Exception:
        pass
    return "\n".join(out)

# GATE smoke: normal interaction -> rc 0, reply OK
rep = reply("smoke").strip()
results["smoke"] = {"pass": (cl("smoke") or {}).get("rc") == 0 and rep == "OK",
                    "rc": (cl("smoke") or {}).get("rc"), "reply": rep[:80],
                    "ctx": ctx("smoke"), "wall_s": round(wall("smoke"), 1)}
# GATE p2: exact {"ok": true}
rep = reply("p2").strip()
results["p2"] = {"pass": rep == '{"ok": true}' and (cl("p2") or {}).get("rc") == 0,
                 "rc": (cl("p2") or {}).get("rc"), "reply": rep[:120],
                 "ctx": ctx("p2"), "wall_s": round(wall("p2"), 1)}
# GATE p3 (informational): baseline FAIL-class = prose containing 40, rc 0.
rep = reply("p3").strip()
results["p3"] = {"pass": (cl("p3") or {}).get("rc") == 0 and "40" in rep,
                 "informational": True,
                 "rc": (cl("p3") or {}).get("rc"), "reply": rep[:120],
                 "ctx": ctx("p3"), "wall_s": round(wall("p3"), 1)}
# GATE p5: bounded tool use -> count 5 (baseline PASS, prose allowed)
rep = reply("p5").strip()
results["p5"] = {"pass": (cl("p5") or {}).get("rc") == 0 and "5" in rep,
                 "rc": (cl("p5") or {}).get("rc"), "reply": rep[:120],
                 "ctx": ctx("p5"), "wall_s": round(wall("p5"), 1)}
# GATE norm: multi-step tools complete, working, no loop block, ctx resolved
results["norm"] = {"pass": (cl("norm") or {}).get("rc") == 0 and
                            liveness("norm") in ("working", None) and
                            ctx("norm") == 262144 and not timed_out("norm"),
                   "rc": (cl("norm") or {}).get("rc"),
                   "liveness": liveness("norm"), "ctx": ctx("norm"),
                   "wall_s": round(wall("norm"), 1)}
# GATE loop: detector fired (blocked OR CRITICAL/veto evidence), NOT a client
# timeout, wall bounded. Evidence from stdout + gateway window + sqlite
# transcript of the loop session.
evidence = ""
for p in (f"{evid}/loop/loop.turn.stdout", f"{evid}/loop/loop.gateway.window.log",
          f"{evid}/loop/loop.client.json", f"{evid}/loop/loop.record.json"):
    try:
        evidence += open(p, errors="replace").read() + "\n"
    except Exception:
        pass
evidence += session_text("agent:kimi:step13f-loop-r1")
loop_evidence = bool(re.search(r"CRITICAL: Called|Session execution blocked|critical.tool.loop|tool.loop.recovery|identical outcomes|blocked after 20|tool-loop|loop detected", evidence, re.I))
det_fired = liveness("loop") == "blocked" or loop_evidence
results["loop"] = {"pass": det_fired and not timed_out("loop") and wall("loop") < 550,
                   "detector_fired": bool(det_fired), "loop_evidence": loop_evidence,
                   "rc": (cl("loop") or {}).get("rc"),
                   "liveness": liveness("loop"), "ctx": ctx("loop"),
                   "wall_s": round(wall("loop"), 1), "timed_out": timed_out("loop")}
# GATE storage: 11B redaction/storage byte-level from the transcript DB.
#  - benign long path (with a 40+ contiguous alnum run = the old false-positive
#    shape) must be stored VERBATIM - the 11B fix must be live
#  - the labeled AWS-docs example secret must NOT be stored verbatim (still
#    redacted). NOTE: the redaction rendering itself is head-6 + U+2026 +
#    tail-4, so a correctly masked secret DOES contain U+2026; therefore the
#    gate is benign-verbatim AND secret-absent, not "no U+2026 anywhere".
stored = ""
try:
    c = sqlite3.connect(db)
    row = c.execute("SELECT current_session_id FROM session_nodes WHERE session_key=?",
                    ("agent:kimi:step13f-storage-r1",)).fetchone()
    if row:
        sid = row[0]
        for seq, e in c.execute("SELECT seq, event_json FROM transcript_events WHERE session_id=? ORDER BY seq", (sid,)).fetchall():
            d = json.loads(e)
            if d.get("type") == "message":
                msg = d.get("message") or {}
                if msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, str):
                        stored = content
                    elif isinstance(content, list):
                        stored = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                    if stored:
                        break
    c.close()
except Exception as ex:
    results["storage_error"] = str(ex)
BENIGN = "this-is-a-very-long-benign-file-name-segment-1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef.txt"
SECRET = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"
results["storage"] = {
  "pass": bool(stored) and (BENIGN in stored) and (SECRET not in stored),
  "stored_chars": len(stored),
  "benign_verbatim": BENIGN in stored,
  "secret_verbatim": SECRET in stored,
  "u2026_occurrences": stored.count("\u2026"),
  "stored_head": stored[:240],
}
json.dump(results, open(f"{evid}/gates.json", "w"), indent=2)
print(json.dumps(results, indent=1))
PY
GATE_RC=$?
[ $GATE_RC -eq 0 ] || { log "gates evaluator failed"; exit 20; }

# ---------------- final verify ----------------
sleep 2
log "--- final verify ---"
python3 - "$EVID" "$PORT" "$GW_PORT" "$DB" "$REDACT_JS" <<'PY'
import json, subprocess, sys
evid, port, gw, db, redact = sys.argv[1:]
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception: return "?"
rec = {
  "step": "13F-final",
  "llama_health": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/health"),
  "resolved_n_ctx": sh(f"curl -s http://127.0.0.1:{port}/props | python3 -c \"import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])\""),
  "gateway_healthz": sh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{gw}/healthz"),
  "llama_pid": sh(f"pgrep -f 'llama-server.*{port}' | head -1"),
  "gateway_pid": sh("launchctl list 2>/dev/null | awk '$3==\"ai.openclaw.gateway\" {print $1}' | head -1"),
  "step11b_sha": sh(f"shasum -a 256 {redact} | cut -c1-10"),
  "fk_violations": sh(f"python3 -c \"import sqlite3; print(len(sqlite3.connect('{db}').execute('PRAGMA foreign_key_check').fetchall()))\""),
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
required = ["smoke", "p2", "p5", "norm", "loop", "storage"]
fails = [k for k in required if not g.get(k, {}).get("pass")]
pid_stable = (p.get("llama_pid") == f.get("llama_pid") and
              p.get("gateway_pid") == f.get("gateway_pid") and
              bool(p.get("llama_pid")))
verify_ok = (f.get("llama_health") == "200" and f.get("gateway_healthz") == "200" and
             f.get("resolved_n_ctx") == "262144" and f.get("fk_violations") == "0" and
             f.get("step11b_sha") == "8baf474684")
no_drift = not p.get("config_drift_vs_13D")
summ = {
  "gates_pass": not fails,
  "failed_gates": fails,
  "pid_stable_across_window": pid_stable,
  "final_verify_ok": verify_ok,
  "no_config_drift": no_drift,
  "required_gates": required,
}
json.dump(summ, open(f"{evid}/summary.json", "w"), indent=2)
print("13F SUMMARY:", json.dumps(summ))
print("PASS" if (not fails and pid_stable and verify_ok and no_drift) else "FAIL")
sys.exit(0 if (not fails and pid_stable and verify_ok and no_drift) else 1)
PY
RC=$?
log "=== 13F driver exit $RC ==="
exit $RC
