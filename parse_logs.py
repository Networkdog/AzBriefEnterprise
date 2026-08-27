
import json
with open("logs/azbrief_20260827_132333.log", "r", encoding="utf-8") as f:
    text = f.read()

events = []
for line in text.split("\n"):
    if "{" in line:
        try:
            data = json.loads(line[line.find("{"):])
            events.append(data)
        except Exception:
            pass

print("=== FOUNDRY ENRICHMENT DETAILS ===")
for e in events:
    logger = e.get("logger", "")
    event = e.get("event", "")
    # Check if this info belongs to foundry backend / multi agent
    if "foundry" in logger or "foundry" in event:
        print(f"logger: {logger}, event: {event}, raw: {e}")

print("=== TOOL EVENTS ===")
for e in events:
    if "tool" in e:
        agent = e.get("agent")
        stage = e.get("stage")
        tool = e.get("tool")
        tool_round = e.get("tool_round")
        print(f"Agent: {agent}, Stage: {stage}, Tool: {tool}, Round: {tool_round}")

print("=== COUNTS ===")
tool_args_filled_count = sum(1 for e in events if e.get("event") == "tool_args_filled_from_context")
print("tool_args_filled_from_context:", tool_args_filled_count)

validation_error_count = sum(1 for e in events if "validation-error" in str(e).lower() or "validation_error" in str(e).lower())
print("validation-error:", validation_error_count)

llm_repair_count = sum(1 for e in events if "llm-repair" in str(e).lower() or "llm_repair" in str(e).lower())
print("LLM-repair:", llm_repair_count)

round_limit_count = sum(1 for e in events if "round" in str(e).lower() and ("limit" in str(e).lower() or "exceeded" in str(e).lower()))
print("round-limit:", round_limit_count)

invalid_count = sum(1 for e in events if "invalid" in str(e).lower())
print("invalid:", invalid_count)

unknown_evidence_count = sum(1 for e in events if "unknown-evidence" in str(e).lower() or "unknown_evidence" in str(e).lower())
print("unknown-evidence:", unknown_evidence_count)

review_events_count = sum(1 for e in events if e.get("agent") == "azbrief-review" or e.get("stage") == "review")
print("review events:", review_events_count)

try:
    import os
    temp_dir = os.environ.get("TEMP", "")
    jsonl_path = os.path.join(temp_dir, "azbrief-foundry-final-smoke.jsonl")
    with open(jsonl_path, "r", encoding="utf-8") as f:
        jsonl_lines = f.readlines()
        for idx, line in enumerate(jsonl_lines):
            json.loads(line)
    print("JSONL is valid, total lines:", len(jsonl_lines))
except Exception as err:
    print("JSONL invalid:", err)

