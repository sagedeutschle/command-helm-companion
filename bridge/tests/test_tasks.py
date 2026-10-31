import concurrent.futures
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from helm_remote.tasks import TaskStore, TaskProxy, LEASE_SECONDS
from helm_remote.store import Conflict, Store
from helm_remote.server import make_server
from helm_remote.remote import call_remote, TransportUnavailable


def request(target='spellbook', prompt='Make a useful file', workspace=''):
    return dict(id=str(uuid.uuid4()), target=target, prompt=prompt, workspace=workspace)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'tasks.sqlite'
        self.now = [1000.0]
        self.store = TaskStore(self.path, clock=lambda: self.now[0])
        self.owner = str(uuid.uuid4())

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_idempotency_persistence_and_conflicting_payload(self):
        body = request()
        first = self.store.submit(body)
        self.assertEqual(first, self.store.submit(body))
        with self.assertRaises(Conflict): self.store.submit(dict(body, prompt='other'))
        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual(first['task'], self.store.snapshot()['tasks'][0])

    def test_input_validation(self):
        for body in [request(target='unknown'), request(prompt=' '), request(workspace='relative'),
                     request(prompt='x'*8001), dict(request(), id='oops'), request(workspace=None)]:
            with self.assertRaises(ValueError): self.store.submit(body)

    def test_atomic_claim_across_connections_and_destination(self):
        body = request()
        self.store.submit(body)
        other = TaskStore(self.path, clock=lambda: self.now[0])
        barrier = threading.Barrier(2)
        def claim(store):
            barrier.wait()
            try: return store.claim('spellbook', str(uuid.uuid4()))['task']
            except Conflict: return None
        try:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(claim, store) for store in [self.store, other]]
                rows = [future.result() for future in futures]
            self.assertEqual(sum(row is not None for row in rows), 1)
            self.assertIsNone(self.store.claim('spelltower', self.owner)['task'])
        finally: other.close()

    def test_lost_claim_not_returned_twice_and_no_interrupted_replay(self):
        body = request()
        self.store.submit(body)
        self.assertEqual(self.store.claim('spellbook', self.owner)['task']['id'], body['id'])
        with self.assertRaises(Conflict): self.store.claim('spellbook', self.owner)
        self.now[0] += LEASE_SECONDS + 1
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'], 'interrupted')
        self.assertIsNone(self.store.claim('spellbook', str(uuid.uuid4()))['task'])
        with self.assertRaises(Conflict):
            self.store.finish('spellbook', self.owner, body['id'], 'completed', '/delivery', 'late report')

    def test_heartbeat_and_finish_require_owner_and_report(self):
        body = request()
        self.store.submit(body); self.store.claim('spellbook', self.owner)
        self.now[0] += 30
        self.store.heartbeat('spellbook', self.owner, body['id'])
        with self.assertRaises(Conflict): self.store.heartbeat('spellbook', str(uuid.uuid4()), body['id'])
        with self.assertRaises(ValueError): self.store.finish('spellbook', self.owner, body['id'], 'completed')
        result = self.store.finish('spellbook', self.owner, body['id'], 'completed', '/delivery', 'Bearer secretvalue\n'+'x'*25000)
        self.assertLessEqual(len(result['task']['report']), 24000)
        self.assertNotIn('secretvalue', result['task']['report'])
        self.assertEqual(result, self.store.finish('spellbook', self.owner, body['id'], 'completed', '/delivery', 'Bearer secretvalue\n'+'x'*25000))

    def test_snapshot_is_bounded_but_older_ids_still_idempotent(self):
        first = request()
        self.store.submit(first)
        for _ in range(105):
            self.now[0] += 1
            self.store.submit(request())
        self.assertEqual(len(self.store.snapshot()['tasks']), 100)
        self.assertNotIn(first['id'], [task['id'] for task in self.store.snapshot()['tasks']])
        self.assertEqual(self.store.snapshot()['tasks'][0]['createdAt'], self.now[0])
        self.assertEqual(self.store.submit(first)['task']['id'], first['id'])

    def test_proxy_uses_fixed_ssh_command_and_json_stdin(self):
        body = request(prompt="literal $(touch /tmp/nope); `id` ' \\\nhi")
        with patch('helm_remote.remote.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({'task': body})
            TaskProxy().submit(body)
            args, kwargs = run.call_args
            self.assertNotIn(body['prompt'], ' '.join(args[0]))
            self.assertIn('StrictHostKeyChecking=yes', args[0])
            self.assertEqual(json.loads(kwargs['input'])['payload'], body)
            self.assertNotIn('shell', kwargs)
        with patch('helm_remote.remote.subprocess.run', side_effect=OSError('offline')):
            with self.assertRaises(Conflict): TaskProxy().snapshot()


class TaskHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'state.sqlite')
        self.tasks = TaskStore(Path(self.tmp.name)/'tasks.sqlite')
        self.server = make_server(self.store, 'private', ('127.0.0.1', 0), tasks=self.tasks)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store.close(); self.tasks.close(); self.tmp.cleanup()

    def http(self, method, path='/v1/tasks', body=None, token='private', origin=''):
        connection = http.client.HTTPConnection(*self.server.server_address)
        headers = {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        value = response.status, json.loads(response.read())
        connection.close()
        return value

    def test_contract_and_private_worker_operations(self):
        body = request(prompt='literal ✨'*800)
        self.assertEqual(self.http('POST', body=body)[0], 200)
        code, snapshot = self.http('GET')
        self.assertEqual(code, 200)
        self.assertEqual(set(snapshot), {'tasks', 'workers'})
        task = snapshot['tasks'][0]
        self.assertEqual(set(task), {'id','target','prompt','workspace','state','createdAt','updatedAt','deliveryPath','report','error'})
        self.assertEqual(task['workspace'], '')
        self.assertEqual(self.http('POST', body=body)[0], 200)
        self.assertEqual(self.http('POST', body=dict(body, prompt='other'))[0], 409)
        self.assertEqual(self.http('GET', token='wrong')[0], 401)
        self.assertEqual(self.http('GET', origin='https://example.com')[0], 403)
        for operation in ('claim', 'heartbeat', 'finish'):
            self.assertEqual(self.http('POST', '/v1/tasks/'+operation, {})[0], 404)
        self.assertEqual(self.http('POST', body=[])[0], 400)

    def test_offline_hub_never_responds_with_completion(self):
        self.server.tasks = TaskProxy(lambda *a: (_ for _ in ()).throw(TransportUnavailable('offline; no delivery confirmed')))
        self.assertEqual(self.http('GET')[0], 503)
        code, body = self.http('POST', body=request())
        self.assertEqual(code, 503)
        self.assertIn('no delivery', body['error'])
        self.assertEqual(self.tasks.snapshot()['tasks'], [])

    def test_lost_proxy_submit_response_retries_same_uuid_without_duplicate(self):
        body = request()
        self.server.tasks = TaskProxy()
        calls = [0]
        def ssh(argv, **kwargs):
            sent = json.loads(kwargs['input'])
            result = self.tasks.dispatch(sent['action'], sent['payload'])
            calls[0] += 1
            if calls[0] == 1:
                import subprocess
                raise subprocess.TimeoutExpired(argv, 9)
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout=json.dumps(result))
        with patch('helm_remote.remote.subprocess.run', side_effect=ssh):
            self.assertEqual(self.http('POST', body=body)[0], 503)
            self.assertEqual(self.tasks.snapshot()['tasks'][0]['id'], body['id'])
            code, receipt = self.http('POST', body=body)
            self.assertEqual(code, 200)
            self.assertEqual(receipt['task']['id'], body['id'])
            self.assertEqual(len(self.tasks.snapshot()['tasks']), 1)
            self.assertEqual(self.http('POST', body=dict(body, prompt='changed'))[0], 409)

    def test_direct_snapshot_includes_machine_identity_without_changing_ids(self):
        sid = self.store.register('Project', '/project', 'codex', 'session')
        for platform, expected in [('darwin', 'spellbook'), ('linux', 'spelltower')]:
            with patch('helm_remote.server.sys.platform', platform):
                code, snapshot = self.http('GET', path='/v1/snapshot')
                self.assertEqual(code, 200)
                self.assertEqual(snapshot['projects'][0]['machineId'], expected)
                self.assertEqual(snapshot['projects'][0]['sessions'][0]['id'], sid)

    def test_unicode_reports_fit_rpc_and_http_serialization(self):
        from types import SimpleNamespace
        owner = str(uuid.uuid4())
        for character in ['漢', '✨', '😀']:
            body = request()
            self.tasks.submit(body)
            self.tasks.claim('spellbook', owner)
            payload = dict(target='spellbook', workerId=owner, taskId=body['id'],
                           state='completed', deliveryPath='/delivery', report=character*24000)
            def ssh(argv, **kwargs):
                # Exercise the exact request shape/serializer sent to the actual RPC.
                self.assertLessEqual(len(kwargs['input']), 524288)
                self.assertIn(character, kwargs['input'])
                sent = json.loads(kwargs['input'])
                result = self.tasks.dispatch(sent['action'], sent['payload'])
                return SimpleNamespace(returncode=0, stdout=json.dumps(result))
            with patch('helm_remote.remote.subprocess.run', side_effect=ssh):
                receipt = call_remote('task_finish', payload)
            self.assertEqual(receipt['task']['report'], character*24000)
        code, snapshot = self.http('GET')
        self.assertEqual(code, 200)
        self.assertEqual(len(snapshot['tasks']), 3)

    def test_rpc_accepts_24000_cjk_and_emoji_reports(self):
        import io
        from helm_remote import rpc
        owner = str(uuid.uuid4())
        state = Path(self.tmp.name)/'rpc-state'
        for character in ['漢', '😀']:
            tasks = TaskStore(state/'tasks.sqlite')
            body = request()
            tasks.submit(body); tasks.claim('spellbook', owner); tasks.close()
            payload = dict(target='spellbook', workerId=owner, taskId=body['id'], state='completed',
                           deliveryPath='/delivery', report=character*24000)
            # Even older ASCII-escaped transports fit the enlarged RPC envelope.
            raw = json.dumps(dict(action='task_finish', payload=payload))
            self.assertGreater(len(raw), 131072)
            output = io.StringIO()
            with patch.object(rpc, 'DEFAULT_STATE', state), patch('sys.stdin', io.StringIO(raw)), patch('sys.stdout', output):
                rpc.main()
            self.assertEqual(json.loads(output.getvalue())['task']['report'], character*24000)
