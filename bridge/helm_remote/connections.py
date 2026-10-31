"""Explicit read-only CLI discovery. Never return raw CLI errors or credentials."""
import ipaddress
import json
import selectors
import subprocess
import time
import threading
from .store import clean


def bounded_run(argv, timeout=6, limit=1024*1024, input_data=None):
    """Bound wall time, stdin and captured bytes, discarding stderr entirely."""
    import os
    if input_data is not None and (not isinstance(input_data,bytes) or len(input_data)>524288):
        raise ValueError('Command input too large')
    process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'output')
    pending = memoryview(input_data or b'')
    if process.stdin:
        os.set_blocking(process.stdin.fileno(),False)
        if pending: selector.register(process.stdin,selectors.EVENT_WRITE,'input')
        else: process.stdin.close()
    deadline = time.monotonic()+timeout
    try:
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError('Status command timed out')
            events = selector.select(min(remaining, .2))
            eof=False
            for key,_ in events:
                if key.data=='input':
                    try: count=os.write(process.stdin.fileno(),pending[:65536])
                    except BlockingIOError:continue
                    pending=pending[count:]
                    if not pending:
                        selector.unregister(process.stdin);process.stdin.close()
                else:
                    data=process.stdout.read1(65536)
                    if not data:eof=True;break
                    output.extend(data)
                    if len(output)>limit:raise ValueError('Status output too large')
            if eof:break
        process.wait(timeout=max(.01, deadline-time.monotonic()))
        return subprocess.CompletedProcess(argv, process.returncode, bytes(output))
    finally:
        selector.close()
        if process.poll() is None: process.kill()
        process.wait()
        process.stdout.close()
        if process.stdin and not process.stdin.closed:process.stdin.close()


def label(value, limit=120):
    # Only expected presentation fields, with control sequences excluded.
    text=''.join(c for c in value if c.isprintable())[:limit] if isinstance(value,str) else ''
    return clean(text,limit) if text.strip() else ''


class Connections:
    def __init__(self, registry, runner=bounded_run):
        self.registry, self.runner = registry, runner
        self.lock=threading.Lock()
        self.cached=None
        self.checked=0
        self.cache_key=None

    def enable(self, payload):
        if set(payload) != {'id','enabled'} or payload.get('id') not in ('netbird','github') or type(payload.get('enabled')) is not bool:
            raise ValueError('Connection ID and boolean enabled required')
        self.registry.set_connection(payload['id'], payload['enabled'])
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            key=tuple(self.registry.connections[name] for name in ('netbird','github'))
            if self.cached is None or self.cache_key!=key or time.monotonic()-self.checked>=5:
                self.cached=self._discover()
                self.checked=time.monotonic()
                self.cache_key=key
            import copy
            return copy.deepcopy(self.cached)

    def _discover(self):
        result, peers = [], []
        for name in ('netbird','github'):
            enabled = self.registry.connections[name]
            item = dict(id=name,enabled=enabled,status='disabled',account='',message='')
            result.append(item)
            if not enabled: continue
            try:
                argv = ['netbird','status','--json'] if name == 'netbird' else ['gh','auth','status','--active','--hostname','github.com','--json','hosts']
                response = self.runner(argv)
                if response.returncode != 0:
                    item.update(status='needsSignIn',message='Open the local setup guide and check sign-in on this computer.')
                    continue
                if len(response.stdout) > 1024*1024: raise ValueError()
                data = json.loads(response.stdout)
                if not isinstance(data, dict): raise ValueError()
                if name == 'github':
                    hosts = data.get('hosts')
                    if not isinstance(hosts, dict): raise ValueError()
                    accounts = hosts.get('github.com', [])
                    if not isinstance(accounts, list): raise ValueError()
                    active = next((x for x in accounts if isinstance(x,dict) and x.get('active') is True), {})
                    item.update(status='connected' if active.get('state') == 'success' else 'needsSignIn', account=label(active.get('login','')))
                else:
                    management = data.get('management')
                    if not isinstance(management,dict) or type(management.get('connected')) is not bool: raise ValueError()
                    item['status'] = 'connected' if management['connected'] else 'needsSignIn'
                    details = data.get('peers', {}).get('details', [])
                    if not isinstance(details, list): raise ValueError()
                    for peer in details[:256]:
                        if not isinstance(peer,dict): raise ValueError()
                        address = str(ipaddress.ip_interface(peer.get('netbirdIp','')).ip)
                        name_label = label(peer.get('fqdn',''))
                        peers.append(dict(id=address,name=name_label or address,address=address,
                            status={'connected':'online','disconnected':'offline','idle':'offline'}.get(str(peer.get('status','')).lower(),'unknown'),os=label(peer.get('os',''))))
            except FileNotFoundError: item.update(status='notInstalled',message='Install the CLI on this computer, then enable discovery again.')
            except (OSError, ValueError, TypeError, AttributeError, TimeoutError, subprocess.TimeoutExpired):
                item.update(status='error',message='Local status unavailable. Check the installed CLI on this computer.')
        return {'connections':result,'peers':peers}
