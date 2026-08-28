#!/usr/bin/env python3
"""Phase 9E selection gate: pipelined repack feasibility estimate.

Models the 9D worker pool (FIFO job grab, W workers, per-pread latency from
the 9D .preads trace) to derive per-kind read-completion times per layer,
then pipelines the per-kind repack+placement stages on top:

    stage k may start once (a) kind k's reads are complete AND (b) the main
    thread has finished stage k-1.  Reads of kinds > k continue in flight on
    the pool threads while the main thread repacks kind k, so part of the
    repack/placement work hides behind the read tail.

Sequential (current 9D):  load = full_batch_read_wall + sum(stage_k)
                           (batch wall = max over ALL jobs, not just the
                           last kind: a straggler in an earlier kind can
                           finish after all of kind 2)
Pipelined:                load = makespan of the stage chain against R_k
                           (R_k = completion of kind k's last job)

Joining: the .preads file's first column is the GLOBAL pread sequence
number, not the decode step.  Both files are written in the same execution
order, so we walk p9c-layer.csv in file order.  Step-0 (prefill) emits
extra warm-tail preads, so the whole prefill block is skipped and each
decode moe row consumes 3 x (misses | n_slots) pread rows:
  zc path (coding/reasoning):    3 x misses  (inserted slots per layer)
  naive path (uncached control): 3 x n_slots (no cache; every slot read)
Consumption is asserted to exhaust the file exactly.

Usage:
    python3 tools/phase09e_repack_overlap.py benchmarks/results/phase-09d/ladder
"""

import csv
import json
import os
import statistics
import sys

KINDS = ("up", "gate", "down")


def load_preads(path):
    """List of (kind, us, bytes) in trace order."""
    rows = []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            p = line.strip().split(",")
            if len(p) < 6:
                continue
            try:
                kind, nbytes, us = p[2], int(p[4]), int(p[5])
            except ValueError:
                continue
            rows.append((kind, us, nbytes))
    return rows


def load_layer_rows(path):
    """All rows in file order (dicts)."""
    with open(path) as f:
        return list(csv.DictReader(f))


def join(layer_rows, preads):
    """Return (layers, consumed, total, skipped_prefill, budget_per_row)."""
    # mode detection: zc rows report misses>0; naive rows report misses==0
    zc = any(r["kind"] == "moe" and int(r["step"]) >= 1 and int(r["misses"]) > 0
             for r in layer_rows)
    budget = []
    for row in layer_rows:
        if row["kind"] != "moe" or int(row["step"]) < 1:
            continue
        n = int(row["misses"]) if zc else int(row["n_slots"])
        budget.append(3 * n)
    decode_budget = sum(budget)
    n_skip = len(preads) - decode_budget
    if n_skip < 0:
        n_skip = 0
    out = []
    cur = n_skip
    for row in layer_rows:
        if row["kind"] != "moe" or int(row["step"]) < 1:
            continue
        n = int(row["misses"]) if zc else int(row["n_slots"])
        n_jobs = 3 * n
        jobs = preads[cur:cur + n_jobs]
        cur += n_jobs
        out.append({
            "step": int(row["step"]),
            "il": int(row["il"]),
            "load_wall_us": int(row["load_wall_us"]),
            "pread_wall_us": int(row["pread_wall_us"]),
            "repack_us": int(row["repack_us"]),
            "placement_us": int(row["placement_us"]),
            "sync_us": int(row["sync_us"]),
            "misses": int(row["misses"]),
            "n_slots": int(row["n_slots"]),
            "jobs": jobs,
        })
    return out, cur, len(preads), n_skip, "zc" if zc else "naive"


