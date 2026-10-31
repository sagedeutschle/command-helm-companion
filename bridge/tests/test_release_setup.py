import contextlib
import io
import json
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest
from unittest.mock import patch


class ReleaseSetupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('openssl'),'openssl not installed')
    def test_setup_private_tls_no_secret_output_and_no_overwrite(self):
        from helm_remote.setup import main
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'new-state'
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['setup','--state-dir',str(state),'--workspace',tmp]),0)
                self.assertEqual(main(['pair','--state-dir',str(state),'--url','https://localhost:8746']),0)
            self.assertNotIn('helmremote://',output.getvalue())
            token=(state/'phone-token').read_text()
            self.assertNotIn(token,output.getvalue())
            self.assertFalse(json.loads((state/'devices.json').read_text())['devices'][0]['terminalEnabled'])
            self.assertEqual((state.stat().st_mode & 0o777),0o700)
            for name in ('phone-token','bridge-key.pem','devices.json'):
                self.assertEqual(((state/name).stat().st_mode & 0o777),0o600)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(state/'bridge-cert.pem',state/'bridge-key.pem')
            before=(state/'devices.json').read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): main(['setup','--state-dir',str(state),'--workspace',tmp])
            self.assertEqual(before,(state/'devices.json').read_bytes())

    def test_missing_dependency_does_not_create_state(self):
        from helm_remote.setup import main
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'new-state'
            with patch('helm_remote.setup.shutil.which',return_value=None):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): main(['setup','--state-dir',str(state),'--workspace',tmp])
            self.assertFalse(state.exists())

    def test_standalone_generic_worker_executes_only_its_configured_target(self):
        import os
        import subprocess
        import sys
        import uuid
        from helm_remote.devices import DeviceRegistry
        from helm_remote.tasks import TaskStore
        from test_release_terminals import configuration
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            fake=state/'fake-codex'
            fake.write_text('#!'+sys.executable+'\nimport pathlib, sys\npathlib.Path(sys.argv[sys.argv.index("-o")+1]).write_text("Completed configured local task.")\n')
            fake.chmod(0o700)
            registry=configuration(tmp)
            registry.devices['laptop']['codexCommand']=str(fake)
            registry.save()
            store=TaskStore(state/'tasks.sqlite',targets=['laptop'])
            task_id=str(uuid.uuid4())
            store.submit(dict(id=task_id,target='laptop',prompt='A harmless test fixture task'))
            script=Path(__file__).resolve().parents[2]/'scripts/helm-bridge'
            result=subprocess.run([sys.executable,str(script),'worker','--state-dir',tmp,'--target','laptop','--once'],capture_output=True,text=True,timeout=10,env=dict(os.environ,HOME=tmp))
            self.assertEqual(result.returncode,0,result.stderr)
            row=store.snapshot()['tasks'][0]
            self.assertEqual(row['state'],'completed')
            self.assertEqual(row['report'],'Completed configured local task.')
            self.assertEqual(Path(row['deliveryPath']).parent,(state/'deliveries').resolve())
            store.close()

    @unittest.skipUnless(shutil.which('openssl'),'openssl not installed')
    def test_standalone_tls_serve_exposes_release_routes(self):
        import http.client
        import os
        import re
        import selectors
        import subprocess
        import sys
        from helm_remote.setup import main
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state'
            with contextlib.redirect_stdout(io.StringIO()):main(['setup','--state-dir',str(state),'--workspace',tmp])
            script=Path(__file__).resolve().parents[2]/'scripts/helm-bridge'
            process=subprocess.Popen([sys.executable,str(script),'serve','--state-dir',str(state),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,HOME=tmp))
            try:
                selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
                try:self.assertTrue(selector.select(5),'Standalone bridge did not become ready')
                finally:selector.close()
                line=process.stdout.readline()
                match=re.search(r'127\.0\.0\.1:(\d+)',line)
                self.assertIsNotNone(match,line)
                context=ssl.create_default_context(cafile=str(state/'bridge-cert.pem'))
                connection=http.client.HTTPSConnection('localhost',int(match[1]),context=context,timeout=5)
                connection.request('GET','/v1/devices',headers={'Authorization':'Bearer '+(state/'phone-token').read_text().strip()})
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())['devices'][0]['id'],'computer')
                connection.close()
            finally:
                process.terminate()
                try:out,err=process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();out,err=process.communicate()
                self.assertEqual(process.returncode,0,err)
                self.assertNotIn('helmremote://',out+err)
