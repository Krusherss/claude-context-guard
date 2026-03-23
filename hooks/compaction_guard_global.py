"""
UserPromptSubmit hook (GLOBAL): token-based context window monitor.
Works across all projects — detects project from CWD and finds matching JSONL.

Primary trigger: last assistant entry's total input tokens = actual context fill level.
Fallback: turn count if no token data available.

Effective window is ~160K (not 200K) — system prompt + tool overhead eats ~40K.
Calibrated: JSONL shows 102K when UI shows 64% used → 102K/0.64 = 160K effective.

Thresholds (150K calibrated window):
  NOTICE:   75K tokens  (50%) or 20 turns — early heads-up, no action needed yet
  WARN:     97.5K tokens (65%) or 28 turns — update docs at next natural pause
  CRITICAL: 127.5K tokens (85%) or 42 turns — STOP and save now, compaction imminent
"""
import os
import sys
import re
import glob
import io
import json
import hashlib
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CONTEXT_WINDOW   = 150_000   # effective window calibrated to fire ~10K earlier than raw 160K estimate
NOTICE_TOKENS    =  75_000   # 50%
WARN_TOKENS      =  97_500   # 65%
CRITICAL_TOKENS  = 127_500   # 85% — right before auto-compaction (~90-96% is when CC compacts)
NOTICE_TURNS     = 20
WARN_TURNS       = 28
CRITICAL_TURNS   = 42

CLAUDE_DIR        = os.path.expanduser(r'~/.claude')
SESSION_STARTS_DIR = os.path.join(CLAUDE_DIR, 'hooks', 'session_starts')


def project_slug():
    """Derive ~/.claude/projects/ slug from CWD (mirrors Claude Code logic)."""
    cwd = os.getcwd()
    slug = cwd
    if len(slug) >= 2 and slug[1] == ':':
        slug = slug[0].lower() + slug[2:]
    slug = slug.replace('\\', '-').replace('/', '-').replace(' ', '-')
    return slug.lstrip('-')


def session_ts_file():
    h = hashlib.md5(os.getcwd().encode()).hexdigest()[:12]
    os.makedirs(SESSION_STARTS_DIR, exist_ok=True)
    return os.path.join(SESSION_STARTS_DIR, f'{h}.txt')


