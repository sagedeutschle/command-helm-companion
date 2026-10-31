"""Durable, session-scoped project updates and human questions. No shell execution."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

class Conflict(Exception):
    pass

STATES = {'working', 'waiting', 'idle', 'finished', 'failed'}
PROVIDERS = ('claude', 'codex', 'castra')

def clean(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Text is empty or too long')
    value = ''.join(c for c in value.strip() if c in '\n\t' or ord(c) >= 32)
    value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', value)
    value = re.sub(r'\b(?:sk-(?:proj-)?|ghp_|github_pat_)[A-Za-z0-9_-]{8,}', '[redacted]', value)
    value = re.sub(r'(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*\S+', r'\1=[redacted]', value)
    return value

class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=DELETE;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, path TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), provider TEXT, state TEXT, seen REAL);
        CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), text TEXT, state TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), text TEXT, options TEXT, state TEXT, created REAL, expires REAL, answer TEXT, reply_id TEXT);
        CREATE TABLE IF NOT EXISTS controllers(session_id TEXT PRIMARY KEY REFERENCES sessions(id), seen REAL);
        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), kind TEXT, text TEXT, state TEXT, created REAL);
        ''')
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def heartbeat(self, sid):
        with self.lock, self.db:
            self._session(sid)
            self.db.execute('INSERT OR REPLACE INTO controllers VALUES (?,?)', (sid, time.time()))

    def disconnect_controller(self, sid):
        with self.lock, self.db:
            self.db.execute('DELETE FROM controllers WHERE session_id=?', (sid,))

    def command(self, sid, kind, text, command_id):
        if kind not in ('guide', 'pause', 'resume'): raise ValueError('Unknown command')
        text = clean(text)
        command_id = clean(command_id, 120)
        with self.lock, self.db:
            old = self.db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            if old:
                if (old['session_id'], old['kind'], old['text']) != (sid, kind, text): raise Conflict('Command identity already used')
                return {'state': old['state']}
            live = self.db.execute('SELECT seen FROM controllers WHERE session_id=?', (sid,)).fetchone()
            if not live or time.time() - live['seen'] > 45: raise Conflict('Agent control connection is offline')
            self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?)', (command_id, sid, kind, text, 'queued', time.time()))
            return {'state': 'queued'}

    def checkpoint(self, sid, quiet=False):
        with self.lock, self.db:
            self._session(sid)
            last = self.db.execute("SELECT kind FROM commands WHERE session_id=? AND kind IN ('pause','resume') ORDER BY created DESC,rowid DESC LIMIT 1", (sid,)).fetchone()
            paused = bool(last and last['kind'] == 'pause')
            if paused:
                self.db.execute("UPDATE commands SET state='paused at checkpoint' WHERE session_id=? AND kind='pause' AND state='queued'", (sid,))
            if quiet and not paused:
                # A background poll (Castra's phone link looks every few seconds) stays visible without claiming work.
                self.db.execute('UPDATE sessions SET seen=? WHERE id=?', (time.time(), sid))
            else:
                self.db.execute('UPDATE sessions SET state=?,seen=? WHERE id=?', ('waiting' if paused else 'working', time.time(), sid))
            rows = [dict(r) for r in self.db.execute("SELECT id,kind,text FROM commands WHERE session_id=? AND state IN ('queued','paused at checkpoint') ORDER BY created,rowid", (sid,))]
            return {'paused': paused, 'directions': rows}

    def acknowledge_commands(self, sid, ids):
        with self.lock, self.db:
            for cid in ids:
                self.db.execute("UPDATE commands SET state='sent to agent connection' WHERE id=? AND session_id=?", (cid, sid))

    def register(self, name, path, provider, session_id):
        name = clean(name, 120)
        if provider not in PROVIDERS: raise ValueError('Unknown provider')
        if not isinstance(path, str) or not path.startswith('/') or len(path) > 2000:
            raise ValueError('An absolute project path is required')
        session_id = clean(session_id, 120)
        project_id = hashlib.sha256(path.encode()).hexdigest()[:24]
        with self.lock, self.db:
            old = self.db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            if old and (old['project_id'] != project_id or old['provider'] != provider):
                raise Conflict('Session identity cannot change projects or providers')
            self.db.execute('INSERT OR IGNORE INTO projects VALUES (?,?,?)', (project_id, name, path))
            self.db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?)', (session_id, project_id, provider, 'idle', time.time()))
            self.db.execute('UPDATE sessions SET seen=? WHERE id=?', (time.time(), session_id))
        return session_id

    def _session(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row: raise Conflict('Unknown session')
        return row

    def update(self, sid, state, text):
        if state not in STATES: raise ValueError('Unknown status')
        text = clean(text)
        with self.lock, self.db:
            self._session(sid)
            now = time.time()
            self.db.execute('UPDATE sessions SET state=?,seen=? WHERE id=?', (state, now, sid))
            self.db.execute('INSERT INTO updates VALUES (?,?,?,?,?)', (str(uuid.uuid4()), sid, text, state, now))
            # Keep the newest 2000 status entries. Questions retain their delivery history.
            self.db.execute('DELETE FROM updates WHERE id NOT IN (SELECT id FROM updates ORDER BY created DESC LIMIT 2000)')

    def ask(self, sid, text, options=None, ttl=300):
        text = clean(text)
        if not isinstance(options or [], list) or len(options or []) > 6: raise ValueError('Use at most six options')
        options = [clean(v, 160) for v in (options or [])]
        if not isinstance(ttl, (int, float)) or not 0 <= ttl <= 86400: raise ValueError('Invalid timeout')
        now = time.time()
        qid = str(uuid.uuid4())
        with self.lock, self.db:
            self._session(sid)
            self.db.execute('INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)', (qid, sid, text, json.dumps(options), 'pending', now, now + ttl, None, None))
            self.db.execute('UPDATE sessions SET state=?,seen=? WHERE id=?', ('waiting', now, sid))
        return {'id': qid, 'expiresAt': now + ttl}

    def _expire(self):
        self.db.execute("UPDATE questions SET state='expired' WHERE state IN ('pending','answered') AND expires<=?", (time.time(),))

    def _question(self, qid):
        row = self.db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
        if not row: raise Conflict('Question no longer exists')
        return row

    def reply(self, qid, answer, reply_id):
        answer = clean(answer)
        reply_id = clean(reply_id, 120)
        with self.lock:
            # Commit expiry even when the subsequent reply is rejected.
            with self.db: self._expire()
            with self.db:
                q = self._question(qid)
                if q['reply_id'] == reply_id and q['answer'] == answer:
                    return {'state': q['state']}
                if q['state'] != 'pending': raise Conflict('Question already answered, expired, or cancelled')
                self.db.execute("UPDATE questions SET state='answered',answer=?,reply_id=? WHERE id=?", (answer, reply_id, qid))
                return {'state': 'answered'}

    def receive(self, sid, qid):
        with self.lock:
            with self.db: self._expire()
            with self.db:
                q = self._question(qid)
                if q['session_id'] != sid: raise Conflict('Question belongs to another session')
                self.db.execute('UPDATE sessions SET seen=? WHERE id=?', (time.time(), sid))
                if q['state'] == 'answered':
                    return {'state': 'answered', 'answer': q['answer']}
                return {'state': q['state'], 'answer': q['answer']}

    def acknowledge(self, sid, qid):
        with self.lock, self.db:
            q = self._question(qid)
            if q['session_id'] != sid: raise Conflict('Question belongs to another session')
            self.db.execute("UPDATE questions SET state='delivered' WHERE id=? AND state='answered'", (qid,))
            self.db.execute("UPDATE sessions SET state='working' WHERE id=?", (sid,))

    def cancel(self, sid, qid):
        with self.lock, self.db:
            q = self._question(qid)
            if q['session_id'] != sid: raise Conflict('Question belongs to another session')
            self.db.execute("UPDATE questions SET state='cancelled' WHERE id=? AND state IN ('pending','answered')", (qid,))

    def snapshot(self):
        with self.lock, self.db:
            self._expire()
            now = time.time()
            projects = []
            for p in self.db.execute('SELECT * FROM projects ORDER BY name COLLATE NOCASE'):
                sessions = [dict(s) for s in self.db.execute('SELECT id,provider,state,seen FROM sessions WHERE project_id=? ORDER BY seen DESC', (p['id'],))]
                for session in sessions:
                    live = self.db.execute('SELECT seen FROM controllers WHERE session_id=?', (session['id'],)).fetchone()
                    session['controllable'] = bool(live and now - live['seen'] < 45)
                    session['commands'] = [dict(r) for r in self.db.execute('SELECT id,kind,text,state FROM commands WHERE session_id=? ORDER BY created DESC,rowid DESC LIMIT 20', (session['id'],))]
                projects.append({'id': p['id'], 'name': p['name'], 'sessions': sessions})
            updates = [dict(r) for r in self.db.execute('''SELECT u.id, s.project_id AS projectId, s.provider, u.text, u.state, u.created AS createdAt FROM updates u JOIN sessions s ON s.id=u.session_id ORDER BY u.created DESC LIMIT 300''')]
            questions = []
            for r in self.db.execute('''SELECT q.id, s.project_id AS projectId, s.provider, q.text, q.options, q.state, q.created AS createdAt, q.expires AS expiresAt, q.answer FROM questions q JOIN sessions s ON s.id=q.session_id ORDER BY q.created DESC LIMIT 500'''):
                q = dict(r)
                q['options'] = json.loads(q['options'])
                questions.append(q)
            return {'generatedAt': now, 'projects': projects, 'updates': updates, 'questions': questions}
