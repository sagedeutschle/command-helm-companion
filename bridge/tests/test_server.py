import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from helm_remote.store import Store
from helm_remote.server import make_server

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.sid = self.store.register('Test project','/test','claude','s')
        self.q = self.store.ask(self.sid, 'Which path?', ['Small', 'Large'])
        self.server = make_server(self.store, 'a-test-token', ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store.close(); self.tmp.cleanup()
    def request(self, path, token='a-test-token', body=None, origin=None):
        c = http.client.HTTPConnection(*self.server.server_address)
        headers = {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        c.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse(); result = (r.status, json.loads(r.read())); c.close()
        return result
    def test_private_snapshot_requires_valid_token(self):
        self.assertEqual(self.request('/v1/snapshot', token='wrong')[0], 401)
        code, body = self.request('/v1/snapshot')
        self.assertEqual(code, 200)
        self.assertEqual(body['projects'][0]['name'], 'Test project')
        self.assertNotIn('/test', json.dumps(body))
    def test_browser_origin_is_rejected(self):
        self.assertEqual(self.request('/v1/snapshot', origin='https://evil.test')[0], 403)
    def test_reply_roundtrip_and_duplicate_conflict(self):
        path = '/v1/questions/'+self.q['id']+'/reply'
        self.assertEqual(self.request(path, body={'answer': 'Small', 'replyId': 'r1'})[0], 200)
        self.assertEqual(self.store.receive(self.sid, self.q['id'])['answer'], 'Small')
        self.assertEqual(self.request(path, body={'answer': 'Large', 'replyId': 'r2'})[0], 409)
    def test_malformed_reply_does_not_break_service(self):
        self.assertEqual(self.request('/v1/questions/'+self.q['id']+'/reply', body=[])[0], 400)
        self.assertEqual(self.request('/v1/snapshot')[0], 200)
    def test_controls_require_live_connection_and_reject_reused_id(self):
        path='/v1/sessions/'+self.sid+'/commands'
        body={'kind':'pause','text':'Pause','commandId':'c1'}
        self.assertEqual(self.request(path,body=body)[0],409)
        self.store.heartbeat(self.sid)
        self.assertEqual(self.request(path,body=body)[0],200)
        self.assertTrue(self.store.checkpoint(self.sid)['paused'])
        self.assertEqual(self.request(path,body=body)[0],200)
        body['kind']='resume'
        self.assertEqual(self.request(path,body=body)[0],409)
        body['commandId']='c2'
        self.assertEqual(self.request(path,body=body)[0],200)
        self.assertFalse(self.store.checkpoint(self.sid)['paused'])
