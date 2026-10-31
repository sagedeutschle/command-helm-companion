import base64
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import uuid
from types import SimpleNamespace
from helm_remote.store import Conflict


def configuration(tmp, enabled=True, transport='local'):
    from helm_remote.devices import DeviceRegistry
    return DeviceRegistry(Path(tmp)/'devices.json', config={'version':1,'devices':[dict(id='laptop',name='Laptop',platform='linux',transport=transport,sshTarget='operator@host',hostKeyAlias='trusted-host',workspace=tmp,terminalEnabled=enabled,codexCommand='codex')],'connections':{}})


def start_payload():
    return dict(id=str(uuid.uuid4()),deviceId='laptop',kind='shell',cols=80,rows=24)


class FakeTransport:
    instances=[]
    def __init__(self, device, socket, session):
        self.writes=[]; self.alive=True; self.__class__.instances.append(self)
    def create(self, kind, cols, rows): pass
    def attach(self, cols, rows, receiver, disconnected): self.receiver=receiver; self.disconnected=disconnected
    def write(self, data): self.writes.append(data)
    def resize(self, cols, rows): pass
    def terminate(self): self.alive=False
    def detach(self): pass


class SimulatedTmux:
    """Remote command fault model; no subprocess, SSH or user socket access."""
    def __init__(self):
        self.present=False
        self.unknown=False
        self.create_failure=False
        self.fault_setting=None
        self.lost_setting_ack=False
        self.kill_failure=None
        self.creates=0
        self.kills=0
        self.attachments=[]
        self.settings={}

    def factory(self, device, socket, session):
        from helm_remote.terminals import TmuxTransport, TerminalUnavailable
        backend=self
        class Transport(TmuxTransport):
            def run(self,argv,check=True):
                def result(code=0,output=b''):
                    if check and code:raise TerminalUnavailable('Simulated command failure')
                    return SimpleNamespace(returncode=code,stdout=output)
                if argv[:2]==['sh','-c']:
                    if backend.unknown:return result(255)
                    return result(output=(b'HELM_SESSIONS\n'+self.session.encode()+b'\n') if backend.present else b'HELM_ABSENT\n')
                operation=argv[5]
                args=argv[6:]
                if operation=='new-session':
                    backend.creates+=1
                    if backend.create_failure:raise TerminalUnavailable('Creation outcome unavailable')
                    backend.present=True
                elif operation=='set-option':
                    key,value=args[-2:]
                    if backend.fault_setting==key:
                        backend.fault_setting=None
                        if backend.lost_setting_ack:
                            backend.settings[key]=value
                            backend.unknown=True
                        raise TerminalUnavailable('Setting outcome unavailable')
                    backend.settings[key]=value
                elif operation=='has-session':
                    return result(255 if backend.unknown else (0 if backend.present else 1))
                elif operation=='kill-session':
                    backend.kills+=1
                    backend.present=False
                    if backend.kill_failure:
                        if backend.kill_failure=='unknown':backend.unknown=True
                        backend.kill_failure=None
                        raise TerminalUnavailable('Kill acknowledgement lost')
                elif operation=='display-message':return result(output=b'0')
                return result()
            def attach(self,cols,rows,receiver,disconnected):
                # A regression must fail in this process, never launch fixture SSH.
                from unittest.mock import patch
                with patch('helm_remote.terminals.subprocess.Popen',side_effect=AssertionError('Unexpected real attachment in simulated transport')):
                    return super().attach(cols,rows,receiver,disconnected)
            def _attach_pty(self,cols,rows,receiver,disconnected):
                backend.attachments.append(dict(backend.settings))
                self.process=SimpleNamespace(poll=lambda:None)
                self.receiver=receiver
            def write(self,data):pass
            def detach(self):self.process=None
        return Transport(device,socket,session)


class ReleaseTerminalTests(unittest.TestCase):
    def test_idempotent_input_and_ambiguous_failure_survive_restart(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        with tempfile.TemporaryDirectory() as tmp:
            registry=configuration(tmp)
            manager=TerminalManager(Path(tmp)/'terminals', registry, transport_factory=FakeTransport)
            payload=start_payload(); manager.start(payload)
            self.assertEqual(manager.start(payload)['session']['id'],payload['id'])
            with self.assertRaises(Conflict): manager.start(dict(payload,cols=90))
            input_payload=dict(id=str(uuid.uuid4()),data=base64.b64encode(b'hello').decode())
            manager.input(payload['id'],input_payload); manager.input(payload['id'],input_payload)
            self.assertEqual(FakeTransport.instances[-1].writes,[b'hello'])
            with self.assertRaises(Conflict): manager.input(payload['id'],dict(input_payload,data='YQ=='))
            transport=FakeTransport.instances[-1]
            def ambiguous(data): raise OSError('private raw transport detail')
            transport.write=ambiguous
            uncertain=dict(id=str(uuid.uuid4()),data='Yg==')
            with self.assertRaises(TerminalUnavailable): manager.input(payload['id'],uncertain)
            manager.shutdown()
            manager=TerminalManager(Path(tmp)/'terminals',registry,transport_factory=FakeTransport)
            count=len(FakeTransport.instances)
            manager.input(payload['id'],input_payload)
            with self.assertRaises(Conflict): manager.input(payload['id'],uncertain)
            self.assertEqual(len(FakeTransport.instances),count)
            self.assertEqual(FakeTransport.instances[-1].writes,[b'hello'])
            manager.shutdown()

    def test_output_bounds_cursor_reset_and_validation(self):
        from helm_remote.terminals import TerminalManager
        with tempfile.TemporaryDirectory() as tmp:
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp),transport_factory=FakeTransport)
            payload=start_payload(); manager.start(payload)
            FakeTransport.instances[-1].receiver(b'a'*(1024*1024+100))
            result=manager.output(payload['id'],0)
            self.assertTrue(result['reset']); self.assertEqual(result['baseCursor'],100)
            self.assertEqual(len(base64.b64decode(result['data'])),65536)
            self.assertEqual(result['cursor'],65636)
            for bad in (-1,True,2**63):
                with self.assertRaises(ValueError): manager.output(payload['id'],bad)
            for bad in ({'cols':1,'rows':24},{'cols':80,'rows':201},{'cols':True,'rows':24}):
                with self.assertRaises(ValueError): manager.resize(payload['id'],bad)
            for bad in ('%%%','a'*24000):
                with self.assertRaises(ValueError): manager.input(payload['id'],dict(id=str(uuid.uuid4()),data=bad))
            with self.assertRaises(ValueError): manager.start(dict(start_payload(),command='rm -rf /'))
            manager.close(payload['id']); manager.close(payload['id'])
            self.assertEqual(manager.sessions()['sessions'][0]['state'],'exited')
            manager.shutdown()

    def test_disabled_unknown_device_and_session_cap(self):
        from helm_remote.terminals import TerminalManager
        with tempfile.TemporaryDirectory() as tmp:
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp,False),transport_factory=FakeTransport)
            with self.assertRaises(ValueError): manager.start(start_payload())
            manager.shutdown()
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp),transport_factory=FakeTransport)
            with self.assertRaises(ValueError): manager.start(dict(start_payload(),deviceId='unknown'))
            for _ in range(8): manager.start(start_payload())
            with self.assertRaises(Conflict): manager.start(start_payload())
            manager.shutdown()

    def test_failed_close_retains_transport_for_inspection(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        with tempfile.TemporaryDirectory() as tmp:
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp),transport_factory=FakeTransport)
            payload=start_payload();manager.start(payload)
            transport=FakeTransport.instances[-1]
            original=transport.terminate
            def unavailable():raise TerminalUnavailable('Computer disconnected')
            transport.terminate=unavailable
            with self.assertRaises(TerminalUnavailable):manager.close(payload['id'])
            manager.input(payload['id'],dict(id=str(uuid.uuid4()),data='YQ=='))
            self.assertEqual(transport.writes,[b'a'])
            transport.terminate=original
            manager.close(payload['id']);manager.shutdown()

    def test_confirmed_failed_starts_do_not_consume_active_slots_or_replay(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        with tempfile.TemporaryDirectory() as tmp:
            backend=SimulatedTmux();backend.create_failure=True
            registry=configuration(tmp,transport='ssh')
            state=Path(tmp)/'terminals'
            manager=TerminalManager(state,registry,transport_factory=backend.factory)
            try:
                first=start_payload()
                for payload in [first]+[start_payload() for _ in range(8)]:
                    with self.assertRaises(TerminalUnavailable):manager.start(payload)
                    self.assertEqual(manager.start(payload)['session']['state'],'exited')
                    self.assertEqual(manager.close(payload['id']),{'ok':True})
                self.assertEqual(backend.creates,9)
            finally:manager.shutdown()
            manager=TerminalManager(state,registry,transport_factory=backend.factory)
            try:
                self.assertEqual(manager.start(first)['session']['state'],'exited')
                self.assertEqual(backend.creates,9)
                backend.create_failure=False
                self.assertEqual(manager.start(start_payload())['session']['state'],'running')
            finally:manager.shutdown()

    def test_unknown_creation_and_close_preserve_identity_until_absence_confirmed(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        with tempfile.TemporaryDirectory() as tmp:
            backend=SimulatedTmux();backend.create_failure=True;backend.unknown=True
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp,transport='ssh'),transport_factory=backend.factory)
            try:
                payload=start_payload()
                with self.assertRaises(TerminalUnavailable):manager.start(payload)
                self.assertEqual(manager.start(payload)['session']['state'],'disconnected')
                with self.assertRaises(TerminalUnavailable):manager.close(payload['id'])
                self.assertNotEqual(manager.sessions()['sessions'][0]['state'],'exited')
                self.assertEqual(backend.creates,1)
                backend.unknown=False
                self.assertEqual(manager.close(payload['id']),{'ok':True})
                self.assertEqual(manager.start(payload)['session']['state'],'exited')
                self.assertEqual(backend.creates,1)
            finally:manager.shutdown()

    def test_lost_kill_ack_requires_positive_absence_not_uncertain_ssh(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        for outcome in ('lost','unknown'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                backend=SimulatedTmux()
                manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp,transport='ssh'),transport_factory=backend.factory)
                try:
                    payload=start_payload();manager.start(payload)
                    backend.kill_failure=outcome
                    if outcome=='lost':self.assertEqual(manager.close(payload['id']),{'ok':True})
                    else:
                        with self.assertRaises(TerminalUnavailable):manager.close(payload['id'])
                        self.assertNotEqual(manager.sessions()['sessions'][0]['state'],'exited')
                        backend.unknown=False
                        self.assertEqual(manager.close(payload['id']),{'ok':True})
                    self.assertEqual(manager.close(payload['id']),{'ok':True})
                    self.assertEqual(manager.start(payload)['session']['state'],'exited')
                    self.assertEqual((backend.creates,backend.kills),(1,1))
                finally:manager.shutdown()

    def test_every_partial_configuration_is_completed_before_reconnect_without_recreation(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable
        required={'remain-on-exit':'on','window-size':'manual','prefix':'None','prefix2':'None','status':'off'}
        for setting in required:
            for lost_ack in (False,True):
                with self.subTest(setting=setting,lost_ack=lost_ack), tempfile.TemporaryDirectory() as tmp:
                    backend=SimulatedTmux();backend.fault_setting=setting;backend.lost_setting_ack=lost_ack
                    registry=configuration(tmp,transport='ssh');state=Path(tmp)/'terminals'
                    manager=TerminalManager(state,registry,transport_factory=backend.factory)
                    payload=start_payload()
                    try:
                        with self.assertRaises(TerminalUnavailable):manager.start(payload)
                        self.assertEqual(manager.sessions()['sessions'][0]['state'],'disconnected')
                        self.assertEqual(backend.attachments,[])
                    finally:manager.shutdown()
                    backend.unknown=False
                    manager=TerminalManager(state,registry,transport_factory=backend.factory)
                    try:
                        self.assertEqual(manager.output(payload['id'],0)['state'],'running')
                        self.assertEqual(backend.attachments,[required])
                        self.assertEqual(backend.creates,1)
                        self.assertEqual(manager.start(payload)['session']['id'],payload['id'])
                        self.assertEqual(backend.creates,1)
                        manager.close(payload['id'])
                    finally:manager.shutdown()

    def test_production_presence_distinguishes_structured_proof_from_unknown_ssh(self):
        from helm_remote.terminals import TmuxTransport
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            session='h'+uuid.uuid4().hex
            transport=TmuxTransport(configuration(tmp,transport='ssh').devices['laptop'],'helm-mobile-'+uuid.uuid4().hex,session)
            cases=[(255,b'HELM_ABSENT\n','unknown'),(1,b'','unknown'),(0,b'PRIVATE RAW ERROR','unknown'),
                   (0,b'HELM_ABSENT\n','absent'),(0,b'HELM_SESSIONS\nother-owned-session\n','absent'),
                   (0,b'HELM_SESSIONS\n'+session.encode()+b'\n','present')]
            for status,output,expected in cases:
                with self.subTest(status=status,expected=expected), patch('helm_remote.terminals.bounded_run',return_value=SimpleNamespace(returncode=status,stdout=output)):
                    self.assertEqual(transport.presence(),expected)
            with patch('helm_remote.terminals.bounded_run',side_effect=TimeoutError('SSH timeout')):
                self.assertEqual(transport.presence(),'unknown')

    @unittest.skipUnless(shutil.which('tmux'),'tmux not installed')
    def test_real_missing_executable_failures_tombstone_absent_owned_socket(self):
        from helm_remote.terminals import TerminalManager, TerminalUnavailable, TmuxTransport
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            manager=TerminalManager(Path(tmp)/'terminals',configuration(tmp))
            try:
                payload=start_payload()
                transport=TmuxTransport(configuration(tmp).devices['laptop'],manager.socket,'h'+uuid.UUID(payload['id']).hex)
                self.assertEqual(transport.presence(),'absent')
                with patch('helm_remote.terminals.shutil.which',return_value=None):
                    for item in [payload]+[start_payload() for _ in range(8)]:
                        with self.assertRaises(TerminalUnavailable):manager.start(item)
                        self.assertEqual(manager.close(item['id']),{'ok':True})
                        self.assertEqual(manager.start(item)['session']['state'],'exited')
                self.assertTrue(all(row['state']=='exited' for row in manager.sessions()['sessions']))
                self.assertEqual(transport.presence(),'absent')
            finally:manager.shutdown()

    def test_strict_ssh_argv_is_fixed_configuration(self):
        from helm_remote.terminals import TmuxTransport
        with tempfile.TemporaryDirectory() as tmp:
            device=configuration(tmp,transport='ssh').devices['laptop']
            transport=TmuxTransport(device,'helm-mobile-'+uuid.uuid4().hex,'h'+uuid.uuid4().hex)
            args=transport.command(['tmux','-L',transport.socket,'has-session','-t',transport.session])
            self.assertIn('StrictHostKeyChecking=yes',args)
            self.assertIn('EscapeChar=none',args)
            self.assertIn('BatchMode=yes',args)
            self.assertIn('HostKeyAlias=trusted-host',args)
            self.assertIn('operator@host',args)

    @unittest.skipUnless(shutil.which('tmux'),'tmux not installed')
    def test_real_tmux_pty_reconnect_resize_and_close(self):
        from helm_remote.terminals import TerminalManager
        with tempfile.TemporaryDirectory() as tmp:
            registry=configuration(tmp)
            state=Path(tmp)/'terminals'
            manager=TerminalManager(state,registry)
            payload=start_payload(); session=payload['id']
            try:
                manager.start(payload)
                manager.resize(session,dict(cols=91,rows=27))
                manager.input(session,dict(id=str(uuid.uuid4()),data=base64.b64encode(b"printf 'HELM_%s\\n' 'PTY_OK'; stty size\n").decode()))
                collected=b''; cursor=0
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    output=manager.output(session,cursor); cursor=output['cursor']; collected+=base64.b64decode(output['data'])
                    if b'HELM_PTY_OK' in collected and b'27 91' in collected: break
                    time.sleep(.05)
                self.assertIn(b'HELM_PTY_OK',collected)
                self.assertIn(b'27 91',collected)
                manager.shutdown()
                manager=TerminalManager(state,registry)
                self.assertEqual(manager.sessions()['sessions'][0]['id'],session)
                output=manager.output(session,cursor)
                self.assertTrue(output['reset'])
                manager.input(session,dict(id=str(uuid.uuid4()),data=base64.b64encode(b"printf 'HELM_%s\\n' 'RECONNECTED'\n").decode()))
                collected=b''; cursor=output['cursor']; deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    output=manager.output(session,cursor);cursor=output['cursor'];collected+=base64.b64decode(output['data'])
                    if b'HELM_RECONNECTED' in collected: break
                    time.sleep(.05)
                self.assertIn(b'HELM_RECONNECTED',collected)
                manager.close(session)
                self.assertEqual(manager.sessions()['sessions'][0]['state'],'exited')
            finally:
                try: manager.close(session)
                except Exception: pass
                manager.shutdown()
