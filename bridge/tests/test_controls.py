import tempfile
import unittest
from pathlib import Path
from helm_remote.store import Store, Conflict

class ControlTests(unittest.TestCase):
    def test_session_scope_offline_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'state.sqlite')
            try:
                store.register('One', '/one', 'codex', 'one')
                store.register('Two', '/two', 'claude', 'two')
                with self.assertRaises(Conflict): store.command('one', 'pause', 'Pause', 'p')
                store.heartbeat('one'); store.heartbeat('two')
                store.command('one', 'pause', 'Pause', 'p')
                self.assertTrue(store.checkpoint('one')['paused'])
                self.assertFalse(store.checkpoint('two')['paused'])
                with self.assertRaises(Conflict): store.command('two', 'pause', 'Pause', 'p')
                store.acknowledge_commands('two', ['p'])
                self.assertEqual(store.command('one', 'pause', 'Pause', 'p')['state'], 'paused at checkpoint')
                store.close()
                store = Store(Path(tmp)/'state.sqlite')
                self.assertTrue(store.checkpoint('one')['paused'])
            finally: store.close()
