"""Run local Codex tasks claimed from the private Command Helm hub."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from .tasks import TaskStore, TaskProxy, TARGETS, safe_text
from .server import DEFAULT_STATE
from .store import Conflict


class Worker:
    def __init__(self, target, hub, state_dir, codex='codex', deliveries=None,
                 poll_interval=5, heartbeat_interval=15, stop=None, targets=None, default_workspace=None):
        if target not in (TARGETS if targets is None else targets):
            raise ValueError('Unknown target')
        self.default_workspace = default_workspace
        self.target, self.hub = target, hub
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.codex = str(codex)
        self.deliveries = Path(deliveries or Path.home()/'CommandHelm/Deliveries').expanduser().resolve()
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.stop = stop or threading.Event()
        self.worker_id = str(uuid.uuid4())
        self.lock_fd = None

    def _call(self, action, **payload):
        return self.hub.dispatch(action, dict(target=self.target, workerId=self.worker_id, **payload))

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: return
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()

    def _record(self, task_id='', child_pid=None):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = dict(target=self.target, workerId=self.worker_id, workerPid=os.getpid(),
                      childPid=child_pid, taskId=task_id, updatedAt=time.time())
        path = self.state_dir / ('task-worker-'+self.target+'.json')
        temporary = path.with_suffix('.tmp-'+self.worker_id)
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def _failure_hint(output_path):
        try:
            with output_path.open('rb') as source:
                source.seek(0, 2)
                source.seek(max(0, source.tell()-8192))
                tail = source.read().decode('utf-8', errors='replace').lower()
            if any(term in tail for term in ('not logged in', 'authentication failed', 'unauthorized', 'please log in', 'invalid api key', '401 unauthorized')):
                return ' Codex authentication failed on this target; authenticate locally before submitting a new task.'
            if any(term in tail for term in ('quota exceeded', 'insufficient_quota', 'rate limit', 'usage limit', 'too many requests')):
                return ' Codex account usage or rate limit reached on this target; check account availability before submitting a new task.'
        except OSError:
            pass
        return ''

    def _execute(self, task):
        delivery = self.deliveries / task['id']
        process = None
        state, error, report = 'failed', '', ''
        try:
            if self.stop.is_set():
                return 'interrupted', '', '', 'Worker stopped before execution'
            self.deliveries.mkdir(parents=True, exist_ok=True, mode=0o700)
            # A pre-existing delivery is evidence of a previous attempt. Do not overwrite it.
            delivery.mkdir(mode=0o700)
            workspace = Path(task['workspace'] or self.default_workspace) if (task['workspace'] or self.default_workspace) else delivery
            if not workspace.is_dir():
                raise ValueError('Requested workspace is not an existing directory on this target')
            report_path = delivery / 'REPORT.md'
            argv = [self.codex, 'exec', '-s', 'workspace-write', '-c',
                    'sandbox_workspace_write.network_access=true', '--skip-git-repo-check',
                    '-C', str(workspace), '-o', str(report_path), '-']
            with (delivery/'output.log').open('xb') as output:
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True,
                                           pass_fds=(self.lock_fd,) if self.lock_fd is not None else ())
                next_heartbeat = time.monotonic() + self.heartbeat_interval
                self._record(task['id'], process.pid)
                preamble = (f"Command Helm task {task['id']} on destination {self.target}.\n"
                            f"Working directory: {workspace}\nDelivery directory: {delivery}\n"
                            "Follow existing repository and agent instructions. Complete the requested work in the working directory. "
                            "Your final response becomes REPORT.md in the delivery directory; summarize changes, validation, and any remaining blockers honestly. "
                            "Keep credentials and tokens out of your final response.\n\nUser task follows:\n")
                pending_input = (preamble + task['prompt']).encode('utf-8')
                while True:
                    if self.stop.is_set():
                        state, error = 'interrupted', 'Worker stopped; this task will not replay'
                        self._terminate(process)
                        break
                    if time.monotonic() >= next_heartbeat:
                        try: self._call('task_heartbeat', taskId=task['id'])
                        except (Conflict, OSError, ValueError, subprocess.TimeoutExpired):
                            state, error = 'interrupted', 'Hub connection or claim lost; execution stopped with outcome unconfirmed'
                            self._terminate(process)
                            break
                        next_heartbeat = time.monotonic() + self.heartbeat_interval
                    try:
                        process.communicate(input=pending_input, timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        pending_input = None
            if report_path.is_file() and not report_path.is_symlink():
                with report_path.open('r', encoding='utf-8', errors='replace') as source:
                    report = safe_text(source.read(24000))
            if state != 'interrupted':
                if process.returncode != 0:
                    error = ('Codex exited with status '+str(process.returncode)+'; review the private output.log'
                             + self._failure_hint(delivery/'output.log'))
                elif not report:
                    error = 'Codex exited without a usable REPORT.md; delivery is not confirmed'
                else:
                    state = 'completed'
        except (OSError, ValueError) as exc:
            error = safe_text(str(exc), 2000)
        finally:
            if process:
                self._terminate(process)
                if process.stdin: process.stdin.close()
        return state, str(delivery), report, error

    def run_once(self):
        task = self._call('task_claim')['task']
        if task is None:
            return False
        state, delivery, report, error = self._execute(task)
        self._call('task_finish', taskId=task['id'], state=state,
                   deliveryPath=delivery, report=report, error=error)
        self._record()
        return True

    def run(self, once=False):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with (self.state_dir / ('task-worker-'+self.target+'.lock')).open('a') as lock:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Conflict('A local worker is already running for this target') from None
                self.lock_fd = lock.fileno()
                self._record()
                while not self.stop.is_set():
                    try:
                        worked = self.run_once()
                        if once: return 0
                        if not worked: self.stop.wait(self.poll_interval)
                    except (OSError, ValueError, Conflict, subprocess.TimeoutExpired):
                        # No successful delivery is inferred from a transport failure.
                        print('Task hub unavailable or claim unconfirmed; no delivery confirmed.', file=sys.stderr, flush=True)
                        if once: return 1
                        self.stop.wait(self.poll_interval)
                return 0
        finally:
            self.lock_fd = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=TARGETS, required=True)
    parser.add_argument('--hub', choices=('local', 'spelltower'), default='local')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--deliveries', type=Path, default=Path.home()/'CommandHelm/Deliveries')
    parser.add_argument('--poll-interval', type=float, default=5)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.poll_interval < 0.1: parser.error('--poll-interval must be at least 0.1 seconds')
    os.umask(0o077)
    hub = TaskProxy() if args.hub == 'spelltower' else TaskStore(args.state_dir/'tasks.sqlite')
    stop = threading.Event()
    def shutdown(signum, frame): stop.set()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        result = Worker(args.target, hub, args.state_dir, args.codex, args.deliveries,
                        args.poll_interval, stop=stop).run(once=args.once)
    except Conflict as exc:
        print(str(exc), file=sys.stderr)
        result = 1
    finally:
        if isinstance(hub, TaskStore): hub.close()
    return result


if __name__ == '__main__':
    sys.exit(main())
