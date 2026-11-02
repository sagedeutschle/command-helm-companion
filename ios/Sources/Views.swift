import SwiftUI

struct RootView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Group {
            if model.connection == nil && !model.demo { PairView() }
            else {
                TabView {
                    NavigationStack { TasksView() }.tabItem { Label("Tasks", systemImage: "checklist") }
                    NavigationStack { TerminalsView() }.tabItem { Label("Terminal", systemImage: "terminal") }
                    NavigationStack { DevicesView() }.tabItem { Label("Devices", systemImage: "desktopcomputer") }
                    NavigationStack { MoreView() }.tabItem { Label("More", systemImage: "ellipsis.circle") }.badge(model.pending.count)
                }
            }
        }.sheet(item: $model.selectedQuestion) { QuestionView(question: $0) }
    }
}
struct PairView: View {
    @EnvironmentObject var model: AppModel
    @State private var address = ""
    @State private var key = ""
    @State private var fingerprint = ""
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(spacing: 14) {
                        Image(systemName: "scope").font(.system(size: 32, weight: .light)).foregroundStyle(HelmStyle.accent).accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("COMMAND HELM").font(.headline.monospaced()).tracking(2)
                            Text("Connect your computer").font(.subheadline).foregroundStyle(.secondary)
                        }
                    }.padding(.vertical, 8)
                    DesktopDownloadPanel()
                    Panel {
                        VStack(alignment: .leading, spacing: 16) {
                            ConsoleHeading(title: "Bridge pairing", icon: "link")
                            TextField("https://your-bridge:8746", text: $address).textContentType(.URL).keyboardType(.URL).accessibilityIdentifier("bridgeURL")
                            Divider()
                            SecureField("Pairing key", text: $key).accessibilityIdentifier("pairingKey")
                            Divider()
                            TextField("Certificate fingerprint (optional)", text: $fingerprint).font(.caption).accessibilityIdentifier("fingerprint")
                            Text("The key stays in this phone’s Keychain.").font(.caption).foregroundStyle(.secondary)
                        }.autocorrectionDisabled().textInputAutocapitalization(.never)
                    }
                    if let message = error ?? model.error { Text(message).font(.callout).foregroundStyle(HelmStyle.gold) }
                    Button {
                        busy = true; error = nil
                        Task {
                            defer { busy = false }
                            do { try await model.pair(Connection(url: address, token: key, fingerprint: fingerprint)) }
                            catch { self.error = error.localizedDescription }
                        }
                    } label: { HStack { Text(busy ? "Connecting…" : "Connect securely"); Spacer(); Image(systemName: "arrow.right") }.padding(8) }
                        .buttonStyle(.borderedProminent).disabled(busy || address.isEmpty || key.isEmpty)
                    Button("Explore demo") { model.explore() }.frame(maxWidth: .infinity)
                    NavigationLink("How to set up your computer") { PairingGuideView() }
                    HStack { Link("Privacy", destination: PublicHelp.privacy); Spacer(); Link("Support", destination: PublicHelp.support) }
                    Text("No Helm account is required. Work runs on your computer. Your bridge must be reachable. Away from home, use your private network connection.").font(.footnote).foregroundStyle(.secondary)
                }.padding(20).frame(maxWidth: 620)
            }.helmConsole()
        }
    }
}
struct ConnectionStrip: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(model.demo ? HelmStyle.rule : !model.stale && model.error == nil ? HelmStyle.signal : HelmStyle.gold).frame(width: 6, height: 6)
            Text(model.demo ? "Demo · sample projects" : model.error == nil && !model.stale ? "Bridge connected" : "Connection needs attention")
            Spacer()
            if let date = model.lastSuccess { Text(date, style: .relative).monospacedDigit() }
        }.font(.caption).foregroundStyle(.secondary)
    }
}
struct OverviewView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                ConnectionStrip()
                HStack(spacing: 12) { metric("PROJECTS", value: model.snapshot.projects.count, icon: "square.stack.3d.up"); metric("NEED YOU", value: model.pending.count, icon: "bubble.left.and.bubble.right") }
                FleetStrip()
                if let error = model.error { Label(error, systemImage: "wifi.exclamationmark").font(.footnote).foregroundStyle(HelmStyle.gold) }
                if let q = model.pending.first {
                    Button { model.selectedQuestion = q } label: {
                        Panel {
                            VStack(alignment: .leading, spacing: 12) {
                                HStack { Label("NEEDS YOUR INPUT", systemImage: "bubble.left").font(.caption.weight(.semibold)).tracking(1); Spacer(); Image(systemName: "arrow.up.right") }.foregroundStyle(HelmStyle.gold)
                                Text(q.text).font(.title3.weight(.medium)).foregroundStyle(.primary).multilineTextAlignment(.leading)
                                Text("\(model.snapshot.projectName(q.projectId)) · \(q.provider.capitalized)").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }.buttonStyle(.plain)
                }
                HStack { ConsoleHeading(title: "Activity log", icon: "text.alignleft"); if model.refreshing { ProgressView() } }
                if model.snapshot.updates.isEmpty { EmptyPanel(title: "Ready for your first update", text: "Connect a Claude Code or Codex project using the Helm bridge. Its updates will appear here.", icon: "antenna.radiowaves.left.and.right") }
                ForEach(model.snapshot.updates.prefix(50)) { update in UpdateCard(update: update, project: model.snapshot.projectName(update.projectId)) }
            }.padding(20).frame(maxWidth: 760)
        }.helmConsole().navigationTitle("Command Helm").navigationBarTitleDisplayMode(.inline).refreshable { await model.refresh() }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("Refresh") } }
    }
    func metric(_ title: String, value: Int, icon: String) -> some View {
        Panel { VStack(alignment: .leading, spacing: 14) {
            HStack { Image(systemName: icon); Spacer(); Text(title).font(.system(size: 10, weight: .semibold, design: .monospaced)) }.foregroundStyle(.secondary)
            Text("\(value)").font(.largeTitle.monospaced().weight(.medium)).monospacedDigit().foregroundStyle(HelmStyle.signal)
        } }
    }
}
struct FleetStrip: View {
    @EnvironmentObject var model: AppModel
    private var deviceIDs: [String] { Set(model.snapshot.projects.map { model.activityDeviceID($0.id) }).sorted() }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ConsoleHeading(title: "Computer activity", icon: "network")
            if deviceIDs.isEmpty { Text("Projects appear when a connected agent reports activity.").font(.footnote).foregroundStyle(.secondary) }
            ForEach(deviceIDs, id: \.self) { id in
                let projects = model.snapshot.projects.filter { model.activityDeviceID($0.id) == id }
                let active = projects.filter { ["working", "waiting"].contains($0.state) }.count
                let state = projects.contains { $0.isOffline } ? "offline" : projects.contains { $0.state == "waiting" } ? "waiting" : active > 0 ? "working" : "idle"
                Panel {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { Label(model.deviceName(.init(rawValue: id)), systemImage: "desktopcomputer").font(.headline); Spacer(); StateTag(state: state) }
                        Text("\(projects.count) \(projects.count == 1 ? "project" : "projects") · \(active) active").font(.caption).foregroundStyle(.secondary)
                    }
                }.accessibilityIdentifier("fleet-\(id)")
            }
        }
    }
}
struct UpdateCard: View {
    let update: Update; let project: String
    var body: some View {
        Panel { VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) { Text(project).font(.headline); Spacer(); StateTag(state: update.state) }
            Text(update.text).font(.callout).foregroundStyle(.primary.opacity(0.88)).textSelection(.enabled)
            HStack { Text(update.provider.capitalized); Text("·"); Text(Date(timeIntervalSince1970: update.createdAt), style: .relative); Text("ago") }.font(.caption).foregroundStyle(.secondary)
        } }
    }
}
struct EmptyPanel: View {
    let title: String; let text: String; let icon: String
    var body: some View {
        Panel { VStack(alignment: .leading, spacing: 12) {
            Image(systemName: icon).font(.title).foregroundStyle(HelmStyle.accent)
            Text(title).font(.headline)
            Text(text).font(.callout).foregroundStyle(.secondary)
        }.padding(.vertical, 14) }
    }
}
struct InboxView: View {
    @EnvironmentObject var model: AppModel
    @State private var showHistory = false
    var questions: [Question] { model.snapshot.questions.filter { showHistory || $0.isAnswerable } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                ConnectionStrip()
                Picker("Questions", selection: $showHistory) { Text("Needs you").tag(false); Text("All questions").tag(true) }.pickerStyle(.segmented)
                if questions.isEmpty { EmptyPanel(title: "Nothing waiting on you", text: "New questions from your connected projects will land here.", icon: "checkmark.bubble") }
                ForEach(questions) { q in
                    Button { model.selectedQuestion = q } label: {
                        Panel { VStack(alignment: .leading, spacing: 12) {
                            HStack { Text(model.snapshot.projectName(q.projectId)).font(.subheadline.weight(.semibold)); Spacer(); Image(systemName: "chevron.right") }.foregroundStyle(.secondary)
                            Text(q.text).font(.headline).foregroundStyle(.primary).multilineTextAlignment(.leading)
                            Text(q.displayState).font(.caption).foregroundStyle(q.isAnswerable ? HelmStyle.gold : HelmStyle.accent)
                        } }
                    }.buttonStyle(.plain)
                }
            }.padding(20).frame(maxWidth: 760)
        }.helmConsole().navigationTitle("Inbox").refreshable { await model.refresh() }
    }
}
struct QuestionView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let question: Question
    @State private var answer = ""
    @State private var showConsent = false
    @State private var consentTarget = ""
    @State private var sending = false
    @State private var sent = false
    @State private var error: String?
    @State private var replyId = UUID().uuidString
    var current: Question { model.snapshot.questions.first { $0.id == question.id } ?? question }
    private func sendAnswer() {
        sending = true; error = nil
        Task {
            defer { sending = false }
            do { try await model.answer(current, text: answer, replyId: replyId); sent = true }
            catch { self.error = error.localizedDescription }
        }
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Label(model.snapshot.projectName(current.projectId), systemImage: "folder").foregroundStyle(HelmStyle.accent)
                    Text(current.text).font(.title2.weight(.semibold)).textSelection(.enabled)
                    Text("Asked by \(current.provider.capitalized)").font(.subheadline).foregroundStyle(.secondary)
                    if model.demo { Label("Sample question · replies are disabled", systemImage: "eye").font(.footnote).foregroundStyle(HelmStyle.gold) }
                    if current.isAnswerable && !sent {
                        ForEach(current.options, id: \.self) { option in
                            Button { answer = option; replyId = UUID().uuidString } label: {
                                HStack { Text(option).multilineTextAlignment(.leading); Spacer(); Image(systemName: answer == option ? "checkmark.circle.fill" : "circle") }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(HelmStyle.surface, in: RoundedRectangle(cornerRadius: 15))
                            }.buttonStyle(.plain).disabled(sending)
                        }
                        TextField("Or write your answer…", text: $answer, axis: .vertical).lineLimit(3...8).padding(16).background(HelmStyle.surface, in: RoundedRectangle(cornerRadius: 15)).disabled(sending).accessibilityIdentifier("replyText")
                        Text("Your answer returns only to the session that asked. A saved reply stays pending until that connection receives it.").font(.footnote).foregroundStyle(.secondary)
                        Button {
                            consentTarget = model.activityDeviceID(current.projectId)
                            if model.hasAIConsent(consentTarget) { sendAnswer() } else { showConsent = true }
                        } label: { HStack { Text(sending ? "Sending…" : "Send answer"); Spacer(); Image(systemName: "paperplane") }.padding(8) }
                            .buttonStyle(.borderedProminent).disabled(model.demo || sending || answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || answer.count > 4000)
                        Text("Available until \(Date(timeIntervalSince1970: current.expiresAt).formatted(date: .omitted, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Label(sent && current.state == "pending" ? "Answer saved" : current.displayState, systemImage: "checkmark.circle").foregroundStyle(HelmStyle.accent)
                        if let answer = current.answer { Text(answer).padding(16).frame(maxWidth: .infinity, alignment: .leading).background(HelmStyle.surface, in: RoundedRectangle(cornerRadius: 15)) }
                    }
                    if let error { Text(error).foregroundStyle(HelmStyle.gold).font(.callout) }
                }.padding(24).frame(maxWidth: 680)
            }.helmConsole().navigationTitle("Your direction").navigationBarTitleDisplayMode(.inline)
                .sheet(isPresented: $showConsent) { AIConsentView(deviceName: model.deviceName(.init(rawValue: consentTarget))) { model.allowAI(consentTarget); showConsent = false; sendAnswer() } }
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}
struct ProjectsView: View {
    @EnvironmentObject var model: AppModel
    @State private var search = ""
    var projects: [Project] { model.snapshot.projects.filter { search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) } }
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ConnectionStrip()
                if projects.isEmpty { EmptyPanel(title: "Your projects live here", text: "Each connected Claude or Codex workspace appears automatically when its agent reports in.", icon: "folder.badge.plus") }
                ForEach(projects) { project in
                    NavigationLink { ProjectView(project: project) } label: {
                        Panel { VStack(alignment: .leading, spacing: 14) {
                            HStack { Image(systemName: "folder").foregroundStyle(HelmStyle.accent); Text(project.name).font(.headline); Spacer(); Image(systemName: "chevron.right").foregroundStyle(.secondary) }
                            HStack { StateTag(state: project.state); Spacer(); Text("\(project.sessions.count) connections").font(.caption).foregroundStyle(.secondary) }
                        } }
                    }.buttonStyle(.plain)
                }
            }.padding(20).frame(maxWidth: 760)
        }.helmConsole().navigationTitle("Projects").searchable(text: $search, prompt: "Find a project").refreshable { await model.refresh() }
    }
}
struct ProjectView: View {
    @EnvironmentObject var model: AppModel
    let project: Project
    var current: Project { model.snapshot.projects.first { $0.id == project.id } ?? project }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Control the next step").font(.title2.bold())
                Text("Directions arrive at the agent’s next checkpoint. Pause waits for that checkpoint; work already running can finish.").font(.footnote).foregroundStyle(.secondary)
                if !current.sessions.contains(where: { $0.controllable == true }) {
                    EmptyPanel(title: "Monitoring only", text: "Launch this project with helm-agent claude or helm-agent codex to enable directions and pause/resume.", icon: "antenna.radiowaves.left.and.right")
                }
                ForEach(current.sessions.filter { $0.controllable == true || !($0.commands ?? []).isEmpty }) { session in
                    AgentControlView(session: session, deviceID: model.activityDeviceID(current.id))
                }
                ForEach(model.pending.filter { $0.projectId == project.id }) { q in
                    Button { model.selectedQuestion = q } label: { Panel { Label(q.text, systemImage: "bubble.left").foregroundStyle(HelmStyle.gold) } }.buttonStyle(.plain)
                }
                ForEach(model.snapshot.updates.filter { $0.projectId == project.id }) { update in UpdateCard(update: update, project: project.name) }
                if !model.snapshot.updates.contains(where: { $0.projectId == project.id }) { EmptyPanel(title: "Connected, awaiting an update", text: "This project’s agent has checked in. Its next status report will appear here.", icon: "antenna.radiowaves.left.and.right") }
            }.padding(20).frame(maxWidth: 760)
        }.helmConsole().navigationTitle(project.name)
    }
}
struct AgentControlView: View {
    @EnvironmentObject var model: AppModel
    let session: AgentSession
    let deviceID: String
    @State private var showConsent = false
    @State private var consentTarget = ""
    @State private var direction = ""
    @State private var sending = false
    @State private var notice: String?
    @State private var requestId = UUID().uuidString
    @State private var lastPayload = ""
    func send(_ kind: String, _ text: String) {
        let payload = kind + text
        if payload != lastPayload { requestId = UUID().uuidString; lastPayload = payload }
        sending = true
        Task {
            defer { sending = false }
            do {
                try await model.direct(session, deviceID: deviceID, kind: kind, text: text, id: requestId)
                notice = "Queued for this agent’s next checkpoint."
                requestId = UUID().uuidString
                if kind == "guide" { direction = "" }
            } catch { notice = error.localizedDescription }
        }
    }
    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 14) {
                Text("\(session.provider.capitalized) · \(session.id.prefix(8))").font(.headline)
                TextField("Change priorities or give direction…", text: $direction, axis: .vertical).lineLimit(2...6).textFieldStyle(.roundedBorder).accessibilityIdentifier("projectDirection")
                Button("Send direction") { if model.hasAIConsent(deviceID) { send("guide", direction) } else { consentTarget = deviceID; showConsent = true } }.buttonStyle(.borderedProminent).disabled(direction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || direction.count > 4000)
                HStack {
                    Button("Pause at checkpoint", systemImage: "pause") { send("pause", "Pause at your next safe checkpoint.") }
                    Button("Resume", systemImage: "play") { send("resume", "Resume work using the latest project directions.") }
                }.buttonStyle(.bordered)
                if let notice { Text(notice).font(.footnote) }
                ForEach(Array((session.commands ?? []).prefix(5))) { command in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(command.text).font(.subheadline)
                        Text(command.state.capitalized).font(.caption).foregroundStyle(HelmStyle.accent)
                    }
                }
                if session.controllable != true { Text("Agent connection offline").font(.caption) }
            }.sheet(isPresented: $showConsent) { AIConsentView(deviceName: model.deviceName(.init(rawValue: consentTarget))) { model.allowAI(consentTarget); showConsent = false; send("guide", direction) } }
            .disabled(sending || session.controllable != true || model.stale || model.demo)
        }
    }
}
struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var confirmUnpair = false
    var body: some View {
        Form {
            Section("Connection") {
                LabeledContent("Mode", value: model.demo ? "Demo" : "Private bridge")
                if let url = model.connection?.url { LabeledContent("Bridge", value: url.host ?? "") }
                Text("Updates refresh every five seconds while the app is open. Background push alerts are not enabled in this build.").font(.footnote).foregroundStyle(.secondary)
                Button(model.demo ? "Leave demo" : "Unpair this phone", role: .destructive) { if model.demo { model.disconnect() } else { confirmUnpair = true } }
            }.listRowBackground(HelmStyle.surface)
            Section("Project connections") {
                Text("Launch Claude Code or Codex with the Helm connector on your Mac. Ask the agent to use helm_status for updates and helm_ask when it needs your direction.")
                Text("Existing sessions need the connector added before they can send questions here. Unreported work is not shown as live.").font(.footnote).foregroundStyle(.secondary)
            }.listRowBackground(HelmStyle.surface)
            Section("Desktop IDE") {
                Link("Download Command Helm Desktop", destination: PublicHelp.desktop).accessibilityIdentifier("downloadDesktopSettings")
                Text("Mac preview for Apple Silicon. See the release notes before installing.").font(.footnote).foregroundStyle(.secondary)
            }.listRowBackground(HelmStyle.surface)
            Section("Help & privacy") {
                NavigationLink("Connections & setup") { ConnectionsView() }
                Link("Getting started", destination: PublicHelp.setup)
                Link("Privacy policy", destination: PublicHelp.privacy)
                Link("Support", destination: PublicHelp.support)
                NavigationLink("Third-party notices") { NoticesView() }
            }.listRowBackground(HelmStyle.surface)
            Section("Command Helm") {
                LabeledContent("Version", value: "\((Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "?") (\((Bundle.main.infoDictionary?["CFBundleVersion"] as? String) ?? "?"))")
            }
        }.helmConsole().navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Unpair this phone?", isPresented: $confirmUnpair, titleVisibility: .visible) {
                Button("Remove pairing and local drafts", role: .destructive) { model.disconnect() }
            } message: { Text("Removes this phone’s pairing, task draft and AI sharing choices. Computer files and running sessions remain there. This does not revoke a copied key; rotate it on the bridge to revoke all copies.") }
    }
}
