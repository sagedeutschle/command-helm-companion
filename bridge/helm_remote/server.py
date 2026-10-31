"""Private phone API. TLS is required on any non-loopback listener."""
import argparse
from contextlib import ExitStack
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import ssl
import threading
import time
import sys
from urllib.parse import urlsplit, parse_qs
from .connections import Connections
from .devices import DeviceRegistry
from .terminals import TerminalManager, TerminalUnavailable
from .store import Store, Conflict
from .tasks import TARGETS, TaskStore, TaskProxy
from .remote import TransportUnavailable

DEFAULT_STATE = Path.home() / ('Library/Application Support/Command Helm Remote' if sys.platform == 'darwin' else '.local/state/command-helm-remote')

class Handler(BaseHTTPRequestHandler):
    server_version = 'HelmRemote/1'
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
    def log_message(self, *args):
        pass  # Do not log tokens, answers, URL parameters or project content.
    def send_json(self, code, value):
        body = json.dumps(value, allow_nan=False, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)
    def authorized(self):
        if 'Origin' in self.headers:
            self.send_json(403, {'error': 'Browser requests are not supported'})
            return False
        provided = self.headers.get('Authorization', '').encode()
        if not hmac.compare_digest(provided, ('Bearer '+self.server.token).encode()):
            self.send_json(401, {'error': 'Pair this device again'})
            return False
        return True
    def do_GET(self):
        if not self.authorized(): return
        if self.path in ('/v1/devices','/v1/connections','/v1/terminals') or self.path.startswith('/v1/terminals/'):
            try:
                if self.path == '/v1/devices' and self.server.devices is not None:
                    result = self.server.devices.inventory(workers=self.server.tasks.snapshot().get('workers',[]))
                elif self.path == '/v1/connections' and self.server.connections is not None:
                    result = self.server.connections.snapshot()
                elif self.path == '/v1/terminals' and self.server.terminals is not None:
                    result = self.server.terminals.sessions()
                else:
                    parts = urlsplit(self.path)
                    match = re.fullmatch(r'/v1/terminals/([a-f0-9-]{36})/output', parts.path)
                    if not match or self.server.terminals is None:
                        self.send_json(404, {'error':'Unknown endpoint'}); return
                    query = parse_qs(parts.query, keep_blank_values=True)
                    if set(query) != {'after'} or len(query['after']) != 1 or not re.fullmatch(r'[0-9]{1,19}',query['after'][0]):
                        raise ValueError('Invalid cursor')
                    result = self.server.terminals.output(match[1],int(query['after'][0]))
                self.send_json(200,result)
            except (ValueError,TypeError,RecursionError): self.send_json(400, {'error':'Invalid request'})
            except Conflict as exc: self.send_json(409, {'error':str(exc)})
            except TerminalUnavailable as exc: self.send_json(503, {'error':str(exc)})
            except OSError: self.send_json(503, {'error':'Local service unavailable'})
            return
        if self.path == '/v1/tasks':
            try: self.send_json(200, self.server.tasks.snapshot())
            except (Conflict, OSError, ValueError):
                self.send_json(503, {'error': 'Task hub unavailable; no delivery is confirmed'})
        elif self.path == '/v1/snapshot':
            snapshot = self.server.store.snapshot()
            if self.server.devices is None:
                local_identity = 'spellbook' if sys.platform == 'darwin' else 'spelltower'
            else:
                local_devices = [device['id'] for device in self.server.devices.devices.values() if device['transport'] == 'local']
                # Reserved non-device identity avoids assigning ambiguous activity
                # to an unrelated configured execution computer.
                local_identity = local_devices[0] if len(local_devices) == 1 else '__bridge__'
            for project in snapshot['projects']:
                project.setdefault('machineId', local_identity)
            self.send_json(200, snapshot)
            if self.headers.get('X-Helm-Client') == 'iphone' and time.time() - getattr(self.server, 'last_phone_log', 0) > 60:
                self.server.last_phone_log = time.time()
                print('iPhone snapshot delivered at '+time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), flush=True)
        else: self.send_json(404, {'error': 'Unknown endpoint'})
    def do_POST(self):
        if not self.authorized(): return
        match = re.fullmatch(r'/v1/questions/([a-zA-Z0-9-]+)/reply', self.path)
        control = re.fullmatch(r'/v1/sessions/([a-zA-Z0-9-]+)/commands', self.path)
        task = self.path == '/v1/tasks'
        terminal = re.fullmatch(r'/v1/terminals/([a-f0-9-]{36})/(input|resize|close)', self.path)
        terminal_start = self.path == '/v1/terminals' and self.server.terminals is not None
        connection = self.path == '/v1/connections' and self.server.connections is not None
        if terminal and self.server.terminals is None: terminal = None
        if not match and not control and not task and not terminal and not terminal_start and not connection:
            self.send_json(404, {'error': 'Unknown endpoint'}); return
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[])) != 1:
                raise ValueError('Content length required')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (65536 if task or terminal else 16384): raise ValueError('Invalid request size')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON required')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict): raise ValueError('JSON object required')
            json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
            if connection:
                result = self.server.connections.enable(payload)
            elif terminal_start:
                result = self.server.terminals.start(payload)
            elif terminal:
                if terminal[2] == 'input': result = self.server.terminals.input(terminal[1],payload)
                elif terminal[2] == 'resize': result = self.server.terminals.resize(terminal[1],payload)
                else:
                    if payload: raise ValueError('Empty close object required')
                    result = self.server.terminals.close(terminal[1])
            elif task:
                result = self.server.tasks.submit(payload)
            elif control:
                result = self.server.store.command(control[1], payload.get('kind'), payload.get('text'), payload.get('commandId'))
            else:
                result = self.server.store.reply(match[1], payload.get('answer'), payload.get('replyId'))
            self.send_json(200, result)
        except TerminalUnavailable as exc: self.send_json(503, {'error': str(exc)})
        except TransportUnavailable as exc: self.send_json(503, {'error': str(exc)})
        except Conflict as exc: self.send_json(409, {'error': str(exc)})
        except (ValueError, TypeError, RecursionError): self.send_json(400, {'error': 'Invalid request'})
        except OSError: self.send_json(503, {'error': 'Task hub unavailable; no delivery is confirmed'})

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    def __init__(self,*args,**kwargs):
        self.request_slots=threading.BoundedSemaphore(32)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.request_slots.acquire(blocking=False):
            # Bound concurrent sockets/threads, including slow TLS handshakes.
            self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:
            self.request_slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.request_slots.release()
    def server_close(self):
        super().server_close()
        if getattr(self, '_owns_tasks', False):
            self.tasks.close()
            self._owns_tasks = False

