#!/usr/bin/env python3
"""
Step 14D(i) -- Persistent Slot Save/Restore Characterization.

Owner-authorized 2026-09-10. Measurement / characterization ONLY.

Purpose: determine whether llama-server *inference state* can persist
independently of OpenClaw's conversation lifecycle, using llama-server's
native slot save/load mechanism and the production `--slot-save-path`
(runtime/state/slot-cache).

Architectural distinction preserved:
    the harness constructs the token sequence;
    llama-server owns the evaluated inference state.

Constraints honored (SERVICE-ROADMAP.md 14D(i)):
  - single slot (production is --parallel 1); sequential requests only
  - no multiple simultaneous inference
  - no OpenClaw compaction change; no model/sampler/context change
  - no automatic RAM<->SSD tiering; no new orchestration

Writes incremental evidence under
    benchmarks/results/service-step-14/14di/
so a reaped/interrupted run still leaves durable partial results.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import datetime
import hashlib

REPO = "/Users/pmains/Code/openclaw/kimi"
BASE = "http://127.0.0.1:18080"
SLOT_DIR = os.path.join(REPO, "runtime/state/slot-cache")
JOB = "com.openclaw.kimi-llama-server"

SMOKE = os.environ.get("SMOKE", "0") == "1"
if SMOKE:
    EVID = os.path.join(REPO, "benchmarks/results/service-step-14/14di-smoke")
    TARGETS = [("smoke", 3000)]
else:
    EVID = os.path.join(REPO, "benchmarks/results/service-step-14/14di")
    TARGETS = [("48k", 48000), ("100k", 100000), ("150k", 150000)]
DO_RESTART = os.environ.get("DO_RESTART", "1") == "1"
# Restoring a TRUNCATED/corrupt snapshot HARD-ABORTS the server (ggml_abort in
# llama_context::state_seq_load_file; observed 2026-09-10, see 14di-smoke).
# Default OFF so the characterization run cannot crash production.
DO_CRASH_TESTS = os.environ.get("DO_CRASH_TESTS", "0") == "1"
SUFFIX = "\n\n### Provenance check\nThe verification phrase for this session is SUFFIX-OK-7412. Reply with only that phrase.\n\nPhrase:"

os.makedirs(EVID, exist_ok=True)
RESULTS_PATH = os.path.join(EVID, "results.json")

RESULTS = {"step": "14D(i)", "started": None, "stages": {}, "env": {}}


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def ts():
    return datetime.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


def log(msg):
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    with open(os.path.join(EVID, "driver.log"), "a") as f:
        f.write(line + "\n")


def save_results():
    tmp = RESULTS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(RESULTS, f, indent=1)
    os.replace(tmp, RESULTS_PATH)


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except Exception as e:
        return f"<error {e}>"


def _req(method, path, body=None, timeout=7200):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.monotonic()
    http = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            http = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        http = e.code
    except Exception as e:
        return {"_error": str(e), "_wall_s": round(time.monotonic() - t0, 3), "_http": None}
    wall = round(time.monotonic() - t0, 3)
    try:
        j = json.loads(raw)
        is_dict = isinstance(j, dict)
    except Exception:
        j = {"_raw": raw.decode("utf-8", "replace")[:800]}
        is_dict = True
    if is_dict:
        j["_wall_s"] = wall
        j["_http"] = http
    return j


def get_slots():
    s = _req("GET", "/slots", timeout=30)
    return s if isinstance(s, list) else []


def slot0():
    for sl in get_slots():
        if sl.get("id") == 0:
            return sl
    return {}


def slot_summary():
    s = slot0()
    return {"n_prompt_tokens": s.get("n_prompt_tokens"),
            "cache": s.get("n_prompt_tokens_cache"),
            "processed": s.get("n_prompt_tokens_processed"),
            "is_processing": s.get("is_processing"),
            "id_task": s.get("id_task")}


def tokenize(text, timeout=600):
    r = _req("POST", "/tokenize", {"content": text}, timeout=timeout)
    return r.get("tokens", []) or []


def ntok(text):
    return len(tokenize(text))


def completion(prompt, n_predict=1, timeout=7200):
    return _req("POST", "/completions", {
        "prompt": prompt, "n_predict": n_predict, "temperature": 0.0,
        "cache_prompt": True, "stream": False,
    }, timeout=timeout)


def save_slot(fn, timeout=1800):
    return _req("POST", "/slots/0?action=save", {"filename": fn}, timeout=timeout)


def restore_slot(fn, timeout=1800):
    return _req("POST", "/slots/0?action=restore", {"filename": fn}, timeout=timeout)


def erase_slot(timeout=300):
    return _req("POST", "/slots/0?action=erase", {}, timeout=timeout)


def timings_of(resp):
    t = resp.get("timings", {}) if isinstance(resp, dict) else {}
    c = t.get("cache_n", 0) or 0
    p = t.get("prompt_n", 0) or 0
    tot = c + p
    return {
        "assembled": tot, "cache_n": c, "prompt_n": p,
        "reuse": round(c / tot, 4) if tot else None,
        "prompt_ms": t.get("prompt_ms"), "prompt_per_s": t.get("prompt_per_second"),
        "predicted_n": t.get("predicted_n"),
        "content": (resp.get("content") or "")[:200],
        "http": resp.get("_http"), "wall_s": resp.get("_wall_s"),
    }


def file_info(fn):
    p = os.path.join(SLOT_DIR, fn)
    if not os.path.exists(p):
        return {"exists": False}
    st = os.stat(p)
    with open(p, "rb") as f:
        head = f.read(64)
    return {"exists": True, "bytes": st.st_size, "path": p,
            "header_hex": head[:32].hex(), "sha256_16": hashlib.sha256(
                open(p, "rb").read()).hexdigest()[:16]}


# ---------------------------------------------------------------- corpus

WORDS = ("ALPHA BRAVO CHARLIE DELTA ECHO FOXTROT GOLF HOTEL INDIA JULIET "
         "KILO LIMA MIKE NOVEMBER OSCAR PAPA QUEBEC ROMEO SIERRA TANGO "
         "UNIFORM VICTOR WHISKEY XRAY YANKEE ZULU").split()


def corpus_line(i):
    ws = " ".join(WORDS[(i + k) % len(WORDS)] for k in range(12))
    return f"{i:06d} || {ws} || seq {i * 2654435761 % 10**12:012d} || END\n"


def build_corpus():
    meta_p = os.path.join(EVID, "corpus-meta.json")
    corpus_p = os.path.join(EVID, "corpus.txt")
    if os.path.exists(meta_p) and os.path.exists(corpus_p):
        return open(corpus_p).read(), json.load(open(meta_p))
    sample = "".join(corpus_line(i) for i in range(40))
    per_line = ntok(sample) / 40.0
    max_target = max(t for _, t in TARGETS)
    n_lines = int(max_target / per_line * 1.18) + 300
    text = "".join(corpus_line(i) for i in range(n_lines))
    total = ntok(text)
    meta = {"tokens_per_line": round(per_line, 3), "n_lines": n_lines,
            "total_tokens": total, "chars": len(text), "built": now()}
    with open(corpus_p, "w") as f:
        f.write(text)
    json.dump(meta, open(meta_p, "w"), indent=1)
    return text, meta


def prompt_for(text, meta, target):
    need = int(target / meta["tokens_per_line"]) + 1
    lines = text.splitlines(keepends=True)[:need]
    return "".join(lines)


# ---------------------------------------------------------------- env

def snapshot_env(tag):
    env = {
        "ts": now(),
        "llama_pid": sh("cat /tmp/kimi-llama-server.pid 2>/dev/null || pgrep -f 'llama-server' | head -1"),
        "health": sh("curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health"),
        "loadavg": sh("sysctl -n vm.loadavg"),
        "disk_slot_cache": sh(f"df -h {SLOT_DIR} | tail -1"),
        "slot": slot_summary(),
        "external_ssd": sh("ls /Volumes 2>/dev/null | tr '\\n' ',' "),
    }
    RESULTS["env"][tag] = env
    save_results()
    return env


def wait_idle(max_s=180):
    t0 = time.time()
    while time.time() - t0 < max_s:
        s = slot0()
        if s and not s.get("is_processing") and not s.get("n_prompt_tokens_processed"):
            return True
        time.sleep(3)
    return False


# ---------------------------------------------------------------- stages

def stage_depth(label, target, text, meta, forward=True, cold_control=False):
    """Build (or extend to) `target` tokens, then save/evict/restore (+optional forward)."""
    st = {"target": target, "ts": now()}
    prompt = prompt_for(text, meta, target)

    # 1. prefill (cold for the first depth; delta for extensions)
    pre = completion(prompt, n_predict=1)
    st["prefill"] = timings_of(pre)
    st["slot_after_prefill"] = slot_summary()
    log(f"{label}: prefill {st['prefill']['assembled']} tok "
        f"(new {st['prefill']['prompt_n']}, cache {st['prefill']['cache_n']}, "
        f"{st['prefill']['prompt_ms']} ms)")

    depth = st["slot_after_prefill"].get("n_prompt_tokens")
    st["depth"] = depth

    # 2. save
    fn = f"14di-{label}.bin"
    sv = save_slot(fn)
    st["save"] = {"http": sv.get("_http"), "wall_s": sv.get("_wall_s"),
                  "n_saved": sv.get("n_saved"), "resp": {k: v for k, v in sv.items()
                                                          if not k.startswith("_") and k != "prompt"}}
    fi = file_info(fn)
    st["snapshot"] = fi
    if fi.get("exists") and sv.get("_wall_s"):
        st["save"]["bytes_per_s"] = round(fi["bytes"] / sv["_wall_s"], 1)
        st["save"]["MiB_per_s"] = round(fi["bytes"] / sv["_wall_s"] / 1048576, 2)
    log(f"{label}: saved {fn} {fi.get('bytes')} bytes in {sv.get('_wall_s')}s")

    # 3. evict
    er = erase_slot()
    time.sleep(1)
    st["after_erase"] = slot_summary()
    st["erase"] = {"http": er.get("_http"), "wall_s": er.get("_wall_s")}

    # 4. restore
    rs = restore_slot(fn)
    st["restore"] = {"http": rs.get("_http"), "wall_s": rs.get("_wall_s"),
                     "n_restored": rs.get("n_restored")}
    st["after_restore"] = slot_summary()
    log(f"{label}: restore http={rs.get('_http')} {rs.get('_wall_s')}s "
        f"n_restored={rs.get('n_restored')} slot_depth="
        f"{st['after_restore'].get('n_prompt_tokens')}")

    # 5. suffix continuation after restore (state-continuation proof).
    #    NOTE (measured): for this hybrid/recurrent model the restore path does
    #    not carry slot.prompt.checkpoints, so the next request is forced to a
    #    full re-process regardless -- this leg MEASURES that.
    if forward:
        sfx = completion(prompt + SUFFIX, n_predict=16)
        st["suffix_after_restore"] = timings_of(sfx)
        log(f"{label}: suffix after restore -> assembled {st['suffix_after_restore']['assembled']}, "
            f"new {st['suffix_after_restore']['prompt_n']}, reuse {st['suffix_after_restore']['reuse']}, "
            f"content={st['suffix_after_restore']['content']!r}")
    else:
        log(f"{label}: forward pass skipped (size/latency-only leg); "
            f"slot_depth={st['after_restore'].get('n_prompt_tokens')}")

    # 6. correctness control (cold re-prefill of the same prefix -> same suffix)
    if cold_control:
        erase_slot()
        time.sleep(1)
        cp = completion(prompt, n_predict=1)
        st["cold_reprefill"] = timings_of(cp)
        sfx2 = completion(prompt + SUFFIX, n_predict=16)
        st["suffix_after_cold"] = timings_of(sfx2)
        same = (st["suffix_after_cold"]["content"] == st["suffix_after_restore"]["content"])
        st["correctness_match_restore_vs_cold"] = same
        # cold prefill of this depth = the comparison baseline for restore
        st["cold_prefill_ms_this_depth"] = st["cold_reprefill"]["prompt_ms"]
        st["restore_vs_cold_ratio"] = (
            round(rs.get("_wall_s", 0) * 1000 / st["cold_reprefill"]["prompt_ms"], 5)
            if st["cold_reprefill"].get("prompt_ms") else None)
        log(f"{label}: correctness restore==cold? {same}; "
            f"cold_prefill_ms={st['cold_reprefill']['prompt_ms']} "
            f"restore_ms={rs.get('_wall_s', 0) * 1000:.0f} "
            f"ratio={st['restore_vs_cold_ratio']}")

    RESULTS["stages"][label] = st
    save_results()
    return st


def stage_restart(label, text, meta, target, snapshot=None):
    """Controlled llama-server restart: state must be lost, snapshot must restore."""
    st = {"ts": now()}
    prompt = prompt_for(text, meta, target)
    fn = snapshot or f"14di-{label}.bin"

    st["pid_before"] = sh("cat /tmp/kimi-llama-server.pid 2>/dev/null")
    st["health_before"] = sh("curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health")
    st["snapshot_used"] = file_info(fn)
    uid = sh("id -u")
    t0 = time.monotonic()
    log(f"restart: kickstart -k gui/{uid}/{JOB} (pid_before={st['pid_before']})")
    sh(f"launchctl kickstart -k gui/{uid}/{JOB}")
    # wait for health to return
    healthy = False
    while time.monotonic() - t0 < 600:
        code = sh("curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health")
        if code == "200":
            healthy = True
            break
        time.sleep(5)
    st["restart_wall_s"] = round(time.monotonic() - t0, 1)
    st["health_after"] = healthy
    st["pid_after"] = sh("cat /tmp/kimi-llama-server.pid 2>/dev/null")
    st["props_after"] = _req("GET", "/props", timeout=30)
    st["slot_after_restart"] = slot_summary()
    log(f"restart: healthy={healthy} in {st['restart_wall_s']}s "
        f"pid_after={st['pid_after']} slot={st['slot_after_restart']}")

    if healthy:
        rs = restore_slot(fn)
        st["restore"] = {"http": rs.get("_http"), "wall_s": rs.get("_wall_s"),
                         "n_restored": rs.get("n_restored")}
        st["slot_after_restore"] = slot_summary()
        st["depth_restored"] = st["slot_after_restore"].get("n_prompt_tokens")
        sfx = completion(prompt + SUFFIX, n_predict=16)
        st["suffix_after_restart_restore"] = timings_of(sfx)
        log(f"restart: restore n_restored={rs.get('n_restored')} "
            f"slot_depth={st['slot_after_restore'].get('n_prompt_tokens')} "
            f"suffix={st['suffix_after_restart_restore']['content']!r}")

    RESULTS["stages"][label] = st
    save_results()
    return st


def stage_invalid(text, meta):
    """Invalid / incompatible state must be rejected, not silently restored."""
    st = {"ts": now(), "tests": {}}
    prompt = prompt_for(text, meta, TARGETS[0][1])
    completion(prompt, n_predict=1)
    good = "14di-48k.bin"
    if not os.path.exists(os.path.join(SLOT_DIR, good)):
        sv = save_slot(good)
        st["prep_save"] = {"http": sv.get("_http"), "wall_s": sv.get("_wall_s")}

    # a) nonexistent file -> should be a clean rejection
    r = restore_slot("14di-does-not-exist.bin")
    st["tests"]["nonexistent"] = {"http": r.get("_http"), "err": r.get("error") or r.get("_raw") or r}

    if not DO_CRASH_TESTS:
        st["crash_tests_skipped"] = (
            "truncated/corrupt/zero-length restore tests skipped (DO_CRASH_TESTS=0): "
            "they hard-abort the server (ggml_abort in state_seq_load_file). "
            "Evidence from the 2026-09-10 smoke run is retained in "
            "14di-smoke/crash-truncated-restore.txt and 14di-smoke/results.json.")
        st["valid_header"] = file_info(good)
        st["slot_after_invalid"] = slot_summary()
        RESULTS["stages"]["invalid"] = st
        save_results()
        return st

    # b) truncated snapshot
    trunc = "14di-truncated.bin"
    src = os.path.join(SLOT_DIR, good)
    if os.path.exists(src):
        data = open(src, "rb").read()
        with open(os.path.join(SLOT_DIR, trunc), "wb") as f:
            f.write(data[: len(data) // 2])
        r = restore_slot(trunc)
        st["tests"]["truncated"] = {"http": r.get("_http"), "err": r.get("error") or r.get("_raw") or r,
                                    "src_bytes": len(data), "trunc_bytes": len(data) // 2}
    # c) corrupted header
    corr = "14di-corrupt.bin"
    if os.path.exists(src):
        data = bytearray(open(src, "rb").read())
        for i in range(min(16, len(data))):
            data[i] ^= 0xFF
        with open(os.path.join(SLOT_DIR, corr), "wb") as f:
            f.write(bytes(data))
        r = restore_slot(corr)
        st["tests"]["corrupt_header"] = {"http": r.get("_http"), "err": r.get("error") or r.get("_raw") or r}
    # d) zero-length file
    zero = "14di-zero.bin"
    open(os.path.join(SLOT_DIR, zero), "wb").close()
    r = restore_slot(zero)
    st["tests"]["zero_length"] = {"http": r.get("_http"), "err": r.get("error") or r.get("_raw") or r}
    # e) header characterization of a valid snapshot
    st["valid_header"] = file_info(good)
    # f) slot state after failed restores
    st["slot_after_invalid"] = slot_summary()
    RESULTS["stages"]["invalid"] = st
    save_results()
    return st


def health_ok():
    return sh("curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/health") == "200"


def ensure_health(max_s=600):
    t0 = time.time()
    while time.time() - t0 < max_s:
        if health_ok():
            return True
        time.sleep(5)
    return False


def main():
    RESULTS["started"] = now()
    save_results()
    log("=== 14D(i) persistent slot save/restore characterization start ===")
    log(f"external SSD mounted? /Volumes = {sh('ls /Volumes 2>/dev/null')!r}")

    snapshot_env("preflight")
    if not ensure_health(120):
        log("ABORT: server unhealthy at start")
        return
    if not wait_idle(120):
        log("WARN: slot busy at start")

    text, meta = build_corpus()
    log(f"corpus: {meta['total_tokens']} tokens, {meta['n_lines']} lines, "
        f"{meta['tokens_per_line']} tok/line")

    # Protocol: 48k carries the FULL protocol (forward + cold control +
    # correctness); 100k/150k are size/latency-only legs to bound wall time
    # (the reuse mechanism is depth-independent and proven at 48k).
    protocol = [
        ("48k", 48000, {"forward": True, "cold_control": True}),
        ("100k", 100000, {"forward": False, "cold_control": False}),
        ("150k", 150000, {"forward": False, "cold_control": False}),
    ]
    for label, target, opts in protocol:
        if not ensure_health(300):
            log(f"ABORT before {label}: server unhealthy")
            break
        stage_depth(label, target, text, meta, **opts)

    if DO_RESTART:
        if ensure_health(300):
            stage_restart("48k", text, meta, 48000, snapshot="14di-48k.bin")

    stage_invalid(text, meta)

    snapshot_env("final")
    RESULTS["finished"] = now()
    RESULTS["llama_pid_stable"] = (
        RESULTS["env"]["preflight"]["llama_pid"] == RESULTS["env"]["final"]["llama_pid"])
    save_results()
    log("=== 14D(i) complete; results.json written ===")


if __name__ == "__main__":
    main()
