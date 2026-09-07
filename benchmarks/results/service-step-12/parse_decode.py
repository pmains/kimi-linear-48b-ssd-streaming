#!/usr/bin/env python3
"""Step 12 canonical parser: llama-window logs -> per-rep decode/prefill/TTFT summary.

Usage: python3 parse_decode.py [results_dir]
Reads benchmarks/results/service-step-12/<wl>/<wl>-r*.client.json plus the
matching .llama.window.log files; writes decode-summary-final.json.
"""
import json, os, re, glob, sys, statistics

def parse_llama(path):
    try:
        lines = open(path, encoding='utf-8', errors='replace').read().splitlines()
    except Exception:
        return []
    tasks, order = {}, []
    for ln in lines:
        tm = re.search(r'task (\d+) \|', ln)
        if not tm:
            continue
        tid = tm.group(1)
        if tid not in tasks:
            tasks[tid] = {'pe_ms': None, 'pe_tok': None, 'ev_ms': None, 'ev_tok': None}
            order.append(tid)
        t = tasks[tid]
        pe = re.search(r'prompt eval time =\s+([\d.]+) ms /\s*(\d+) tokens', ln)
        ev = re.search(r'\s+eval time =\s+([\d.]+) ms /\s*(\d+) tokens', ln)
        if pe: t['pe_ms'], t['pe_tok'] = float(pe.group(1)), int(pe.group(2))
        if ev: t['ev_ms'], t['ev_tok'] = float(ev.group(1)), int(ev.group(2))
    return [tasks[t] for t in order]

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'benchmarks/results/service-step-12'
    wl_map = {'short-p3': 'P3.md', 'res-r1': 'R1.md', 'eng-c1s12': 'C1-s12.md', 'long-g1': 'G1.md'}
    rows = []
    for w, pf in wl_map.items():
        for cf in sorted(glob.glob(os.path.join(base, w, w + '-r*.client.json'))):
            rec = json.load(open(cf))
            lbl = os.path.basename(cf).replace('.client.json', '')
            llama = parse_llama(os.path.join(base, w, lbl + '.llama.window.log'))
            pe_ms = sum(t['pe_ms'] or 0 for t in llama); pe_tok = sum(t['pe_tok'] or 0 for t in llama)
            ev_ms = sum(t['ev_ms'] or 0 for t in llama); ev_tok = sum(t['ev_tok'] or 0 for t in llama)
            t0 = llama[0] if llama else None
            ttft = None
            if t0 and t0['pe_ms'] is not None and t0['ev_tok']:
                ttft = round((t0['pe_ms'] + t0['ev_ms'] / t0['ev_tok']) / 1000, 2)
            rows.append({
                'workload': w, 'prompt': pf, 'rep': lbl, 'rc': rec['rc'], 'timed_out': rec['timed_out'],
                'wall_s': round(rec['wall_s'], 1), 'reply_chars': rec['reply_chars'],
                'llama_tasks': len(llama),
                'prefill_s': round(pe_ms / 1000, 1), 'prefill_tokens': pe_tok,
                'decode_s': round(ev_ms / 1000, 2), 'decode_tokens': ev_tok,
                'decode_tps': round(ev_tok / (ev_ms / 1000), 2) if ev_ms else None,
                'ttft_proxy_s': ttft,
                'decode_frac_of_wall': round(ev_ms / 1000 / rec['wall_s'], 3) if rec['wall_s'] else None,
                'prefill_frac_of_wall': round(pe_ms / 1000 / rec['wall_s'], 3) if rec['wall_s'] else None,
            })
    out = os.path.join(base, 'decode-summary-final.json')
    json.dump(rows, open(out, 'w'), indent=2)
    print('wrote', out)
    for r in rows:
        print("%-14s rc=%s tout=%s wall=%7.1fs ttft~%5.2fs prefill=%7.1fs(%6dt %3.0f%%) decode=%7.2fs(%5dt %3.0f%%) %5.2ftps reply=%dc" % (
            r['rep'], r['rc'], r['timed_out'], r['wall_s'], r['ttft_proxy_s'] or 0,
            r['prefill_s'], r['prefill_tokens'], (r['prefill_frac_of_wall'] or 0) * 100,
            r['decode_s'], r['decode_tokens'], (r['decode_frac_of_wall'] or 0) * 100,
            r['decode_tps'] or 0, r['reply_chars']))
    print('\n=== decode tps by workload (rc=0 reps) ===')
    for w in wl_map:
        v = [r['decode_tps'] for r in rows if r['workload'] == w and r['rc'] == 0 and r['decode_tps']]
        if v:
            print("%-12s n=%d %s median=%.2f" % (w, len(v), v, statistics.median(v)))

if __name__ == '__main__':
    main()
