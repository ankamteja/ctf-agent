import json,sys
d=json.load(sys.stdin)
print(f"  {d['eval_count']/(d['eval_duration']/1e9):.1f} tok/s  ({d['eval_count']} tok)")
