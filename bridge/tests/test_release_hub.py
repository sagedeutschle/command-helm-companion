import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from helm_remote.tasks import TaskProxy


class ReleaseHubTests(unittest.TestCase):
    def test_configured_strict_hub_uses_fixed_rpc_and_validates_response(self):
        from helm_remote.hub import SSHHubTransport
        calls=[]
        def run(argv, **kwargs):
            calls.append((argv,kwargs))
            return SimpleNamespace(returncode=0,stdout=b'{"tasks":[],"workers":[]}')
        transport=SSHHubTransport('user@hub','/srv/companion source','/srv/private state',host_key_alias='trusted-hub',runner=run)
        result=TaskProxy(transport).snapshot()
        self.assertEqual(result,{'tasks':[],'workers':[]})
        argv,kwargs=calls[0]
        self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertIn('HostKeyAlias=trusted-hub',argv)
        self.assertIn('user@hub',argv)
        self.assertIn("'/srv/companion source/bridge'",argv[-1])
        self.assertIn("'/srv/private state'",argv[-1])
        self.assertEqual(json.loads(kwargs['input_data']),{'action':'task_snapshot','payload':{}})
        for target in ('-bad','host;touch /tmp/bad','user@host\n'):
            with self.assertRaises(ValueError):SSHHubTransport(target,'/src','/state')
        with self.assertRaises(ValueError):transport('snapshot')

    def test_rpc_explicit_state_accepts_configured_ids(self):
        from test_release_terminals import configuration
        with tempfile.TemporaryDirectory() as tmp:
            registry=configuration(tmp);registry.save()
            task=dict(id=str(uuid.uuid4()),target='laptop',prompt='A generic task')
            command=[sys.executable,'-m','helm_remote.rpc','--state-dir',tmp,'--devices-config',str(registry.path)]
            result=subprocess.run(command,input=json.dumps({'action':'task_submit','payload':task}),capture_output=True,text=True,timeout=8,env=dict(os.environ,HOME=tmp))
            self.assertEqual(result.returncode,0,result.stderr)
            response=json.loads(result.stdout)
            self.assertEqual(response['task']['target'],'laptop')
            result=subprocess.run(command,input=json.dumps({'action':'task_claim','payload':dict(target='laptop',workerId=str(uuid.uuid4()))}),capture_output=True,text=True,timeout=8,env=dict(os.environ,HOME=tmp))
            self.assertEqual(json.loads(result.stdout)['task']['id'],task['id'])

    def test_task_capability_requires_current_worker(self):
        from test_release_terminals import configuration
        with tempfile.TemporaryDirectory() as tmp:
            registry=configuration(tmp)
            self.assertFalse(registry.inventory()['devices'][0]['capabilities']['tasks'])
            self.assertTrue(registry.inventory(workers=[dict(target='laptop',seen=1000,state='idle')],now=1001)['devices'][0]['capabilities']['tasks'])
            self.assertTrue(registry.inventory(workers=[dict(target='laptop',seen=1000,state='idle')],now=1100)['devices'][0]['capabilities']['tasks'])