def simulate(layers, workers):
    """Per-layer pipeline model. Returns dict + per-step gains."""
    gains, seq_loads, pipe_loads = [], [], []
    r0s, r1s, r2s, wall_sims, wall_sim_errs = [], [], [], [], []
    per_step = {}
    bad = 0
    for L in layers:
        groups = {}
        order = []
        for kind, us, nb in L["jobs"]:
            if kind not in KINDS:
                bad += 1
                continue
            if kind not in groups:
                groups[kind] = []
                order.append(kind)
            groups[kind].append((us, nb))
        if not order:
            continue
        busy = [0] * workers
        comp = {}
        wall_sim = 0
        for kind in order:
            for us, nb in groups[kind]:
                start = min(busy)
                finish = start + us
                busy[busy.index(start)] = finish
                comp[kind] = max(comp.get(kind, 0), finish)
                wall_sim = max(wall_sim, finish)
        R = [comp.get(k, 0) for k in KINDS]
        tot_b = sum(sum(nb for _, nb in g) for g in groups.values())
        frac = {k: (sum(nb for _, nb in groups[k]) / tot_b if tot_b else 0.0)
                for k in groups}
        stages = []
        for k in KINDS:
            f = frac.get(k, 0.0)
            stages.append(L["repack_us"] * f + L["placement_us"] * f)
        seq = wall_sim + sum(stages)
        t_cur = 0.0
        for k in range(3):
            t_cur = max(R[k], t_cur) + stages[k]
        pipe = t_cur
        gain = seq - pipe
        gains.append(gain)
        seq_loads.append(seq)
        pipe_loads.append(pipe)
        per_step[L["step"]] = per_step.get(L["step"], 0.0) + gain
        r0s.append(R[0]); r1s.append(R[1]); r2s.append(R[2])
        wall_sims.append(wall_sim)
        if L["pread_wall_us"] > 0:
            wall_sim_errs.append(wall_sim / L["pread_wall_us"])
    return {
        "layers": len(gains),
        "bad_kind_rows": bad,
        "hidden_mean_us": statistics.mean(gains) if gains else 0,
        "hidden_median_us": statistics.median(gains) if gains else 0,
        "hidden_p25_us": _pct(gains, 25),
        "hidden_p75_us": _pct(gains, 75),
        "seq_load_mean_us": statistics.mean(seq_loads) if seq_loads else 0,
        "pipe_load_mean_us": statistics.mean(pipe_loads) if pipe_loads else 0,
        "wall_sim_mean_us": statistics.mean(wall_sims) if wall_sims else 0,
        "R0_mean_us": statistics.mean(r0s) if r0s else 0,
        "R1_mean_us": statistics.mean(r1s) if r1s else 0,
        "R2_mean_us": statistics.mean(r2s) if r2s else 0,
        "wall_sim_ratio_mean": statistics.mean(wall_sim_errs) if wall_sim_errs else 0,
        "wall_sim_ratio_median": statistics.median(wall_sim_errs) if wall_sim_errs else 0,
        "gain_per_step_mean_us": statistics.mean(per_step.values()) if per_step else 0,
        "gain_per_step_median_us": statistics.median(per_step.values()) if per_step else 0,
        "gain_per_step_p75_us": _pct(list(per_step.values()), 75),
    }


def _pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def config_summary(root, config, workers):
    d = os.path.join(root, config, f"w{workers}")
    if not os.path.isdir(d):
        return None
    preads = load_preads(os.path.join(d, "p9c-layer.csv.preads"))
    layer_rows = load_layer_rows(os.path.join(d, "p9c-layer.csv"))
    layers, consumed, total, n_skip, mode = join(layer_rows, preads)
    res = simulate(layers, workers)
    res["config"] = config
    res["workers"] = workers
    res["mode"] = mode
    res["pread_consumed"] = consumed
    res["pread_total"] = total
    res["pread_skipped_prefill"] = n_skip
    res["join_exact"] = (consumed == total)
    res["pread_wall_mean_us"] = statistics.mean(
        L["pread_wall_us"] for L in layers) if layers else 0
    res["pread_wall_median_us"] = statistics.median(
        L["pread_wall_us"] for L in layers) if layers else 0
    res["repack_mean_us"] = statistics.mean(
        L["repack_us"] for L in layers) if layers else 0
    res["repack_median_us"] = statistics.median(
        L["repack_us"] for L in layers) if layers else 0
    stats_path = os.path.join(d, "stats.csv")
    step_us = []
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            for row in csv.DictReader(f):
                if row["phase"] == "decode":
                    step_us.append(int(row["total_us"]))
    res["step_us_mean"] = statistics.mean(step_us) if step_us else 0
    res["step_us_median"] = statistics.median(step_us) if step_us else 0
    return res


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/results/phase-09d/ladder"
    out = []
    for cfg in ("coding-cap4", "reasoning-cap8", "uncached"):
        for w in (2, 4, 8):
            r = config_summary(root, cfg, w)
            if r:
                out.append(r)
    print(f"{'config':<15}{'W':<3}{'mode':<7}{'n':<5}{'exact':<6}"
          f"{'gain_ms/step':<13}{'g_med':<8}{'seq_ms':<8}{'pipe_ms':<8}"
          f"{'R0':<6}{'R2':<6}{'w_sim':<8}{'w_rat':<7}{'step_ms':<9}{'gain%':<7}")
    for r in out:
        step = r["step_us_median"] or r["step_us_mean"]
        g = r["gain_per_step_mean_us"]
        gain_pct = 100 * g / step if step else 0
        print(f"{r['config']:<15}{r['workers']:<3}{r['mode']:<7}{r['layers']:<5}"
              f"{str(r['join_exact']):<6}"
              f"{g/1000:>9.2f}   {r['gain_per_step_median_us']/1000:>7.2f}  "
              f"{r['seq_load_mean_us']/1000:>7.2f}  {r['pipe_load_mean_us']/1000:>7.2f}  "
              f"{r['R0_mean_us']/1000:>5.2f} {r['R2_mean_us']/1000:>5.2f} "
              f"{r['wall_sim_mean_us']/1000:>6.2f}  "
              f"{r['wall_sim_ratio_median']:>6.2f}  {step/1000:>7.1f}  {gain_pct:>6.1f}")
    with open("benchmarks/results/phase-09e-overlap-sim.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nwrote benchmarks/results/phase-09e-overlap-sim.json")


if __name__ == "__main__":
    main()
