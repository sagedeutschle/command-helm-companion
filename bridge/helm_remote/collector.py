"""Read bounded assistant-text excerpts from recent local sessions. Never execute or reply."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from .store import clean

def records(data):
    for line in data.splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict): yield obj
        except (ValueError, UnicodeError): continue

def collect_file(store, path, provider):
    if path.is_symlink(): return
    stat = path.stat()
    if stat.st_size > 100_000_000: return
    with path.open('rb') as f:
        head = f.read(32768)
        f.seek(max(0, stat.st_size - 262144))
        tail = f.read(262144)
    meta = list(records(head))
    entries = list(records(tail))
    cwd = None
    sid = None
    state = 'idle'
    text = None
    observed = stat.st_mtime
    for obj in meta + entries:
        if provider == 'codex' and obj.get('type') == 'session_meta':
            cwd = obj.get('payload', {}).get('cwd'); sid = obj.get('payload', {}).get('id'); break
        if provider == 'claude' and obj.get('cwd'):
            cwd = obj['cwd']; sid = obj.get('sessionId'); break
    if not isinstance(cwd, str) or not cwd.startswith('/'): return
    for obj in entries:
        content = []
        if provider == 'codex':
            payload = obj.get('payload', {})
            if obj.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'assistant' and payload.get('channel') in (None, 'commentary', 'final'):
                content = payload.get('content', [])
            if obj.get('type') == 'event_msg' and payload.get('type') in ('task_complete', 'turn_complete'):
                state = 'finished'
            elif obj.get('type') == 'event_msg' and payload.get('type') in ('task_started', 'turn_started'):
                state = 'working'
        elif obj.get('type') == 'assistant': content = obj.get('message', {}).get('content', [])
        if not isinstance(content, list): continue
        pieces = [p['text'] for p in content if isinstance(p, dict) and p.get('type') in ('text', 'output_text') and isinstance(p.get('text'), str)]
        if pieces:
            text = '\n'.join(pieces)
            raw_time = obj.get('timestamp')
            if isinstance(raw_time, str):
                try: observed = datetime.fromisoformat(raw_time.replace('Z', '+00:00')).timestamp()
                except ValueError: pass
    if not text or not text.strip(): return
    text = clean(text[:3900])
    session = 'observed-' + provider + '-' + hashlib.sha256(str(path).encode()).hexdigest()[:24]
    project = Path(cwd).name or 'Home'
    uid = hashlib.sha256((session + text).encode()).hexdigest()
    store.register(project, cwd, provider, session)
    with store.lock, store.db:
        store.db.execute('INSERT OR IGNORE INTO updates VALUES (?,?,?,?,?)', (uid, session, text, state, observed))
        store.db.execute('UPDATE sessions SET state=?,seen=? WHERE id=?', (state, observed, session))

def recent_files(home):
    cutoff = time.time() - 7 * 86400
    candidates = []
    today = datetime.now()
    for delta in range(8):
        day = today - timedelta(days=delta)
        root = home/'.codex/sessions'/day.strftime('%Y/%m/%d')
        if root.is_dir(): candidates += [(p, 'codex') for p in root.glob('*.jsonl')]
    for account in home.glob('.claude*'):
        project_root = account/'projects'
        if project_root.is_dir(): candidates += [(p, 'claude') for p in project_root.glob('*/*.jsonl')]
    recent = []
    for path, provider in candidates:
        try:
            if path.stat().st_mtime >= cutoff and not path.is_symlink(): recent.append((path, provider))
        except OSError: pass
    return sorted(recent, key=lambda pair: pair[0].stat().st_mtime, reverse=True)[:100]

def collect_recent(store, home=Path.home()):
    count = 0
    for path, provider in recent_files(home):
        try: collect_file(store, path, provider); count += 1
        except (OSError, ValueError, TypeError, KeyError): continue
    return count