def make_server(store, token, address, tasks=None, devices=None, connections=None, terminals=None):
    if not token: raise ValueError('Token required')
    server = Server(address, Handler)
    server.store = store
    server.devices, server.connections, server.terminals = devices, connections, terminals
    server.token = token
    server._owns_tasks = tasks is None
    server.tasks = tasks if tasks is not None else TaskStore(Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent / 'tasks.sqlite', targets=set(devices.devices) | {'spellbook','spelltower'} if devices is not None else None)
    return server

def ensure_token(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, 'w') as f: f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8746)
    parser.add_argument('--tls-cert', type=Path)
    parser.add_argument('--tls-key', type=Path)
    parser.add_argument('--devices-config', type=Path, help='Absolute path to the optional companion device configuration')
    parser.add_argument('--collect-local', action='store_true', help='Read assistant text from recent local Claude and Codex sessions')
    parser.add_argument('--task-hub', choices=('local', 'spelltower'), default='local')
    parser.add_argument('--spelltower', action='store_true', help='Include Spelltower through the verified SSH alias')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not (args.tls_cert and args.tls_key):
        parser.error('Remote listeners require --tls-cert and --tls-key')
    if args.devices_config is not None and not args.devices_config.is_absolute():
        parser.error('--devices-config requires an absolute path')
    devices = DeviceRegistry(args.devices_config) if args.devices_config is not None else None
    os.umask(0o077)
    stop = threading.Event()
    def shutdown(signum, frame):
        # Interrupt serve_forever on its own thread; shutdown() here would deadlock.
        raise KeyboardInterrupt
    try:
        with ExitStack() as cleanup:
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous = signal.signal(sig, shutdown)
                    cleanup.callback(signal.signal, sig, previous)
            store = Store(args.state_dir / 'state.sqlite')
            cleanup.callback(store.close)
            targets = set(devices.devices) | set(TARGETS) if devices is not None else None
            tasks = TaskProxy() if args.task_hub == 'spelltower' else TaskStore(args.state_dir / 'tasks.sqlite', targets=targets)
            if isinstance(tasks, TaskStore): cleanup.callback(tasks.close)
            terminals = TerminalManager(args.state_dir / 'terminals', devices) if devices is not None else None
            if terminals is not None: cleanup.callback(terminals.shutdown)
            server = make_server(store, ensure_token(args.state_dir / 'phone-token'), (args.host, args.port),
                tasks=tasks, devices=devices, connections=Connections(devices) if devices is not None else None, terminals=terminals)
            cleanup.callback(server.server_close)
            if args.tls_cert and args.tls_key:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.load_cert_chain(args.tls_cert, args.tls_key)
                server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
            if args.spelltower:
                from .remote import FederatedStore
                server.store = FederatedStore(store)
                threading.Thread(target=server.store.run, daemon=True).start()
            def collect():
                from .collector import collect_recent
                while not stop.is_set():
                    collect_recent(store)
                    stop.wait(30)
            worker = threading.Thread(target=collect, daemon=True) if args.collect_local else None
            if worker:
                worker.start()
                cleanup.callback(worker.join, timeout=10)
            cleanup.callback(stop.set)
            print(f'Command Helm Remote listening on {args.host}:{server.server_port}. Pairing token stays in the private state directory.', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__': main()
