"""Operator-configured strict SSH transport for a worker's durable task hub."""
import json
from pathlib import PurePosixPath
import shlex
import subprocess
from .connections import bounded_run
from .devices import strict_ssh
from .remote import TransportUnavailable
from .store import Conflict


class SSHHubTransport:
    def __init__(self,target,source_dir,state_dir,host_key_alias=None,runner=bounded_run):
        self.ssh=strict_ssh(target,host_key_alias)
        for value in (source_dir,state_dir):
            if not isinstance(value,str) or not value.startswith('/') or len(value)>4096 or '\x00' in value or '\n' in value:
                raise ValueError('Hub source and state must be absolute POSIX paths')
        self.target,self.source_dir,self.state_dir,self.runner=target,source_dir,state_dir,runner

    def __call__(self,action,payload=None):
        if action not in ('task_snapshot','task_submit','task_claim','task_heartbeat','task_finish'):
            raise ValueError('Only task RPC operations are allowed')
        if payload is not None and not isinstance(payload,dict):raise ValueError('Task object required')
        request=json.dumps(dict(action=action,payload=payload or {}),allow_nan=False).encode()
        if len(request)>524288:raise ValueError('Task RPC request too large')
        source=str(PurePosixPath(self.source_dir)/'bridge')
        config=str(PurePosixPath(self.state_dir)/'devices.json')
        # Operator paths are each quoted exactly once; phone task text goes only via stdin.
        command='env PYTHONPATH='+shlex.quote(source)+' '+shlex.join(['python3','-m','helm_remote.rpc','--state-dir',self.state_dir,'--devices-config',config])
        try:
            result=self.runner([*self.ssh,'-T',self.target,command],timeout=15,limit=1024*1024,input_data=request)
            if result.returncode:raise ValueError()
            response=json.loads(result.stdout)
            if not isinstance(response,dict):raise ValueError()
        except (OSError,ValueError,TypeError,TimeoutError,subprocess.TimeoutExpired):
            raise TransportUnavailable('Configured task hub unavailable; execution outcome is unconfirmed. Check strict SSH access and the configured hub paths.') from None
        if 'error' in response:raise Conflict('Task hub rejected this operation; refresh before retrying. No task is automatically replayed.')
        return response
