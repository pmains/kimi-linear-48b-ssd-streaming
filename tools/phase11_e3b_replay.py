#!/usr/bin/env python3
"""Phase 11 E3B — standalone no-inference/no-Metal expert-read replay driver.

Replays the exact (offset, length) pread sequence recorded by a live
llama-expert-streamer run (KIMI_PHASE9C_TRACE capture) against the GGUF
backing file, with NO inference and NO Metal involved. This isolates the SSD
read pattern itself from any interaction/serialization with Metal execution.

Usage:
    phase11_e3b_replay.py <trace.preads> <model.gguf> [concurrency...]

Concurrency defaults to 1 2 4 8. For each concurrency the driver:
  - loads the (offset, length) sequence in trace order;
  - issues os.pread calls through a worker pool (bounded concurrency);
  - preserves ordering by assigning sequence indices to workers;
  - measures wall time, effective bandwidth, per-read latency stats,
    and the sum-of-read-times (pread_us analog).

The trace columns are: call#, il, kind, offset, bytes, us (us from the live
run, retained for comparison).
"""
import os
import statistics as st
import sys
import threading
import time

TRACE, GGUF = sys.argv[1], sys.argv[2]
CONCS = [int(x) for x in sys.argv[3:]] or [1, 2, 4, 8]


def load_trace(p):
    reads = []  # (offset, length)
    with open(p) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split(",")
            # call#, il, kind, offset, bytes, us
            reads.append((int(parts[3]), int(parts[4])))
    return reads


def replay(reads, fd, conc, out):
    n = len(reads)
    results = [None] * n
    next_idx = 0
    lock = threading.Lock()
    us_sum = 0
    us_lock = threading.Lock()

    def worker():
        nonlocal us_sum, next_idx
        while True:
            with lock:
                i = next_idx
                next_idx += 1
            if i >= n:
                return
            off, ln = reads[i]
            t0 = time.perf_counter()
            buf = os.pread(fd, ln, off)
            dt = (time.perf_counter() - t0) * 1e6
            results[i] = (off, ln, dt, len(buf))
            with us_lock:
                us_sum += dt

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(conc)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    total_B = sum(r[1] for r in reads)
    ok = sum(1 for r in results if r is not None and r[3] == r[1])
    us = [r[2] for r in results if r is not None]
    lat = sorted(us)
    r = {
        "conc": conc, "n": n, "wall_s": wall,
        "total_B": total_B, "eff_GBs": total_B / wall / 1e9,
        "pread_us_sum_s": us_sum / 1e6,
        "eff_by_sum_GBs": total_B / us_sum * 1e-3 if us_sum else 0,
        "ok": ok,
        "lat_us_mean": st.mean(us), "lat_us_median": st.median(us),
        "lat_us_p90": lat[int(0.9 * len(lat))], "lat_us_p99": lat[int(0.99 * len(lat))],
        "lat_us_max": max(us),
    }
    out.append(r)
    return r


def main():
    reads = load_trace(TRACE)
    print(f"trace: {len(reads)} reads, {sum(r[1] for r in reads)/1e9:.2f} GB total, "
          f"{len(set(r[0] for r in reads))} distinct offsets")
    fd = os.open(GGUF, os.O_RDONLY)
    results = []
    try:
        for conc in CONCS:
            print(f"--- concurrency {conc} ---", flush=True)
            r = replay(reads, fd, conc, results)
            results.append(r)
            print(f"  wall={r['wall_s']:.2f}s  eff={r['eff_GBs']:.2f}GB/s  "
                  f"sum_read_us={r['pread_us_sum_s']:.2f}s  eff_by_sum={r['eff_by_sum_GBs']:.2f}GB/s  "
                  f"ok={r['ok']}/{r['n']}")
            print(f"  lat us: mean={r['lat_us_mean']:.0f} med={r['lat_us_median']:.0f} "
                  f"p90={r['lat_us_p90']:.0f} p99={r['lat_us_p99']:.0f} max={r['lat_us_max']:.0f}")
    finally:
        os.close(fd)

    print("\n=== summary ===")
    print(f"{'conc':>4} {'wall_s':>8} {'eff_GBs':>8} {'sum_us_s':>9} {'eff_by_sum':>9} "
          f"{'mean_us':>8} {'p99_us':>7} {'max_us':>7}")
    for r in results:
        print(f"{r['conc']:>4} {r['wall_s']:8.2f} {r['eff_GBs']:8.2f} {r['pread_us_sum_s']:9.2f} "
              f"{r['eff_by_sum_GBs']:9.2f} {r['lat_us_mean']:8.0f} {r['lat_us_p99']:7.0f} {r['lat_us_max']:7.0f}")


if __name__ == "__main__":
    main()
