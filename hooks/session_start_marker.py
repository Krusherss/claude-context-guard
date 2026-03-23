"""
SessionStart hook (GLOBAL): records session start timestamp per project.
Used by compaction_guard_global.py to count turns within this session only.
"""
import os
import sys
import time
import hashlib

CLAUDE_DIR = os.path.expanduser(r'~/.claude')
SESSION_STARTS_DIR = os.path.join(CLAUDE_DIR, 'hooks', 'session_starts')

try:
    os.makedirs(SESSION_STARTS_DIR, exist_ok=True)
    h = hashlib.md5(os.getcwd().encode()).hexdigest()[:12]
    ts_file = os.path.join(SESSION_STARTS_DIR, f'{h}.txt')
    with open(ts_file, 'w') as f:
        f.write(str(time.time()))
except Exception:
    pass
