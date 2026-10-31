"""Durable destination-specific task queue. Worker operations stay off HTTP."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from .store import Conflict, clean

TARGETS = ('spellbook', 'spelltower')
LEASE_SECONDS = 90
REPORT_LIMIT = 24000


def target_name(value, targets=TARGETS):
    if not isinstance(value, str) or value not in targets:
        raise ValueError('Unknown target')
    return value


def identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Canonical UUID required')
    return value


def safe_text(value, limit=REPORT_LIMIT):
    if not isinstance(value, str):
        raise ValueError('Text required')
    value = value[:limit]
    return clean(value, limit) if value.strip() else ''


class TaskStore:
    def __init__(self, path, clock=time.time, targets=None):
        self.targets = set(TARGETS if targets is None else targets)
        from .devices import device_id
        for target in self.targets: device_id(target)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, target TEXT NOT NULL, prompt TEXT NOT NULL,
                workspace TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                delivery TEXT NOT NULL DEFAULT '', report TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS workers (
                target TEXT PRIMARY KEY, owner TEXT NOT NULL, seen REAL NOT NULL,
                state TEXT NOT NULL, task_id TEXT NOT NULL);
        ''')

    def close(self):
        with self.lock:
            self.db.close()

    def _transaction(self, operation):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result = operation()
                self.db.execute('COMMIT')
                return result
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    @staticmethod
    def _task(row):
        return dict(id=row['id'], target=row['target'], prompt=row['prompt'],
                    workspace=row['workspace'], state=row['state'], createdAt=row['created'],
                    updatedAt=row['updated'], deliveryPath=row['delivery'],
                    report=row['report'], error=row['error'])

    def _expire(self, now):
        self.db.execute('''UPDATE tasks SET state='interrupted', updated=?,
            error='Worker heartbeat expired. Execution outcome is unknown; this task will not replay.'
            WHERE state='running' AND updated < ?''', (now, now - LEASE_SECONDS))
        self.db.execute("UPDATE workers SET state='idle', task_id='' WHERE seen < ?", (now - LEASE_SECONDS,))

    def snapshot(self):
        def read():
            self._expire(self.clock())
            return {'tasks': [self._task(row) for row in self.db.execute('SELECT * FROM tasks ORDER BY created DESC,id DESC LIMIT 100')],
                    'workers': [dict(target=row['target'], seen=row['seen'], state=row['state'], taskId=row['task_id'])
                                for row in self.db.execute('SELECT * FROM workers ORDER BY target')]}
        return self._transaction(read)

    def submit(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Task object required')
        task_id = identity(payload.get('id'))
        target = target_name(payload.get('target'), self.targets)
        prompt = payload.get('prompt')
        workspace = payload.get('workspace', '')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000 or '\x00' in prompt:
            raise ValueError('Prompt must contain 1 to 8000 characters')
        if not isinstance(workspace, str) or len(workspace) > 4096 or '\x00' in workspace or (workspace and not Path(workspace).is_absolute()):
            raise ValueError('Workspace must be an absolute directory')
        def write():
            old = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if old:
                if (old['target'], old['prompt'], old['workspace']) != (target, prompt, workspace):
                    raise Conflict('Task identity already used for different input')
                return {'task': self._task(old)}
            now = self.clock()
            self.db.execute('INSERT INTO tasks (id,target,prompt,workspace,state,created,updated) VALUES (?,?,?,?,?,?,?)',
                            (task_id, target, prompt, workspace, 'queued', now, now))
            return {'task': self._task(self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())}
        return self._transaction(write)

    def _worker(self, target, owner, now):
        target_name(target, self.targets); identity(owner)
        worker = self.db.execute('SELECT * FROM workers WHERE target=?', (target,)).fetchone()
        if worker and worker['owner'] != owner and worker['seen'] >= now - LEASE_SECONDS:
            raise Conflict('Another worker owns this target')
        return worker

    def claim(self, target, owner):
        def write():
            now = self.clock()
            self._expire(now)
            worker = self._worker(target, owner, now)
            # A lost claim response must never cause its job to execute twice.
            running = self.db.execute("SELECT id FROM tasks WHERE target=? AND state='running'", (target,)).fetchone()
            if running:
                raise Conflict('Target already has a running task; recover its outcome before claiming')
            row = self.db.execute("SELECT * FROM tasks WHERE target=? AND state='queued' ORDER BY created,id LIMIT 1", (target,)).fetchone()
            task_id = row['id'] if row else ''
            self.db.execute('INSERT OR REPLACE INTO workers VALUES (?,?,?,?,?)',
                            (target, owner, now, 'working' if row else 'idle', task_id))
            if row:
                self.db.execute("UPDATE tasks SET state='running', owner=?, updated=? WHERE id=?", (owner, now, task_id))
                row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            return {'task': self._task(row) if row else None}
        return self._transaction(write)

    def heartbeat(self, target, owner, task_id=''):
        def write():
            now = self.clock()
            self._expire(now)
            worker = self._worker(target, owner, now)
            if not worker or worker['owner'] != owner or worker['task_id'] != task_id:
                raise Conflict('Worker claim is no longer current')
            if task_id:
                row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                if not row or row['state'] != 'running' or row['owner'] != owner:
                    raise Conflict('Task is no longer running')
                self.db.execute('UPDATE tasks SET updated=? WHERE id=?', (now, task_id))
            self.db.execute('UPDATE workers SET seen=? WHERE target=?', (now, target))
            return {'ok': True}
        return self._transaction(write)

    def finish(self, target, owner, task_id, state, delivery_path='', report='', error=''):
        target_name(target, self.targets); identity(owner); identity(task_id)
        if state not in ('completed', 'failed', 'interrupted'):
            raise ValueError('Terminal task state required')
        report, error = safe_text(report), safe_text(error, 2000)
        if not isinstance(delivery_path, str) or len(delivery_path) > 4096 or (delivery_path and not Path(delivery_path).is_absolute()):
            raise ValueError('Absolute delivery path required')
        if state == 'completed' and (not report or not delivery_path):
            raise ValueError('Completed delivery requires a report and path')
        def write():
            now = self.clock()
            self._expire(now)
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row['target'] != target or row['owner'] != owner:
                raise Conflict('Task claim does not belong to this worker')
            if row['state'] != 'running':
                if (row['state'], row['delivery'], row['report'], row['error']) == (state, delivery_path, report, error):
                    return {'task': self._task(row)}
                raise Conflict('Task already stopped; its execution will not replay')
            self.db.execute('UPDATE tasks SET state=?,updated=?,delivery=?,report=?,error=? WHERE id=?',
                            (state, now, delivery_path, report, error, task_id))
            self.db.execute("UPDATE workers SET seen=?,state='idle',task_id='' WHERE target=? AND owner=?", (now, target, owner))
            return {'task': self._task(self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())}
        return self._transaction(write)

    def dispatch(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError('Object required')
        if action == 'task_snapshot': return self.snapshot()
        if action == 'task_submit': return self.submit(payload)
        if action == 'task_claim': return self.claim(payload.get('target'), payload.get('workerId'))
        if action == 'task_heartbeat': return self.heartbeat(payload.get('target'), payload.get('workerId'), payload.get('taskId', ''))
        if action == 'task_finish':
            return self.finish(payload.get('target'), payload.get('workerId'), payload.get('taskId'), payload.get('state'),
                               payload.get('deliveryPath', ''), payload.get('report', ''), payload.get('error', ''))
        raise ValueError('Unknown task operation')


class TaskProxy:
    """Only the fixed trusted SSH command in remote.call_remote carries requests."""
    def __init__(self, transport=None):
        if transport is None:
            from .remote import call_remote
            transport = call_remote
        self.transport = transport

    def snapshot(self):
        return self.transport('task_snapshot')

    def submit(self, payload):
        return self.transport('task_submit', payload)

    def dispatch(self, action, payload):
        return self.transport(action, payload)
