#!/usr/bin/env python3
"""
PreToolUse hook (GLOBAL): FOUR-TIER enforcement + STRUCTURE + SNAPSHOTS

GLOBAL DEPLOYMENT: Runs in all projects, maintains per-project state
  - Counter: .claude/hooks/change_counter.txt (in each project)
  - Notepad: .omc/notepad.md (in each project)
  - Snapshots: .claude/notepad_archive/ (in each project)
  - Flag: .claude/hooks/disable_enforcement.flag (per-project kill switch)

FOUR-TIER SYSTEM (Session 290, Defense-in-Depth fix - cumulative protection):
  Op 7-13:  MINI checkpoint - structure + notepad fresh (Defense in Depth, NIST SP 800-172)
  Op 14-27: LIGHT checkpoint - structure + notepad fresh + git clean
  Op 28+:   HEAVY checkpoint - structure + notepad + CLAUDE.md + git (resets to 0)

  Each tier provides COMPLETE protection (no gaps). Snapshots saved at ops 7, 14, 21, 28.

MCP GRACE PERIOD (Session 293): Fixes mtime bug in MINI/LIGHT/HEAVY checkpoints
  - Problem: MCP tools write .omc/notepad.md but don't update modification time
  - Result: is_notepad_fresh() returns False → false blocks in MINI checkpoint (ops 7-13)
  - Solution: Track MCP writes, skip mtime check for 3 operations after MCP write
  - File: .claude/hooks/last_mcp_write.json (auto-expires, fail-open on corruption)
  - Prevents: False blocks in all projects using MCP notepad tools

STRUCTURE VALIDATION (Session 290): Required notepad sections
  - Done: [what just finished]
  - Next: [immediate next step]
  - Blocked: [blockers] or NONE

SNAPSHOT ARCHIVING (Session 290): Auto-save at checkpoints
  - Saves: .omc/notepad.md → .claude/notepad_archive/session_X_opN_YYYY-MM-DD_HHMM.md
  - Timing: BEFORE checkpoint enforcement (crash-safe)
  - Cleanup: Max 20 snapshots, delete oldest 10 when over limit
  - Research-backed: Industry standard log rotation patterns

QUALITY ENFORCEMENT (Session 290): Hybrid model (industry best practice)
  BLOCKS (exit 2) - Critical issues:
    - Empty notepad (<20 chars)
    - No structure (missing all section headers)
    - All generic phrases (vague content)
  WARNS (stderr) - Suggestions:
    - Short content (20-50 chars)
    - Missing status markers (✅/🔍/⏸️)
    - Missing "what's next" section
    - Some vague language

BLOCKS when:
  - ANY TIME: notepad quality critical-bad (see above)
  - Op 7/21: structure invalid (missing Done/Next/Blocked)
  - Op 14: structure invalid OR notepad stale (>10 min) OR git dirty
  - Op 28: structure invalid OR notepad stale OR CLAUDE.md stale (>30 min) OR git dirty

BYPASSES (prevent deadlock):
  1. Write/Edit to .omc/notepad.md - always allow
  2. Write/Edit to CLAUDE.md - always allow (with path traversal prevention)
  3. Write to .claude/notepad_archive/* - always allow (Session 290 - critical!)
  4. Git commands (Bash starting with "git") - always allow
  5. Read-only Bash (ls, cat, grep, git status/diff) - always allow
  6. Read/Grep/Glob tools - not affected by this hook

ESCAPE HATCHES:
  1. Delete this file: ~/.claude/hooks/change_enforcement.py
  2. Edit ~/.claude/settings.json (remove hook entry)
  3. Delete counter: .claude/hooks/change_counter.txt (in project)
  4. Create disable flag: .claude/hooks/disable_enforcement.flag (in project)
  5. Hook fails open (exit 0) on ANY error

PLATFORM BUGS TESTED:
  - PreToolUse Edit/Write blocking: WORKS (tested Session 290)
  - PreToolUse Agent blocking: NOT APPLICABLE (platform bug #26923)

PROCESS BOUNDARY TESTING (hook-creator v2.0 Step 5D):
  - Subagent isolation: TESTED (2026-04-01) - test_change_enforcement_subagent.py
  - File handle inheritance: PREVENTED (uses 'with' statements, msvcrt file locking)
  - PID-based counters: Each process gets own counter file (change_counter_{PID}.txt)
  - Session 292 bug: Retroactively validated - 3/3 tests pass

Session 290 (2026-03-29): Created global version from project hook
  - Converted from project to global deployment
  - Per-project state maintained (counter + notepad)
  - Project root detection via cwd
  - Hook-creator protocol: Steps 0-8 complete

Session 290 (2026-03-30): Added quality enforcement (hook-creator modification)
  - Hybrid enforcement: block critical, warn suggestions
  - Industry best practice (git pre-commit hooks, AI assistants)
  - Test harness: 10 tests

Session 290 (2026-03-30): Added auto-setup (prevents circular dependencies)
  - First run: auto-adds .claude/, *.log, *.checkpoint.json, .omc/* to .gitignore
  - Auto-adds !.omc/notepad.md to enable git tracking for hook verification
  - Replaces old .omc/ with .omc/* + !.omc/notepad.md if found
  - Prevents hook state files from making git dirty
  - Warns if no git repo (but doesn't block)
  - Self-sufficient deployment

Session 290 (2026-03-31): Adjusted thresholds to 7/14/21/28
  - Research-backed: AI agent checkpointing every 3-5 tool calls (Fast.io)
  - Target: 4-6 checkpoints per session before context fills
  - Prevents progress loss on unexpected session end
  - User feedback: Resume chat lost progress before hitting old 20/40 thresholds

Session 290 (2026-03-30): Added structure validation + snapshot archiving
  - Hook-creator protocol: Steps 0-8 complete (full 8-step safety process)
  - Structure validation: Enforces Done:/Next:/Blocked: sections at all checkpoints
  - Snapshot archiving: Auto-saves notepad at ops 10/20/30/40 to .claude/notepad_archive/
  - Cleanup: Max 20 snapshots, FIFO deletion (delete oldest 10 when hit 21)
  - Bypass 4 added: .claude/notepad_archive/* writes don't increment counter
  - Test harness: test_snapshot_structure.py (11 tests)
  - Research-backed: Checkpoint timing, SPE pattern, log rotation
  - Implementation: ~200 lines added, hook now 1050+ lines total

Last tested: 2026-03-30
"""
import sys
import json
import io
import os
import subprocess
import time
import re
import uuid
from pathlib import Path

# Session 293: Import shared notepad validation (phrase-specific suggestions)
try:
    from notepad_validation import detect_generic_phrases, get_concrete_suggestion
except ImportError:
    # Fallback if module not found (shouldn't happen but fail-open)
    detect_generic_phrases = None
    get_concrete_suggestion = None

# UTF-8 output (prevents encoding errors)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Constants - Four-Tier Thresholds (Session 290: Research-backed 3-5 tool calls)
MINI_CHECKPOINT_1 = 7   # Mini checkpoint: structure validation + snapshot
LIGHT_THRESHOLD = 14    # Light checkpoint: structure + notepad + git + snapshot
MINI_CHECKPOINT_2 = 21  # Mini checkpoint: structure validation + snapshot
HEAVY_THRESHOLD = 28    # Heavy checkpoint: structure + notepad + CLAUDE.md + git + snapshot
NOTEPAD_STALE_SECONDS = 600   # 10 minutes
CLAUDEMD_STALE_SECONDS = 1800 # 30 minutes
MAX_SNAPSHOTS = 20      # Max snapshot archives to keep (Session 290)
READ_ONLY_COMMANDS = ['ls', 'cat', 'grep', 'git status', 'git diff', 'git log', 'pwd']
# Note: 'echo' removed - echo with redirection (>, >>) writes files and should count

# Session 293: Timing instrumentation paths (passive logging for empirical analysis)
TIMING_STATE_FILE = os.path.expanduser("~/.claude/hooks/checkpoint_timing_state.json")
TIMING_LOG_FILE = os.path.expanduser("~/.claude/hooks/checkpoint_timing.log")

