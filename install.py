#!/usr/bin/env python3
"""
Install claude-context-guard hooks into ~/.claude/

Run: python install.py
"""
import os
import json
import shutil
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"

HOOK_FILES = [
    "hooks/session_start_marker.py",
    "hooks/compaction_guard_global.py",
]

NEW_HOOKS = {
    "SessionStart": [{"hooks": [{"type": "command", "command": f'python "{HOOKS_DIR / "session_start_marker.py"}"', "timeout": 5}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": f'python "{HOOKS_DIR / "compaction_guard_global.py"}"', "timeout": 10}]}],
}


def main():
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # Copy hook files
    script_dir = Path(__file__).parent
    for hook_file in HOOK_FILES:
        src = script_dir / hook_file
        dst = HOOKS_DIR / Path(hook_file).name
        shutil.copy2(src, dst)
        print(f"  Copied {hook_file} -> {dst}")

    # Merge into settings.json
    settings = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = json.load(f)

    hooks = settings.setdefault("hooks", {})

    for event, entries in NEW_HOOKS.items():
        existing = hooks.get(event, [])
        # Don't add duplicates — normalize path separators before comparing
        existing_cmds = {h["command"].replace("\\", "/").lower() for e in existing for h in e.get("hooks", [])}
        for entry in entries:
            cmd = entry["hooks"][0]["command"].replace("\\", "/").lower()
            if cmd not in existing_cmds:
                existing.append(entry)
                print(f"  Added {event} hook")
            else:
                print(f"  {event} hook already present, skipping")
        hooks[event] = existing

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print(f"\nDone. Restart Claude Code for hooks to take effect.")


if __name__ == "__main__":
    main()
