# Command Helm Companion

Keep your computers within reach. Command Helm lets you send a task from your iPhone, follow its progress, and return to the files on the computer that did the work.

The companion connects to a bridge you run on your own Mac or Linux computer. It does not require the Command Helm desktop IDE. Start with one computer; add more when you need them.

The App Store release is being prepared. Its download link will appear here once Apple makes the app available. You can build the app and run the bridge from this source today; see [build instructions](CONTRIBUTING.md).

- Send durable tasks to a configured worker and read its saved report.
- Reconnect to persistent shell and Codex terminal sessions.
- Follow connected agents and answer their questions.
- See your configured devices and optionally discover NetBird peers or check GitHub sign-in status.
- Explore clearly marked sample data before pairing.

## Set up your computer

Follow the [bridge setup guide](docs/backend-setup.md), then [pair your phone](docs/getting-started.md). The bridge needs Python 3.10 or newer, OpenSSL and, for terminals, tmux. Remote terminal targets need OpenSSH, tmux and a compatible POSIX environment. Native Windows is not currently supported, and WSL has not yet been verified.

Your computer must be awake and reachable to execute work. Queued tasks can wait for a previously configured worker to return. Live phone updates resume when you reopen the app. There are no background push notifications in this release.

## Optional connections

NetBird can provide a private route between your phone and computer when you are away from home. [Link your NetBird network](docs/netbird.md). A local network or another route you already administer can also work.

GitHub discovery reports the account already signed in through the GitHub CLI on your computer. It does not sign you in, copy an access token to your phone or grant repository permissions. AI tasks use the Codex installation and provider account configured on the target computer; provider usage and availability are separate from this app.

## Help and source

[Support](docs/support.md) · [Privacy](docs/privacy.md) · [Source and issues](https://github.com/sagedeutschle/command-helm-companion)

Original Command Helm companion and bridge source is available under the MIT license. The native terminal uses [SwiftTerm](ios/Vendor/SwiftTerm/README.md), with its original license and a documented iOS source adaptation. Command Helm is an independent project by Sage Deutschle; provider names identify compatible services and do not imply endorsement.