# Wisdom checkpoint integration (Session XXX): Pattern capture prompting
WISDOM_STATE_FILE = Path.home() / ".claude" / ".wisdom_checkpoint_state"


def increment_wisdom_checkpoint():
    """Increment wisdom checkpoint counter after heavy checkpoint reset.

    Returns:
        int: New checkpoint count (1, 2, 3, ...)
    """
    # Session 293 Bug Fix: Prevent state corruption on exceptions
    # Old behavior: exception → return 0 → corrupts state (1→0)
    # New behavior: calculate increment first, return it even if write fails

    checkpoint_count = 1  # Default for first run
    last_prompted = 0

    try:
        # Read current state (checkpoint_count, last_prompted)
        if WISDOM_STATE_FILE.exists():
            lines = WISDOM_STATE_FILE.read_text().strip().split('\n')
            checkpoint_count = int(lines[0]) + 1  # Increment immediately
            last_prompted = int(lines[1]) if len(lines) > 1 else 0
        # else: keep defaults (1, 0)
    except Exception:
        # Fail open - if read fails, treat as first run
        checkpoint_count = 1
        last_prompted = 0

    # Try to save state, but don't fail if write errors
    try:
        WISDOM_STATE_FILE.write_text(f"{checkpoint_count}\n{last_prompted}")
    except Exception:
        pass  # Write failed, but we still return the incremented value

    return checkpoint_count


def should_display_wisdom_prompt():
    """Check if we should display wisdom prompt (every 2 heavy checkpoints).

    Returns:
        bool: True if checkpoint_count is even and > 0
    """
    try:
        if not WISDOM_STATE_FILE.exists():
            return False

        lines = WISDOM_STATE_FILE.read_text().strip().split('\n')
        checkpoint_count = int(lines[0])

        # Prompt every 2 heavy checkpoints (2, 4, 6, ...)
        return checkpoint_count > 0 and checkpoint_count % 2 == 0
    except Exception:
        # Fail open
        return False


def display_wisdom_prompt():
    """Display wisdom prompt to stderr and reset checkpoint counter."""
    try:
        # Read current state
        if WISDOM_STATE_FILE.exists():
            lines = WISDOM_STATE_FILE.read_text().strip().split('\n')
            checkpoint_count = int(lines[0])
        else:
            checkpoint_count = 0

        # Display prompt
        print("\n" + "="*70, file=sys.stderr, flush=True)
        print("🧠 WISDOM CHECKPOINT - Pattern Capture Opportunity", file=sys.stderr, flush=True)
        print("="*70, file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)
        print(f"You've completed {checkpoint_count} heavy checkpoints (56+ operations).", file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)
        print("Have you discovered any reusable coding patterns worth capturing?", file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)
        print("Examples:", file=sys.stderr, flush=True)
        print("  • Architectural decisions that worked well", file=sys.stderr, flush=True)
        print("  • Bug fixes that revealed general principles", file=sys.stderr, flush=True)
        print("  • Performance optimizations applicable elsewhere", file=sys.stderr, flush=True)
        print("  • API integration patterns", file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)
        print("To capture: Use /capture-win skill", file=sys.stderr, flush=True)
        print("To skip: Just continue - this is advisory only", file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)
        print("="*70, file=sys.stderr, flush=True)
        print("", file=sys.stderr, flush=True)

        # Reset checkpoint_count to 0, save last_prompted value
        WISDOM_STATE_FILE.write_text(f"0\n{checkpoint_count}")

    except Exception:
        # Fail open - if prompt fails, just continue silently
        pass


def get_project_root():
    """Get project root by finding git repository root

    Walks up from current directory to find .git/, ensuring we always
    find the correct project root regardless of current working directory.
    Falls back to os.getcwd() if no git repo found.
    """
    current = os.getcwd()

    # Walk up directory tree looking for .git/
    while True:
        git_dir = os.path.join(current, '.git')
        if os.path.isdir(git_dir) or os.path.isfile(git_dir):  # .git can be a file in worktrees
            return current

        parent = os.path.dirname(current)
        if parent == current:  # Reached root of filesystem
            break
        current = parent

    # No git repo found - fall back to cwd
    return os.getcwd()

def get_session_identifier():
    """Get or create session identifier for this Claude session

    Uses session lock file approach:
    - Reads existing session lock if fresh (<2 hours)
    - Creates new session ID if no lock or stale
    - Prevents PID fragmentation (Session 293 bug fix)
    - SUBAGENT ISOLATION (Session 2026-04-08): Detects subagent context and returns
      isolated session ID to prevent counter sharing between parent and subagents

    Returns: session_id (str) - short UUID for this session, with optional subagent suffix
    """
    project_root = get_project_root()
    lock_file = os.path.join(project_root, ".claude", "hooks", "session.lock")

    # SUBAGENT DETECTION: Multi-tier approach for reliable isolation
    # Prevents subagents from inheriting parent's change counter
    subagent_suffix = None
    try:
        # TIER 1: Check for explicit environment variable (future-proof, cross-platform)
        agent_id = os.environ.get('CLAUDE_AGENT_ID')
        if agent_id:
            subagent_suffix = f"_agent_{agent_id[:8]}"

        # TIER 2: Check for task output path environment variable
        # Agent tool may set this to indicate subagent context
        elif os.environ.get('CLAUDE_TASK_OUTPUT'):
            output_path = os.environ.get('CLAUDE_TASK_OUTPUT', '')
            # Extract agent ID from filename (pattern: {agent_id}.output)
            basename = os.path.basename(output_path)
            if '.output' in basename:
                extracted_id = basename.replace('.output', '')
                if extracted_id:
                    subagent_suffix = f"_agent_{extracted_id[:8]}"

        # TIER 3 REMOVED (Session 293 debug-code fix):
        # PID-based detection was too aggressive - treated every subprocess
        # (Write/Edit/Bash) as a subagent, fragmenting counter into files all at "1".
        # This prevented checkpoints (7/14/21/28) from ever firing.
        # Now only TIER 1 & 2 (explicit Agent env vars) trigger isolation.
    except Exception:
        pass  # Fail open - if detection fails, proceed as normal session

    try:
        # Try to read existing lock
        if os.path.exists(lock_file):
            with open(lock_file, 'r') as f:
                data = json.load(f)
                # Check if lock is stale (>2 hours = 7200 seconds)
                age = time.time() - data.get('timestamp', 0)
                if age < 7200:
                    base_session = data['session_id']
                    # If subagent, append isolation suffix
                    return base_session + (subagent_suffix or '')
    except (json.JSONDecodeError, IOError, KeyError):
        pass  # Corrupted lock, create new session

    # Create new session
    session_id = str(uuid.uuid4())[:8]  # Short ID (8 chars)

    # Session 293: Counter migration when lock expires
    # Prevents counter regression (14→2→3) when 2-hour lock expires
    # Session 8 fix: only migrate from valid 8-char hex session files;
    # delete old files after migration to prevent 370+ file accumulation.
    try:
        import glob
        import re as _re
        _VALID_SESSION_FILE = _re.compile(r'^change_counter_([0-9a-f]{8})\.txt$')
        counter_dir = os.path.join(project_root, ".claude", "hooks")
        counter_pattern = os.path.join(counter_dir, "change_counter_*.txt")
        all_counters = glob.glob(counter_pattern)
        # Only consider files with valid 8-char hex session IDs (not PID files or _proc_ files)
        existing_counters = [
            f for f in all_counters
            if _VALID_SESSION_FILE.match(os.path.basename(f))
        ]

        if existing_counters:
            # Always start new sessions at 0 — never migrate old values.
            # Migrating caused stale counter files from parallel/background runs
            # to poison new sessions (counter starts at 14, 99, etc. immediately).
            new_counter_path = os.path.join(counter_dir, f"change_counter_{session_id}.txt")
            os.makedirs(counter_dir, exist_ok=True)
            with open(new_counter_path, 'w') as f:
                f.write("0")

            # Cleanup: delete ALL old counter files (valid sessions + PID/proc files)
            # to prevent accumulation of stale files from background subagents
            for old_file in all_counters:
                if old_file != new_counter_path:
                    try:
                        os.remove(old_file)
                    except OSError:
                        pass
    except Exception:
        pass  # Fail open - if migration fails, start at 0

    try:
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, 'w') as f:
            json.dump({
                'session_id': session_id,
                'timestamp': time.time(),
                'project_root': project_root,
                'pid': os.getpid()  # Record PID for Tier 3 subagent detection
            }, f)
    except IOError:
        pass  # Fail open - return session ID anyway

    # Return session ID with subagent suffix if applicable
    return session_id + (subagent_suffix or '')

