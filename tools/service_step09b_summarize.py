#!/usr/bin/env python3
"""Aggregate Step 9B ladder + replay rep files into summary tables."""
import json, glob
from collections import Counter

rows = []
for f in sorted(glob.glob('benchmarks/results/service-step-09b-verify/ladder/*/*/rep*.json')) + \
         sorted(glob.glob('benchmarks/results/service-step-09b-verify/replay/*/*/rep*.json')):
    rows.append(json.load(open(f)))

order = ['C0', 'C1', 'C1a-D', 'C1a-T', 'C2', 'C2a-D', 'C2a-T', 'C3', 'C4']
lines = []
lines.append("=== CLASS I ladder: pass counts (x/3) and family tallies ===")
for probe in ['P1', 'P1b', 'P2', 'P3']:
    lines.append(f"----- {probe} -----")
    for cond in order:
        sub = [r for r in rows if r['cond'] == cond and r['probe'] == probe]
        if not sub:
            continue
        fam = Counter(r['family'] for r in sub)
        p = sum(1 for r in sub if r['pass'])
        n = len(sub)
        lines.append(f"  {cond:7s} pass={p}/{n}  {dict(fam)}")
lines.append("")
lines.append("=== CLASS II replays: pass counts (x/3) and family tallies ===")
for cond in ['R1', 'R2']:
    for probe in ['P4', 'P5', 'P6', 'P7']:
        sub = [r for r in rows if r['cond'] == cond and r['probe'] == probe]
        if not sub:
            continue
        fam = Counter(r['family'] for r in sub)
        p = sum(1 for r in sub if r['pass'])
        n = len(sub)
        lines.append(f"  {cond}/{probe:3s} pass={p}/{n}  {dict(fam)}")

out = '\n'.join(lines)
print(out)
with open('benchmarks/results/service-step-09b-verify/summary-tables.txt', 'w') as fh:
    fh.write(out + '\n')
