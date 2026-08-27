import json, re, os

log_path = r"logs\azbrief_20260827_133137.log"
with open(log_path, 'r', encoding='utf-8') as f:
    text = f.read()

lines = text.splitlines()

# Parse JSON records
records = []
for line in lines:
    match = re.search(r'src\.agent\.analyzer:\s*({.*})', line)
    if match:
        try:
            records.append(json.loads(match.group(1)))
        except:
            pass

print("=== TOTAL EVENTS COUT ===")
from collections import Counter
counts = Counter([r.get("event") for r in records if r.get("event")])
for k, v in counts.items():
    print(f"  {k}: {v}")

# 1. Start and end times / elapsed
# Search for "foundry_enrichment_node_started" and "foundry_enrichment_node_completed"
start_time = None
end_time = None
total_elapsed = None
for r in records:
    ev = r.get("event")
    if ev == "foundry_enrichment_node_enabled":
        print("Foundry Node enabled:", r)
    elif ev == "analysis_completed":
        total_elapsed = r.get("elapsed_s")
        print("Analysis completed, elapsed:", total_elapsed)
    elif ev == "foundry_enrichment_node_started":
        start_time = r.get("timestamp")
    elif ev == "foundry_enrichment_node_completed":
        end_time = r.get("timestamp")
        print("Foundry enrichment completed, info:", r)

# 2. Research / impact tools and rounds
# Retained stages, claims, gaps
# Let's search for agent logs
for r in records:
    ev = r.get("event")
    if "agent" in str(ev) or "round" in str(ev) or "tool" in str(ev) or "stage" in str(ev):
        print("AGENT EVENT:", ev, {k: v for k, v in r.items() if k not in ["logger", "level", "timestamp", "trace_id"]})

# Check JSONL validity
out_jsonl = os.path.join(os.environ.get('TEMP', ''), 'azbrief-foundry-forced-smoke.jsonl')
if os.path.exists(out_jsonl):
    print("JSONL Exists at:", out_jsonl)
    with open(out_jsonl, 'r', encoding='utf-8') as f:
        jl_lines = f.readlines()
    print("JSONL Line count:", len(jl_lines))
    try:
        for jl in jl_lines:
            json.loads(jl)
        print("JSONL Validity: Valid JSONL!")
    except Exception as e:
        print("JSONL Validity: Invalid!", e)
else:
    print("JSONL does not exist at:", out_jsonl)

