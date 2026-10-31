"""Fixed SSH transport to Sage's Spelltower; phone input is JSON stdin, never shell."""
import copy
import json
import subprocess
import threading
import time
from .store import Conflict

PREFIX = 'spelltower-'


class TransportUnavailable(Conflict):
    """A delivery acknowledgment is unknown; retry the same identity."""


def _call_remote(action, payload=None):
    request = json.dumps({'action': action, 'payload': payload or {}}, ensure_ascii=False)
    result = subprocess.run([
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=archbox',
        'spelltower',
        'cd ~/.local/share/command-helm-remote/bridge && python3 -m helm_remote.rpc'
    ], input=request, text=True, capture_output=True, timeout=9)
    if result.returncode != 0: raise TransportUnavailable('Spelltower is unreachable. No delivery is confirmed; retry with the same request.')
    response = json.loads(result.stdout)
    if not isinstance(response, dict): raise ValueError('Invalid remote response')
    if 'error' in response: raise Conflict(response['error'])
    return response

def call_remote(action, payload=None):
    try: return _call_remote(action, payload)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise TransportUnavailable('Spelltower did not confirm delivery. Retry the same request after reconnecting.') from None

class FederatedStore:
    def __init__(self, local, transport=call_remote):
        self.local = local
        self.transport = transport
        self.lock = threading.Lock()
        self.cached = None
        self.checked = 0
        self.online = False
    def poll(self):
        try:
            snapshot = self.transport('snapshot')
            with self.lock:
                self.cached = snapshot; self.checked = time.time(); self.online = True
        except (OSError, ValueError, Conflict, subprocess.TimeoutExpired):
            with self.lock: self.online = False
    def run(self):
        while True:
            self.poll()
            time.sleep(15)
    def snapshot(self):
        result = self.local.snapshot()
        for p in result['projects']:
            p['name'] += ' · MacBook'
            p['machineId'] = 'spellbook'
        with self.lock:
            remote = copy.deepcopy(self.cached)
            online = self.online and time.time() - self.checked < 45
        if remote:
            for p in remote['projects']:
                p['machineId'] = 'spelltower'
                p['id'] = PREFIX + p['id']
                p['name'] += ' · Spelltower' + ('' if online else ' · Offline')
                for s in p['sessions']:
                    s['id'] = PREFIX + s['id']
                    if not online: s['controllable'] = False
            for key in ('updates', 'questions'):
                for row in remote[key]:
                    row['id'] = PREFIX + row['id']
                    row['projectId'] = PREFIX + row['projectId']
            for key in ('projects', 'updates', 'questions'): result[key].extend(remote[key])
            result['updates'].sort(key=lambda r: r['createdAt'], reverse=True)
            result['questions'].sort(key=lambda r: r['createdAt'], reverse=True)
        return result
    def reply(self, qid, answer, reply_id):
        if not qid.startswith(PREFIX): return self.local.reply(qid, answer, reply_id)
        result = self.transport('reply', {'id': qid[len(PREFIX):], 'answer': answer, 'replyId': reply_id})
        return result
    def command(self, sid, kind, text, command_id):
        if not sid.startswith(PREFIX): return self.local.command(sid, kind, text, command_id)
        result = self.transport('command', {'id': sid[len(PREFIX):], 'kind': kind, 'text': text, 'commandId': command_id})
        return result
