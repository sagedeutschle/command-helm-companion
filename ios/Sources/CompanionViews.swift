import SwiftUI

struct AIConsentView: View {
    let deviceName: String
    let allow: () -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Image(systemName: "sparkles.rectangle.stack").font(.largeTitle).foregroundStyle(HelmStyle.accent)
                    Text("Before using AI on \(deviceName)").font(.title.bold())
                    Text("AI tools on this computer may share your prompts, answers, project content and tool results with the AI provider configured there.")
                    Text("Review the connected tool’s settings and the provider’s privacy policy before sending private material. Command Helm does not choose or sign in to that provider for you.").foregroundStyle(.secondary)
                    Text("Your choice applies to AI tasks, Codex terminals, text directions and Inbox answers for this paired computer. Shell terminals and Pause/Resume do not require this permission.").font(.footnote)
                    Link("Command Helm privacy", destination: PublicHelp.privacy)
                    Button("Allow AI sharing on this computer", action: allow).buttonStyle(.borderedProminent).accessibilityIdentifier("allowAISharing")
                    Button("Cancel", role: .cancel) { dismiss() }
                }.padding(24)
            }.helmConsole().navigationTitle("AI sharing").navigationBarTitleDisplayMode(.inline)
        }
    }
}
enum MoreDestination: Hashable { case overview, projects, inbox, settings }
struct MoreView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        List {
            if model.demo { Label("Demo · sample data only", systemImage: "eye") }
            NavigationLink(value: MoreDestination.overview) { Label("Overview", systemImage: "safari") }
            NavigationLink(value: MoreDestination.projects) { Label("Projects", systemImage: "square.stack.3d.up") }
            NavigationLink(value: MoreDestination.inbox) { Label("Inbox", systemImage: "tray"); Spacer(); if !model.pending.isEmpty { Text("\(model.pending.count)").foregroundStyle(.secondary) } }.accessibilityIdentifier("moreInbox")
            NavigationLink(value: MoreDestination.settings) { Label("Settings", systemImage: "slider.horizontal.3") }
        }.listRowSpacing(8).helmConsole().navigationTitle("More").navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: MoreDestination.self) { destination in
                switch destination {
                case .overview: OverviewView()
                case .projects: ProjectsView()
                case .inbox: InboxView()
                case .settings: SettingsView()
                }
            }
    }
}
struct DevicesView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if model.demo { Label("Demo · sample devices only", systemImage: "eye").font(.caption).foregroundStyle(HelmStyle.gold) }
                ConsoleHeading(title: "Registered computers", icon: "network")
                if let error = model.deviceError { Text(error).foregroundStyle(HelmStyle.gold) }
                if model.devices.isEmpty && model.deviceError == nil {
                    EmptyPanel(title: "No computers configured", text: "Add a device to your bridge to make it available here.", icon: "desktopcomputer")
                }
                ForEach(model.devices) { device in
                    Panel {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .top) {
                                Label(device.name, systemImage: device.platform == "macos" ? "laptopcomputer" : "desktopcomputer").font(.headline)
                                Spacer(minLength: 8)
                                StateTag(state: device.status)
                            }
                            Text("\(device.platform.capitalized) / \(device.transport == "ssh" ? "Trusted SSH" : "Bridge computer")")
                                .font(.caption.monospaced()).foregroundStyle(.secondary)
                            Divider().overlay(HelmStyle.rule)
                            VStack(alignment: .leading, spacing: 8) {
                                capability("Task worker", enabled: device.capabilities.tasks, yes: "Registered", no: "Not registered")
                                capability("Terminal", enabled: device.capabilities.terminal, yes: "Enabled", no: "Disabled")
                                capability("Codex", enabled: device.capabilities.codex, yes: "Available", no: "Unavailable")
                            }
                            if device.capabilities.tasks && device.status == "offline" { Text("Tasks can queue while this computer is offline.").font(.caption).foregroundStyle(HelmStyle.gold) }
                            if device.lastSeen > 0 { Text("Worker last seen \(Date(timeIntervalSince1970: device.lastSeen).formatted())").font(.caption).foregroundStyle(.secondary) }
                            Text(device.id).font(.caption.monospaced()).foregroundStyle(.secondary)
                        }
                    }
                }
                Panel {
                    VStack(alignment: .leading, spacing: 14) {
                        NavigationLink { ConnectionsView() } label: { Label("Connections & setup", systemImage: "point.3.connected.trianglepath.dotted").frame(minHeight: 44) }
                        Link("Pairing and computer setup guide", destination: PublicHelp.setup).frame(minHeight: 44)
                        Text("Only configured devices can accept work. Device status does not verify terminal SSH access.").font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }.padding(16).frame(maxWidth: 760)
        }.helmConsole().navigationTitle("Devices").navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refreshDevices() }.task { await model.refreshDevices() }
    }
    private func capability(_ title: String, enabled: Bool, yes: String, no: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.subheadline)
            Spacer(minLength: 12)
            Text(enabled ? yes : no).font(.caption.monospaced()).foregroundStyle(enabled ? HelmStyle.signal : .secondary)
        }
    }
}
struct ConnectionsView: View {
    @EnvironmentObject var model: AppModel
    @State private var changing = false
    var body: some View {
        List {
            if model.demo { Label("Demo · discovery stays off", systemImage: "eye") }
            if let error = model.connectionsError { Text(error).foregroundStyle(HelmStyle.gold) }
            Section("Optional status discovery") {
                ForEach(model.services.connections) { connection in
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle(connection.id == "netbird" ? "NetBird status" : "GitHub status", isOn: Binding(get: { connection.enabled }, set: { enabled in
                            changing = true
                            Task { await model.setDiscovery(id: connection.id, enabled: enabled); changing = false }
                        })).disabled(model.demo || changing)
                        Text(connection.message.isEmpty ? connection.status : connection.message).font(.footnote).foregroundStyle(.secondary)
                        if !connection.account.isEmpty { LabeledContent("Signed in on computer", value: connection.account).font(.caption) }
                    }
                }
                Text("These switches allow bounded read-only checks through the installed clients on your bridge computer. They do not sign in, install software, enroll peers or change network policy.").font(.footnote).foregroundStyle(.secondary)
            }.listRowBackground(HelmStyle.surface)
            Section("Connect with NetBird") {
                setupStep(1, "Install on both devices", "Install NetBird on your iPhone and the bridge computer using its official apps.", "iphone.and.arrow.forward")
                setupStep(2, "Sign in to the same network", "Use the same NetBird network on both. Approve devices and configure network access in NetBird yourself.", "person.2")
                setupStep(3, "Choose your computer", "Enable status discovery to inspect peers. Add the chosen computer explicitly to your bridge configuration; discovery alone never links it.", "desktopcomputer")
                setupStep(4, "Pair and verify Helm", "Use the private bridge address, pairing key and certificate fingerprint from your computer. Check Devices, then test a shell with pwd.", "checkmark.shield")
                Link("NetBird official setup", destination: URL(string: "https://docs.netbird.io/how-to/getting-started")!)
            }
            if !model.services.peers.isEmpty {
                Section("Discovered peers · not enrolled") {
                    ForEach(model.services.peers) { peer in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack { Text(peer.name); Spacer(); StateTag(state: peer.status) }
                            Text([peer.address, peer.os].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption.monospaced()).textSelection(.enabled)
                        }
                    }
                }
            }
            Section("GitHub sign-in on your computer") {
                Text("Install the official GitHub CLI, then open a shell on your bridge computer and run the command below. Follow GitHub’s browser or device-code flow there. Never paste a token or password into Command Helm.")
                Text("gh auth login --hostname github.com").font(.body.monospaced()).textSelection(.enabled)
                Text("After signing in, enable GitHub status discovery above. This checks the active GitHub.com account only; Helm has no GitHub account or cloud login.").font(.footnote).foregroundStyle(.secondary)
                Link("GitHub CLI documentation", destination: URL(string: "https://cli.github.com/manual/gh_auth_login")!)
            }
            Section { Link("Computer and worker setup", destination: PublicHelp.setup) }
        }.helmConsole().navigationTitle("Connections").navigationBarTitleDisplayMode(.inline).refreshable { await model.refreshDevices() }
    }
    private func setupStep(_ number: Int, _ title: String, _ detail: String, _ icon: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Text("\(number)").font(.title2.bold()).foregroundStyle(HelmStyle.accent).frame(minWidth: 28)
            VStack(alignment: .leading, spacing: 7) { Label(title, systemImage: icon).font(.headline); Text(detail).font(.subheadline).foregroundStyle(.secondary) }
        }.padding(.vertical, 8).accessibilityElement(children: .combine)
    }
}
struct NoticesView: View {
    var body: some View {
        ScrollView { Text((Bundle.main.url(forResource: "SwiftTerm-LICENSE", withExtension: "txt").flatMap { try? String(contentsOf: $0, encoding: .utf8) }) ?? "License resource unavailable.").font(.footnote.monospaced()).textSelection(.enabled).padding() }.helmConsole().navigationTitle("Third-party notices")
    }
}
struct PairingGuideView: View {
    var body: some View {
        List {
            Section("1 · Prepare your computer") {
                Link("Download Command Helm Desktop", destination: PublicHelp.desktop).accessibilityIdentifier("downloadDesktopGuide")
                Text("The desktop IDE is a Mac preview for Apple Silicon. It is development-signed and not notarized; read its release notes before installing.").font(.footnote).foregroundStyle(.secondary)
                Text("Install the standalone bridge on a Mac or Linux computer you own and configure a project folder. The desktop IDE is optional; both apps connect to this bridge.")
                Link("Open computer setup guide", destination: PublicHelp.setup)
            }.listRowBackground(HelmStyle.surface)
            Section("2 · Make it reachable") { Text("Keep the computer awake. Start on the same private network, or set up NetBird on both devices yourself.") }
            Section("3 · Pair securely") { Text("Display the bridge pairing information privately on your computer. Enter its HTTPS address, private pairing key and certificate fingerprint here, or open its private pairing link. Compare the address and fingerprint before connecting. Never share that key or link.") }
            Section("4 · Choose your work") { Text("Check Devices for registered workers and enabled terminals. Queued Codex tasks require a worker. Interactive shell and Codex terminals require tmux on the execution computer. Provider sign-in stays on your computer.") }
            Section { Text("Sample mode works offline and sends no work. No Helm account or subscription is required."); Link("Privacy", destination: PublicHelp.privacy); Link("Support", destination: PublicHelp.support) }
        }.helmConsole().navigationTitle("Get started").navigationBarTitleDisplayMode(.inline)
    }
}
