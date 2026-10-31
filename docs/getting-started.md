# Start with one computer

Command Helm connects your phone to a bridge you control. The desktop IDE is optional.

1. Install and start the bridge using the [computer setup guide](backend-setup.md). Choose a project folder that you intend to work in.
2. Make the bridge reachable from your phone. Start on the same local network, or use your own private network such as [NetBird](netbird.md). Do not forward a router port just to complete setup.
3. On the computer, display the pairing information with the explicit pairing command in the setup guide. This includes the bridge address, a private pairing key and its certificate fingerprint.
4. In Command Helm on your phone, open the pairing screen and enter those values, or open the generated pairing link privately on that phone. Check the displayed address and fingerprint against your computer before accepting.
5. Open Devices and check the computer's capabilities. A task worker and terminal support are separate features; enable the ones you want on the computer.
6. Send a small task into a disposable project folder, or start a shell and run `pwd`. Check the report or terminal output before moving to your real project.

Keep pairing keys and links private. Anyone with a valid pairing key and network access to the bridge can use its enabled controls. A certificate mismatch means you should verify the bridge on the computer before replacing the saved pairing.

## Tasks and terminals

A task enters a durable queue for the selected computer. The worker runs it there and saves a report. A completed task means that process exited successfully; read the report to see what it actually checked.

A terminal is an interactive session on the selected computer. Close the phone view to disconnect while leaving the session running. Use the explicit Close Session action only when you want to end that session and the process it owns. If input delivery becomes uncertain, inspect the remote output before sending the same command again.

Codex uses its configuration and signed-in provider on that computer. Prompts, selected project content and tool results may be sent to that provider according to its settings. Review those settings before sending private material.

## Add another computer

Add an explicit device entry to the bridge configuration. For a remote terminal, configure a trusted SSH destination and verify its host key on the bridge computer first. For queued tasks, run the documented worker on the target computer and point it at your hub. The phone cannot enroll an arbitrary SSH destination by typing a host into the terminal screen.

An offline device and a missing worker are different states. An existing worker can accept queued work once it returns; a newly listed terminal target still needs a worker before it can execute queued tasks.
