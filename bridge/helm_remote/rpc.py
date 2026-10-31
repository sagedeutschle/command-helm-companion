"""Allowlisted bridge operations over an already-authenticated SSH connection."""
import argparse
import json
import os
from pathlib import Path
import sys
from .collector import collect_recent
from .store import Store, Conflict
from .tasks import TaskStore
from .server import DEFAULT_STATE

def main(argv=()):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=DEFAULT_STATE)
    parser.add_argument('--devices-config',type=Path)
    args=parser.parse_args(argv)
    targets=None
    if args.devices_config is not None:
        from .devices import DeviceRegistry
        from .tasks import TARGETS
        targets=set(DeviceRegistry(args.devices_config).devices)|set(TARGETS)
    os.umask(0o077)
    store = Store(args.state_dir/'state.sqlite')
    tasks = TaskStore(args.state_dir/'tasks.sqlite',targets=targets)
    try:
        raw = sys.stdin.read(524289)
        if len(raw) > 524288: raise ValueError('Request too large')
        request = json.loads(raw)
        if not isinstance(request, dict): raise ValueError('Object required')
        action, payload = request.get('action'), request.get('payload', {})
        if isinstance(action, str) and action.startswith('task_'):
            result = tasks.dispatch(action, payload)
        elif action == 'snapshot':
            collect_recent(store)
            result = store.snapshot()
        elif action == 'reply': result = store.reply(payload.get('id'), payload.get('answer'), payload.get('replyId'))
        elif action == 'command': result = store.command(payload.get('id'), payload.get('kind'), payload.get('text'), payload.get('commandId'))
        else: raise ValueError('Unknown operation')
        print(json.dumps(result))
    except (ValueError, TypeError, Conflict): print(json.dumps({'error': 'Remote operation rejected; refresh before retrying.'}))
    finally: store.close(); tasks.close()

if __name__ == '__main__': main(sys.argv[1:])
