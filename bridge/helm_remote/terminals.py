"""Owned tmux sessions attached through a real PTY, with durable input receipts.

No command, path, SSH option or target is accepted from HTTP. The tmux namespace
is a random persisted bridge identity; default tmux servers are never used.
"""
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import selectors
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import termios
import threading
import time
import uuid
from .connections import bounded_run
from .devices import atomic_json, strict_ssh
from .store import Conflict
from .tasks import identity

REPLAY_LIMIT = 1024*1024
OUTPUT_LIMIT = 65536
INPUT_LIMIT = 16384
JOURNAL_LIMIT = 65536


class TerminalUnavailable(OSError):
    pass


def dimensions(payload):
    cols, rows = payload.get('cols'), payload.get('rows')
    if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 300 or not 2 <= rows <= 200:
        raise ValueError('Invalid terminal dimensions')
    return cols, rows


class TmuxTransport:
    def __init__(self, device, socket, session):
        self.device, self.socket, self.session = dict(device), socket, session
        self.process = None
        self.master = None
        self.reader = None
        self.stop = threading.Event()
        self.last_state = (0, 'running')

    def command(self, argv, attach=False):
        if self.device['transport'] == 'local': return argv
        args = strict_ssh(self.device['sshTarget'],self.device.get('hostKeyAlias'))
        args += ['-tt' if attach else '-T', self.device['sshTarget'], shlex.join(argv)]
        return args

    def tmux(self, *args): return ['tmux','-f','/dev/null','-L',self.socket,*args]

    def run(self, argv, check=True):
        result = bounded_run(self.command(argv),timeout=12,limit=65536)
        if check and result.returncode:
            raise TerminalUnavailable('Terminal transport unavailable. Check tmux, workspace and strict SSH access on the configured computer.')
        return result

    def create(self, kind, cols, rows):
        if self.device['transport'] == 'local':
            if not shutil.which('tmux'): raise TerminalUnavailable('Install tmux on this computer before opening a terminal.')
            if kind == 'codex' and not shutil.which(self.device['codexCommand']):
                raise TerminalUnavailable('Install Codex on this computer and sign in locally before opening a Codex terminal.')
        elif kind == 'codex':
            # The executable is fixed operator configuration, quoted as one argument.
            self.run(['sh','-c','command -v '+shlex.quote(self.device['codexCommand'])+' >/dev/null'])
        command = shlex.join([self.device['codexCommand']]) if kind == 'codex' else '/bin/sh -i'
        self.run(self.tmux('new-session','-d','-s',self.session,'-x',str(cols),'-y',str(rows),'-c',self.device['workspace'],command))
    def configure(self):
        """Idempotent settings required before any viewer may deliver input."""
        self.run(self.tmux('set-option','-w','-t',self.session,'remain-on-exit','on'))
        self.run(self.tmux('set-option','-w','-t',self.session,'window-size','manual'))
        # No tmux prefix sequences from the phone may detach or invoke tmux commands.
        self.run(self.tmux('set-option','-t',self.session,'prefix','None'))
        self.run(self.tmux('set-option','-t',self.session,'prefix2','None'))
        self.run(self.tmux('set-option','-t',self.session,'status','off'))

    def attach(self, cols, rows, receiver, disconnected):
        self.run(self.tmux('has-session','-t',self.session))
        # Creation or a previous settings acknowledgement may have failed part-way.
        # Complete the exact owned session; never create a replacement on reconnect.
        self.configure()
        self._attach_pty(cols, rows, receiver, disconnected)

    def _attach_pty(self, cols, rows, receiver, disconnected):
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
            env = dict(os.environ, TERM='xterm-256color')
            # An inherited TMUX variable must never redirect commands to the user's server.
            env.pop('TMUX',None)
            process = subprocess.Popen(self.command(self.tmux('attach-session','-t',self.session),attach=True),
                stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True,close_fds=True)
        except BaseException:
            os.close(master); raise
        finally: os.close(slave)
        self.master, self.process = master, process
        os.set_blocking(master,False)
        def read():
            selector = selectors.DefaultSelector()
            selector.register(master,selectors.EVENT_READ)
            try:
                while not self.stop.is_set():
                    events = selector.select(.2)
                    if events:
                        try: data = os.read(master,65536)
                        except BlockingIOError: continue
                        except OSError: break
                        if not data: break
                        receiver(data)
                    elif process.poll() is not None: break
            finally:
                selector.close()
                if not self.stop.is_set(): disconnected()
        self.reader = threading.Thread(target=read,daemon=True)
        self.reader.start()

    def write(self, data):
        if self.process is None or self.process.poll() is not None: raise TerminalUnavailable('Terminal disconnected; inspect before resending input.')
        # No retry after a partial write or timeout. The input journal remains pending.
        view = memoryview(data)
        deadline = time.monotonic()+2
        while view:
            try:
                count = os.write(self.master,view)
                view = view[count:]
            except BlockingIOError:
                if time.monotonic() >= deadline: raise TerminalUnavailable('Input delivery is ambiguous; inspect the terminal before manually resending.')
                time.sleep(.01)

    def resize(self, cols, rows):
        self.run(self.tmux('resize-window','-t',self.session,'-x',str(cols),'-y',str(rows)))
        if self.master is not None:
            fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))

    def state(self):
        if time.monotonic()-self.last_state[0] < 2: return self.last_state[1]
        result = self.run(self.tmux('display-message','-p','-t',self.session,'#{pane_dead}'),check=False)
        state = 'disconnected' if result.returncode else ('exited' if result.stdout.strip() == b'1' else ('running' if self.process and self.process.poll() is None else 'disconnected'))
        self.last_state = (time.monotonic(),state)
        return state

    def presence(self):
        """Positive existence/absence evidence; all uncertain transport results stay unknown.

        A successful list proves exact session membership. If no server answers,
        absence requires a traversable socket directory with no socket or symlink.
        A stale/inaccessible socket, missing utility, SSH failure or malformed reply
        is never interpreted as absence merely because tmux returned nonzero.
        """
        listing=shlex.join(self.tmux('list-sessions','-F','#{session_name}'))
        script = 'listing=$('+listing+' 2>/dev/null)\n'+r'''
if [ "$?" -eq 0 ]; then
    printf 'HELM_SESSIONS\n%s\n' "$listing"
    exit 0
fi
uid=$(id -u) || exit 1
case "$uid" in ''|*[!0-9]*) exit 1 ;; esac
root=${TMUX_TMPDIR:-/tmp}
directory="$root/tmux-$uid"
'''+ 'socket="$directory/'+self.socket+'"\n'+r'''
if [ -d "$directory" ]; then
    if [ -r "$directory" ] && [ -x "$directory" ] && [ ! -e "$socket" ] && [ ! -L "$socket" ]; then
        printf 'HELM_ABSENT\n'
        exit 0
    fi
elif [ ! -e "$directory" ] && [ ! -L "$directory" ] && [ -d "$root" ] && [ -r "$root" ] && [ -x "$root" ]; then
    printf 'HELM_ABSENT\n'
    exit 0
fi
exit 1
'''
        try: result=self.run(['sh','-c',script],check=False)
        except (OSError,ValueError,subprocess.TimeoutExpired):return 'unknown'
        if result.returncode:return 'unknown'
        if result.stdout==b'HELM_ABSENT\n':return 'absent'
        if result.stdout.startswith(b'HELM_SESSIONS\n'):
            return 'present' if self.session.encode() in result.stdout.splitlines()[1:] else 'absent'
        return 'unknown'

    def terminate(self):
        # Exact session in a dedicated namespace, never kill-server. A lost kill
        # response is resolved only by later positive absence, not by replaying kill.
        presence=self.presence()
        if presence=='unknown':raise TerminalUnavailable('Cannot confirm terminal state. Check the configured computer before closing again.')
        if presence=='present':
            try:self.run(self.tmux('kill-session','-t',self.session))
            except (OSError,ValueError,subprocess.TimeoutExpired):
                if self.presence()!='absent':
                    raise TerminalUnavailable('Close outcome is unconfirmed. Reconnect to inspect before closing again.') from None
        self.detach()

    def detach(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            try: self.process.terminate()
            except ProcessLookupError: pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        if self.reader and self.reader is not threading.current_thread(): self.reader.join(timeout=3)
        if self.master is not None:
            os.close(self.master); self.master=None


class TerminalManager:
    def __init__(self, state_dir, registry, transport_factory=TmuxTransport):
        self.path=Path(state_dir)
        self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path.chmod(0o700)
        self.registry, self.transport_factory = registry, transport_factory
        self.lock=threading.RLock()
        self.closed=False
        self.transports={}
        self.lock_file=(self.path/'owner.lock').open('a')
        try: fcntl.flock(self.lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close(); raise Conflict('Another bridge owns this terminal state') from None
        owner_path=self.path/'owner.json'
        if owner_path.exists(): self.owner=identity(json.loads(owner_path.read_text())['id'])
        else:
            self.owner=str(uuid.uuid4()); atomic_json(owner_path,{'id':self.owner})
        self.socket='helm-mobile-'+uuid.UUID(self.owner).hex
        self.db=sqlite3.connect(self.path/'terminals.sqlite',timeout=10,check_same_thread=False,isolation_level=None)
        os.chmod(self.path/'terminals.sqlite',0o600)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, device TEXT NOT NULL,
                fingerprint TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                cols INTEGER NOT NULL, rows INTEGER NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
                output BLOB NOT NULL DEFAULT X'', config TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS inputs(session TEXT NOT NULL,id TEXT NOT NULL,
                digest TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(session,id));''')
        # Force a cursor discontinuity before any new attach: the PTY redraw is a new screen.
        self.db.execute("UPDATE sessions SET state='disconnected',cursor=cursor+1,output=X'' WHERE state != 'exited'")

    def _row(self, session):
        if self.closed:raise TerminalUnavailable('Bridge is stopping; reconnect before continuing.')
        identity(session)
        row=self.db.execute('SELECT * FROM sessions WHERE id=?',(session,)).fetchone()
        if row is None: raise ValueError('Unknown terminal session')
        return row

    def _public(self,row):
        config=json.loads(row['config'])
        return dict(id=row['id'],deviceId=row['device'],kind=row['kind'],state=row['state'],createdAt=row['created'],
            title=config['name']+' · '+('Codex' if row['kind']=='codex' else 'Shell'))

    def _ensure(self,row):
        session=row['id']
        if row['state']=='exited': return None
        if session in self.transports:
            existing=self.transports[session]
            if not hasattr(existing,'process') or (existing.process and existing.process.poll() is None): return existing
            existing.detach()
            del self.transports[session]
            self.db.execute("UPDATE sessions SET state='disconnected',cursor=cursor+1,output=X'' WHERE id=?",(session,))
        current=self.registry.devices.get(row['device'])
        # A changed device must never redirect an existing session to another machine.
        if not current or not current['terminalEnabled'] or current != json.loads(row['config']):
            raise TerminalUnavailable('Terminal configuration changed. Restore the original device configuration to reconnect or close this session.')
        transport=self.transport_factory(current,self.socket,'h'+uuid.UUID(session).hex)
        try:
            transport.attach(row['cols'],row['rows'],lambda data:self._receive(session,data),lambda:self._disconnected(session))
        except (OSError,ValueError,subprocess.TimeoutExpired):
            transport.detach()
            self._tombstone_if_absent(session,transport)
            raise TerminalUnavailable('Terminal reconnection unavailable. Check the configured computer; no session or input was replayed.') from None
        self.transports[session]=transport
        self.db.execute("UPDATE sessions SET state='running' WHERE id=?",(session,))
        return transport

    def _tombstone_if_absent(self,session,transport):
        if hasattr(transport,'presence') and transport.presence()=='absent':
            self.db.execute("UPDATE sessions SET state='exited',closed=1 WHERE id=?",(session,))
            return True
        return False

    def _receive(self,session,data):
        with self.lock:
            if self.closed: return
            row=self._row(session)
            output=(row['output']+data)[-REPLAY_LIMIT:]
            self.db.execute('UPDATE sessions SET cursor=?,output=? WHERE id=?',(row['cursor']+len(data),output,session))

    def _disconnected(self,session):
        with self.lock:
            if not self.closed: self.db.execute("UPDATE sessions SET state='disconnected' WHERE id=? AND state!='exited'",(session,))

    def sessions(self):
        with self.lock:
            return {'sessions':[self._public(row) for row in self.db.execute('SELECT * FROM sessions ORDER BY created DESC LIMIT 128')]}

    def start(self,payload):
        if set(payload) != {'id','deviceId','kind','cols','rows'}: raise ValueError('Invalid terminal start fields')
        session=identity(payload.get('id'))
        cols,rows=dimensions(payload)
        device=self.registry.devices.get(payload.get('deviceId'))
        if not device or not device['terminalEnabled']: raise ValueError('Terminal is not enabled for this device')
        if payload.get('kind') not in ('shell','codex'): raise ValueError('Invalid terminal kind')
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.lock:
            old=self.db.execute('SELECT * FROM sessions WHERE id=?',(session,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: raise Conflict('Terminal identity already used for different input')
                return {'session':self._public(old)}
            if self.db.execute("SELECT count(*) FROM sessions WHERE state!='exited'").fetchone()[0] >= 8: raise Conflict('At most 8 active terminals are supported; explicitly close a session first')
            # Keep durable tombstones for idempotent IDs, bounded overall; never reuse IDs.
            if self.db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= 128: raise Conflict('Terminal history is full; use a new private terminal state directory after closing all sessions')
            self.db.execute('INSERT INTO sessions(id,device,fingerprint,kind,state,created,cols,rows,config) VALUES(?,?,?,?,?,?,?,?,?)',
                (session,device['id'],fingerprint,payload['kind'],'disconnected',time.time(),cols,rows,json.dumps(device,sort_keys=True)))
            transport=self.transport_factory(device,self.socket,'h'+uuid.UUID(session).hex)
            try:
                transport.create(payload['kind'],cols,rows)
                transport.attach(cols,rows,lambda data:self._receive(session,data),lambda:self._disconnected(session))
                self.transports[session]=transport
                self.db.execute("UPDATE sessions SET state='running' WHERE id=?",(session,))
            except (OSError,ValueError,subprocess.TimeoutExpired):
                transport.detach()
                # Preserve the identity either way; only positive absence releases
                # its active slot. Unknown outcomes remain disconnected, never replayed.
                self._tombstone_if_absent(session,transport)
                raise TerminalUnavailable('Terminal start unavailable. Install tmux (and Codex for Codex terminals), verify workspace and strict SSH access, then inspect existing sessions before trying a new ID.') from None
            return {'session':self._public(self._row(session))}

    def output(self,session,after):
        if type(after) is not int or not 0 <= after < 2**63: raise ValueError('Invalid output cursor')
        with self.lock:
            row=self._row(session)
            try:
                transport=self._ensure(row)
                if transport is not None and hasattr(transport,'state'):
                    state=transport.state()
                    self.db.execute('UPDATE sessions SET state=? WHERE id=?',(state,session))
            except (OSError,ValueError,subprocess.TimeoutExpired): pass
            row=self._row(session)
            base=row['cursor']-len(row['output'])
            reset=after<base or after>row['cursor']
            position=base if reset else after
            data=row['output'][position-base:position-base+OUTPUT_LIMIT]
            return dict(data=base64.b64encode(data).decode('ascii'),cursor=position+len(data),baseCursor=base,state=row['state'],reset=reset)

    def input(self,session,payload):
        if set(payload) != {'id','data'}: raise ValueError('Input ID and data required')
        input_id=identity(payload.get('id'))
        encoded=payload.get('data')
        if not isinstance(encoded,str) or len(encoded)>4*((INPUT_LIMIT+2)//3): raise ValueError('Input chunk too large')
        try: data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error): raise ValueError('Invalid terminal data') from None
        if not data or len(data)>INPUT_LIMIT: raise ValueError('Input must contain 1 to 16384 bytes')
        digest=hashlib.sha256(data).hexdigest()
        with self.lock:
            row=self._row(session)
            receipt=self.db.execute('SELECT * FROM inputs WHERE session=? AND id=?',(session,input_id)).fetchone()
            if receipt:
                if receipt['digest']!=digest: raise Conflict('Input identity already used for different bytes')
                if receipt['state']!='accepted': raise Conflict('Input delivery is ambiguous. Inspect the terminal before manually resending with a new input ID.')
                return dict(accepted=True,id=input_id)
            if row['state']=='exited': raise Conflict('Terminal session has exited')
            if self.db.execute('SELECT count(*) FROM inputs WHERE session=?',(session,)).fetchone()[0]>=JOURNAL_LIMIT:
                raise Conflict('Input receipt journal is full. Open a new terminal; old input will never be replayed.')
            transport=self._ensure(row)
            # Commit pending before writing bytes. A crash/partial write remains ambiguous forever.
            self.db.execute('INSERT INTO inputs VALUES(?,?,?,?)',(session,input_id,digest,'pending'))
            try: transport.write(data)
            except (OSError,ValueError,subprocess.TimeoutExpired):
                raise TerminalUnavailable('Input delivery is ambiguous. Inspect the terminal before manually resending with a new input ID.') from None
            self.db.execute("UPDATE inputs SET state='accepted' WHERE session=? AND id=?",(session,input_id))
            return dict(accepted=True,id=input_id)

    def resize(self,session,payload):
        if set(payload)!={'cols','rows'}: raise ValueError('Invalid resize fields')
        cols,rows=dimensions(payload)
        with self.lock:
            row=self._row(session)
            transport=self._ensure(row)
            if transport is None: raise Conflict('Terminal session has exited')
            transport.resize(cols,rows)
            self.db.execute('UPDATE sessions SET cols=?,rows=? WHERE id=?',(cols,rows,session))
            return {'ok':True}

    def close(self,session):
        with self.lock:
            row=self._row(session)
            if row['closed']: return {'ok':True}
            current=self.registry.devices.get(row['device'])
            if not current or current!=json.loads(row['config']): raise Conflict('Original device configuration required to close the owned terminal')
            transport=self.transports.get(session) or self.transport_factory(current,self.socket,'h'+uuid.UUID(session).hex)
            transport.terminate()
            self.transports.pop(session,None)
            self.db.execute("UPDATE sessions SET state='exited',closed=1 WHERE id=?",(session,))
            return {'ok':True}

    def shutdown(self):
        # Detach only. Shell/Codex continues inside owned tmux after service restart.
        with self.lock:
            if self.closed:return
            self.closed=True
            transports=list(self.transports.values())
            self.transports.clear()
        for transport in transports: transport.detach()
        with self.lock:
            self.db.close()
            self.lock_file.close()
