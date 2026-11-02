import SwiftUI
import SwiftTerm
import UIKit

struct TerminalsView: View {
    @EnvironmentObject var model: AppModel
    @State private var deviceID = ""
    @State private var kind = "shell"
    @State private var startID = UUID().uuidString.lowercased()
    @State private var starting = false
    @State private var notice: String?
    @State private var selection: TerminalSession?
    @State private var consentDevice: HelmDevice?
    @State private var pendingConnect: TerminalSession?
    @State private var confirmClose: TerminalSession?
    private var available: [HelmDevice] { model.devices.filter { $0.capabilities.terminal } }
    private var selected: HelmDevice? { available.first { $0.id == deviceID } ?? available.first }
    var body: some View {
        List {
            if model.demo { Label("Demo · sample terminal only", systemImage: "eye") }
            Section("Start on a computer") {
                if available.isEmpty { Text("Enable terminal support in your bridge configuration. The execution computer needs tmux; Codex also needs its configured CLI.").foregroundStyle(.secondary) }
                else {
                    Picker("Computer", selection: $deviceID) { ForEach(available) { Text($0.name).tag($0.id) } }
                    Picker("Session", selection: $kind) { Text("Shell").tag("shell"); if selected?.capabilities.codex == true { Text("Codex").tag("codex") } }
                    Button(starting ? "Opening…" : "Open terminal") {
                        guard let device = selected else { return }
                        if kind == "codex" && !model.hasAIConsent(device.id) && !model.demo { pendingConnect = nil; consentDevice = device }
                        else { start(device) }
                    }.disabled(starting)
                }
                Text("Sessions run on the selected computer. Leaving this view detaches the phone; it does not stop the session. Shell input can run commands on that computer.").font(.footnote).foregroundStyle(.secondary)
                if let notice { Text(notice).font(.footnote).foregroundStyle(HelmStyle.gold) }
                if let error = model.terminalError { Text(error).font(.footnote).foregroundStyle(HelmStyle.gold) }
            }.listRowBackground(HelmStyle.surface)
            Section("Persistent sessions") {
                if model.sessions.isEmpty { Text("No sessions yet. Reconnect here after leaving the app.").foregroundStyle(.secondary) }
                ForEach(model.sessions) { session in
                    Button {
                        if session.kind == "codex", !model.demo, !model.hasAIConsent(session.deviceId) {
                            if let device = model.devices.first(where: { $0.id == session.deviceId }) { pendingConnect = session; consentDevice = device }
                            else { notice = "Refresh Devices before reconnecting this Codex session." }
                        } else { selection = session }
                    } label: {
                        HStack { VStack(alignment: .leading, spacing: 5) { Text(session.title).foregroundStyle(.primary); Text(session.kind.capitalized + " · " + model.deviceName(.init(rawValue: session.deviceId))).font(.caption).foregroundStyle(.secondary) }; Spacer(); StateTag(state: session.state); Image(systemName: "chevron.right").foregroundStyle(.secondary) }
                    }.swipeActions { if !model.demo { Button("Close session", role: .destructive) { confirmClose = session } } }
                }
            }
        }.helmConsole().navigationTitle("Terminal").navigationBarTitleDisplayMode(.inline)
            .task { await model.refreshDevices(); await model.refreshTerminals(); if deviceID.isEmpty { deviceID = available.first?.id ?? "" } }
            .onChange(of: deviceID) { _, _ in kind = "shell"; restoreStartID() }
            .onChange(of: kind) { _, _ in restoreStartID() }
            .refreshable { await model.refreshTerminals() }
            .navigationDestination(item: $selection) { session in TerminalScreen(session: session, connection: model.demo ? nil : model.connection, demo: model.demo) }
            .sheet(item: $consentDevice) { device in AIConsentView(deviceName: device.name) { model.allowAI(device.id); consentDevice = nil; if let session = pendingConnect { selection = session; pendingConnect = nil } else { start(device) } } }
            .confirmationDialog("End this session and its running process?", isPresented: Binding(get: { confirmClose != nil }, set: { if !$0 { confirmClose = nil } }), titleVisibility: .visible) {
                Button("Close session", role: .destructive) {
                    guard let session = confirmClose, let connection = model.connection else { return }
                    confirmClose = nil
                    Task { do { try await BridgeClient(connection: connection).closeTerminal(id: session.id); await model.refreshTerminals() } catch { notice = error.localizedDescription } }
                }
            } message: { Text("This closes only the selected bridge-owned session on the computer. To leave it running, go back without closing.") }
    }
    private func startKey(deviceID: String, kind: String) -> String? {
        model.connection.map { AIConsentVault.key($0, device: deviceID) + ".pendingTerminalStart." + kind }
    }
    private func restoreStartID() {
        startID = startKey(deviceID: deviceID, kind: kind).flatMap { UserDefaults.standard.string(forKey: $0) } ?? UUID().uuidString.lowercased()
    }
    private func start(_ device: HelmDevice) {
        if model.demo { selection = .demo; return }
        starting = true; notice = nil
        let requestKind = kind
        let pendingKey = startKey(deviceID: device.id, kind: requestKind)
        let requestID = pendingKey.flatMap { UserDefaults.standard.string(forKey: $0) } ?? startID
        if let pendingKey { UserDefaults.standard.set(requestID, forKey: pendingKey) }
        Task {
            defer { starting = false }
            do {
                selection = try await model.startTerminal(device: device, kind: requestKind, id: requestID)
                if let pendingKey, UserDefaults.standard.string(forKey: pendingKey) == requestID { UserDefaults.standard.removeObject(forKey: pendingKey) }
                startID = UUID().uuidString.lowercased()
            }
            catch { notice = error.localizedDescription + " Refresh sessions before retrying. The same start identity has been retained." }
        }
    }
}
extension TerminalSession: Hashable { func hash(into hasher: inout Hasher) { hasher.combine(id) } }

