import json
from pathlib import Path
import tempfile
import unittest
import uuid
from helm_remote.tasks import TaskStore


class ReleaseDevicesTests(unittest.TestCase):
    def test_configured_task_target_and_removed_target_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'tasks.sqlite'
            store = TaskStore(path, targets=['laptop'])
            payload = dict(id=str(uuid.uuid4()), target='laptop', prompt='Hello')
            store.submit(payload)
            with self.assertRaises(ValueError):
                store.submit(dict(payload, id=str(uuid.uuid4()), target='unknown'))
            store.close()
            store = TaskStore(path, targets=['other'])
            self.assertEqual(store.snapshot()['tasks'][0]['target'], 'laptop')
            store.close()

    def test_registry_validates_before_writing_and_does_not_probe(self):
        from helm_remote.devices import DeviceRegistry
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'devices.json'
            config = {'version':1,'devices':[dict(id='laptop', name='Laptop', platform='linux', transport='local', workspace=tmp, terminalEnabled=False, codexCommand='codex')], 'connections':{'netbird':False,'github':False}}
            registry = DeviceRegistry(path, config=config)
            self.assertEqual(registry.inventory()['devices'][0]['status'], 'online')
            registry.save()
            old = path.read_bytes()
            config['devices'][0]['id'] = '../bad'
            with self.assertRaises(ValueError): DeviceRegistry(path, config=config)
            self.assertEqual(path.read_bytes(), old)
            config['devices'][0]['id'] = 'remote'
            config['devices'][0].update(transport='ssh', sshTarget='-oProxyCommand=bad')
            with self.assertRaises(ValueError): DeviceRegistry(path, config=config)
