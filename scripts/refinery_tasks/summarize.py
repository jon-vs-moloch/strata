"""Summarize raw_pilot.py / run_pipeline.py JSONL output per task.
Usage: python summarize.py raw=raw.jsonl pipeline=pipe.jsonl [--ids=a,b]"""
import json, sys, collections
args = [a for a in sys.argv[1:] if not a.startswith("--ids")]
ids = None
for a in sys.argv[1:]:
    if a.startswith("--ids="): ids = set(a.split("=", 1)[1].split(","))
for spec in args:
    label, path = spec.split("=", 1)
    recs = [json.loads(l) for l in open(path) if l.strip()]
    if ids: recs = [r for r in recs if r["id"] in ids]
    by = collections.defaultdict(list)
    for r in recs: by[r["id"]].append(r)
    passed = [r.get("passed", r.get("final_passed")) for r in recs]
    tok = [int((r.get("usage") or {}).get("total_tokens") or r.get("total_tokens") or 0) for r in recs]
    wall = [float(r.get("wall_s") or 0) for r in recs]
    print(f"## {label}: {len(recs)} runs over {len(by)} tasks; pass {sum(map(bool, passed))}/{len(recs)} = {sum(map(bool, passed))/max(1,len(recs)):.1%}; "
          f"mean tokens {sum(tok)/max(1,len(tok)):.0f}; mean wall {sum(wall)/max(1,len(wall)):.0f}s")
    for i, rs in sorted(by.items()):
        cells = []
        for r in rs:
            p = r.get("passed", r.get("final_passed"))
            extra = f" any={r['any_passed']} cands={r['n_candidates']} att={len(r.get('attempts', []))} calls={r.get('model_calls')} settle={r.get('settle')}" if "attempts" in r else f" finish={r.get('finish_reason')}"
            cells.append(f"rep{r['rep']}:{'PASS' if p else 'fail'} tok={(r.get('usage') or {}).get('total_tokens') or r.get('total_tokens')} wall={r.get('wall_s')}{extra} [{(r.get('pytest') or (r.get('candidates') or [{}])[-1].get('pytest') or '')[:60]}]")
        print(f"  {i:16s} " + " | ".join(cells))
