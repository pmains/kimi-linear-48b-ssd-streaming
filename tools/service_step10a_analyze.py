#!/usr/bin/env python3
"""Step 10A - analysis driver (retained, 2026-09-04).

Merges the instrumented per-turn records from the 64K and 128K real-path legs
(benchmarks/results/service-step-10a/{64k,128k}/<probe>/<probe>-r<N>.*) and
attributes wall time to phases:

  client phases (from final CLI doc + client.json):
    - total wall (client.json wall_s; doc durationMs)
    - error kind / status / livenessState / executionTrace (retries/fallback)
    - agentMeta: contextTokens (resolved precheck bound!), promptTokens,
      usage {input, output, cacheRead}
  server phases (from <label>.llama.window.log):
    - per llama task: prompt eval (prefill) ms + tokens, eval (decode) ms +
      tokens, total ms  -> number of model calls in the turn, sum prefill,
      sum decode, server total
  gateway/client handling (from <label>.gateway.window.log):
    - model-fetch start/response pairs with elapsedMs -> per-call
      client-observed dispatch->response time
  residual: wall - server total - gateway fetch total -> context
    construction/precheck + tool execution + gateway non-fetch overhead

Questions answered:
  1. Why does overflow take 582-824 s to surface? (round count x per-round
     cost decomposition; where the precheck refusal lands in the timeline)
  2. Does 128K eliminate or materially reduce the failures? (overflow
     incidence, resolved contextTokens, promptTokens at last successful call)
  3. Which phase dominates wall time? (construction/precheck vs prefill vs
     decode vs gateway/tool gaps)
  4. Is the precheck bound resolved from config (32768) or the server
     (65536/131072)? (agentMeta.contextTokens per leg)

Outputs:
  benchmarks/results/service-step-10a/analysis/analysis.json
  benchmarks/results/service-step-10a/analysis/analysis-summary.txt

Usage: python3 tools/service_step10a_analyze.py
"""
import glob
import json
import math
import os
import re
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S10A = os.environ.get("STEP10A_OUT", os.path.join(BASE, "benchmarks/results/service-step-10a"))
OUTDIR = os.path.join(S10A, "analysis")
LEGS = [x for x in os.environ.get("STEP10A_LEGS", "64k,128k").split(",") if x]
PROBES = ["P1", "P2"]
Z = 1.959963985


def wilson_ci(k, n):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / float(n)
    denom = 1.0 + Z * Z / float(n)
    centre = (p + Z * Z / (2.0 * float(n))) / denom
    half = Z * math.sqrt(p * (1.0 - p) / float(n) + Z * Z / (4.0 * float(n) * float(n))) / denom
    return (centre - half, centre + half)


