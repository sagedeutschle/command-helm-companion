"""Disposable installed-entry tests; no accounts, SSH, PTYs or real assistants."""
import contextlib
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid


class LegacyEntryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'private-state'
        self.state.mkdir(mode=0o700)
        self.token = 'disposable-pairing-credential'
        (self.state / 'phone-token').write_text(self.token)
        (self.state / 'phone-token').chmod(0o600)
        self.config = self.root / 'devices.json'
        self.config.write_text(json.dumps(dict(version=1, devices=[dict(
            id='workstation', name='Test computer', platform='linux', transport='local',
            workspace=str(self.root), terminalEnabled=True, codexCommand='missing-test-codex')],
            connections=dict(netbird=False, github=False))))
        # Fail closed if any route/startup regression tries to launch a CLI or PTY.
        guard = self.root / 'guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text(
            'import subprocess\n'
            'def forbidden(*args, **kwargs):\n'
            '    raise AssertionError("External commands are forbidden in CLI tests")\n'
            'subprocess.Popen = forbidden\nsubprocess.run = forbidden\n')
        self.environment = dict(HOME=str(self.root), PATH=os.defpath, PYTHONNOUSERSITE='1',
            XDG_CONFIG_HOME=str(self.root / 'config'), XDG_STATE_HOME=str(self.root / 'state'),
            PYTHONPATH=os.pathsep.join([str(guard), str(Path(__file__).resolve().parents[1])]))

    def tls_fixture(self):
        if not shutil.which('openssl'):
            self.skipTest('openssl not installed')
        self.cert, self.key = self.root / 'existing-cert.pem', self.root / 'existing-key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(self.key), '-out', str(self.cert), '-days', '1', '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost'], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.environment)

    def command(self, configured=True):
        argv = [sys.executable, '-m', 'helm_remote.server', '--state-dir', str(self.state), '--port', '0',
            '--tls-cert', str(self.cert), '--tls-key', str(self.key)]
        if configured:
            argv += ['--devices-config', str(self.config)]
        return argv

    @contextlib.contextmanager
    def running(self, configured=True, stop_signal=signal.SIGTERM):
        self.tls_fixture()
        process = subprocess.Popen(self.command(configured), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=self.environment)
        try:
            selector = selectors.DefaultSelector()
            try:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(5), 'CLI did not report readiness')
            finally:
                selector.close()
            self.readiness = process.stdout.readline()
            match = re.search(r'127\.0\.0\.1:(\d+)\.', self.readiness)
            self.assertIsNotNone(match, self.readiness + process.stderr.read() if process.poll() is not None else self.readiness)
            self.port = int(match[1])
            self.assertGreater(self.port, 0, 'Readiness must report the bound ephemeral port')
            yield process
        finally:
            if process.poll() is None:
                process.send_signal(stop_signal)
            try:
                out, err = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                out, err = process.communicate()
                self.fail('CLI did not shut down within five seconds')
            self.assertNotIn(self.token, self.readiness + out + err)
            self.assertNotIn('helmremote://', self.readiness + out + err)
            self.assertEqual(process.returncode, 0, err)
            self.assertEqual(err, '')

    def request(self, path, payload=None, token=None):
        connection = http.client.HTTPSConnection('localhost', self.port, timeout=3,
            context=ssl.create_default_context(cafile=str(self.cert)))
        try:
            connection.request('GET' if payload is None else 'POST', path,
                body=None if payload is None else json.dumps(payload),
                headers={'Authorization': 'Bearer ' + (self.token if token is None else token),
                         'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_configured_tls_routes_and_dynamic_queue_preserve_pairing(self):
        with self.running():
            cert_before, key_before = self.cert.read_bytes(), self.key.read_bytes()
            code, inventory = self.request('/v1/devices')
            self.assertEqual(code, 200)
            self.assertEqual(inventory['devices'][0]['id'], 'workstation')
            self.assertEqual(self.request('/v1/terminals'), (200, {'sessions': []}))
            code, connections = self.request('/v1/connections')
            self.assertEqual(code, 200)
            self.assertEqual([(row['id'], row['status']) for row in connections['connections']],
                             [('netbird', 'disabled'), ('github', 'disabled')])
            for target in ('workstation', 'spellbook', 'spelltower'):
                code, task = self.request('/v1/tasks', dict(id=str(uuid.uuid4()), target=target, prompt='Queue only'))
                self.assertEqual(code, 200)
                self.assertEqual(task['task']['state'], 'queued')
            self.assertEqual(len(self.request('/v1/tasks')[1]['tasks']), 3)
            self.assertEqual(self.request('/v1/devices', token='wrong')[0], 401)
            context = ssl.create_default_context(cafile=str(self.cert))
            with socket.create_connection(('127.0.0.1', self.port), timeout=3) as tcp:
                with context.wrap_socket(tcp, server_hostname='localhost') as tls:
                    expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert_before.decode())).digest()
                    self.assertEqual(hashlib.sha256(tls.getpeercert(binary_form=True)).digest(), expected)
            self.assertEqual(self.cert.read_bytes(), cert_before)
            self.assertEqual(self.key.read_bytes(), key_before)
            self.assertEqual((self.state / 'phone-token').read_text(), self.token)
        # A graceful restart releases both the socket and the terminal owner lock.
        from helm_remote.devices import DeviceRegistry
        from helm_remote.terminals import TerminalManager
        manager = TerminalManager(self.state / 'terminals', DeviceRegistry(self.config))
        manager.shutdown()
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.port), timeout=.2)

    def test_unconfigured_tls_keeps_companion_routes_absent_and_sigint_exits(self):
        with self.running(configured=False, stop_signal=signal.SIGINT):
            for route in ('/v1/devices', '/v1/connections', '/v1/terminals'):
                self.assertEqual(self.request(route)[0], 404)
                self.assertEqual(self.request(route, {})[0], 404)
            self.assertEqual(self.request('/v1/snapshot')[0], 200)
            self.assertEqual(self.request('/v1/tasks', dict(id=str(uuid.uuid4()), target='workstation', prompt='No'))[0], 400)
        self.assertFalse((self.state / 'terminals').exists())

    def test_invalid_config_is_rejected_before_creating_persistence(self):
        self.tls_fixture()
        self.config.write_text('{"version": 99, "devices": []}')
        result = subprocess.run(self.command(), env=self.environment, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Device configuration version 1 required', result.stderr)
        self.assertFalse((self.state / 'state.sqlite').exists())
        self.assertFalse((self.state / 'tasks.sqlite').exists())
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_relative_config_path_is_rejected(self):
        self.tls_fixture()
        argv = self.command()
        argv[-1] = 'relative-devices.json'
        result = subprocess.run(argv, env=self.environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('absolute', result.stderr.lower())
        self.assertFalse((self.state / 'state.sqlite').exists())

    def invoke_with_boundaries(self, extra=(), fail_server=False, fail_tls=False):
        from helm_remote import server
        from helm_remote.devices import DeviceRegistry
        from helm_remote.remote import FederatedStore
        from helm_remote.tasks import TaskProxy, TaskStore
        from helm_remote.terminals import TerminalManager
        captured = {}
        real_make_server = server.make_server

        def no_external(*args, **kwargs):
            raise AssertionError('Remote and PTY boundaries are disabled')

        def make_server(*args, **kwargs):
            captured.update(store=args[0], **kwargs)
            if fail_server:
                raise OSError('Disposable bind failure')
            instance = real_make_server(*args, **kwargs)
            captured['server'] = instance
            instance.serve_forever = lambda: None
            return instance

        def terminals(*args):
            return TerminalManager(*args, transport_factory=no_external)

        def federation(local):
            instance = FederatedStore(local, transport=no_external)
            instance.run = lambda: None
            captured['federation'] = instance
            return instance

        argv = ['helm_remote.server', '--state-dir', str(self.state), '--port', '0',
                '--devices-config', str(self.config), *extra]
        with patch.dict(os.environ, self.environment, clear=True), patch.object(sys, 'argv', argv), \
                patch.object(server, 'DEFAULT_STATE', self.root / 'unused-default'), \
                patch.object(server, 'make_server', side_effect=make_server), \
                patch.object(server, 'TerminalManager', side_effect=terminals), \
                patch.object(server, 'TaskProxy', side_effect=lambda: TaskProxy(transport=no_external)), \
                patch('helm_remote.remote.FederatedStore', side_effect=federation), \
                patch('subprocess.Popen', side_effect=no_external), patch('subprocess.run', side_effect=no_external), \
                contextlib.redirect_stdout(io.StringIO()):
            if fail_server or fail_tls:
                with self.assertRaises(OSError):
                    server.main()
            else:
                previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                server.main()
                self.assertEqual({sig: signal.getsignal(sig) for sig in previous}, previous)
        self.assertIsInstance(captured['devices'], DeviceRegistry)
        self.assertTrue(captured['terminals'].closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured['store'].db.execute('SELECT 1')
        if isinstance(captured['tasks'], TaskStore):
            with self.assertRaises(sqlite3.ProgrammingError):
                captured['tasks'].snapshot()
        return captured

    def test_proxy_and_federation_are_preserved_with_all_external_boundaries_disabled(self):
        from helm_remote.tasks import TaskProxy
        captured = self.invoke_with_boundaries(('--task-hub', 'spelltower', '--spelltower'))
        self.assertIsInstance(captured['tasks'], TaskProxy)
        self.assertIs(captured['server'].store, captured['federation'])
        self.assertIs(captured['federation'].local, captured['store'])

    def test_initialization_failure_closes_local_persistence_and_terminal_owner(self):
        self.invoke_with_boundaries(fail_server=True)
        from helm_remote.devices import DeviceRegistry
        from helm_remote.terminals import TerminalManager
        manager = TerminalManager(self.state / 'terminals', DeviceRegistry(self.config))
        manager.shutdown()

    def test_tls_initialization_failure_also_closes_bound_http_socket(self):
        captured = self.invoke_with_boundaries(
            ('--tls-cert', str(self.root / 'absent-cert.pem'), '--tls-key', str(self.root / 'absent-key.pem')),
            fail_tls=True)
        self.assertEqual(captured['server'].socket.fileno(), -1)