def get_counter_path():
    """Get path to counter file (relative to project root)

    Uses session ID for persistence across tool uses while maintaining
    isolation between concurrent sessions (Session 293 bug fix).

    Replaced PID-based approach which fragmented into 69 counters all at "1".
    """
    project_root = get_project_root()
    session_id = get_session_identifier()
    return os.path.join(project_root, ".claude", "hooks", f"change_counter_{session_id}.txt")

def get_disable_flag_path():
    """Get path to disable flag (relative to project root)"""
    project_root = get_project_root()
    return os.path.join(project_root, ".claude", "hooks", "disable_enforcement.flag")

def get_checkpoint_state_path():
    """Get path to checkpoint state file (tracks active checkpoint)"""
    project_root = get_project_root()
    return os.path.join(project_root, ".claude", "hooks", "checkpoint_state.json")

def read_checkpoint_state():
    """Read checkpoint state - returns dict with 'active', 'type', 'count'"""
    try:
        state_path = get_checkpoint_state_path()
        if not os.path.exists(state_path):
            return {"active": False, "type": None, "count": 0}
        with open(state_path, 'r') as f:
            return json.load(f)
    except (ValueError, IOError):
        return {"active": False, "type": None, "count": 0}

def write_checkpoint_state(active, checkpoint_type, count):
    """Write checkpoint state"""
    try:
        state_path = get_checkpoint_state_path()
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, 'w') as f:
            json.dump({"active": active, "type": checkpoint_type, "count": count}, f)
    except IOError as e:
        print(f"⚠️  Warning: Can't update checkpoint state: {e}", file=sys.stderr)

def clear_checkpoint_state():
    """Clear checkpoint state (satisfied)"""
    try:
        state_path = get_checkpoint_state_path()
        if os.path.exists(state_path):
            os.remove(state_path)
    except IOError:
        pass  # Ignore errors

def get_grace_period_path():
    """Get path to MCP grace period tracking file"""
    project_root = get_project_root()
    return os.path.join(project_root, ".claude", "hooks", "last_mcp_write.json")

def create_grace_period(counter):
    """Create grace period after MCP write (expires after 3 operations)

    Args:
        counter: Current operation counter

    Grace period allows 3 operations after MCP notepad write without checking mtime.
    This prevents false blocks when MCP tools write content but don't update mtime.
    """
    try:
        grace_path = get_grace_period_path()
        os.makedirs(os.path.dirname(grace_path), exist_ok=True)

        grace_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "counter": counter,
            "expires_at_op": counter + 3  # 3 operations grace period
        }

        with open(grace_path, 'w') as f:
            json.dump(grace_data, f)

    except IOError as e:
        # Fail open - don't block if can't create grace period
        print(f"⚠️  Warning: Can't create grace period: {e}", file=sys.stderr)

def check_grace_period(counter):
    """Check if within MCP write grace period

    Args:
        counter: Current operation counter

    Returns:
        True if within grace period (skip mtime check), False otherwise
    """
    try:
        grace_path = get_grace_period_path()
        if not os.path.exists(grace_path):
            return False

        with open(grace_path, 'r') as f:
            grace_data = json.load(f)

        expires_at = grace_data.get("expires_at_op", 0)

        # Check if still within grace period
        if counter <= expires_at:
            return True
        else:
            # Grace period expired - clean up
            try:
                os.remove(grace_path)
            except:
                pass
            return False

    except (ValueError, IOError, KeyError):
        # Fail open - if grace period file corrupted, return False (check mtime normally)
        return False

def clear_grace_period():
    """Clear grace period file"""
    try:
        grace_path = get_grace_period_path()
        if os.path.exists(grace_path):
            os.remove(grace_path)
    except IOError:
        pass  # Ignore errors