def get_open_findings():
    claude_md = os.path.join(os.getcwd(), 'CLAUDE.md')
    try:
        with open(claude_md, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return []
    open_statuses = ['NEEDS_SCAN', 'NEEDS_CHECK', 'SCAN_PASSED', 'READY_FOR_FIX', 'BLOCKED']
    open_rows = []
    match = re.search(r'## PENDING FINDINGS.*?(?=\n---)', content, re.DOTALL)
    if match:
        for line in match.group(0).split('\n'):
            if line.startswith('|') and '----' not in line and 'Finding' not in line:
                for status in open_statuses:
                    if status in line:
                        parts = [p.strip() for p in line.split('|') if p.strip()]
                        if parts:
                            open_rows.append(parts[0])
                        break
    return open_rows


try:
    ts_file = session_ts_file()
    if not os.path.exists(ts_file):
        sys.exit(0)
    with open(ts_file, 'r') as f:
        session_start = float(f.read().strip())

    # Find JSONL — project slug first, fallback to newest overall
    slug = project_slug()
    project_jsonl_dir = os.path.join(CLAUDE_DIR, 'projects', slug)
    jsonl_files = glob.glob(os.path.join(project_jsonl_dir, '*.jsonl'))
    if not jsonl_files:
        jsonl_files = glob.glob(os.path.join(CLAUDE_DIR, 'projects', '*', '*.jsonl'))
    if not jsonl_files:
        sys.exit(0)
    latest = max(jsonl_files, key=os.path.getmtime)

    # Scan entries since session start
    user_turns       = 0
    last_input_tokens = 0  # last assistant's total input = current context fill

    with open(latest, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            try:
                entry = json.loads(line)
                ts = entry.get('timestamp', 0)
                if isinstance(ts, str):
                    ts = datetime.datetime.fromisoformat(
                        ts.replace('Z', '+00:00')).timestamp()
                if ts < session_start:
                    continue

                etype = entry.get('type')
                if entry.get('isSidechain'):
                    continue

                if etype == 'user':
                    user_turns += 1

                elif etype == 'assistant':
                    msg = entry.get('message', {})
                    if isinstance(msg, dict):
                        usage = msg.get('usage', {})
                        if isinstance(usage, dict):
                            total = (
                                usage.get('input_tokens', 0)
                                + usage.get('cache_creation_input_tokens', 0)
                                + usage.get('cache_read_input_tokens', 0)
                            )
                            if total > 0:
                                last_input_tokens = total  # keep updating — want the latest

            except Exception:
                continue

    # If no user turns yet, this is session initialization overhead — don't warn
    if user_turns == 0:
        sys.exit(0)

    # Determine alert level — token-based primary, turn-based fallback
    if last_input_tokens > 0:
        pct         = last_input_tokens / CONTEXT_WINDOW * 100
        is_notice   = last_input_tokens >= NOTICE_TOKENS
        is_warn     = last_input_tokens >= WARN_TOKENS
        is_critical = last_input_tokens >= CRITICAL_TOKENS
        metric_str  = f"{last_input_tokens:,} tokens ({pct:.0f}% of {CONTEXT_WINDOW//1000}K)"
        detail_str  = f"  Tokens: {last_input_tokens:,}/{CONTEXT_WINDOW:,} ({pct:.0f}%)  |  Turns this session: {user_turns}"
    else:
        # Fallback: no token data (very early in session or JSONL issue)
        is_notice   = user_turns >= NOTICE_TURNS
        is_warn     = user_turns >= WARN_TURNS
        is_critical = user_turns >= CRITICAL_TURNS
        metric_str  = f"{user_turns} turns (no token data)"
        detail_str  = f"  Turns: {user_turns}  |  No token data available"

    if not is_notice:
        sys.exit(0)

    open_findings = get_open_findings()

    # Read user's current message — check for acknowledgment keywords
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        user_msg = data.get("message", "") or ""
        if isinstance(user_msg, list):
            user_msg = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in user_msg
            )
        user_msg_lower = user_msg.lower()
    except Exception:
        user_msg_lower = ""

    ACK_KEYWORDS = ["update docs", "compact", "save progress", "update claude",
                    "acknowledged", "update the docs"]
    is_ack = any(kw in user_msg_lower for kw in ACK_KEYWORDS)

    findings_str = (
        ', '.join(open_findings[:6]) + ('...' if len(open_findings) > 6 else '')
        if open_findings else "none (all fixed)"
    )

    if is_critical and not is_ack:
        print("=" * 60)
        print(f"COMPACTION CRITICAL — {metric_str}")
        print(detail_str)
        print(f"  Open findings: {findings_str}")
        print("  Context compaction is IMMINENT — stop work and save state now.")
        print("")
        print("CLAUDE — YOU MUST DO THIS BEFORE RESPONDING:")
        print("  1. STOP whatever you are doing.")
        print(f"  2. Tell user: 'COMPACTION CRITICAL: {metric_str} — update docs now?'")
        print("  3. Do NOT proceed with any task until user acknowledges.")
        print("=" * 60)
    elif is_warn and not is_ack:
        print("=" * 60)
        print(f"COMPACTION WARNING — {metric_str}")
        print(detail_str)
        print(f"  Open findings: {findings_str}")
        print("  UPDATE CLAUDE.md and PROGRESS_LOG at your next natural pause.")
        print("  Compaction is silent — don't wait until session end.")
        print("")
        print("CLAUDE: At your next pause, tell user:")
        print(f"  'COMPACTION WARN: {metric_str} — should I update docs now?'")
        print("=" * 60)
    elif is_notice:
        print(f"[context {metric_str} — update docs by 60%]")

except Exception:
    sys.exit(0)
