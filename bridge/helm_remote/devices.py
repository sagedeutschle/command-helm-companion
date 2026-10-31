"""Private operator-owned device allowlist. Discovery never enrolls devices."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time


def device_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        raise ValueError('Invalid device ID')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class DeviceRegistry:
    def __init__(self, path, config=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        if config is None:
            if self.path.stat().st_size > 65536: raise ValueError('Device configuration too large')
            config = json.loads(self.path.read_text())
        self.config = self.validate(copy.deepcopy(config))
        self.devices = {item['id']:item for item in self.config['devices']}
        self.connections = self.config['connections']

    @staticmethod
    def validate(config):
        if not isinstance(config, dict) or config.get('version') != 1: raise ValueError('Device configuration version 1 required')
        devices = config.get('devices')
        if not isinstance(devices, list) or len(devices) > 64: raise ValueError('At most 64 devices supported')
        ids = set()
        for item in devices:
            if not isinstance(item, dict): raise ValueError('Device object required')
            value = device_id(item.get('id'))
            if value in ids: raise ValueError('Duplicate device ID')
            ids.add(value)
            for key in ('name', 'platform'):
                text = item.get(key)
                if not isinstance(text, str) or not 1 <= len(text) <= 120 or any(ord(c) < 32 for c in text): raise ValueError('Invalid device label')
            if item.get('transport') not in ('local', 'ssh'): raise ValueError('Invalid transport')
            workspace = item.get('workspace')
            if not isinstance(workspace, str) or len(workspace) > 4096 or '\x00' in workspace or not Path(workspace).is_absolute(): raise ValueError('Absolute workspace required')
            if item['transport'] == 'local' and not Path(workspace).is_dir(): raise ValueError('Workspace does not exist')
            if type(item.get('terminalEnabled', False)) is not bool: raise ValueError('terminalEnabled must be boolean')
            item.setdefault('terminalEnabled', False)
            command = item.get('codexCommand', 'codex')
            if not isinstance(command, str) or not re.fullmatch(r'(?:/[A-Za-z0-9_./ -]+|[A-Za-z0-9_.-]+)', command) or command.startswith('-'): raise ValueError('Invalid Codex executable')
            item['codexCommand'] = command
            if item['transport'] == 'ssh':
                # Host aliases or user@hostname only; no options, whitespace or shell punctuation.
                target = item.get('sshTarget')
                if not isinstance(target, str) or len(target) > 253 or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*', target): raise ValueError('Invalid SSH target')
                alias = item.get('hostKeyAlias')
                if alias is not None and (not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}', alias)): raise ValueError('Invalid host key alias')
        connections = config.setdefault('connections', {})
        if not isinstance(connections, dict) or set(connections) - {'netbird','github'}: raise ValueError('Invalid connections')
        for name in ('netbird','github'):
            connections.setdefault(name, False)
            if type(connections[name]) is not bool: raise ValueError('Connection enabled must be boolean')
        return config

    def save(self):
        with self.lock: atomic_json(self.path, self.config)

    def set_connection(self, name, enabled):
        with self.lock:
            candidate = copy.deepcopy(self.config)
            candidate['connections'][name] = enabled
            atomic_json(self.path, candidate)
            self.config = candidate
            self.connections = candidate['connections']

    def inventory(self, workers=None, now=None):
        now = time.time() if now is None else now
        workers = {row['target']:row for row in (workers or [])}
        result=[]
        for item in self.devices.values():
            local = item['transport'] == 'local'
            worker = workers.get(item['id'])
            active = worker is not None and worker['seen'] >= now-90
            result.append(dict(id=item['id'],name=item['name'],platform=item['platform'],transport=item['transport'],
                status='online' if local or active else ('offline' if worker else 'unknown'),lastSeen=worker['seen'] if worker else 0,
                capabilities=dict(tasks=worker is not None,terminal=item['terminalEnabled'],codex=item['terminalEnabled'] and (not local or shutil.which(item['codexCommand']) is not None))))
        return {'devices':result}


def strict_ssh(target, alias=None):
    if not isinstance(target,str) or len(target)>253 or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*',target):
        raise ValueError('Invalid SSH target')
    if alias is not None and (not isinstance(alias,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}',alias)):
        raise ValueError('Invalid host key alias')
    args=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',
          '-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','PermitLocalCommand=no',
          '-o','EscapeChar=none','-o','ControlMaster=no','-o','ControlPath=none','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2']
    if alias is not None:args += ['-o','HostKeyAlias='+alias]
    return args
