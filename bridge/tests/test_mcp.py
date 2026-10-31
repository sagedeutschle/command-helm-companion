import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from helm_remote.store import Store

class MCPTests(unittest.TestCase):
    def test_concurrent_checkpoints_deliver_direction_once(self):
        p,path=self.start('codex')
        self.send(p, {'id':1,'method':'initialize','params':{}}); self.read(p)
        store=Store(path/'state.sqlite')
        try:
            sid=store.snapshot()['projects'][0]['sessions'][0]['id']
            store.command(sid,'guide','Do this once','g1')
            for i in (2,3): self.send(p,{'id':i,'method':'tools/call','params':{'name':'helm_checkpoint','arguments':{}}})
            results=[json.loads(self.read(p)['result']['content'][0]['text']) for _ in range(2)]
            self.assertEqual(sum(len(r['directions']) for r in results),1)
        finally: store.close()
    def test_pause_waits_for_resume_and_delivers_guidance(self):
        for provider in ('claude', 'codex'):
            with self.subTest(provider=provider):
                p, path = self.start(provider)
                self.send(p, {'id':1,'method':'initialize','params':{}})
                self.read(p)
                store = Store(path/'state.sqlite')
                try:
                    sid = store.snapshot()['projects'][0]['sessions'][0]['id']
                    store.command(sid, 'guide', 'Prioritize the import bug', 'g1')
                    store.command(sid, 'pause', 'Wait for me', 'p1')
                    self.send(p, {'id':2,'method':'tools/call','params':{'name':'helm_checkpoint','arguments':{}}})
                    self.assertFalse(select.select([p.stdout], [], [], .5)[0])
                    commands = store.snapshot()['projects'][0]['sessions'][0]['commands']
                    self.assertEqual(commands[0]['state'], 'paused at checkpoint')
                    self.assertEqual(store.command(sid, 'guide', 'Prioritize the import bug', 'g1')['state'], 'queued')
                    store.command(sid, 'resume', 'Continue', 'r1')
                    result = json.loads(self.read(p)['result']['content'][0]['text'])
                    self.assertFalse(result['paused'])
                    self.assertEqual([d['id'] for d in result['directions']], ['g1','p1','r1'])
                    deadline = time.monotonic()+2
                    while time.monotonic() < deadline:
                        commands = store.snapshot()['projects'][0]['sessions'][0]['commands']
                        if all(c['state'] == 'sent to agent connection' for c in commands): break
                        time.sleep(.02)
                    self.assertTrue(all(c['state'] == 'sent to agent connection' for c in commands))
                finally: store.close()

    def start(self, provider):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        p = subprocess.Popen([sys.executable, '-m', 'helm_remote.mcp', '--state-dir', tmp.name, '--provider', provider, '--project', '/tmp/helm-project'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def close():
            if p.poll() is None: p.terminate()
            p.wait(timeout=5)
            p.stdin.close(); p.stdout.close(); p.stderr.close()
        self.addCleanup(close)
        return p, Path(tmp.name)
    def send(self, p, obj):
        p.stdin.write(json.dumps({'jsonrpc':'2.0', **obj})+'\n'); p.stdin.flush()
    def read(self,p):
        self.assertTrue(select.select([p.stdout], [], [], 5)[0], 'MCP response timed out')
        line = p.stdout.readline(); self.assertTrue(line, 'MCP exited without a response')
        return json.loads(line)
    def test_both_providers_receive_only_their_answer(self):
        for provider in ('codex', 'claude'):
            with self.subTest(provider=provider):
                p, path = self.start(provider)
                self.send(p, {'id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'integration-test','version':'1'}}})
                self.assertIn('tools', self.read(p)['result'].get('capabilities', {}))
                self.send(p, {'id':2,'method':'tools/call','params':{'name':'helm_status','arguments':{'state':'working','text':'Running focused checks'}}})
                self.assertFalse(self.read(p)['result'].get('isError', False))
                self.send(p, {'id':3,'method':'tools/call','params':{'name':'helm_ask','arguments':{'question':'Which candidate?','options':['A','B'],'timeoutSeconds':30}}})
                store = Store(path / 'state.sqlite')
                try:
                    deadline = time.monotonic()+5
                    while time.monotonic()<deadline:
                        questions = store.snapshot()['questions']
                        if questions: break
                        time.sleep(.03)
                    self.assertTrue(questions)
                    self.assertEqual(questions[0]['provider'], provider)
                    store.reply(questions[0]['id'], 'B', 'phone-reply')
                    reply = self.read(p)
                    self.assertEqual(reply['id'],3)
                    self.assertEqual(json.loads(reply['result']['content'][0]['text'])['answer'], 'B')
                finally: store.close()
    def test_castra_provider_keeps_one_session_and_a_quiet_checkpoint_keeps_the_state(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        def adapter():
            p = subprocess.Popen([sys.executable, '-m', 'helm_remote.mcp', '--state-dir', tmp.name, '--provider', 'castra', '--session-id', 'castra-phone', '--project', '/tmp/Castra'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.send(p, {'id':1,'method':'initialize','params':{}}); self.read(p)
            return p
        def stop(p):
            p.stdin.close(); p.wait(timeout=5); p.stdout.close(); p.stderr.close()
        p = adapter()
        self.send(p, {'id':2,'method':'tools/call','params':{'name':'helm_status','arguments':{'state':'idle','text':'Nothing running'}}}); self.read(p)
        self.send(p, {'id':3,'method':'tools/call','params':{'name':'helm_checkpoint','arguments':{'quiet':True}}}); self.read(p)
        store = Store(Path(tmp.name)/'state.sqlite')
        try:
            session = store.snapshot()['projects'][0]['sessions'][0]
            self.assertEqual((session['id'], session['provider'], session['state']), ('castra-phone', 'castra', 'idle'))
            self.send(p, {'id':4,'method':'tools/call','params':{'name':'helm_checkpoint','arguments':{}}}); self.read(p)
            self.assertEqual(store.snapshot()['projects'][0]['sessions'][0]['state'], 'working')
            self.send(p, {'id':5,'method':'tools/call','params':{'name':'helm_checkpoint','arguments':{'quiet':'yes'}}})
            self.assertTrue(self.read(p)['result']['isError'])
            stop(p)
            p = adapter()
            self.assertEqual([s['id'] for s in store.snapshot()['projects'][0]['sessions']], ['castra-phone'])
            stop(p)
        finally: store.close()
    def test_cancel_stops_waiting_and_expires_phone_prompt(self):
        p, path = self.start('claude')
        self.send(p, {'id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}})
        self.assertIn('tools', self.read(p)['result'].get('capabilities', {}))
        self.send(p, {'id':2,'method':'tools/call','params':{'name':'helm_ask','arguments':{'question':'Continue?','timeoutSeconds':30}}})
        time.sleep(.2)
        self.send(p, {'method':'notifications/cancelled','params':{'requestId':2}})
        self.assertTrue(self.read(p)['result']['isError'])
        store = Store(path/'state.sqlite')
        try: self.assertEqual(store.snapshot()['questions'][0]['state'], 'cancelled')
        finally: store.close()
