# Standalone Command Helm bridge

The phone connects to a bridge on a computer you own. It does not execute tasks on iOS. The bridge runs on macOS or Linux with Python 3.10 or newer and OpenSSL. Interactive terminals additionally require tmux on the execution computer. Codex tasks and Codex terminals require the real Codex CLI, installed and authenticated locally by you. No Helm account, package installation or provider sign-in is performed by setup.

Native Windows is not a bridge or terminal execution target. A Linux environment such as WSL can work if you deliberately configure its Linux Python/tmux/SSH environment; this release has not verified WSL. SSH terminal targets require a POSIX shell, tmux and a trusted host-key entry. A Windows SSH server running PowerShell is not supported.

Obtain the clean companion source from [the public companion repository](https://github.com/sagedeutschle/command-helm-companion). From its root, run:

```sh
python3 scripts/helm-bridge setup --workspace "$HOME/Projects" --device-id computer --name 'My computer' --hostname computer.local
python3 scripts/helm-bridge serve
```

Use an existing workspace directory. Setup creates a private state directory, device configuration, random bearer credential and a self-signed TLS certificate/key. It refuses to overwrite an existing directory. The default state directory is `~/Library/Application Support/Command Helm Remote` on macOS and `~/.local/state/command-helm-remote` on Linux. Every command accepts `--state-dir /absolute/private/folder` to select a separate installation.

Setup defaults to **terminals disabled**, both connection integrations disabled, no history collection and a loopback-only TLS listener. To enable terminals during first setup, add `--enable-terminal`; otherwise edit `terminalEnabled` in the private configuration and restart the bridge. No default tmux server or existing user terminal is attached or closed. tmux uses a random persistent `helm-mobile-…` socket namespace and ignores the user's tmux configuration.

For phone access, bind explicitly to an address on your private network, for example:

```sh
python3 scripts/helm-bridge serve --host 10.0.0.20 --allow-remote
python3 scripts/helm-bridge pair --url https://computer.local:8746
```

Use your actual private address and certificate hostname. Configure a private VPN such as NetBird yourself if off-network access is needed. The bridge changes no firewall, SSH trust, VPN policy or network enrollment. Avoid exposing the bearer-authorized terminal service to the public Internet. The computer must remain awake and reachable.

`pair` shows the certificate fingerprint without revealing the credential. In a private terminal, explicitly add `--show-uri` to display the `helmremote://pair?...` link for the phone. That URI grants access to your bridge, including enabled terminals. Do not put it in screenshots, tickets, shell recordings or shared logs. No pairing link is printed by setup or serve.

## Devices and local tasks

The private `devices.json` uses this structure (replace the example path with an existing directory):

```json
{
  "version": 1,
  "devices": [
    {
      "id": "computer",
      "name": "My computer",
      "platform": "macos",
      "transport": "local",
      "sshTarget": "",
      "workspace": "/absolute/existing/workspace",
      "terminalEnabled": false,
      "codexCommand": "codex"
    }
  ],
  "connections": {"netbird": false, "github": false}
}
```

IDs are stable lowercase ASCII strings matching `[a-z0-9][a-z0-9_-]{0,63}`. Display names can change; stored tasks retain their original target ID. `codexCommand` is one executable name or absolute path, never a shell command with arguments. Keep device configuration private and restart the bridge after editing it. An existing terminal will reconnect only when its original device configuration is restored, preventing a configuration change from redirecting input to another host.

A configured terminal target does not by itself run queued tasks. Start a separate worker on each execution computer. For a single computer:

```sh
python3 scripts/helm-bridge worker --target computer
```

The worker claims durable tasks, runs Codex in the configured workspace unless the task supplies an absolute workspace, and writes reports under the selected private state's `deliveries/` directory. `--once` claims at most one task. Failed or interrupted work is never automatically replayed. A worker heartbeat registers task support for that device. Task support remains true when that worker later goes offline, allowing tasks to queue; a never-registered device does not claim task support.

## SSH terminals and task workers on another computer

These are separate routes: the bridge opens an SSH terminal **to the execution computer**, while the execution computer's task worker connects **back to the bridge's task hub**. Neither route installs software or starts workers automatically.

To add an SSH terminal target on the hub, add a device such as:

```json
{
  "id": "workstation",
  "name": "Workstation",
  "platform": "linux",
  "transport": "ssh",
  "sshTarget": "operator@workstation",
  "hostKeyAlias": "workstation-trusted-key",
  "workspace": "/home/operator/Projects",
  "terminalEnabled": true,
  "codexCommand": "codex"
}
```

`sshTarget` is a trusted SSH alias or `user@hostname`. Use an SSH alias for IPv6 or extra operator-managed SSH options. Optional `hostKeyAlias` selects an existing trusted host-key record. Host keys must already be verified by you. All connections use `BatchMode=yes`, `StrictHostKeyChecking=yes`, disabled agent/port forwarding, no local command and no SSH terminal escape commands. Hostnames, commands and workspaces cannot be supplied by phone terminal requests.

For tasks, install the clean source and configure a **local** device with the same ID on the workstation:

```sh
python3 scripts/helm-bridge setup --device-id workstation --name Workstation --workspace "$HOME/Projects"
python3 scripts/helm-bridge worker --target workstation \
  --hub-target operator@hub \
  --hub-source-dir /absolute/path/to/command-helm-companion \
  --hub-state-dir /absolute/private/hub-state \
  --hub-host-key-alias hub-trusted-key
```

The optional host-key alias can be omitted when your normal SSH hostname is already trusted. Hub source/state paths must refer to the **hub computer**, not the workstation. The hub source contains `bridge/helm_remote`; its private state contains `devices.json` with the matching `workstation` ID. The worker uses only allowlisted task RPC actions over fixed strict SSH. Task prompts are bounded JSON on stdin, never remote shell arguments. No phone bearer credential is copied to workers. Use `--state-dir` consistently on every workstation command when using nondefault state.

`GET /v1/devices` reports the local bridge device online. SSH devices report unknown before a worker heartbeat, online while that heartbeat is current, and offline when it becomes stale. `lastSeen` is that worker's timestamp, or zero. These observations do not prove that terminal SSH or VPN enrollment is working; terminal attempts report their own errors.

## Optional connections and monitoring

The phone's Connections switches persist an explicit opt-in. NetBird discovery runs only `netbird status --json`. GitHub discovery runs only `gh auth status --active --hostname github.com --json hosts`; this release checks GitHub.com, not enterprise hosts. Both adapters have execution/output bounds, five-second caching and sanitized presentation fields. Missing tools, sign-in problems and malformed output produce status messages without returning raw diagnostics, tokens or WireGuard public keys. The installed NetBird CLI may omit peer operating systems; `os` is then empty. Discovery never adds devices to the trusted configuration.

Install and sign in to these tools yourself in a terminal on the computer. The bridge never runs sign-in, changes network policy or asks the phone to handle provider credentials.

Tasks and interactive terminals do not require session-history monitoring. To deliberately enable passive local monitoring, start:

```sh
python3 scripts/helm-bridge serve --collect-local
```

That option reads recent assistant-text excerpts from local Codex JSONL session files and Claude profile project-session JSONL files from the last seven days, up to 100 recent files. It reads bounded file heads/tails, records the most recent assistant text (up to 3,900 characters per collected session) and project path/status in the private bridge database, and sends those excerpts to paired phones. It does not read provider credential files or execute observed sessions. Keep secrets out of assistant output; pattern redaction is a safeguard, not a complete secret detector. The existing MCP adapter can instead publish explicit updates/questions without enabling passive history collection; use `scripts/helm-agent codex` or `scripts/helm-agent claude` from a project, or configure `scripts/mcp --provider codex` in your local assistant tool. The MCP adapter's `--state-dir` can select the same private state as the bridge.

## Terminal persistence and recovery

Shell and Codex sessions run in tmux with a real PTY. Phone navigation/disconnection and a bridge restart detach the viewer without killing the session. On reconnect the phone receives a screen reset/redraw; it must honor output cursors, page replay at up to 64 KiB per response and display loss/reset explicitly. The replay buffer retains the latest 1 MiB per session. The private terminal database contains that output and hashes/receipts for input IDs; it is sensitive local user data.

Input is at most 16 KiB per request. Accepted input IDs are deduplicated across bridge restarts. A transport failure after delivery begins leaves that ID permanently ambiguous; the bridge never replays it. Inspect the terminal before choosing to resend with a new ID. Acceptance means bytes reached the bridge's PTY transport; it does not prove a remote shell or Codex completed the action.

Reconnect first reapplies the required tmux settings to the existing owned session before attaching; it never creates a replacement session. A failed start whose session is positively confirmed absent retains its ID as an exited tombstone and releases its active slot. Unknown SSH/transport outcomes remain disconnected until inspected. Explicit Close also succeeds when that owned session is positively confirmed absent; a lost kill acknowledgement is resolved only with later positive absence. A stale or inaccessible socket remains unknown. Explicit Close otherwise kills only the selected session in the bridge-owned tmux namespace. The bridge limits active sessions to eight, retains at most 128 session identities and 65,536 input receipts per session, and fails closed when those limits are reached. It never evicts an old input ID and later replays it. At a history/journal limit, close existing sessions and intentionally set up a new private state directory (which requires pairing again). Do not delete state while sessions are running: the owner identity and receipts are needed for safe reconnect and input deduplication. Computer reboot, remote tmux failure and deleted state can terminate or orphan work; phone disconnection alone does not.

Validation for this release uses disposable local TLS/state directories and a dedicated local tmux namespace. Actual remote SSH execution, WSL and your account/network setup require verification on your own configured machines.