def log_checkpoint_timing(checkpoint_type, operation_count):
    """Log checkpoint timing data for empirical analysis (Session 293)

    Passive logging only - records time between checkpoints to understand
    empirical timing patterns. Does not affect hook behavior.

    Args:
        checkpoint_type: One of "MINI_1", "LIGHT", "MINI_2", "HEAVY"
        operation_count: Current operation counter value

    Log format: ISO timestamp, checkpoint_type, operation_count, delta_seconds
    State file: Last checkpoint timestamp for delta calculation

    Fails open - all errors ignored to prevent blocking operations.
    """
    try:
        current_time = time.time()
        current_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # Read previous checkpoint time
        delta_seconds = None
        if os.path.exists(TIMING_STATE_FILE):
            try:
                with open(TIMING_STATE_FILE, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    prev_time = state.get('last_checkpoint_time')
                    if prev_time:
                        delta_seconds = current_time - prev_time
            except (IOError, json.JSONDecodeError, ValueError):
                pass  # No previous data, start fresh

        # Write current timestamp to state
        try:
            with open(TIMING_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'last_checkpoint_time': current_time,
                    'last_checkpoint_type': checkpoint_type,
                    'last_operation_count': operation_count
                }, f)
        except IOError:
            pass  # State write failed, but don't block

        # Append to log file
        try:
            log_entry = f"{current_timestamp} | {checkpoint_type:8s} | ops={operation_count:3d} | delta={delta_seconds:.1f}s\n" if delta_seconds else f"{current_timestamp} | {checkpoint_type:8s} | ops={operation_count:3d} | delta=N/A (first)\n"

            with open(TIMING_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except IOError:
            pass  # Log write failed, but don't block

    except Exception:
        # Fail open - any error should not affect hook behavior
        pass

def ensure_project_setup():
    """Ensure project has necessary setup (gitignore, git repo, notepad)

    Called on first run in a project (when counter doesn't exist).
    Prevents circular dependencies and hook failures.
    Creates .omc/notepad.md if missing to enable checkpoint system.
    """
    try:
        project_root = get_project_root()

        # Check if git repo exists
        git_dir = os.path.join(project_root, ".git")
        if not os.path.exists(git_dir):
            print("⚠️  No git repo found. Hook requires git for checkpoints.", file=sys.stderr, flush=True)
            print("   Run: git init", file=sys.stderr, flush=True)
            print("   (Hook will allow operations but git checkpoint will fail)", file=sys.stderr, flush=True)

        # Ensure .omc/ directory exists
        omc_dir = os.path.join(project_root, ".omc")
        if not os.path.exists(omc_dir):
            os.makedirs(omc_dir, exist_ok=True)
            print(f"✓ Created .omc/ directory", file=sys.stderr, flush=True)

        # Ensure .omc/notepad.md exists (NEVER overwrite)
        notepad_path = os.path.join(omc_dir, "notepad.md")
        if not os.path.exists(notepad_path):
            with open(notepad_path, 'w', encoding='utf-8') as f:
                f.write("""# Session Notepad

## Current Work
- Project initialized

## Status
⏸️ Ready to start

## Next Steps
- Begin work
""")
            print(f"✓ Created .omc/notepad.md (enables checkpoint system)", file=sys.stderr, flush=True)

        # Ensure .gitignore exists and has necessary patterns
        gitignore_path = os.path.join(project_root, ".gitignore")

        # Patterns that MUST be ignored to prevent circular dependencies
        required_patterns = [
            ".claude/",           # Hook state files (counter, checkpoint, flags)
            "*.log",              # Runtime logs
            "*.checkpoint.json",  # Process checkpoints
            "*.pid",              # Process IDs
        ]

        # Read existing gitignore
        existing_lines = []
        if os.path.exists(gitignore_path):
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                existing_lines = [line.rstrip() for line in f.readlines()]

        # Check which patterns are missing
        missing_patterns = []
        for pattern in required_patterns:
            # Check if pattern exists (exact match or as comment)
            found = any(pattern in line for line in existing_lines)
            if not found:
                missing_patterns.append(pattern)

        # Special handling for .omc/ → .omc/* + !.omc/notepad.md (Session 290)
        # This allows git to track notepad.md while ignoring other .omc files
        needs_omc_fix = False
        has_omc_wildcard = any(".omc/*" in line for line in existing_lines)
        has_omc_negation = any("!.omc/notepad.md" in line for line in existing_lines)
        has_old_omc = any(line.strip() == ".omc/" for line in existing_lines)

        if has_old_omc and not has_omc_wildcard:
            # Replace .omc/ with .omc/* (in-place edit)
            with open(gitignore_path, 'w', encoding='utf-8') as f:
                for line in existing_lines:
                    if line.strip() == ".omc/":
                        f.write(".omc/*\n")
                        if not has_omc_negation:
                            f.write("!.omc/notepad.md\n")
                        print(f"✓ Updated .omc/ → .omc/* in .gitignore", file=sys.stderr, flush=True)
                        needs_omc_fix = True
                    else:
                        f.write(line + "\n")
        elif not has_omc_wildcard:
            # Add .omc/* and !.omc/notepad.md as new patterns
            missing_patterns.extend([".omc/*", "!.omc/notepad.md"])

        # Add missing patterns
        if missing_patterns:
            with open(gitignore_path, 'a', encoding='utf-8') as f:
                if existing_lines and existing_lines[-1].strip():
                    f.write("\n")  # Add blank line if file doesn't end with one
                f.write("\n# Added by change_enforcement hook (prevents circular dependencies)\n")
                for pattern in missing_patterns:
                    f.write(f"{pattern}\n")

            print(f"✓ Added {len(missing_patterns)} patterns to .gitignore", file=sys.stderr, flush=True)
            print(f"  Patterns: {', '.join(missing_patterns)}", file=sys.stderr, flush=True)

        if needs_omc_fix and not missing_patterns:
            print(f"✓ Updated .gitignore for notepad tracking", file=sys.stderr, flush=True)

    except Exception as e:
        # Fail open - don't block if setup fails
        print(f"⚠️  Project setup warning (non-blocking): {e}", file=sys.stderr, flush=True)

def read_counter():
    """Read current counter value, return 0 if doesn't exist or invalid"""
    try:
        counter_path = get_counter_path()
        if not os.path.exists(counter_path):
            return 0
        with open(counter_path, 'r') as f:
            value = int(f.read().strip())
            # Sanity check
            if value < 0:
                return 0
            return value
    except (ValueError, IOError) as e:
        # Corrupted file - fail open, reset to 0
        print(f"Hook error: Corrupted counter file ({e}), resetting to 0", file=sys.stderr)
        return 0

def write_counter(value):
    """Write counter value"""
    try:
        counter_path = get_counter_path()
        os.makedirs(os.path.dirname(counter_path), exist_ok=True)
        with open(counter_path, 'w') as f:
            f.write(str(value))
    except IOError as e:
        # Can't write counter - log but don't block
        print(f"⚠️  Warning: Can't update counter: {e}", file=sys.stderr)

def increment_counter():
    """Increment counter by 1 (atomic with file locking to prevent race conditions)"""
    import msvcrt  # Windows file locking

    counter_path = get_counter_path()
    os.makedirs(os.path.dirname(counter_path), exist_ok=True)

    # Create file if doesn't exist
    if not os.path.exists(counter_path):
        try:
            with open(counter_path, 'w') as f:
                f.write('0')
        except IOError:
            pass

    # Atomic read-increment-write with file locking
    try:
        with open(counter_path, 'r+') as f:
            # Lock file (exclusive lock, blocks until available)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                # Read current value
                content = f.read().strip()
                current = int(content) if content else 0
                if current < 0:
                    current = 0

                # Write new value
                new_value = current + 1
                f.seek(0)
                f.truncate()
                f.write(str(new_value))
                f.flush()

                return new_value
            finally:
                # Unlock file
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except (ValueError, IOError) as e:
        # Locking failed or file corrupted - fall back to non-atomic (log warning)
        print(f"⚠️  Counter locking failed ({e}), using non-atomic increment", file=sys.stderr)
        current = read_counter()
        write_counter(current + 1)
        return current + 1

def reset_counter():
    """Reset counter to 0 (saves value before reset for wisdom_checkpoint)"""
    try:
        # Save current counter value for wisdom_checkpoint hook
        current = read_counter()
        if current >= 28:  # Only save if at heavy checkpoint
            project_root = get_project_root()
            marker_path = os.path.join(project_root, ".claude", ".last_heavy_checkpoint")
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, 'w') as f:
                f.write(str(current))
    except Exception:
        pass  # Fail gracefully - don't block on marker file errors

    # Reset counter
    write_counter(0)


def cleanup_old_counters(counter_dir=None, max_age_days=7):
    """
    Delete old counter files to prevent proliferation.

    Session 293: Counter file cleanup mechanism (design-code v2.0).

    Args:
        counter_dir: Directory containing counter files (default: project .claude/hooks/)
        max_age_days: Files older than this are deleted (default: 7 days)

    Returns:
        int: Number of files deleted (for logging)

    Failure modes handled:
        - Permission errors: Fail open (skip file, continue)
        - Pattern matching: Only deletes change_counter_*.txt
        - Active session: Uses current session ID to avoid deleting active files
    """
    import glob

    if counter_dir is None:
        counter_dir = os.path.dirname(get_counter_path())

    # Calculate cutoff time (files older than this are deleted)
    cutoff = time.time() - (max_age_days * 86400)

    # Get current session/process identifiers to preserve active files
    try:
        session_id = get_session_identifier()
        current_pid = os.getpid()
    except:
        # If we can't get identifiers, don't delete anything (fail safe)
        return 0

    deleted_count = 0

    # Find all counter files matching pattern
    pattern = os.path.join(counter_dir, "change_counter_*.txt")

    try:
        for filepath in glob.glob(pattern):
            try:
                # Skip if it's our own counter file (active session/process)
                filename = os.path.basename(filepath)
                if session_id in filename or f"_proc_{current_pid}" in filename:
                    continue

                # Check file age
                mtime = os.path.getmtime(filepath)
                if mtime < cutoff:
                    # Delete old file
                    os.remove(filepath)
                    deleted_count += 1

            except (OSError, IOError, PermissionError):
                # Fail open - skip files we can't delete (locked, permissions, etc.)
                continue

    except Exception:
        # Fail open - cleanup errors don't block operations
        pass

    return deleted_count


def is_notepad_fresh():
    """Check if notepad was updated in last 5 minutes"""
    try:
        project_root = get_project_root()
        notepad_path = os.path.join(project_root, ".omc", "notepad.md")

        if not os.path.exists(notepad_path):
            return False  # Notepad doesn't exist = stale

        mtime = os.path.getmtime(notepad_path)
        age_seconds = time.time() - mtime
        return age_seconds < NOTEPAD_STALE_SECONDS
    except (OSError, IOError):
        return False  # Can't check = assume stale

def is_claudemd_fresh():
    """Check if CLAUDE.md was updated in last 30 minutes"""
    try:
        project_root = get_project_root()
        claudemd_path = os.path.join(project_root, "CLAUDE.md")

        if not os.path.exists(claudemd_path):
            return False  # CLAUDE.md doesn't exist = stale

        mtime = os.path.getmtime(claudemd_path)
        age_seconds = time.time() - mtime
        return age_seconds < CLAUDEMD_STALE_SECONDS
    except (OSError, IOError):
        return False  # Can't check = assume stale

def is_git_clean():
    """Check if git has uncommitted changes (excludes untracked/gitignored files)"""
    try:
        project_root = get_project_root()
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root
        )
        if result.returncode != 0:
            return True  # fail open
        lines = result.stdout.strip().splitlines()
        # Exclude .omc/notepad.md — the hook writes this file itself as part of
        # its own checkpoint workflow. Counting it as "dirty" causes a deadlock:
        # hook writes notepad → git dirty → hook blocks next tool → no way to commit.
        non_notepad = [l for l in lines if "notepad.md" not in l]
        return not non_notepad
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        # Can't check git status - fail open (assume clean)
        return True

