import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

class ReleaseConnectionsTests(unittest.TestCase):
    def test_opt_in_bounded_allowlisted_output(self):
        from helm_remote.devices import DeviceRegistry
        from helm_remote.connections import Connections
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp)/'devices.json', config={'version':1,'devices':[], 'connections':{'netbird':False,'github':False}})
            calls=[]
            def runner(argv):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps({'hosts':{'github.com':[{'active':True,'state':'success','login':'developer','token':'DO-NOT-RETURN'}]}}).encode())
            connections = Connections(registry, runner=runner)
            self.assertEqual(connections.snapshot()['connections'][1]['status'], 'disabled')
            self.assertEqual(calls, [])
            result = connections.enable({'id':'github','enabled':True})
            self.assertEqual(result['connections'][1]['account'], 'developer')
            self.assertNotIn('DO-NOT-RETURN', json.dumps(result))
            self.assertTrue(DeviceRegistry(registry.path).connections['github'])
            with self.assertRaises(ValueError): connections.enable({'id':'github','enabled':'yes'})

    def test_missing_cli_and_malformed_json_never_echo_output(self):
        from helm_remote.devices import DeviceRegistry
        from helm_remote.connections import Connections
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp)/'devices.json', config={'version':1,'devices':[], 'connections':{'netbird':True,'github':True}})
            def missing(argv): raise FileNotFoundError()
            result = Connections(registry, runner=missing).snapshot()
            self.assertEqual([x['status'] for x in result['connections']], ['notInstalled','notInstalled'])
            result = Connections(registry, runner=lambda argv: SimpleNamespace(returncode=0,stdout=b'PRIVATE MALFORMED')).snapshot()
            self.assertEqual([x['status'] for x in result['connections']], ['error','error'])
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_netbird_documented_fields_and_redaction(self):
        from helm_remote.devices import DeviceRegistry
        from helm_remote.connections import Connections
        with tempfile.TemporaryDirectory() as tmp:
            registry=DeviceRegistry(Path(tmp)/'devices.json',config={'version':1,'devices':[],'connections':{'netbird':True}})
            sample={'management':{'connected':True,'url':'PRIVATE-MANAGEMENT','error':''},'peers':{'total':1,'connected':1,'details':[{'fqdn':'workstation.example','netbirdIp':'100.64.0.42/32','publicKey':'DO-NOT-RETURN','status':'Connected','connectionType':'P2P','networks':[]}]},'publicKey':'DO-NOT-RETURN'}
            result=Connections(registry,runner=lambda argv:SimpleNamespace(returncode=0,stdout=json.dumps(sample).encode())).snapshot()
            self.assertEqual(result['connections'][0]['status'],'connected')
            self.assertEqual(result['peers'],[dict(id='100.64.0.42',name='workstation.example',address='100.64.0.42',status='online',os='')])
            self.assertNotIn('DO-NOT-RETURN',json.dumps(result))
            self.assertNotIn('PRIVATE-MANAGEMENT',json.dumps(result))

    def test_real_subprocess_time_and_output_limits(self):
        import sys
        from helm_remote.connections import bounded_run
        with self.assertRaises(ValueError):bounded_run([sys.executable,'-c','print("x"*100000)'],limit=1024)
        with self.assertRaises(TimeoutError):bounded_run([sys.executable,'-c','import time; time.sleep(2)'],timeout=.05)
        response=bounded_run([sys.executable,'-c','import sys; print(sys.stdin.read())'],input_data=b'fixed private input',timeout=2)
        self.assertEqual(response.stdout,b'fixed private input\n')
