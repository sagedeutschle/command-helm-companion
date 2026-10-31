import tempfile
import unittest
from pathlib import Path
from helm_remote.store import Store, Conflict
from helm_remote.remote import FederatedStore

class RemoteTests(unittest.TestCase):
    def test_routing_and_offline_retains_updates_disables_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Store(Path(tmp)/'local.sqlite')
            remote = Store(Path(tmp)/'remote.sqlite')
            try:
                for store in (local,remote):
                    store.register('Project','/project','claude','session')
                    store.heartbeat('session')
                q=remote.ask('session','Which direction?')['id']
                calls=[]
                def transport(action,payload=None):
                    calls.append(action)
                    if action=='snapshot': return remote.snapshot()
                    if action=='reply': return remote.reply(payload['id'],payload['answer'],payload['replyId'])
                    if action=='command': return remote.command(payload['id'],payload['kind'],payload['text'],payload['commandId'])
                hub=FederatedStore(local,transport)
                hub.poll()
                snapshot=hub.snapshot()
                self.assertEqual(len(snapshot['projects']),2)
                self.assertNotEqual(snapshot['projects'][0]['id'], snapshot['projects'][1]['id'])
                hub.command('spelltower-session','pause','Pause','cmd')
                self.assertTrue(remote.checkpoint('session')['paused'])
                self.assertFalse(local.checkpoint('session')['paused'])
                hub.reply('spelltower-'+q,'Go','reply')
                self.assertEqual(remote.receive('session',q)['answer'],'Go')
                hub.transport=lambda *a: (_ for _ in ()).throw(Conflict('offline'))
                hub.poll()
                project=hub.snapshot()['projects'][1]
                self.assertIn('Offline',project['name'])
                self.assertFalse(project['sessions'][0]['controllable'])
            finally: local.close(); remote.close()
