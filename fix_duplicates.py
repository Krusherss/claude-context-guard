#!/usr/bin/env python3
import json
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"

with open(SETTINGS, encoding="utf-8") as f:
    s = json.load(f)

for event in ["SessionStart", "UserPromptSubmit"]:
    hooks = s.get("hooks", {}).get(event, [])
    seen = set()
    deduped = []
    for entry in hooks:
        cmds = tuple(h["command"] for h in entry.get("hooks", []))
        # normalize: forward slashes, lowercase
        key = tuple(c.replace("\\", "/").lower() for c in cmds)
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    s["hooks"][event] = deduped
    print(f"{event}: {len(hooks)} -> {len(deduped)} entries")

with open(SETTINGS, "w", encoding="utf-8") as f:
    json.dump(s, f, indent=2)
print("Done.")
