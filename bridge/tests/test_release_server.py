import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from helm_remote.server import make_server
from helm_remote.store import Store
from helm_remote.tasks import TaskStore
from test_release_terminals import configuration, FakeTransport, start_payload


class ReleaseServerTests(unittest.TestCase):
    def test_routes_auth_validation_and_terminal_lifecycle(self):
        from helm_remote.terminals import TerminalManager
        from helm_remote.connections import Connections
        with tempfile.TemporaryDirectory() as tmp:
            registry=configuration(tmp)
            store=Store(Path(tmp)/'state.sqlite')
            tasks=TaskStore(Path(tmp)/'tasks.sqlite',targets=registry.devices)
            terminals=TerminalManager(Path(tmp)/'terminals',registry,transport_factory=FakeTransport)
            server=make_server(store,'test-token',('127.0.0.1',0),tasks=tasks,devices=registry,terminals=terminals,connections=Connections(registry))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,payload=None,headers=None):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
                values={'Authorization':'Bearer test-token','Content-Type':'application/json'}
                if headers:values.update(headers)
                connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=values)
                response=connection.getresponse();result=(response.status,json.loads(response.read()));connection.close();return result
            try:
                self.assertEqual(request('GET','/v1/devices')[1]['devices'][0]['id'],'laptop')
                self.assertEqual(request('GET','/v1/connections')[1]['connections'][0]['status'],'disabled')
                self.assertEqual(request('GET','/v1/devices',headers={'Origin':'https://example.com'})[0],403)
                payload=start_payload();session=payload['id']
                self.assertEqual(request('POST','/v1/terminals',payload)[0],200)
                self.assertEqual(len(request('GET','/v1/terminals')[1]['sessions']),1)
                self.assertEqual(request('GET',f'/v1/terminals/{session}/output?after=0')[0],200)
                self.assertEqual(request('GET',f'/v1/terminals/{session}/output?after=-1')[0],400)
                self.assertEqual(request('POST',f'/v1/terminals/{session}/input',dict(id=str(uuid.uuid4()),data='YQ=='))[0],200)
                self.assertEqual(request('POST',f'/v1/terminals/{session}/resize',dict(cols=80,rows=24))[0],200)
                self.assertEqual(request('POST',f'/v1/terminals/{session}/close',{'command':'bad'})[0],400)
                self.assertEqual(request('POST',f'/v1/terminals/{session}/close',{})[0],200)
                self.assertEqual(request('POST','/v1/connections',dict(id='github',enabled='true'))[0],400)
                self.assertEqual(request('POST','/v1/tasks',dict(id=str(uuid.uuid4()),target='laptop',prompt='Hi'))[0],200)
            finally:
                server.shutdown();server.server_close();thread.join();terminals.shutdown();tasks.close();store.close()

    def test_snapshot_machine_identity_uses_local_configuration_and_preserves_explicit_ids(self):
        for count, explicit, expected in [(1, None, 'laptop'), (2, None, '__bridge__'), (0, None, '__bridge__'), (1, 'federated-worker', 'federated-worker')]:
            with self.subTest(count=count, explicit=explicit), tempfile.TemporaryDirectory() as tmp:
                registry=configuration(tmp)
                original=registry.devices['laptop']
                registry.devices={f'local-{i}':dict(original,id=f'local-{i}') for i in range(count)}
                if count==1:registry.devices={'laptop':original}
                store=Store(Path(tmp)/'state.sqlite')
                project={'id':'project','name':'Project','sessions':[]}
                if explicit:project['machineId']=explicit
                store.snapshot=lambda:dict(generatedAt=0,projects=[dict(project)],updates=[],questions=[])
                server=make_server(store,'test-token',('127.0.0.1',0),devices=registry)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
                    connection.request('GET','/v1/snapshot',headers={'Authorization':'Bearer test-token'})
                    response=connection.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertEqual(json.loads(response.read())['projects'][0]['machineId'],expected)
                    connection.close()
                finally:
                    server.shutdown();server.server_close();thread.join();store.close()
