import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from helm_remote.tasks import TaskStore, TaskProxy, LEASE_SECONDS
from helm_remote.worker import Worker
from helm_remote.store import Conflict

BRIDGE = Path(__file__).resolve().parents[1]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root/'state'
        self.store = TaskStore(self.state/'tasks.sqlite')
        self.fake = self.root/'fake codex'
        self.fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
report = pathlib.Path(args[args.index('-o')+1])
workspace = pathlib.Path(args[args.index('-C')+1])
received = sys.stdin.read()
prompt = received.split('User task follows:\\n', 1)[-1]
(workspace/'received.json').write_text(json.dumps({'args':args,'prompt':prompt,'received':received}))
(workspace/'started.tmp').write_text(str(os.getpid()))
(workspace/'started.tmp').replace(workspace/'started')
if prompt == 'sleep':
    time.sleep(30)
if prompt != 'no-report':
    report.write_text('Verified report\\n')
if prompt == 'long-report':
    report.write_text('A'*30000 + '\\nLast full line\\n')
if prompt == 'auth-fail':
    print('authentication failed: Bearer secretvalue', file=sys.stderr)
    sys.exit(1)
if prompt == 'fail':
    sys.exit(7)
''')
        self.fake.chmod(0o700)
        self.worker = Worker('spellbook', self.store, self.state, str(self.fake),
                             self.root/'deliveries', heartbeat_interval=0.05)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def submit(self, prompt='build a file', target='spellbook', workspace=''):
        task = dict(id=str(uuid.uuid4()), target=target, prompt=prompt, workspace=workspace)
        self.store.submit(task)
        return task

    def row(self): return self.store.snapshot()['tasks'][0]

    def test_real_subprocess_literal_prompt_and_generated_workspace(self):
        prompt = "create `literal`; $(touch 'not-run')\nquotes ' \" \\ $HOME"
        task = self.submit(prompt)
        self.assertEqual(self.worker.run(once=True), 0)
        row = self.row()
        self.assertEqual(row['state'], 'completed')
        self.assertEqual(row['workspace'], '')
        delivery = Path(row['deliveryPath'])
        self.assertEqual(delivery, self.root/'deliveries'/task['id'])
        self.assertEqual((delivery/'REPORT.md').read_text(), 'Verified report\n')
        got = json.loads((delivery/'received.json').read_text())
        self.assertEqual(got['prompt'], prompt)
        self.assertIn('destination spellbook', got['received'])
        self.assertIn(str(delivery), got['received'])
        self.assertIn('workspace-write', got['args'])
        self.assertIn('sandbox_workspace_write.network_access=true', got['args'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', got['args'])
        self.assertTrue((delivery/'output.log').is_file())
        self.assertFalse(self.worker.run_once())

    def test_supplied_workspace_and_failure_missing_report(self):
        workspace = self.root/'project with spaces; literal'
        workspace.mkdir()
        self.submit('no-report', workspace=str(workspace))
        self.worker.run_once()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertIn('without a usable', self.row()['error'])
        self.assertTrue((workspace/'received.json').is_file())
        self.assertNotEqual(self.row()['deliveryPath'], str(workspace))

    def test_nonzero_process_is_failed_even_with_report(self):
        self.submit('fail'); self.worker.run_once()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertIn('status 7', self.row()['error'])

    def test_invalid_workspace_and_existing_delivery_fail_without_overwrite(self):
        task = self.submit(workspace=str(self.root/'missing'))
        self.worker.run_once()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertIn('not an existing directory', self.row()['error'])
        self.assertFalse((self.root/'deliveries'/task['id']/'started').exists())
        task = self.submit()
        directory = self.root/'deliveries'/task['id']
        directory.mkdir(); (directory/'keep').write_text('untouched')
        self.worker.run_once()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertEqual((directory/'keep').read_text(), 'untouched')

    def test_stop_interrupts_process_without_replay(self):
        task = self.submit('sleep')
        thread = threading.Thread(target=self.worker.run_once)
        thread.start()
        started = self.root/'deliveries'/task['id']/'started'
        deadline = time.monotonic()+5
        while not started.exists() and time.monotonic() < deadline: time.sleep(0.02)
        self.assertTrue(started.exists())
        pid = int(started.read_text())
        self.worker.stop.set(); thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.row()['state'], 'interrupted')
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        self.assertFalse(self.worker.run_once())

    def test_offline_claim_does_not_execute_or_complete(self):
        self.submit()
        self.worker.hub = TaskProxy(lambda *a: (_ for _ in ()).throw(Conflict('offline')))
        self.assertEqual(self.worker.run(once=True), 1)
        self.assertEqual(self.row()['state'], 'queued')
        self.assertFalse((self.root/'deliveries').exists())

    def test_connection_loss_during_execution_stops_process_no_success(self):
        task = self.submit('sleep')
        def transport(action, payload):
            if action == 'task_heartbeat': raise Conflict('offline')
            return self.store.dispatch(action, payload)
        self.worker.hub = TaskProxy(transport)
        self.worker.run_once()
        self.assertEqual(self.row()['state'], 'interrupted')
        self.assertEqual(self.row()['report'], '')
        self.assertFalse(self.worker.run_once())

    def test_native_lock_excludes_another_worker(self):
        import fcntl
        self.state.mkdir(exist_ok=True)
        with (self.state/'task-worker-spellbook.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(Conflict): self.worker.run(once=True)

    def test_cli_once_and_sigterm_interrupt(self):
        task = self.submit('sleep')
        env = dict(os.environ, PYTHONPATH=str(BRIDGE))
        argv = [sys.executable, '-m', 'helm_remote.worker', '--target', 'spellbook',
                '--state-dir', str(self.state), '--codex', str(self.fake),
                '--deliveries', str(self.root/'cli-deliveries'), '--once']
        process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started = self.root/'cli-deliveries'/task['id']/'started'
        try:
            deadline = time.monotonic()+5
            while not started.exists() and time.monotonic() < deadline: time.sleep(0.02)
            self.assertTrue(started.exists())
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(self.row()['state'], 'interrupted')
        finally:
            if process.poll() is None: process.kill(); process.communicate()

    def test_full_private_report_preserved_and_hub_report_bounded(self):
        self.submit('long-report'); self.worker.run_once()
        row = self.row()
        self.assertEqual(row['state'], 'completed')
        self.assertEqual(len(row['report']), 24000)
        self.assertTrue((Path(row['deliveryPath'])/'REPORT.md').read_text().endswith('Last full line\n'))
        self.assertGreater((Path(row['deliveryPath'])/'REPORT.md').stat().st_size, 24000)

    def test_authentication_failure_has_safe_actionable_hint(self):
        self.submit('auth-fail'); self.worker.run_once()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertIn('authenticate locally', self.row()['error'])
        self.assertNotIn('secretvalue', self.row()['error'])

    def test_sigkill_orphan_retains_native_lock_no_new_worker(self):
        task = self.submit('sleep')
        env = dict(os.environ, PYTHONPATH=str(BRIDGE))
        argv = [sys.executable, '-m', 'helm_remote.worker', '--target', 'spellbook',
                '--state-dir', str(self.state), '--codex', str(self.fake),
                '--deliveries', str(self.root/'orphan-deliveries'), '--once']
        process = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = self.root/'orphan-deliveries'/task['id']/'started'
        child_pid = None
        try:
            deadline = time.monotonic()+5
            while not started.exists() and time.monotonic() < deadline: time.sleep(0.02)
            self.assertTrue(started.exists())
            child_pid = int(started.read_text())
            process.kill(); process.wait(timeout=3)
            record = json.loads((self.state/'task-worker-spellbook.json').read_text())
            self.assertEqual(record['childPid'], child_pid)
            with self.assertRaises(Conflict): self.worker.run(once=True)
            # A dead worker never turns the task into a completed delivery.
            self.assertEqual(self.row()['state'], 'running')
            self.store.clock = lambda: time.time()+LEASE_SECONDS+1
            self.assertEqual(self.row()['state'], 'interrupted')
        finally:
            if process.poll() is None: process.kill(); process.wait()
            if child_pid:
                try: os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass
