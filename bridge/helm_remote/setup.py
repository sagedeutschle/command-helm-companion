"""Standalone Mac/Linux bridge setup, serving and local task worker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import sys
import tempfile
import threading
from urllib.parse import urlencode, urlsplit
from .connections import Connections, bounded_run
from .devices import DeviceRegistry, atomic_json, device_id
from .server import DEFAULT_STATE, ensure_token, make_server
from .store import Store, Conflict
from .tasks import TaskStore, TaskProxy, TARGETS
from .terminals import TerminalManager
from .worker import Worker


def configure(args):
    if args.state_dir.exists(): raise ValueError('State directory already exists; setup will not overwrite pairing or device configuration. Use a new --state-dir.')
    if not shutil.which('openssl'): raise ValueError('OpenSSL is required to generate a private TLS certificate. Install it locally, then run setup again.')
    workspace=args.workspace.expanduser().resolve()
    if not workspace.is_dir(): raise ValueError('Workspace must be an existing directory')
    device_id(args.device_id)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',args.hostname): raise ValueError('Hostname must be a DNS name or IPv4 address')
    if args.enable_terminal and not shutil.which('tmux'): raise ValueError('tmux is required when --enable-terminal is selected. Install it locally, then run setup again.')
    config={'version':1,'devices':[dict(id=args.device_id,name=args.name,platform='macos' if sys.platform=='darwin' else 'linux',transport='local',sshTarget='',workspace=str(workspace),terminalEnabled=args.enable_terminal,codexCommand='codex')], 'connections':{'netbird':False,'github':False}}
    # Validate everything before touching state, and publish only a complete setup.
    DeviceRegistry(args.state_dir/'devices.json',config=config)
    args.state_dir.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=Path(tempfile.mkdtemp(prefix='.helm-setup-',dir=args.state_dir.parent))
    try:
        registry=DeviceRegistry(temporary/'devices.json',config=config);registry.save()
        ensure_token(temporary/'phone-token')
        dns='DNS:'+args.hostname
        try:
            import ipaddress
            dns='IP:'+str(ipaddress.ip_address(args.hostname))
        except ValueError: pass
        result=bounded_run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','825',
            '-keyout',str(temporary/'bridge-key.pem'),'-out',str(temporary/'bridge-cert.pem'),
            '-subj','/CN=Command Helm Bridge','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1,'+dns],timeout=20)
        if result.returncode: raise ValueError('TLS certificate generation failed. Check that the installed OpenSSL supports req -addext.')
        for child in temporary.iterdir(): child.chmod(0o600)
        # Refuse races with another setup; rename cannot replace a nonempty directory.
        if args.state_dir.exists(): raise ValueError('State directory appeared during setup; it was not changed')
        temporary.rename(args.state_dir)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    print('Private bridge state and TLS certificate created. Connections remain disabled. Terminals '+('enabled.' if args.enable_terminal else 'disabled; edit devices.json to opt in.'))
    print('Start with helm-bridge serve. Pairing credentials stay in the private state directory.')
    return 0


def pairing(args):
    parts=urlsplit(args.url)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('Pairing URL must be an HTTPS origin without credentials, query or fragment')
    # Credentials are read only by this explicit local pairing command, never logged by serve.
    certificate=(args.state_dir/'bridge-cert.pem').read_text()
    fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    if args.show_uri:
        token=(args.state_dir/'phone-token').read_text().strip()
        print('helmremote://pair?'+urlencode(dict(url=args.url.rstrip('/'),token=token,fingerprint=fingerprint)))
    else:
        print('Certificate SHA-256: '+fingerprint)
        print('Pairing URI hidden. Use pair --show-uri only in a private terminal; it contains the bridge credential.')
    return 0


def serve(args):
    registry=DeviceRegistry(args.state_dir/'devices.json')
    if args.host not in ('127.0.0.1','localhost','::1') and not args.allow_remote:
        raise ValueError('A remote listener requires explicit --allow-remote and --host. Prefer a private network address.')
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(args.state_dir/'bridge-cert.pem',args.state_dir/'bridge-key.pem')
    store=Store(args.state_dir/'state.sqlite')
    tasks=TaskStore(args.state_dir/'tasks.sqlite',targets=set(registry.devices)|set(TARGETS))
    terminals=None;server=None
    stop=threading.Event()
    def shutdown(signum,frame):
        stop.set()
        if server: threading.Thread(target=server.shutdown,daemon=True).start()
    try:
        terminals=TerminalManager(args.state_dir/'terminals',registry)
        server=make_server(store,ensure_token(args.state_dir/'phone-token'),(args.host,args.port),tasks=tasks,devices=registry,connections=Connections(registry),terminals=terminals)
        server.socket=ctx.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
        print('Pinned TLS bridge listening on '+args.host+':'+str(server.server_port)+'. Pairing credentials remain private.',flush=True)
        collector=None
        if args.collect_local:
            from .collector import collect_recent
            def collect():
                while not stop.is_set():
                    collect_recent(store)
                    stop.wait(30)
            collector=threading.Thread(target=collect,daemon=True);collector.start()
        try:server.serve_forever()
        finally:
            stop.set()
            if collector:collector.join(timeout=10)
    finally:
        if server:server.server_close()
        if terminals:terminals.shutdown()
        tasks.close();store.close()
    return 0


def worker(args):
    registry=DeviceRegistry(args.state_dir/'devices.json')
    device=registry.devices.get(args.target)
    if device is None or device['transport']!='local': raise ValueError('Worker target must be an explicitly configured local device on this computer. SSH terminals do not automatically enroll task workers.')
    if not shutil.which(device['codexCommand']): raise ValueError('Codex is not installed. Install and authenticate it locally before starting the worker.')
    if args.hub_target:
        if not args.hub_source_dir or not args.hub_state_dir:raise ValueError('--hub-target requires --hub-source-dir and --hub-state-dir')
        from .hub import SSHHubTransport
        hub=TaskProxy(SSHHubTransport(args.hub_target,args.hub_source_dir,args.hub_state_dir,args.hub_host_key_alias))
    else:
        if args.hub_source_dir or args.hub_state_dir or args.hub_host_key_alias:raise ValueError('Hub path/alias flags require --hub-target')
        hub=TaskStore(args.state_dir/'tasks.sqlite',targets=set(registry.devices)|set(TARGETS))
    stop=threading.Event()
    def shutdown(signum,frame):stop.set()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    try:
        return Worker(args.target,hub,args.state_dir,codex=device['codexCommand'],
            deliveries=args.state_dir/'deliveries',stop=stop,targets=registry.devices,default_workspace=device['workspace']).run(once=args.once)
    finally:
        if isinstance(hub,TaskStore):hub.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('setup','serve','worker','pair'):
        command=commands.add_parser(name)
        command.add_argument('--state-dir',type=Path,default=DEFAULT_STATE)
        if name=='setup':
            command.add_argument('--workspace',type=Path,default=Path.cwd())
            command.add_argument('--device-id',default='computer')
            command.add_argument('--name',default='My computer')
            command.add_argument('--hostname',default='localhost')
            command.add_argument('--enable-terminal',action='store_true')
        elif name=='serve':
            command.add_argument('--host',default='127.0.0.1')
            command.add_argument('--port',type=int,default=8746)
            command.add_argument('--allow-remote',action='store_true')
            command.add_argument('--collect-local',action='store_true',help='Opt in to reading recent local assistant session excerpts for monitoring')
        elif name=='worker':
            command.add_argument('--target',required=True)
            command.add_argument('--once',action='store_true')
            command.add_argument('--hub-target')
            command.add_argument('--hub-source-dir')
            command.add_argument('--hub-state-dir')
            command.add_argument('--hub-host-key-alias')
        else:
            command.add_argument('--url',required=True)
            command.add_argument('--show-uri',action='store_true')
    args=parser.parse_args(argv)
    args.state_dir=args.state_dir.expanduser().absolute()
    os.umask(0o077)
    try:return {'setup':configure,'serve':serve,'worker':worker,'pair':pairing}[args.command](args)
    except (OSError,ValueError,KeyError,Conflict) as exc:
        # Never include raw subprocess diagnostics, TLS key contents or credentials.
        if isinstance(exc,(ValueError,Conflict)): parser.error(str(exc))
        parser.error('Local bridge operation failed. Check state paths, dependencies and listener availability.')

if __name__=='__main__':sys.exit(main())
