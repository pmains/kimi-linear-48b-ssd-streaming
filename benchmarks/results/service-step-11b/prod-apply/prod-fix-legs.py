#!/usr/bin/env python3
# Step 11B PRODUCTION fix validation driver (authorized 2026-09-06).
# Runs the SAME six validated E2E legs against the production gateway
# (:18789) after the one-line dist patch, byte-proofs from the production
# kimi transcript DB (seq=1 message.content), writes results under
# benchmarks/results/service-step-11b/fix-prod/.
# Legs (inputs byte-identical to the isolated validation):
#  1-discriminator, 2-gstyle, 3-longpath-match, 4-longpath-nomatch,
#  5-true-secret (must STILL redact), 6-long-message (44,520 B)
# Expectations: benign legs byte-identical + 0 U+2026; secret leg raw
# values absent + masked head-6 + U+2026 + tail-4 present.
# ASCII-clean source; token read at runtime, never typed or echoed.
# Exit 0 = all pass; nonzero = regression or timeout (rollback trigger).

import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

ROOT = "/Users/pmains/Code/openclaw/kimi"
MSG_DIR = os.path.join(ROOT, "benchmarks/results/service-step-11b/fix")
OUT_DIR = os.path.join(ROOT, "benchmarks/results/service-step-11b/fix-prod")
DB = "/Users/pmains/.openclaw/agents/kimi/agent/openclaw-agent.sqlite"
CLI = os.path.expanduser("~/.openclaw/tmp/agent-cli/openclaw")
CFG = "/Users/pmains/.openclaw/openclaw.json"
GATEWAY = "http://127.0.0.1:18789"
ELL = "\u2026"
POLL_SECONDS = 360

os.makedirs(OUT_DIR, exist_ok=True)

legs = [
    ("1-discriminator", "leg-1-discriminator.msg", "benign"),
    ("2-gstyle", "leg-2-gstyle.msg", "benign"),
    ("3-longpath-match", "leg-3-longpath-match.msg", "benign"),
    ("4-longpath-nomatch", "leg-4-longpath-nomatch.msg", "benign"),
    ("5-true-secret", "leg-5-true-secret.msg", "secret"),
    ("6-long-message", "leg-6-long-message.msg", "benign"),
]


def read_token():
    out = subprocess.run(
        ["python3", "/tmp/oc11b-env/readtok.py", CFG], capture_output=True
    )
    tok = out.stdout.decode().strip()
    if len(tok) < 8:
        print("FATAL token read failed", file=sys.stderr)
        sys.exit(2)
    return tok


auth_token = read_token()
ENV = dict(os.environ)
ENV.update({
    "OPENCLAW_GATEWAY_URL": GATEWAY,
    "OPENCLAW_GATEWAY_TOKEN": auth_token,
    "OPENCLAW_CONFIG_PATH": CFG,
    "OPENCLAW_HOME": "/Users/pmains/.openclaw",
    "OPENCLAW_STATE_DIR": "/Users/pmains/.openclaw",
})


def db_conn():
    return sqlite3.connect(DB, timeout=30)


def reset_session(session_key):
    con = db_conn()
    try:
        row = con.execute(
            "SELECT session_id FROM session_windows WHERE session_key=?", (session_key,)
        ).fetchone()
        if row:
            con.execute("DELETE FROM transcript_events WHERE session_id=?", (row[0],))
        con.execute("DELETE FROM session_windows WHERE session_key=?", (session_key,))
        con.commit()
    finally:
        con.close()


def stored_content(session_key):
    con = db_conn()
    try:
        row = con.execute(
            "SELECT session_id FROM session_windows WHERE session_key=?", (session_key,)
        ).fetchone()
        if not row:
            return None
        ev = con.execute(
            "SELECT event_json FROM transcript_events WHERE session_id=? AND seq=1",
            (row[0],),
        ).fetchone()
        if not ev:
            return None
        obj = json.loads(ev[0])
        return obj["message"]["content"]
    finally:
        con.close()


results = {}
for idx, (name, fname, kind) in enumerate(legs, start=1):
    session_key = "agent:kimi:step11b-prod-fix-" + str(idx)
    reset_session(session_key)
    src = os.path.join(MSG_DIR, fname)
    raw = pathlib.Path(src).read_bytes()
    # record a byte copy of the input in the prod evidence dir
    pathlib.Path(os.path.join(OUT_DIR, fname)).write_bytes(raw)
    outpath = os.path.join(OUT_DIR, "leg-" + name + ".out")
    errpath = os.path.join(OUT_DIR, "leg-" + name + ".err")
    with open(outpath, "wb") as fo, open(errpath, "wb") as fe:
        proc = subprocess.Popen(
            [CLI, "agent", "--agent", "kimi", "--session-key", session_key,
             "--message-file", src],
            stdout=fo, stderr=fe, env=ENV,
        )
    stored = None
    deadline = time.time() + POLL_SECONDS
    while time.time() < deadline:
        stored = stored_content(session_key)
        if stored is not None:
            break
        time.sleep(3)
    if stored is None:
        time.sleep(5)
        stored = stored_content(session_key)
    try:
        proc.kill()
    except Exception:
        pass
    proc.wait(timeout=5)

    if stored is None:
        results[name] = {"ok": False, "reason": "timeout-no-stored-row",
                         "input_len": len(raw)}
        print(name, "FAIL timeout-no-stored-row")
        continue

    stored_bytes = stored.encode("utf-8")
    if kind == "secret":
        # extract the 6 secret values from the input message itself
        secrets = set(re.findall(rb"[A-Za-z0-9/+=]{40}", raw))
        raw_absent = all(s.decode() not in stored for s in secrets)
        masked_present = all(
            (s[:6].decode() + ELL + s[-4:].decode()) in stored for s in secrets
        )
        ok = raw_absent and masked_present
        results[name] = {
            "ok": ok, "raw_absent": raw_absent, "masked_present": masked_present,
            "u2026_count": stored.count(ELL), "secrets": len(secrets),
        }
        print(name, "OK" if ok else "FAIL",
              "| raw_absent:", raw_absent, "| masked_present:", masked_present,
              "| u2026 count:", stored.count(ELL), "| secrets:", len(secrets))
    else:
        byte_identical = stored_bytes == raw
        u2026_zero = stored.count(ELL) == 0
        ok = byte_identical and u2026_zero
        results[name] = {
            "ok": ok, "byte_identical": byte_identical, "u2026_zero": u2026_zero,
            "stored_len": len(stored_bytes), "input_len": len(raw),
        }
        print(name, "OK" if ok else "FAIL",
              "| byte_identical:", byte_identical, "| u2026_zero:", u2026_zero,
              "| stored_len:", len(stored_bytes), "| input_len:", len(raw))

out = {"all_ok": all(r["ok"] for r in results.values()), "results": results}
with open(os.path.join(OUT_DIR, "fix-prod-results.json"), "w") as fh:
    json.dump(out, fh, indent=2)
print("SUMMARY all_ok:", out["all_ok"])
sys.exit(0 if out["all_ok"] else 1)
