# Command Helm support

[Open a support issue](https://github.com/sagedeutschle/command-helm-companion/issues/new) for setup help or a reproducible problem. Include the app version, computer operating system, bridge version and the step that failed. Include only sanitized screenshots or error text.

Never post a pairing key, pairing link, provider credential, SSH private key or private project output. A certificate fingerprint is not a password, but a screenshot of the pairing page may include the actual key.

## Connection problems

- **The computer is offline:** wake it and verify your local or private network connection. NetBird discovery does not start the bridge for you.
- **The bridge is reachable but pairing fails:** verify the address, pairing key and certificate fingerprint directly on the computer. Re-pair only after that check.
- **New screens say the bridge needs an update:** update the bridge using its setup guide. Older task and inbox features remain available where the paired bridge supports them.
- **A task stays queued:** make sure the worker for that exact device is running and can reach the task hub. Do not create repeated copies of the task to retry a connection.
- **A terminal disconnects:** reopen the existing session. Remote work can continue while your phone is disconnected. If input acknowledgement is uncertain, read the output before repeating a command.
- **GitHub or NetBird needs sign-in:** sign in through that service's official client on your computer. Discovery only reports status.

[Getting started](getting-started.md) · [NetBird guide](netbird.md) · [Privacy](privacy.md)
