# claude-context-guard

Warn before Claude Code silently compacts your context window.

Claude Code auto-compacts at ~90% context usage — silently, without warning. You lose tool history, reasoning context, and mid-task state. By the time you notice, it's too late.

**claude-context-guard** monitors your actual token usage and warns Claude to tell you before it happens.

## Quickstart

```bash
python install.py
```

Restart Claude Code. Done.

## How it works

Two hooks:

- **`SessionStart`** — records session start timestamp per project
- **`UserPromptSubmit`** — reads the JSONL conversation file on every prompt, extracts real input token counts from Claude's API responses, fires at three levels:

| Level | Threshold | Behavior |
|-------|-----------|----------|
| NOTICE | 50% (75K tokens) | Quiet inline note |
| WARN | 65% (97.5K tokens) | Claude reminds you to update docs |
| CRITICAL | 85% (127.5K tokens) | Claude stops and asks for acknowledgment |

## Why token-based, not turn-based

Turns vary wildly — a short debugging session and a long architecture session both hit compaction at different turn counts. Token-based is accurate.

The hook reads `~/.claude/projects/<slug>/*.jsonl` and sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` from the last assistant message. This is what Claude actually sees.

### Calibration

Effective window is ~150K, not the theoretical 200K — system prompt, tool definitions, and MCP overhead consume ~40-50K. Calibrated empirically: JSONL showed 102K tokens when the UI showed 64% used → 102K / 0.64 ≈ 160K effective, rounded to 150K for safety margin.

## What Claude sees at WARN level

```
============================================================
COMPACTION WARNING — 97,676 tokens (65% of 150K)
  Tokens: 97,676/150,000 (65%)  |  Turns this session: 42
  UPDATE CLAUDE.md and PROGRESS_LOG at your next natural pause.
  Compaction is silent — don't wait until session end.
============================================================
```

## Requirements

- Python 3.x (stdlib only, no dependencies)
- Claude Code with hooks support

## Manual install

Copy `hooks/session_start_marker.py` and `hooks/compaction_guard_global.py` to `~/.claude/hooks/`, then add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "python ~/.claude/hooks/session_start_marker.py", "timeout": 5}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python ~/.claude/hooks/compaction_guard_global.py", "timeout": 10}]}
    ]
  }
}
```

## Optional: open findings tracking

If your project has a `CLAUDE.md` with a `## PENDING FINDINGS` table, the WARN/CRITICAL messages include any open findings. Works automatically — no configuration needed.
