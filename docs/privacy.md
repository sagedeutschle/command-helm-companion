# Command Helm privacy

Effective October 31, 2026. Published by Sage Deutschle.

Command Helm is a client for a bridge you run. The app does not create a Command Helm account, include advertising or analytics SDKs, or send your project activity to a developer-operated cloud service.

## Information on your phone

The app stores your bridge address, pairing credential and certificate fingerprint in the iOS Keychain. Draft tasks and app preferences can be stored in the app's local storage. Project activity, device information, questions, reports and terminal output are displayed from the paired bridge. Demo content is fictional and does not connect to a computer.

## Information on your computers

When you submit a task, answer a question, send a direction or type into a terminal, that content travels to your paired bridge over HTTPS. The bridge stores task history, reports, agent messages and session metadata in its private state directory. Terminal sessions run on the computer you selected and may preserve output and command history there. Your computer's operating system, shell and developer tools may also retain their normal histories.

Optional local monitoring reads bounded recent assistant messages from supported agent session files on the bridge computer. Enable it only for activity you want visible through your paired phone. Optional NetBird and GitHub discovery reads status through the installed clients on the bridge computer. It can display device names, addresses, operating systems, connection status and a signed-in account name. It does not return provider access tokens to the phone.

## Services you choose

An AI task or interactive Codex session uses the provider and account configured in Codex on the selected computer. That tool may send prompts, project content and tool results to its provider. Review its settings and the provider's privacy policy before sending private material. NetBird and GitHub remain separate services governed by their own privacy policies. Command Helm does not add a VPN profile or sign into those services for you.

The privacy, support and source links open external websites. Those websites receive ordinary web requests and apply their own policies. If you open a public support issue, the information you submit will be public; never include pairing keys, credentials or private project contents.

## Control and deletion

Use the app's unpair action to remove the saved bridge pairing from the phone. This disconnects the phone; it does not delete work on your computers or revoke a copied pairing key. To revoke all copies, rotate the pairing key on the bridge and pair your devices again. You control the bridge's private state directory and can stop its services and remove its stored history. Back up anything you want to keep before deleting computer data.

Disable discovery or monitoring on the bridge to stop future collection of that information. Sign out of provider accounts through their official clients. No developer-hosted Command Helm account exists to delete.

## Contact

For questions, use [Command Helm support](support.md). Describe the issue without including secrets or private project contents.