@MainActor final class TerminalRuntime: ObservableObject {
    @Published var status = "Disconnected"
    @Published var inputStatus = "Input paused"
    @Published var uncertain = false
    @Published var transcript = ""
    @Published var replayNotice: String?
    weak var view: SwiftTerm.TerminalView?
    let session: TerminalSession
    let connection: Connection?
    let demo: Bool
    private var replay = TerminalReplay()
    private var gate = TerminalInputGate()
    private var queued = TerminalInputQueue()
    private var inputTask: Task<Void, Never>?
    private var resizeTask: Task<Void, Never>?
    private var cols = 80
    private var rows = 24
    private var active = false
    private var epoch = UUID()
    private var uncertaintyKey: String? { connection.map { AIConsentVault.key($0, device: session.deviceId) + ".terminal." + session.id } }
    init(session: TerminalSession, connection: Connection?, demo: Bool) {
        self.session = session; self.connection = connection; self.demo = demo
        if let uncertaintyKey, UserDefaults.standard.bool(forKey: uncertaintyKey) { gate.failed(); uncertain = true; inputStatus = "Delivery uncertain · inspect before resuming" }
    }
    func attach(_ terminal: SwiftTerm.TerminalView) { view = terminal }
    func run() async {
        active = true; let runEpoch = UUID(); epoch = runEpoch
        if demo {
            let sample = "\u{1b}[2J\u{1b}[HSAMPLE SESSION · offline demonstration\r\n$ pwd\r\n/Projects/sample\r\n$ echo Ready\r\nReady\r\n$ "
            view?.feed(text: sample); transcript = sample; status = "Sample · no network"; inputStatus = "Sample input disabled"; return
        }
        guard let connection else { return }
        status = "Connecting…"
        resize(cols: cols, rows: rows)
        defer { if epoch == runEpoch { stop(keepStatus: !Task.isCancelled && !active) } }
        while active && !Task.isCancelled && epoch == runEpoch {
            do {
                let output = try await BridgeClient(connection: connection).terminalOutput(id: session.id, after: replay.cursor)
                guard active, !Task.isCancelled, epoch == runEpoch else { return }
                let bytes = try replay.consume(output)
                if output.reset { view?.feed(text: "\u{1b}c"); transcript = ""; replayNotice = "Replay reset: earlier output may be unavailable. The screen was rebuilt from the retained buffer."; status = "Connected · replay reset" }
                else { status = output.state == "running" ? "Connected · running" : output.state.capitalized }
                if !bytes.isEmpty {
                    view?.feed(byteArray: [UInt8](bytes)[...])
                    transcript = String((transcript + String(decoding: bytes, as: UTF8.self)).suffix(16000))
                }
                if !uncertain && gate.pending == nil && ["Input paused", "Reconnect before typing"].contains(inputStatus) { inputStatus = "Input ready" }
                if output.state == "exited" { active = false; inputStatus = "Session exited"; return }
                try await Task.sleep(for: .milliseconds(350))
            } catch {
                if Task.isCancelled || epoch != runEpoch { return }
                status = error.localizedDescription; active = false; return
            }
        }
    }
    func stop(keepStatus: Bool = false) {
        active = false; epoch = UUID()
        if gate.pending != nil { markUncertain() }
        queued.removeAll(); inputTask?.cancel(); inputTask = nil; resizeTask?.cancel(); resizeTask = nil
        if !demo {
            if !keepStatus && status != "Exited" { status = "Detached · reconnect to check session" }
            if !uncertain { inputStatus = status == "Exited" ? "Session exited" : "Input paused" }
        }
    }
    func send(_ bytes: Data) {
        guard active, !demo, !uncertain, let connection, !bytes.isEmpty else { return }
        do { try queued.append(bytes) } catch { markUncertain(); queued.removeAll(); return }
        guard inputTask == nil else { return }
        let inputEpoch = epoch
        inputTask = Task {
            defer { if epoch == inputEpoch { inputTask = nil } }
            while active && !Task.isCancelled && epoch == inputEpoch && !queued.isEmpty && !uncertain {
                guard let next = queued.take() else { return }
                do {
                    let payload = try gate.begin(next)
                    // Persist only the identity/warning before dispatch, never typed bytes.
                    // An app termination while awaiting the response must fail closed.
                    if let uncertaintyKey {
                        UserDefaults.standard.set(true, forKey: uncertaintyKey)
                        UserDefaults.standard.set(payload.id, forKey: uncertaintyKey + ".inputID")
                    }
                    inputStatus = "Awaiting input acknowledgement…"
                    let receipt = try await BridgeClient(connection: connection).terminalInput(id: session.id, input: payload)
                    guard active, !Task.isCancelled, epoch == inputEpoch else { return }
                    try gate.acknowledge(receipt)
                    if !uncertain {
                        if let uncertaintyKey { UserDefaults.standard.removeObject(forKey: uncertaintyKey); UserDefaults.standard.removeObject(forKey: uncertaintyKey + ".inputID") }
                        inputStatus = "Input accepted by bridge"
                    }
                } catch {
                    guard epoch == inputEpoch else { return }
                    markUncertain(); queued.removeAll(); return
                }
            }
        }
    }
    private func markUncertain() {
        gate.failed(); uncertain = true; inputStatus = "Delivery uncertain · input paused; nothing will replay"
        if let uncertaintyKey { UserDefaults.standard.set(true, forKey: uncertaintyKey) }
    }
    func resumeAfterInspection() {
        guard gate.pending == nil || inputTask == nil else { return }
        gate.resumeAfterInspection(); uncertain = false
        if let uncertaintyKey { UserDefaults.standard.removeObject(forKey: uncertaintyKey); UserDefaults.standard.removeObject(forKey: uncertaintyKey + ".inputID") }
        inputStatus = active ? "Input ready · previous input was not resent" : "Reconnect before typing"
    }
    func resize(cols: Int, rows: Int) {
        self.cols = cols; self.rows = rows
        guard active, !demo, let connection else { return }
        resizeTask?.cancel()
        resizeTask = Task {
            do { try await Task.sleep(for: .milliseconds(200)); try await BridgeClient(connection: connection).resizeTerminal(id: session.id, cols: cols, rows: rows) }
            catch { if !Task.isCancelled { status = "Resize unavailable. Reconnect to check the terminal size." } }
        }
    }
}
struct TerminalScreen: View {
    @StateObject private var runtime: TerminalRuntime
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var reconnect = UUID()
    @State private var inspect = false
    @State private var showTranscript = false
    @State private var showClose = false
    @State private var closeNotice: String?
    init(session: TerminalSession, connection: Connection?, demo: Bool) { _runtime = StateObject(wrappedValue: TerminalRuntime(session: session, connection: connection, demo: demo)) }
    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 5) {
                Text(runtime.status).font(.caption).accessibilityIdentifier("terminalConnectionState")
                Text(runtime.inputStatus).font(.caption).foregroundStyle(runtime.uncertain ? HelmStyle.gold : .secondary).accessibilityIdentifier("terminalInputState")
                if let replayNotice = runtime.replayNotice { Text(replayNotice).font(.caption).foregroundStyle(HelmStyle.gold) }
                if let closeNotice { Text(closeNotice).font(.caption).foregroundStyle(HelmStyle.gold) }
                if runtime.uncertain { Button("I inspected the terminal · resume input") { inspect = true }.font(.footnote) }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(12).background(HelmStyle.surface)
            NativeTerminal(runtime: runtime, fontSize: typeSize.isAccessibilitySize ? 20 : 14).accessibilityIdentifier("nativeTerminal")
            ScrollView(.horizontal) {
                HStack(spacing: 12) {
                    control("Esc", bytes: [27], label: "Escape")
                    control("Tab", bytes: [9], label: "Tab")
                    control("Ctrl-C", bytes: [3], label: "Interrupt with Control C")
                    control("Ctrl-D", bytes: [4], label: "End input with Control D")
                    control("↑", bytes: [27,91,65], label: "Up arrow")
                    control("↓", bytes: [27,91,66], label: "Down arrow")
                    control("←", bytes: [27,91,68], label: "Left arrow")
                    control("→", bytes: [27,91,67], label: "Right arrow")
                    Button("Keyboard") { _ = runtime.view?.becomeFirstResponder() }.accessibilityLabel("Show terminal keyboard")
                    Button("Hide keyboard") { _ = runtime.view?.resignFirstResponder() }
                }.buttonStyle(.bordered).buttonBorderShape(.roundedRectangle(radius: 3)).font(.callout.monospaced()).padding(8)
            }.background(HelmStyle.surface).disabled(runtime.demo || runtime.uncertain)
        }.helmConsole().navigationTitle(runtime.session.title).navigationBarTitleDisplayMode(.inline)
            .task(id: "\(scenePhase)-\(reconnect)") { if scenePhase == .active { await runtime.run() } else { runtime.stop() } }
            .onDisappear { runtime.stop() }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Reconnect") { runtime.stop(); reconnect = UUID() }
                        Button("Accessible output text") { showTranscript = true }
                        if !runtime.demo { Button("Close session", role: .destructive) { showClose = true } }
                    } label: { Image(systemName: "ellipsis.circle") }.accessibilityLabel("Terminal actions")
                }
            }
            .sheet(isPresented: $showTranscript) { NavigationStack { ScrollView { Text(runtime.transcript).font(.body.monospaced()).textSelection(.enabled).padding() }.navigationTitle("Recent output text").toolbar { Button("Done") { showTranscript = false } } } }
            .confirmationDialog("Resume input after inspecting the remote screen?", isPresented: $inspect, titleVisibility: .visible) { Button("Resume without resending") { runtime.resumeAfterInspection() } } message: { Text("Previously uncertain input will never be resent automatically. Check whether it ran before typing it again.") }
            .confirmationDialog("End this session and its process?", isPresented: $showClose, titleVisibility: .visible) {
                Button("Close session", role: .destructive) {
                    guard let connection = runtime.connection else { return }
                    runtime.stop()
                    Task { do { try await BridgeClient(connection: connection).closeTerminal(id: runtime.session.id); closeNotice = "Session closed." } catch { closeNotice = error.localizedDescription } }
                }
            } message: { Text("Leaving this screen keeps work running. Close explicitly ends this selected session on the computer.") }
    }
    private func control(_ title: String, bytes: [UInt8], label: String) -> some View { Button(title) { runtime.send(Data(bytes)) }.accessibilityLabel(label).frame(minHeight: 44) }
}
private struct NativeTerminal: UIViewRepresentable {
    @ObservedObject var runtime: TerminalRuntime
    let fontSize: CGFloat
    func makeCoordinator() -> Coordinator { Coordinator(runtime) }
    func makeUIView(context: Context) -> SwiftTerm.TerminalView {
        let view = SwiftTerm.TerminalView(frame: .zero, font: .monospacedSystemFont(ofSize: fontSize, weight: .regular), options: TerminalOptions(kittyGraphics: .init(storageLimitBytesPerScreen: 0)))
        view.nativeBackgroundColor = UIColor(red: 0.025, green: 0.045, blue: 0.055, alpha: 1)
        view.nativeForegroundColor = UIColor(red: 0.87, green: 0.94, blue: 0.93, alpha: 1)
        view.terminalDelegate = context.coordinator
        runtime.attach(view)
        return view
    }
    func updateUIView(_ uiView: SwiftTerm.TerminalView, context: Context) {
        if uiView.font.pointSize != fontSize { uiView.font = .monospacedSystemFont(ofSize: fontSize, weight: .regular) }
    }
    static func dismantleUIView(_ uiView: SwiftTerm.TerminalView, coordinator: Coordinator) { coordinator.runtime.stop(); uiView.terminalDelegate = nil; close(uiView) }
    private static func close(_ view: SwiftTerm.TerminalView) { if !view.updateUiClosed() { DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { close(view) } } }
    @MainActor final class Coordinator: NSObject, TerminalViewDelegate {
        let runtime: TerminalRuntime
        init(_ runtime: TerminalRuntime) { self.runtime = runtime }
        func sizeChanged(source: SwiftTerm.TerminalView, newCols: Int, newRows: Int) { runtime.resize(cols: newCols, rows: newRows) }
        func send(source: SwiftTerm.TerminalView, data: ArraySlice<UInt8>) { runtime.send(Data(data)) }
        func setTerminalTitle(source: SwiftTerm.TerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: SwiftTerm.TerminalView, directory: String?) {}
        func scrolled(source: SwiftTerm.TerminalView, position: Double) {}
        func requestOpenLink(source: SwiftTerm.TerminalView, link: String, params: [String: String]) {}
        func bell(source: SwiftTerm.TerminalView) {}
        func clipboardCopy(source: SwiftTerm.TerminalView, content: Data) {}
        func clipboardRead(source: SwiftTerm.TerminalView) -> Data? { nil }
        func iTermContent(source: SwiftTerm.TerminalView, content: ArraySlice<UInt8>) {}
        func rangeChanged(source: SwiftTerm.TerminalView, startY: Int, endY: Int) {}
    }
}