def parse_llama_window(text):
    """Parse llama-server log window into per-task records."""
    tasks = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.search(r"task (\d+)", line)
        if "slot launch_slot_" in line and m:
            cur = {"task": int(m.group(1)), "prefill_ms": None, "prefill_tokens": None,
                   "decode_ms": None, "decode_tokens": None, "total_ms": None,
                   "release_tokens": None}
            tasks.append(cur)
            continue
        if "print_timing" in line and m:
            tm = re.search(r"prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if tm and cur is not None:
                cur["prefill_ms"] = float(tm.group(1))
                cur["prefill_tokens"] = int(tm.group(2))
                continue
            tm = re.search(r"eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if tm and cur is not None:
                cur["decode_ms"] = float(tm.group(1))
                cur["decode_tokens"] = int(tm.group(2))
                continue
            tm = re.search(r"total time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if tm and cur is not None:
                cur["total_ms"] = float(tm.group(1))
                continue
        if "slot release" in line and m and cur is not None:
            rm = re.search(r"n_tokens = (\d+)", line)
            if rm:
                cur["release_tokens"] = int(rm.group(1))
            cur = None
    return tasks


def parse_gateway_window(text):
    """Parse gateway log window into model-fetch records."""
    fetches = []
    cur = None
    for raw in text.splitlines():
        if "[model-fetch] start" in raw and "provider=" in raw:
            cur = {"provider": None, "model": None, "status": None, "elapsed_ms": None,
                   "ts": raw[:30]}
            m = re.search(r"provider=(\S+)", raw)
            if m:
                cur["provider"] = m.group(1)
            m = re.search(r"model=(\S+)", raw)
            if m:
                cur["model"] = m.group(1)
            fetches.append(cur)
            continue
        if "[model-fetch] response" in raw and cur is not None:
            m = re.search(r"status=(\S+)", raw)
            if m:
                cur["status"] = m.group(1)
            m = re.search(r"elapsedMs=([\d.]+)", raw)
            if m:
                cur["elapsed_ms"] = float(m.group(1))
            cur = None
    return fetches


def load_turn(leg, probe, label):
    d = os.path.join(S10A, leg, probe)
    rec_path = os.path.join(d, label + ".record.json")
    cli_path = os.path.join(d, label + ".client.json")
    llama_path = os.path.join(d, label + ".llama.window.log")
    gw_path = os.path.join(d, label + ".gateway.window.log")
    turn = {"leg": leg, "probe": probe, "label": label}
    if not os.path.exists(rec_path):
        return None
    with open(rec_path) as f:
        rec = json.load(f)
    turn["record"] = rec
    with open(cli_path) as f:
        turn["client"] = json.load(f)
    llama_tasks = parse_llama_window(open(llama_path).read() if os.path.exists(llama_path) else "")
    gw_fetches = parse_gateway_window(open(gw_path).read() if os.path.exists(gw_path) else "")
    turn["llama_tasks"] = llama_tasks
    turn["gateway_fetches"] = [g for g in gw_fetches if g.get("provider") == "llama-server"]
    # derived server-side sums
    turn["server"] = {
        "n_tasks": len(llama_tasks),
        "n_with_prefill": sum(1 for t in llama_tasks if t["prefill_ms"] is not None),
        "sum_prefill_ms": round(sum(t["prefill_ms"] for t in llama_tasks if t["prefill_ms"] is not None), 1),
        "sum_prefill_tokens": sum(t["prefill_tokens"] for t in llama_tasks if t["prefill_tokens"] is not None),
        "sum_decode_ms": round(sum(t["decode_ms"] for t in llama_tasks if t["decode_ms"] is not None), 1),
        "sum_decode_tokens": sum(t["decode_tokens"] for t in llama_tasks if t["decode_tokens"] is not None),
        "max_total_ms": max((t["total_ms"] for t in llama_tasks if t["total_ms"] is not None), default=None),
    }
    turn["server"]["sum_total_ms"] = round(sum(t["total_ms"] for t in llama_tasks if t["total_ms"] is not None), 1)
    turn["gateway"] = {
        "n_fetches": len(turn["gateway_fetches"]),
        "sum_elapsed_ms": round(sum(g["elapsed_ms"] for g in turn["gateway_fetches"] if g["elapsed_ms"] is not None), 1),
        "max_elapsed_ms": max((g["elapsed_ms"] for g in turn["gateway_fetches"] if g["elapsed_ms"] is not None), default=None),
    }
    # error classification
    rec = turn["record"]
    err = rec.get("error") or {}
    turn["error_kind"] = err.get("kind")
    turn["error_message"] = (err.get("message") or "")[:120]
    turn["doc_status"] = rec.get("doc_status")
    turn["context_tokens"] = (rec.get("agentMeta") or {}).get("contextTokens")
    turn["context_source"] = (rec.get("agentMeta") or {}).get("contextTokensSource")
    turn["prompt_tokens"] = (rec.get("agentMeta") or {}).get("promptTokens")
    turn["usage"] = (rec.get("agentMeta") or {}).get("usage")
    turn["last_call_usage"] = (rec.get("agentMeta") or {}).get("lastCallUsage")
    turn["liveness"] = rec.get("livenessState")
    return turn


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    turns = []
    for leg in LEGS:
        for probe in PROBES:
            d = os.path.join(S10A, leg, probe)
            for rp in sorted(glob.glob(os.path.join(d, probe + "-r*.client.json"))):
                label = os.path.basename(rp).replace(".client.json", "")
                t = load_turn(leg, probe, label)
                if t:
                    turns.append(t)

    out = {"legs": {}, "turns": [], "determination": {}}

    # ---------------- per-leg rollups ----------------
    for leg in LEGS:
        leg_turns = [t for t in turns if t["leg"] == leg]
        overflow = [t for t in leg_turns if t["error_kind"] == "context_overflow"]
        client_err = [t for t in leg_turns if t["client"]["rc"] != 0 and t["error_kind"] != "context_overflow"]
        ok = [t for t in leg_turns if t["client"]["rc"] == 0]
        walls = [t["client"]["wall_s"] for t in leg_turns]
        ctx_vals = [t["context_tokens"] for t in leg_turns if t["context_tokens"] is not None]
        prompt_tok_last = [t["prompt_tokens"] for t in ok if t["prompt_tokens"] is not None]
        out["legs"][leg] = {
            "n_turns": len(leg_turns),
            "n_overflow": len(overflow),
            "overflow_labels": [t["label"] for t in overflow],
            "n_client_error_other": len(client_err),
            "n_rc0": len(ok),
            "wall_s": {"min": round(min(walls), 1) if walls else None,
                       "median": round(sorted(walls)[len(walls) // 2], 1) if walls else None,
                       "max": round(max(walls), 1) if walls else None},
            "context_tokens_resolved": Counter(ctx_vals),
            "prompt_tokens_last_ok": {
                "median": round(sorted(prompt_tok_last)[len(prompt_tok_last) // 2], 1) if prompt_tok_last else None,
                "max": max(prompt_tok_last) if prompt_tok_last else None},
            "overflow_walls_s": [t["client"]["wall_s"] for t in overflow],
            "server_phases": {
                "n_tasks_median": sorted([t["server"]["n_tasks"] for t in leg_turns])[len(leg_turns) // 2] if leg_turns else None,
                "sum_prefill_ms_median": round(sorted([t["server"]["sum_prefill_ms"] for t in leg_turns])[len(leg_turns) // 2], 1) if leg_turns else None,
                "sum_decode_ms_median": round(sorted([t["server"]["sum_decode_ms"] for t in leg_turns])[len(leg_turns) // 2], 1) if leg_turns else None,
                "sum_total_ms_median": round(sorted([t["server"]["sum_total_ms"] for t in leg_turns])[len(leg_turns) // 2], 1) if leg_turns else None,
                "gateway_sum_elapsed_ms_median": round(sorted([t["gateway"]["sum_elapsed_ms"] for t in leg_turns])[len(leg_turns) // 2], 1) if leg_turns else None,
            },
        }

    # ---------------- per-turn records ----------------
    for t in turns:
        c = t["client"]
        wall = c["wall_s"]
        srv = t["server"]
        gw = t["gateway"]
        rec = t["record"]
        am = rec.get("agentMeta") or {}
        usage = am.get("usage") or {}
        row = {
            "leg": t["leg"], "probe": t["probe"], "label": t["label"],
            "rc": c["rc"], "wall_s": round(wall, 1),
            "error_kind": t["error_kind"], "doc_status": t["doc_status"],
            "context_tokens": t["context_tokens"], "context_source": t["context_source"],
            "prompt_tokens": t["prompt_tokens"],
            "usage": usage,
            "n_llama_tasks": srv["n_tasks"],
            "sum_prefill_s": round(srv["sum_prefill_ms"] / 1000.0, 1),
            "sum_decode_s": round(srv["sum_decode_ms"] / 1000.0, 1),
            "sum_server_s": round(srv["sum_total_ms"] / 1000.0, 1),
            "gateway_n_fetches": gw["n_fetches"],
            "gateway_sum_s": round(gw["sum_elapsed_ms"] / 1000.0, 1),
            "residual_s": round(max(wall - srv["sum_total_ms"] / 1000.0, 0.0), 1),
            "liveness": t["liveness"],
            "reply": (c.get("reply_chars") or 0),
        }
        out["turns"].append(row)

    # ---------------- determination ----------------
    det = {}
    for leg in LEGS:
        L = out["legs"][leg]
        det[leg] = {"overflow_incidence": str(L["n_overflow"]) + "/" + str(L["n_turns"]),
                    "context_tokens_resolved": dict(L["context_tokens_resolved"])}
    ov_walls_64 = out["legs"]["64k"]["overflow_walls_s"]
    ov_walls_128 = out["legs"].get("128k", {}).get("overflow_walls_s", [])
    notes = []
    notes.append("64k overflow walls: " + json.dumps(ov_walls_64))
    notes.append("128k overflow walls: " + json.dumps(ov_walls_128))
    notes.append("64k resolved context: " + json.dumps(det["64k"]["context_tokens_resolved"]))
    notes.append("128k resolved context: " + json.dumps(det.get("128k", {}).get("context_tokens_resolved", {})))

    decomp = []
    for t in turns:
        if t["error_kind"] == "context_overflow":
            usage = t.get("usage") or {}
            decomp.append({
                "label": t["label"], "leg": t["leg"], "wall_s": round(t["client"]["wall_s"], 1),
                "n_llama_tasks": t["server"]["n_tasks"],
                "sum_prefill_s": round(t["server"]["sum_prefill_ms"] / 1000.0, 1),
                "sum_decode_s": round(t["server"]["sum_decode_ms"] / 1000.0, 1),
                "sum_server_s": round(t["server"]["sum_total_ms"] / 1000.0, 1),
                "gateway_sum_s": round(t["gateway"]["sum_elapsed_ms"] / 1000.0, 1),
                "usage": usage,
                "context_tokens": t["context_tokens"],
            })
    out["overflow_decomposition"] = decomp

    n_ov_64 = out["legs"]["64k"]["n_overflow"]
    n_64 = out["legs"]["64k"]["n_turns"]
    _l128 = out["legs"].get("128k", {})
    n_ov_128 = _l128.get("n_overflow", 0)
    n_128 = _l128.get("n_turns", 0)

    def fisher_two_sided(a, b, c, d):
        def table_prob(x):
            return (math.comb(a + b, x) * math.comb(c + d, a + c - x)) / math.comb(a + b + c + d, a + c)
        lo = max(0, (a + c) - (c + d))
        hi = min(a + b, a + c)
        p_obs = table_prob(a)
        total = 0.0
        for x in range(lo, hi + 1):
            p = table_prob(x)
            if p <= p_obs + 1e-15:
                total += p
        return total

    p_fisher = fisher_two_sided(n_ov_64, n_64 - n_ov_64, n_ov_128, n_128 - n_ov_128) if (n_64 and n_128) else float("nan")
    notes.append("fisher overflow 64k-vs-128k p=" + str(round(p_fisher, 4)))

    ctx64 = set(out["legs"]["64k"]["context_tokens_resolved"].keys())
    ctx128 = set(out["legs"].get("128k", {}).get("context_tokens_resolved", {}).keys())
    if n_ov_128 == 0 and n_ov_64 > 0:
        verdict = "128K ELIMINATES overflow (0 failures) -> bound tracks the server window"
    elif n_ov_128 > 0 and n_ov_128 < n_ov_64:
        verdict = "128K REDUCES but does not eliminate overflow"
    elif n_ov_128 >= n_ov_64 and n_ov_64 > 0:
        verdict = "128K DOES NOT reduce overflow -> bound is config-resolved, not server ctx"
    elif n_ov_64 == 0 and n_ov_128 == 0:
        verdict = "No overflow observed in either leg (underpowered; see prompt_tokens ceiling evidence)"
    else:
        verdict = "INCONCLUSIVE"
    notes.append("overflow verdict: " + verdict)
    notes.append("resolved-context comparison: 64k=" + json.dumps(sorted(ctx64)) +
                 " 128k=" + json.dumps(sorted(ctx128)))
    if ctx64 == ctx128 and ctx64 == {32768}:
        notes.append("CONFIRMED: resolved precheck bound is 32768 in BOTH legs (config llama-server "
                     "provider contextWindow=32768), independent of server ctx")
    dominant = {}
    for leg in LEGS:
        L = out["legs"][leg]
        s = L["server_phases"]
        dom = "n/a"
        if L["n_turns"]:
            med_prefill = s["sum_prefill_ms_median"] or 0
            med_decode = s["sum_decode_ms_median"] or 0
            med_wall = L["wall_s"]["median"] or 0
            srv = (s["sum_total_ms_median"] or 0) / 1000.0
            resid = med_wall - srv
            dom = max([("prefill", med_prefill / 1000.0), ("decode", med_decode / 1000.0),
                       ("gateway/tool/residual", resid)], key=lambda x: x[1])[0]
        dominant[leg] = dom
    notes.append("dominant phase per leg: " + json.dumps(dominant))
    out["determination"] = {"notes": notes, "verdict": verdict, "dominant_phase": dominant,
                            "fisher_overflow_p": round(p_fisher, 4)}

    with open(os.path.join(OUTDIR, "analysis.json"), "w") as f:
        json.dump(out, f, indent=1)
    L = []
    L.append("===== Step 10A analysis =====")
    L.append("verdict: " + verdict)
    for n_ in notes:
        L.append("note: " + n_)
    L.append("")
    for leg in LEGS:
        L.append("----- " + leg + " leg -----")
        g = out["legs"][leg]
        L.append(" ".join(["turns=" + str(g["n_turns"]), "overflow=" + str(g["n_overflow"]),
                           "rc0=" + str(g["n_rc0"]), "walls=" + json.dumps(g["wall_s"]),
                           "ctx_resolved=" + json.dumps(g["context_tokens_resolved"]),
                           "prompt_tok_last_ok_median=" + str(g["prompt_tokens_last_ok"].get("median"))]))
    L.append("")
    L.append("----- per-turn phase attribution -----")
    L.append("\t".join(["leg", "label", "rc", "wall_s", "err", "ctx", "prompt_tok", "n_tasks",
                        "prefill_s", "decode_s", "server_s", "gw_s", "resid_s"]))
    for r in out["turns"]:
        L.append("\t".join([r["leg"], r["label"], str(r["rc"]), str(r["wall_s"]),
                            str(r["error_kind"] or ""), str(r["context_tokens"]),
                            str(r["prompt_tokens"]), str(r["n_llama_tasks"]),
                            str(r["sum_prefill_s"]), str(r["sum_decode_s"]),
                            str(r["sum_server_s"]), str(r["gateway_sum_s"]), str(r["residual_s"])]))
    L.append("")
    L.append("----- overflow decomposition (why 582-824s) -----")
    for d_ in decomp:
        L.append(json.dumps(d_))
    txt = "\n".join(L) + "\n"
    print(txt)
    with open(os.path.join(OUTDIR, "analysis-summary.txt"), "w") as f:
        f.write(txt)
    sys.stdout.write("analysis.json + analysis-summary.txt -> " + OUTDIR + "\n")


if __name__ == "__main__":
    main()