def is_writing_to_doc_file(tool_name, tool_input):
    """Check if operation is writing/editing notepad or CLAUDE.md (bypass to prevent deadlock)"""
    if tool_name not in ("Write", "Edit"):
        return False

    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False

    # Normalize path
    normalized = os.path.normpath(file_path)
    basename = os.path.basename(normalized)

    # Check if writing to notepad (must be in .omc directory)
    if basename == "notepad.md" and ".omc" in normalized:
        return True

    # Check if writing to CLAUDE.md (security: prevent path traversal outside project)
    # Allows: CLAUDE.md, ./CLAUDE.md, /any/path/CLAUDE.md
    # Blocks: ../../../etc/CLAUDE.md (tries to escape outside current tree)
    if basename == "CLAUDE.md":
        # Security check: normalized path must not start with ".." (path traversal attempt)
        # After normpath, a legitimate path won't start with ".." but a traversal attempt will
        normalized = os.path.normpath(file_path)
        if normalized.startswith(".."):
            return False  # Path traversal attempt - block it
        return True  # Legitimate CLAUDE.md write - allow bypass

    return False

def check_claude_md_format(file_path, content=None):
    """Validate CLAUDE.md follows session-wrap format (Session 293)

    WARNS LOUDLY but doesn't block. Encourages proper documentation.

    Expected format (session-wrap skill):
    - "CURRENT WORK" section with brief summary
    - Keep under 200 lines total
    - Link to session-logs.md for details

    Args:
        file_path: Path to CLAUDE.md file
        content: Optional content to validate (for testing); if None, reads from file
    """
    try:
        # Get content (either passed in or read from file)
        if content is None:
            if not os.path.exists(file_path):
                return  # File doesn't exist yet, can't validate
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

        lines = content.split('\n')

        line_count = len(lines)
        has_current_work = "CURRENT WORK" in content or "Current Work" in content
        has_session_logs_link = "session-logs.md" in content

        # Check if format violations exist
        violations = []

        if line_count > 250:
            violations.append(f"File is {line_count} lines (should be <200 per session-wrap)")

        if not has_current_work:
            violations.append("Missing 'CURRENT WORK' section")

        if not has_session_logs_link:
            violations.append("No link to .claude/rules/session-logs.md")

        # If violations found, SCREAM LOUDLY
        if violations:
            print("\n" + "="*80, file=sys.stderr, flush=True)
            print("🚨 🚨 🚨  WARNING: CLAUDE.md FORMAT VIOLATION  🚨 🚨 🚨", file=sys.stderr, flush=True)
            print("="*80, file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("You are about to update CLAUDE.md without following session-wrap format!", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("VIOLATIONS DETECTED:", file=sys.stderr, flush=True)
            for v in violations:
                print(f"  ❌ {v}", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("REQUIRED FORMAT (session-wrap skill):", file=sys.stderr, flush=True)
            print("  1. Brief summary in 'CURRENT WORK' section (2-3 sentences)", file=sys.stderr, flush=True)
            print("  2. Link to .claude/rules/session-logs.md for details", file=sys.stderr, flush=True)
            print("  3. Keep CLAUDE.md under 200 lines total", file=sys.stderr, flush=True)
            print("  4. Detailed entry goes in session-logs.md (not here)", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("💡 TIP: Use /session-wrap skill to update properly", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("This is a WARNING only - operation will proceed.", file=sys.stderr, flush=True)
            print("But seriously... follow the format. It's there for a reason.", file=sys.stderr, flush=True)
            print("="*80, file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)

    except Exception as e:
        # Fail open - don't block on validation errors
        pass

def is_git_command(tool_name, tool_input):
    """Check if Bash command is a git command (bypass)
    Handles compound commands like: git status, cd /path && git commit
    """
    if tool_name != "Bash":
        return False
    command = tool_input.get("command", "")
    # Split on &&, ||, ;, | to isolate individual commands
    parts = re.split(r'[;&|]+', command)
    # Check if ANY part is a git command
    for part in parts:
        part_stripped = part.strip()
        # Strip leading env var assignments (e.g., LINT_GATE_BYPASS=1 git commit)
        part_stripped = re.sub(r'^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+', '', part_stripped)
        if part_stripped.startswith("git ") or part_stripped == "git":
            return True
    return False

def is_read_only_bash(tool_name, tool_input):
    """Check if Bash command is read-only (bypass for safe operations)"""
    if tool_name != "Bash":
        return False
    command = tool_input.get("command", "").strip()
    # Check if command starts with any read-only command
    for read_cmd in READ_ONLY_COMMANDS:
        if command.startswith(read_cmd):
            return True
    return False

def validate_notepad_structure():
    """Validate notepad has required structure (Session 290+)

    Required sections (case-insensitive, flexible formatting):
    - ✅ DONE: or Done: or DONE: (what just finished)
    - 🔍 NEXT: or Next: or NEXT: (what comes next)
    - ⏸️ BLOCKED: or Blocked: or BLOCKED: (blockers or NONE)

    Optional sections:
    - ⚠️ RECENT ISSUES: or Recent issues: (problems encountered, Session 290)

    Returns: (is_valid, error_message)
    """
    try:
        project_root = get_project_root()
        notepad_path = os.path.join(project_root, ".omc", "notepad.md")

        if not os.path.exists(notepad_path):
            return (False, "Notepad file doesn't exist")

        with open(notepad_path, 'r', encoding='utf-8') as f:
            content = f.read()

        content_lower = content.lower()

        # Check for required sections (flexible patterns)
        required_sections = [
            ("done:", ["✅ done:", "done:", "✅done:"]),
            ("next:", ["🔍 next:", "next:", "🔍next:"]),
            ("blocked:", ["⏸️ blocked:", "blocked:", "⏸️blocked:"])
        ]

        for section_name, patterns in required_sections:
            found = any(pattern in content_lower for pattern in patterns)
            if not found:
                return (False, f"Missing required section: {section_name.title()}")

            # Check section not empty (find section, check next line has content)
            for pattern in patterns:
                if pattern in content_lower:
                    # Find position of section
                    pos = content_lower.index(pattern)
                    # Get text after section header (don't strip - preserves line structure)
                    after_section = content[pos + len(pattern):]

                    # Get first NON-EMPTY line (skip blank lines, stop at next section header)
                    lines = after_section.split('\n')
                    first_line = ""
                    # Include optional sections as boundaries (Session 290)
                    section_headers = ["done:", "next:", "blocked:", "recent issues:"]
                    for line in lines[:10]:  # Check up to 10 lines (reasonable limit)
                        stripped = line.strip()
                        # Stop if we hit another section header
                        if any(header in stripped.lower() for header in section_headers):
                            break
                        # Found content
                        if len(stripped) >= 3:
                            first_line = stripped
                            break

                    if len(first_line) < 3:
                        return (False, f"Section '{section_name.title()}' is empty")
                    break

        return (True, None)

    except Exception as e:
        # Fail open on errors
        print(f"⚠️ Structure validation error (allowing): {e}", file=sys.stderr)
        return (True, None)  # Allow on errors

def get_archive_dir():
    """Get path to snapshot archive directory"""
    project_root = get_project_root()
    return os.path.join(project_root, ".claude", "notepad_archive")

def save_notepad_snapshot(counter):
    """Save current notepad to archive (Session 290)

    Saves BEFORE checkpoint enforcement (crash-safe pattern).
    Filename: session_{session_id}_op{counter}_{timestamp}.md

    Returns: snapshot_path or None on error
    """
    try:
        project_root = get_project_root()
        notepad_path = os.path.join(project_root, ".omc", "notepad.md")

        if not os.path.exists(notepad_path):
            return None  # No notepad to snapshot

        # Create archive directory
        archive_dir = get_archive_dir()
        os.makedirs(archive_dir, exist_ok=True)

        # Generate filename
        # Session ID = use counter divided by 28 (resets every 28 ops)
        session_id = (counter // 28) + 1
        timestamp = time.strftime("%Y-%m-%d_%H%M")
        filename = f"session_{session_id}_op{counter}_{timestamp}.md"
        snapshot_path = os.path.join(archive_dir, filename)

        # Read notepad content
        with open(notepad_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Save snapshot (timeout protection via threading)
        def save_file():
            with open(snapshot_path, 'w', encoding='utf-8') as f:
                f.write(content)

        import threading
        save_thread = threading.Thread(target=save_file)
        save_thread.daemon = True
        save_thread.start()
        save_thread.join(timeout=5.0)  # 5 second timeout

        if save_thread.is_alive():
            print("⚠️ Snapshot save timeout (>5s), skipping", file=sys.stderr, flush=True)
            return None

        return snapshot_path

    except Exception as e:
        # Fail open - don't block on snapshot errors
        print(f"⚠️ Snapshot save error (non-blocking): {e}", file=sys.stderr, flush=True)
        return None

def cleanup_old_snapshots(max_count=20):
    """Cleanup old snapshots - keep max_count newest (Session 290)

    Strategy: When count > max_count, delete oldest (max_count // 2)
    Industry standard: max 5-10 for logs, using 20 for sessions

    Returns: number of files deleted
    """
    try:
        archive_dir = get_archive_dir()

        if not os.path.exists(archive_dir):
            return 0  # No archive yet

        # Get all snapshot files
        files = [f for f in os.listdir(archive_dir) if f.endswith('.md')]

        if len(files) <= max_count:
            return 0  # Under limit, no cleanup needed

        # Sort by modification time (oldest first)
        files_with_mtime = []
        for f in files:
            filepath = os.path.join(archive_dir, f)
            try:
                mtime = os.path.getmtime(filepath)
                files_with_mtime.append((f, mtime))
            except OSError:
                continue  # Skip files we can't stat

        files_with_mtime.sort(key=lambda x: x[1])  # Sort by mtime

        # Delete oldest half
        delete_count = len(files_with_mtime) - (max_count // 2)
        if delete_count <= 0:
            return 0

        deleted = 0
        for f, _ in files_with_mtime[:delete_count]:
            try:
                filepath = os.path.join(archive_dir, f)
                os.remove(filepath)
                deleted += 1
            except OSError as e:
                print(f"⚠️ Couldn't delete snapshot {f}: {e}", file=sys.stderr, flush=True)
                continue

        if deleted > 0:
            print(f"✓ Cleaned up {deleted} old snapshots (kept newest {len(files_with_mtime) - deleted})", file=sys.stderr, flush=True)

        return deleted

    except Exception as e:
        # Fail open - don't block on cleanup errors
        print(f"⚠️ Snapshot cleanup error (non-blocking): {e}", file=sys.stderr, flush=True)
        return 0

def check_notepad_quality():
    """Check notepad quality - returns (exit_code, message)
    exit_code: 0=allow, 2=block
    message: error/warning message for stderr
    """
    try:
        project_root = get_project_root()
        notepad_path = os.path.join(project_root, ".omc", "notepad.md")

        if not os.path.exists(notepad_path):
            # No notepad = can't check quality, allow (first use case)
            return (0, None)

        # Read notepad content
        with open(notepad_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        # BLOCK CONDITIONS (exit 2)

        # Block 1: Empty or too short (<20 chars)
        if len(content) < 20:
            msg = """======================================================================
🛑 BLOCKED: NOTEPAD QUALITY - Content too short
======================================================================

Notepad content is only {len} characters. Minimum 20 chars required.

Good notepad updates include:
  ✅ What you just completed (specific action + result)
  🔍 What you're working on now
  ⏸️ What's paused/blocked
  ➡️  What's next (concrete next step)

Emergency escape:
  Delete: ~/.claude/hooks/change_enforcement.py
  Or touch: .claude/hooks/disable_enforcement.flag
======================================================================""".format(len=len(content))
            return (2, msg)

        # Block 2: No structure (missing ALL section headers)
        has_headers = bool(re.search(r'^#{1,3}\s+', content, re.MULTILINE))
        if not has_headers:
            msg = """======================================================================
🛑 BLOCKED: NOTEPAD QUALITY - No structure
======================================================================

Notepad must have section headers (##, ###) to organize information.

Example structure:
  ## Session 290 - Progress

  ### COMPLETED ✅
  - [what was done]

  ### IN PROGRESS 🔍
  - [current work]

  ### NEXT
  - [concrete next step]

Emergency escape:
  Delete: ~/.claude/hooks/change_enforcement.py
======================================================================"""
            return (2, msg)

        # Block 3: All generic phrases (no specific content)
        # Session 293: Use shared module for phrase-specific suggestions
        if detect_generic_phrases:
            generic_issues = detect_generic_phrases(content)
            generic_count = len(generic_issues)
        else:
            # Fallback if module not loaded
            generic_patterns = [
                r'\bworking on\b', r'\bmade progress\b', r'\bcontinuing work\b',
                r'\bdoing things\b', r'\bworked on stuff\b', r'\bsome things\b'
            ]
            content_lower = content.lower()
            generic_count = sum(1 for p in generic_patterns if re.search(p, content_lower))
            generic_issues = []

        # If >50% of content is generic phrases, block
        words = len(content.split())
        if generic_count >= 3 and words < 30:
            # Build suggestion list from detected phrases
            suggestions_text = ""
            if generic_issues:
                for phrase, suggestion in generic_issues[:3]:  # Show first 3
                    suggestions_text += f'\n  - "{phrase}" → {suggestion}'

            msg = f"""======================================================================
🛑 BLOCKED: NOTEPAD QUALITY - Too vague
======================================================================

Notepad contains {generic_count} generic phrases. Be more specific.

Replace generic phrases:{suggestions_text if suggestions_text else '''
  ❌ "Worked on hooks"
  ✅ "Fixed change_enforcement git bypass bug (line 198-227)"

  ❌ "Made progress"
  ✅ "Added quality checks: block <20 chars, warn missing markers"'''}

Emergency escape:
  Delete: ~/.claude/hooks/change_enforcement.py
======================================================================"""
            return (2, msg)

        # WARN CONDITIONS (exit 0 but print warning)

        warnings = []

        # Warn 1: Short but not empty (20-50 chars)
        if 20 <= len(content) < 50:
            warnings.append("  - Content is short (20-50 chars) - consider adding more detail")

        # Warn 2: Missing status markers (✅/🔍/⏸️)
        has_status_markers = bool(re.search(r'[✅🔍⏸️]', content))
        if not has_status_markers:
            warnings.append("  - Missing status markers (✅ COMPLETED / 🔍 IN PROGRESS / ⏸️ PAUSED)")

        # Warn 3: Missing "next" section
        has_next = bool(re.search(r'(next|todo|remaining)', content, re.IGNORECASE))
        if not has_next:
            warnings.append("  - Missing 'what's next' section")

        # Warn 4: Some vague language (not blocking amount)
        if 1 <= generic_count < 3:
            vague_details = ""
            if generic_issues:
                for phrase, suggestion in generic_issues[:2]:  # Show first 2 for warnings
                    vague_details += f"\n    • \"{phrase}\" → {suggestion}"
            warnings.append(f"  - Contains vague phrases ({generic_count} found):{vague_details}")

        if warnings:
            warn_msg = """⚠️  NOTEPAD QUALITY WARNING:
{warnings}

Consider:
  - What you just did (specific action + result)
  - What's next (concrete step)
  - Any blockers/issues

(Operation allowed)
""".format(warnings='\n'.join(warnings))
            # Print warning but allow (exit 0)
            print(warn_msg, file=sys.stderr, flush=True)

        return (0, None)

    except Exception as e:
        # Fail open on quality check errors
        print(f"⚠️  Notepad quality check error (allowing): {e}", file=sys.stderr)
        return (0, None)

def main():
    # ALWAYS wrap in try/except - fail open on ANY error
    try:
        # Check for disable flag
        disable_flag = get_disable_flag_path()
        if os.path.exists(disable_flag):
            sys.exit(0)  # Hook disabled

        # Parse hook input
        hook_input = json.load(sys.stdin)
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})

        # Subagent exemption: Claude Code's Agent tool sets parent_tool_use_id
        # on every PreToolUse payload coming from inside a spawned subagent.
        # Parent sessions have this field as null/absent. Subagents are isolated
        # workers — the checkpoint/commit discipline belongs to the parent
        # session where decisions are made, not inside delegated tasks.
        if hook_input.get("parent_tool_use_id"):
            sys.exit(0)

        # Only care about specific tools (Read/Grep/Glob not affected)
        # Exception: MCP notepad tools need special handling (clear checkpoint)
        is_mcp_notepad = "mcp__" in tool_name and "notepad" in tool_name

        if tool_name not in ("Edit", "Write", "Bash", "Agent") and not is_mcp_notepad:
            sys.exit(0)

        # === BYPASS CONDITIONS (prevent deadlock) ===

        # Bypass 0: MCP notepad tools (Session 293 deadlock fix + Session 293 grace period)
        # These tools write to .omc/notepad.md but have different tool names
        # MCP tools write content but DON'T update mtime → create grace period
        if is_mcp_notepad:
            # Read current counter to create grace period at right operation
            counter_path = get_counter_path()
            current_counter = 0
            if os.path.exists(counter_path):
                try:
                    with open(counter_path, 'r') as f:
                        current_counter = int(f.read().strip())
                except:
                    pass  # Fail open

            # Create grace period (3 operations without mtime check)
            create_grace_period(current_counter)

            # Clear checkpoint state to prevent "notepad stale" deadlock
            checkpoint_state_path = get_checkpoint_state_path()
            if os.path.exists(checkpoint_state_path):
                try:
                    os.remove(checkpoint_state_path)
                except:
                    pass  # Fail open
            sys.exit(0)  # Allow without counting

        # Bypass 1: Writing to .omc/notepad.md or CLAUDE.md
        if is_writing_to_doc_file(tool_name, tool_input):
            # Session 293: Validate CLAUDE.md format (LOUD warning if violated)
            if tool_name in ("Write", "Edit"):
                file_path = tool_input.get("file_path", "")
                if "CLAUDE.md" in file_path or "Claude.md" in file_path:
                    # Pass content for Write, None for Edit (will read from file)
                    content = tool_input.get("content") if tool_name == "Write" else None
                    check_claude_md_format(file_path, content)

            # Session 295: Create grace period for Write tool (fixes Windows mtime race)
            if tool_name == "Write":
                file_path = tool_input.get("file_path", "")
                if ".omc/notepad.md" in file_path or ".omc\\notepad.md" in file_path:
                    current_counter = read_counter()
                    create_grace_period(current_counter)

            # Auto-clear checkpoint if active (prevents "one more blocked op" after notepad update)
            checkpoint_state_path = get_checkpoint_state_path()
            if os.path.exists(checkpoint_state_path):
                try:
                    os.remove(checkpoint_state_path)
                except:
                    pass  # Fail open - don't block if clear fails
            sys.exit(0)

        # Bypass 2: Git commands
        if is_git_command(tool_name, tool_input):
            sys.exit(0)

        # Bypass 3: Read-only Bash commands
        if is_read_only_bash(tool_name, tool_input):
            sys.exit(0)

        # Bypass 4: Writing to snapshot archive (Session 290)
        # CRITICAL: Snapshot saves MUST NOT increment counter (prevents infinite loop)
        if tool_name in ("Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            if ".claude/notepad_archive/" in file_path or "\\notepad_archive\\" in file_path:
                sys.exit(0)  # Allow without counting

        # === FIRST RUN SETUP ===

        # If counter doesn't exist, this is first run in project - ensure setup
        counter_path = get_counter_path()
        if not os.path.exists(counter_path):
            ensure_project_setup()

        # === QUALITY CHECK (runs on Write/Edit operations only) ===

        # Check notepad quality before allowing Write/Edit operations
        # (Bash/Agent operations don't require notepad quality enforcement)
        if tool_name in ("Write", "Edit"):
            exit_code, error_msg = check_notepad_quality()
            if exit_code != 0:
                print(error_msg, file=sys.stderr, flush=True)
                sys.exit(exit_code)

        # === MAIN LOGIC ===

        # Read current counter (don't increment yet - only increment on success)
        current_count = read_counter()
        prospective_count = current_count + 1  # What count WOULD be if operation succeeds

        # Check if checkpoint is already active (from previous operation)
        checkpoint_state = read_checkpoint_state()

        # If checkpoint active, enforce it NOW (don't wait for exact count)
        if checkpoint_state["active"]:
            checkpoint_type = checkpoint_state["type"]

            if checkpoint_type == "light":
                notepad_fresh = is_notepad_fresh()
                git_clean = is_git_clean()

                if not notepad_fresh or not git_clean:
                    print("="*70, file=sys.stderr, flush=True)
                    print(f"BLOCKED: Light checkpoint (active since {checkpoint_state['count']}) - Documentation required", file=sys.stderr, flush=True)
                    print("="*70, file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print(f"Current count: {prospective_count}", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("Before continuing, you must:", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    if not notepad_fresh:
                        print("  [X] Update notepad (.omc/notepad.md)", file=sys.stderr, flush=True)
                        print("     - What you just did", file=sys.stderr, flush=True)
                        print("     - What's next", file=sys.stderr, flush=True)
                        print("     - Any blockers/issues", file=sys.stderr, flush=True)
                    else:
                        print("  [OK] Notepad is up to date", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    if not git_clean:
                        print("  [X] Commit changes to git", file=sys.stderr, flush=True)
                        print("     - git add <files>", file=sys.stderr, flush=True)
                        print("     - git commit -m \"<message>\"", file=sys.stderr, flush=True)
                    else:
                        print("  [OK] Git is clean", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("Both required to continue.", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("Emergency escape (if hook misfiring):", file=sys.stderr, flush=True)
                    print("  Delete: ~/.claude/hooks/change_enforcement.py", file=sys.stderr, flush=True)
                    print("  Or touch: .claude/hooks/disable_enforcement.flag", file=sys.stderr, flush=True)
                    print("="*70, file=sys.stderr, flush=True)
                    sys.exit(2)

                # Checkpoint satisfied - save snapshot and clear state
                snapshot_path = save_notepad_snapshot(checkpoint_state['count'])
                if snapshot_path:
                    cleanup_old_snapshots(MAX_SNAPSHOTS)

                clear_checkpoint_state()
                print(f"✓ Light checkpoint passed ({prospective_count} changes documented)", file=sys.stderr, flush=True)

                # Session 293: Log timing data
                log_checkpoint_timing("LIGHT", prospective_count)

            elif checkpoint_type == "heavy":
                notepad_fresh = is_notepad_fresh()
                claudemd_fresh = is_claudemd_fresh()
                git_clean = is_git_clean()

                if not notepad_fresh or not claudemd_fresh or not git_clean:
                    print("="*70, file=sys.stderr, flush=True)
                    print(f"BLOCKED: Heavy checkpoint (active since {checkpoint_state['count']}) - CLAUDE.md update required", file=sys.stderr, flush=True)
                    print("="*70, file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print(f"Current count: {prospective_count}", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("Before continuing, you must:", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    if not notepad_fresh:
                        print("  [X] Update notepad (.omc/notepad.md)", file=sys.stderr, flush=True)
                        print("     - What you just did", file=sys.stderr, flush=True)
                        print("     - What's next", file=sys.stderr, flush=True)
                        print("     - Any blockers/issues", file=sys.stderr, flush=True)
                    else:
                        print("  [OK] Notepad is up to date", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    if not claudemd_fresh:
                        print("  [X] Update CLAUDE.md (project documentation)", file=sys.stderr, flush=True)
                        print("     - Session summary", file=sys.stderr, flush=True)
                        print("     - Key decisions made", file=sys.stderr, flush=True)
                        print("     - Files modified", file=sys.stderr, flush=True)
                    else:
                        print("  [OK] CLAUDE.md is up to date", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    if not git_clean:
                        print("  [X] Commit changes to git", file=sys.stderr, flush=True)
                        print("     - git add <files>", file=sys.stderr, flush=True)
                        print("     - git commit -m \"<message>\"", file=sys.stderr, flush=True)
                    else:
                        print("  [OK] Git is clean", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("All three required to continue.", file=sys.stderr, flush=True)
                    print("", file=sys.stderr, flush=True)
                    print("Emergency escape (if hook misfiring):", file=sys.stderr, flush=True)
                    print("  Delete: ~/.claude/hooks/change_enforcement.py", file=sys.stderr, flush=True)
                    print("  Or touch: .claude/hooks/disable_enforcement.flag", file=sys.stderr, flush=True)
                    print("="*70, file=sys.stderr, flush=True)
                    sys.exit(2)

                # Heavy checkpoint satisfied - save snapshot, reset counter, clear state
                snapshot_path = save_notepad_snapshot(checkpoint_state['count'])
                if snapshot_path:
                    cleanup_old_snapshots(MAX_SNAPSHOTS)

                reset_counter()
                clear_checkpoint_state()

                # Wisdom checkpoint integration - prompt for pattern capture
                checkpoint_count = increment_wisdom_checkpoint()
                if should_display_wisdom_prompt():
                    display_wisdom_prompt()

                print(f"✓ Heavy checkpoint passed ({prospective_count} changes documented)", file=sys.stderr, flush=True)

                # Session 293: Log timing data
                log_checkpoint_timing("HEAVY", prospective_count)

        # === SESSION 290 FIX: RANGE-BASED ENFORCEMENT (GPT's approach) ===
        # Enforce based on current count ranges, not exact equality
        # This prevents checkpoint bypass when counter skips past exact values
        # Research: Fail-closed enforcement (2024 best practices)

        # FAIL-SAFE: Hard cap at 50 operations (prevents runaway counter)
        if prospective_count > 50:
            print("="*70, file=sys.stderr, flush=True)
            print("FATAL: Operation counter critically high (>50)", file=sys.stderr, flush=True)
            print("="*70, file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("Enforcement has been bypassed - manual intervention required.", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("To reset:", file=sys.stderr, flush=True)
            print("  1. Update .omc/notepad.md with current state", file=sys.stderr, flush=True)
            print("  2. Update CLAUDE.md with session summary", file=sys.stderr, flush=True)
            print("  3. Commit all changes to git", file=sys.stderr, flush=True)
            print("  4. Delete: .claude/hooks/change_counter.txt", file=sys.stderr, flush=True)
            print("", file=sys.stderr, flush=True)
            print("="*70, file=sys.stderr, flush=True)
            sys.exit(3)

        # HEAVY checkpoint: >= 40 operations
        if prospective_count >= HEAVY_THRESHOLD:
            is_valid, error_msg = validate_notepad_structure()

            # Session 293: Check grace period before mtime (MCP tools don't update mtime)
            within_grace_period = check_grace_period(prospective_count)
            notepad_fresh = within_grace_period or is_notepad_fresh()
            claudemd_fresh = is_claudemd_fresh()
            git_clean = is_git_clean()

            if not is_valid or not notepad_fresh or not claudemd_fresh or not git_clean:
                print("="*70, file=sys.stderr, flush=True)
                print(f"BLOCKED: Heavy checkpoint (>={HEAVY_THRESHOLD} changes)", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                print(f"Current count: {prospective_count}", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                print("Before continuing, you must:", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not is_valid:
                    print(f"  [X] Fix notepad structure: {error_msg}", file=sys.stderr, flush=True)
                    print("     Required: Done: / Next: / Blocked:", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad structure valid", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not notepad_fresh:
                    print("  [X] Update notepad (.omc/notepad.md)", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad is up to date", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not claudemd_fresh:
                    print("  [X] Update CLAUDE.md (session summary)", file=sys.stderr, flush=True)
                else:
                    print("  [OK] CLAUDE.md is up to date", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not git_clean:
                    print("  [X] Commit changes to git", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Git is clean", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                print("All required to continue.", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                sys.exit(2)

            # Heavy checkpoint satisfied - save snapshot + reset counter
            snapshot_path = save_notepad_snapshot(prospective_count)
            if snapshot_path:
                cleanup_old_snapshots(MAX_SNAPSHOTS)
            reset_counter()

            # Wisdom checkpoint integration - prompt for pattern capture
            checkpoint_count = increment_wisdom_checkpoint()
            if should_display_wisdom_prompt():
                display_wisdom_prompt()

            print(f"✓ Heavy checkpoint passed (counter reset)", file=sys.stderr, flush=True)

            # Session 293: Log timing data
            log_checkpoint_timing("HEAVY", prospective_count)

        # LIGHT checkpoint: >= 20 operations (if not already handled by heavy)
        elif prospective_count >= LIGHT_THRESHOLD:
            is_valid, error_msg = validate_notepad_structure()

            # Session 293: Check grace period before mtime (MCP tools don't update mtime)
            within_grace_period = check_grace_period(prospective_count)
            notepad_fresh = within_grace_period or is_notepad_fresh()
            git_clean = is_git_clean()

            if not is_valid or not notepad_fresh or not git_clean:
                print("="*70, file=sys.stderr, flush=True)
                print(f"BLOCKED: Light checkpoint (>={LIGHT_THRESHOLD} changes)", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                print(f"Current count: {prospective_count}", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not is_valid:
                    print(f"  [X] Fix notepad structure: {error_msg}", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad structure valid", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not notepad_fresh:
                    print("  [X] Update notepad (.omc/notepad.md)", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad is up to date", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not git_clean:
                    print("  [X] Commit changes to git", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Git is clean", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                sys.exit(2)

            # Light checkpoint satisfied - save snapshot
            snapshot_path = save_notepad_snapshot(prospective_count)
            if snapshot_path:
                cleanup_old_snapshots(MAX_SNAPSHOTS)

            # Session 10: MINI_CHECKPOINT_2 = 21 was dead code (fell into LIGHT branch).
            # Give op 21 a distinct visible message so it's not silent.
            if prospective_count == MINI_CHECKPOINT_2:
                print(f"✓ Mini-2 snapshot saved (op {prospective_count})", file=sys.stderr, flush=True)
                log_checkpoint_timing("MINI_2", prospective_count)
            else:
                print(f"✓ Light checkpoint passed", file=sys.stderr, flush=True)
                log_checkpoint_timing("LIGHT", prospective_count)

        # MINI checkpoint: >= 7 operations (structure + staleness - Defense in Depth, NIST SP 800-172)
        elif prospective_count >= MINI_CHECKPOINT_1:
            is_valid, error_msg = validate_notepad_structure()

            # Session 293: Check grace period before mtime (MCP tools don't update mtime)
            within_grace_period = check_grace_period(prospective_count)
            notepad_fresh = within_grace_period or is_notepad_fresh()

            if not is_valid or not notepad_fresh:
                print("="*70, file=sys.stderr, flush=True)
                print(f"BLOCKED: Mini checkpoint (>={MINI_CHECKPOINT_1} changes)", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                print(f"Current count: {prospective_count}", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not is_valid:
                    print(f"  [X] Fix notepad structure: {error_msg}", file=sys.stderr, flush=True)
                    print("     Required: Done: / Next: / Blocked:", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad structure valid", file=sys.stderr, flush=True)
                print("", file=sys.stderr, flush=True)
                if not notepad_fresh:
                    print("  [X] Update notepad (.omc/notepad.md)", file=sys.stderr, flush=True)
                else:
                    print("  [OK] Notepad is up to date", file=sys.stderr, flush=True)
                print("="*70, file=sys.stderr, flush=True)
                sys.exit(2)

            # Mini checkpoint passed - save snapshot at round intervals
            if prospective_count % 7 == 0:  # Every 7 ops
                snapshot_path = save_notepad_snapshot(prospective_count)
                if snapshot_path:
                    cleanup_old_snapshots(MAX_SNAPSHOTS)
                print(f"✓ Snapshot saved (op {prospective_count})", file=sys.stderr, flush=True)

                # Session 293: Log timing data (distinguish MINI_1 from MINI_2)
                checkpoint_name = "MINI_1" if prospective_count == MINI_CHECKPOINT_1 else "MINI_2"
                log_checkpoint_timing(checkpoint_name, prospective_count)

        # Allow operation - increment counter NOW (only on success)
        increment_counter()
        sys.exit(0)

    except Exception as e:
        # CRITICAL: Fail open - don't block on errors
        print(f"⚠️  Hook error (allowing operation): {e}", file=sys.stderr)
        sys.exit(0)

if __name__ == "__main__":
    main()
