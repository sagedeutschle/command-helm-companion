#!/usr/bin/env python3
"""Simulator tests against disposable pinned TLS bridges and an in-memory PTY fixture.

No accounts, existing bridges, real terminal processes or phone state are used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', help='Explicit xcodebuild simulator destination')
    parser.add_argument('--only-testing', action='append', default=[])
    parser.add_argument('--evidence', type=Path, default=Path('evidence'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if args.destination:
        destination = args.destination
        if not destination.startswith('platform=iOS Simulator,'):
            parser.error('Simulator destinations only')
    else:
        inventory = json.loads(subprocess.check_output(['xcrun', 'simctl', 'list', 'devices', 'available', '--json']))
        available = [d for runtime, devices in inventory['devices'].items() if '.iOS-' in runtime for d in devices if 'iPhone' in d['name']]
        if not available:
            parser.error('No available iPhone simulator; supply --destination')
        destination = 'platform=iOS Simulator,id=' + available[0]['udid']
    print('Simulator destination:', destination, flush=True)
    sys.path.insert(0, str(root / 'bridge'))

    class FixtureTransport:
        """Only test bytes; never spawns a shell, CLI, tmux or SSH process."""
        def __init__(self, device, socket, session):
            self.alive = True
        def create(self, kind, cols, rows):
            pass
        def attach(self, cols, rows, receiver, disconnected):
            self.receiver = receiver
            receiver(b'Fixture shell\r\n$ ')
        def write(self, data):
            self.receiver(data)
        def resize(self, cols, rows):
            self.receiver(('size=%dx%d\r\n' % (cols, rows)).encode())
        def state(self):
            return 'running' if self.alive else 'exited'
        def terminate(self):
            self.alive = False
        def detach(self):
            pass

    with tempfile.TemporaryDirectory(prefix='helm-ios-test-') as folder:
        state = Path(folder)
        original_home = os.environ.get('HOME')
        os.environ['HOME'] = str(state / 'home')
        (state / 'home').mkdir()
        from helm_remote.store import Store
        from helm_remote.server import make_server
        from helm_remote.devices import DeviceRegistry
        from helm_remote.connections import Connections
        from helm_remote.terminals import TerminalManager

        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(state / 'test-key.pem'), '-out', str(state / 'test-cert.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(state / 'test-key.pem', 0o600)
        pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert((state / 'test-cert.pem').read_text())).hexdigest()
        registry = DeviceRegistry(state / 'devices.json', config={'version': 1, 'devices': [dict(id='computer', name='My computer', platform='macos', transport='local', workspace=str(state), terminalEnabled=True, codexCommand='codex')], 'connections': {}})
        registry.save()
        terminals = TerminalManager(state / 'terminals', registry, transport_factory=FixtureTransport)
        store = Store(state / 'state.sqlite')
        legacy_store = Store(state / 'legacy.sqlite')
        sid = store.register('Integration project', '/test/integration', 'codex', 'integration-session')
        stop_heartbeat = threading.Event()
        store.heartbeat(sid)
        def heartbeat():
            while not stop_heartbeat.wait(5):
                store.heartbeat(sid)
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        store.update(sid, 'working', 'The TLS integration bridge is ready for the simulator UI check.')
        store.ask(sid, 'Unit integration question', ['Confirmed'], ttl=1800)
        store.ask(sid, 'Confirm the phone reply path?', ['Confirmed'], ttl=1800)
        server = make_server(store, 'integration-fixture-only', ('127.0.0.1', 0), devices=registry, connections=Connections(registry), terminals=terminals)
        server.tasks.claim('computer', '00000000-0000-4000-8000-000000000001')
        legacy = make_server(legacy_store, 'integration-fixture-only', ('127.0.0.1', 0))
        servers = [server, legacy]
        threads = []
        for target in servers:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(state / 'test-cert.pem', state / 'test-key.pem')
            target.socket = ctx.wrap_socket(target.socket, server_side=True, do_handshake_on_connect=False)
            thread = threading.Thread(target=target.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        support = root / 'ios/TestSupport'
        support.mkdir(exist_ok=True)
        (support / 'TestBridge.swift').write_text('enum TestBridge { static let url = "https://localhost:%d"; static let legacyURL = "https://localhost:%d"; static let credential = "integration-fixture-only"; static let pin = "%s" }\n' % (server.server_port, legacy.server_port, pin))
        try:
            # Only the bounded fixture helpers above see the private HOME. Xcode keeps
            # its standard toolchain/cache configuration; no real bridge is loaded.
            if original_home:
                os.environ['HOME'] = original_home
            subprocess.run(['xcodegen', 'generate'], cwd=root / 'ios', check=True)
            command = ['xcodebuild', 'test', '-onlyUsePackageVersionsFromResolvedFile', '-project', 'ios/CommandHelmRemote.xcodeproj', '-scheme', 'CommandHelmRemote', '-destination', destination, '-derivedDataPath', str(Path.home() / 'Library/Caches/CommandHelmMobileArchiveFix1Tests20261031'), 'CODE_SIGN_IDENTITY=-', '-resultBundlePath', str(evidence / 'ios-tests.xcresult')]
            command += ['-only-testing:' + value for value in args.only_testing]
            with (evidence / 'ios-final-tests.log').open('w') as log:
                result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
            print('iOS test exit:', result.returncode, flush=True)
            return result.returncode
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()
            for target in servers:
                target.shutdown()
                target.server_close()
            for thread in threads:
                thread.join()
            terminals.shutdown()
            store.close()
            legacy_store.close()


if __name__ == '__main__':
    raise SystemExit(main())
