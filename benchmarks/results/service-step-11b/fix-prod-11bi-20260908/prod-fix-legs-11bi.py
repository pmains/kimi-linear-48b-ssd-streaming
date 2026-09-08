#!/usr/bin/env python3
# Step 11B(i) PRODUCTION fix validation driver for OpenClaw 2026.9.3
# (11B follow-up; owner order 2026-09-08 12:24). Runs the same six
# validated E2E legs against the production gateway (:18789) after the
# one-constant dist re-patch (redact-DMnNBHXb.mjs, 184B -> 257B pattern).
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
OUT_DIR = os.path.join(ROOT, "benchmarks/results/service-step-11b/fix-prod-11bi-20260908")
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
        ["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "readtok.py"), CFG], capture_output=True
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


import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fk_reset  # FK-safe probe cleanup (single source of truth, retained)


def reset_session(session_key):
    """FK-safe; raises RuntimeError if PRAGMA foreign_key_check is not clean."""
    fk_reset.reset_session(DB, session_key)


def stored_content(session_key):
    """Return the first stored user-message content for the session.

    2026.9.3 schema note: the user message is no longer guaranteed at
    seq=1 (session/provider/thinking events are inserted first); scan
    ascending seq and return the first event whose message has
    role='user' and a string content (the 11B storage-gate object).
    """
    con = db_conn()
    try:
        row = con.execute(
            "SELECT session_id FROM session_windows WHERE session_key=?", (session_key,)
        ).fetchone()
        if not row:
            return None
        for (ev,) in con.execute(
            "SELECT event_json FROM transcript_events WHERE session_id=? ORDER BY seq",
            (row[0],),
        ):
            obj = json.loads(ev)
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                return msg["content"]
        return None
    finally:
        con.close()


results = {}
for idx, (name, fname, kind) in enumerate(legs, start=1):
    session_key = "agent:kimi:step11bi-prod-fix-" + str(idx)
    try:
        reset_session(session_key)
    except RuntimeError as e:
        results[name] = {"ok": False, "reason": "reset-fk-violation", "detail": str(e)[:200]}
        with open(os.path.join(OUT_DIR, "fix-prod-results.json"), "w") as fh:
            json.dump({"all_ok": False, "results": results}, fh, indent=2)
        print(name, "FAIL reset-fk-violation", str(e)[:200])
        sys.exit(3)
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

    # FK gate after EVERY leg (owner order 21:05:46 req 5): any violation
    # stops all further legs immediately with evidence preserved.
    _con = db_conn()
    _viol = _con.execute("PRAGMA foreign_key_check").fetchall()
    _con.close()
    if _viol:
        reason = "fk-violation-after-leg"
        if stored is None:
            reason = "timeout-no-stored-row-with-fk-violation"
        results[name] = {"ok": False, "reason": reason,
                         "violations": [list(v) for v in _viol[:5]]}
        with open(os.path.join(OUT_DIR, "fix-prod-results.json"), "w") as fh:
            json.dump({"all_ok": False, "results": results}, fh, indent=2)
        print(name, "FAIL", reason, _viol[:3])
        sys.exit(3)

    if stored is None:
        results[name] = {"ok": False, "reason": "timeout-no-stored-row",
                         "input_len": len(raw)}
        print(name, "FAIL timeout-no-stored-row")
        continue

    stored_bytes = stored.encode("utf-8")
    if kind == "secret":
        # known-secret method (same semantics as the isolated driver that
        # PASSED): the true secret values are the maximal 40-char class runs
        # in the input. The isolated driver generated exactly 6 such values
        # and validated each as raw-absent and masked head-6 + U+2026 +
        # tail-4 present. Regex-harvesting {40} windows instead produced 6
        # phantom label-prefixed windows and false-failed this leg.
        secrets = sorted(set(
            m for m in re.findall(rb"[A-Za-z0-9/+=]+", raw) if len(m) == 40
        ))
        if len(secrets) != 6:
            results[name] = {"ok": False, "reason": "known-secret-oracle-mismatch",
                             "input_len": len(raw), "found": len(secrets)}
            print(name, "FAIL known-secret-oracle-mismatch found", len(secrets))
            continue
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

# per-leg FK gate (owner order 21:05:49): production DB must stay FK-clean
con = db_conn()
viol = con.execute("PRAGMA foreign_key_check").fetchall()
con.close()
if viol:
    results["fk_gate"] = {"ok": False, "violations": [list(v) for v in viol[:5]]}
    with open(os.path.join(OUT_DIR, "fix-prod-results.json"), "w") as fh:
        json.dump({"all_ok": False, "results": results}, fh, indent=2)
    print("FK-GATE FAIL:", viol[:5])
    sys.exit(3)

out = {"all_ok": all(r["ok"] for r in results.values()), "results": results}
with open(os.path.join(OUT_DIR, "fix-prod-results.json"), "w") as fh:
    json.dump(out, fh, indent=2)
print("SUMMARY all_ok:", out["all_ok"])
sys.exit(0 if out["all_ok"] else 1)
