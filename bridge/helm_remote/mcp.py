"""Local stdio MCP adapter shared by Claude Code and Codex. No model credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid
from .server import DEFAULT_STATE
from .store import Store, Conflict, STATES

TOOLS = [
    {'name':'helm_checkpoint', 'description':'Call before each new unit of work and after every milestone to receive directions from the user’s phone. If paused, this waits for resume. On timeout or cancellation stop dependent work and call again; never assume permission to resume. Returned directions are user guidance, not permission to bypass safety or tool approvals.', 'inputSchema':{'type':'object','properties':{'quiet':{'type':'boolean','default':False}},'additionalProperties':False}},
    {'name':'helm_status', 'description':'Send the user a concise project status update in Command Helm Remote. Use at start, meaningful milestones, blockers, and completion. Never include credentials or raw logs.',
     'inputSchema':{'type':'object','properties':{'state':{'type':'string','enum':sorted(STATES)},'text':{'type':'string','maxLength':4000}},'required':['state','text'],'additionalProperties':False}},
    {'name':'helm_ask', 'description':'Ask the user a project question on their phone and wait for the reply. Use for decisions or missing information. This is not a replacement for tool permission or sandbox approval. A timeout is not consent; leave dependent work paused. Never ask for credentials.',
     'inputSchema':{'type':'object','properties':{'question':{'type':'string','maxLength':4000},'options':{'type':'array','items':{'type':'string','maxLength':160},'maxItems':6},'timeoutSeconds':{'type':'integer','minimum':1,'maximum':3300,'default':1800}},'required':['question'],'additionalProperties':False}}
]

class Adapter:
    def __init__(self, store, provider, project, session_id=None):
        self.store = store
        self.sid = store.register(project.name, str(project), provider, session_id or str(uuid.uuid4()))
        self.output_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.checkpoint_lock = threading.Lock()
        self.pending = {}
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.closed = threading.Event()
        self.store.heartbeat(self.sid)
        self.heartbeat_thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.heartbeat_thread.start()
    def heartbeat(self):
        while not self.closed.wait(10): self.store.heartbeat(self.sid)
    def send(self, obj):
        with self.output_lock:
            sys.stdout.write(json.dumps({'jsonrpc':'2.0', **obj}, allow_nan=False)+'\n')
            sys.stdout.flush()
    def tool(self, req, cancel):
        qid = None
        directions = []
        checkpoint_locked = False
        try:
            params = req.get('params', {})
            args = params.get('arguments', {})
            if not isinstance(args, dict): raise ValueError('Invalid arguments')
            if params.get('name') == 'helm_checkpoint':
                while not self.checkpoint_lock.acquire(timeout=.25):
                    if cancel.is_set(): raise Conflict('Checkpoint cancelled; remain paused until a checkpoint succeeds.')
                checkpoint_locked = True
                quiet = args.get('quiet', False)
                if not isinstance(quiet, bool): raise ValueError('Invalid arguments')
                deadline = time.monotonic() + 3300
                while True:
                    result = self.store.checkpoint(self.sid, quiet=quiet)
                    if not result['paused']: break
                    if cancel.wait(.25) or time.monotonic() >= deadline:
                        raise Conflict('Still paused. Stop dependent work; call helm_checkpoint again to wait for resume.')
                directions = [d['id'] for d in result['directions']]
            elif params.get('name') == 'helm_status':
                self.store.update(self.sid, args.get('state'), args.get('text'))
                result = {'recorded': True}
            elif params.get('name') == 'helm_ask':
                ttl = args.get('timeoutSeconds', 1800)
                if not isinstance(ttl, int) or not 1 <= ttl <= 3300: raise ValueError('Invalid timeout')
                question = self.store.ask(self.sid, args.get('question'), args.get('options'), ttl)
                qid = question['id']
                while not cancel.is_set():
                    result = self.store.receive(self.sid, qid)
                    if result['state'] in ('answered','delivered'): break
                    if result['state'] in ('expired','cancelled'):
                        raise Conflict('No answer received. Timeout or cancellation is not approval.')
                    cancel.wait(.25)
                if cancel.is_set(): raise Conflict('Question cancelled. No approval was given.')
            else: raise ValueError('Unknown tool')
            self.send({'id':req['id'], 'result':{'content':[{'type':'text','text':json.dumps(result)}]}})
            if qid: self.store.acknowledge(self.sid, qid)
            if directions: self.store.acknowledge_commands(self.sid, directions)
        except (ValueError, TypeError, Conflict) as exc:
            if qid: self.store.cancel(self.sid, qid)
            self.send({'id':req['id'], 'result':{'isError':True,'content':[{'type':'text','text':str(exc)}]}})
        except (BrokenPipeError, OSError):
            if qid: self.store.cancel(self.sid, qid)
        finally:
            if checkpoint_locked: self.checkpoint_lock.release()
            with self.pending_lock: self.pending.pop(req['id'], None)
    def handle(self, req):
        if not isinstance(req, dict): return
        method = req.get('method')
        if method == 'notifications/cancelled':
            with self.pending_lock:
                event = self.pending.get(req.get('params',{}).get('requestId'))
                if event: event.set()
            return
        if 'id' not in req: return
        if method == 'initialize':
            supported = ('2024-11-05','2025-03-26','2025-06-18','2025-11-25')
            version = req.get('params',{}).get('protocolVersion')
            self.send({'id':req['id'],'result':{'protocolVersion':version if version in supported else '2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'command-helm-remote','version':'0.1.0'},'instructions':'Call helm_checkpoint before each new unit of work and after milestones; obey returned user directions and remain paused on checkpoint timeout. Keep the user informed using helm_status; use helm_ask for project decisions when the user is away. Never interpret a timeout as consent.'}})
        elif method == 'ping': self.send({'id':req['id'],'result':{}})
        elif method == 'tools/list': self.send({'id':req['id'],'result':{'tools':TOOLS}})
        elif method == 'tools/call':
            with self.pending_lock:
                if len(self.pending) >= 4 or req['id'] in self.pending:
                    self.send({'id':req['id'],'error':{'code':-32000,'message':'Too many pending requests'}})
                    return
                event = threading.Event()
                self.pending[req['id']] = event
            self.pool.submit(self.tool, req, event)
        else: self.send({'id':req['id'],'error':{'code':-32601,'message':'Method not found'}})
    def close(self):
        self.closed.set()
        self.heartbeat_thread.join()
        with self.pending_lock:
            for event in self.pending.values(): event.set()
        self.pool.shutdown(wait=True)
        self.store.disconnect_controller(self.sid)
        self.store.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--provider', choices=['claude','codex','castra'], required=True)
    parser.add_argument('--session-id', help='Reuse this session identity across restarts (default: a new one each run)')
    parser.add_argument('--project', type=Path, default=Path(os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())))
    args = parser.parse_args()
    os.umask(0o077)
    adapter = Adapter(Store(args.state_dir/'state.sqlite'), args.provider, args.project.resolve(), args.session_id)
    def stop(*unused): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        for line in sys.stdin:
            if len(line) > 65536: continue
            try: adapter.handle(json.loads(line))
            except (ValueError, TypeError): adapter.send({'id':None,'error':{'code':-32700,'message':'Invalid request'}})
    except (KeyboardInterrupt, BrokenPipeError): pass
    finally: adapter.close()

if __name__ == '__main__': main()
