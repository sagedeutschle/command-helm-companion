import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

@MainActor final class AppModel: ObservableObject {
    static var testing: Bool { ProcessInfo.processInfo.arguments.contains("--ui-testing") || NSClassFromString("XCTestCase") != nil }
    @Published var devices: [HelmDevice] = []
    @Published var services: ConnectionsSnapshot = .empty
    @Published var deviceError: String?
    @Published var connectionsError: String?
    @Published var legacyBridge = false
    @Published var deviceRefreshing = false
    @Published var sessions: [TerminalSession] = []
    @Published var terminalError: String?
    @Published var snapshot: Snapshot = .empty
    @Published var connection: Connection?
    @Published var demo = false
    @Published var error: String?
    @Published var taskSnapshot: TaskSnapshot = .empty
    @Published var taskError: String?
    @Published var taskLastSuccess: Date?
    @Published var taskRefreshing = false
    @Published var taskSending = false
    @Published var taskNotice: String?
    @Published var taskDraft = TaskDraft() {
        didSet {
            if taskDraft.id != oldValue.id { taskNotice = nil }
            if !Self.testing { TaskDraftVault.save(taskDraft) }
        }
    }
    @Published var refreshing = false
    @Published var lastSuccess: Date?
    @Published var pairingProposal: Connection?
    @Published var selectedQuestion: Question?
    typealias TaskSender = @MainActor (Connection, TaskSubmission) async throws -> HelmTask
    private let taskSender: TaskSender
    private var generation = UUID()
    private var knownPending: Set<String>?
    var pending: [Question] { snapshot.questions.filter(\.isAnswerable) }
    var stale: Bool { lastSuccess.map { Date().timeIntervalSince($0) > 30 } ?? true }
    var tasksStale: Bool { taskLastSuccess.map { Date().timeIntervalSince($0) > 30 } ?? true }
    var taskTargets: [TaskTarget] {
        if demo { return devices.filter { $0.capabilities.tasks }.map(\.target) }
        if !legacyBridge { return devices.filter { $0.capabilities.tasks }.map(\.target) }
        let known = Set(taskSnapshot.workers.map(\.target) + taskSnapshot.tasks.map(\.target))
        return (known.isEmpty ? [.spellbook, .spelltower] : Array(known)).sorted { $0.rawValue < $1.rawValue }
    }
    func deviceName(_ target: TaskTarget) -> String { if target.rawValue == "__bridge__" { return "Bridge activity" }; return devices.first { $0.id == target.rawValue }?.name ?? target.rawValue }
    func activityDeviceID(_ projectID: String) -> String {
        snapshot.projects.first { $0.id == projectID }?.machineId ?? (projectID.hasPrefix(Machine.remotePrefix) ? "spelltower" : "spellbook")
    }
    func hasAIConsent(_ device: String) -> Bool { !demo && connection.map { AIConsentVault.contains($0, device: device) } == true }
    func allowAI(_ device: String) { if let connection, !demo { AIConsentVault.grant(connection, device: device); objectWillChange.send() } }
    var canSubmitTask: Bool { !demo && connection != nil && !tasksStale && taskError == nil && !taskSending }
    init(taskSender: @escaping TaskSender = { connection, submission in try await BridgeClient(connection: connection).submitTask(submission) }) {
        self.taskSender = taskSender
        if !Self.testing { connection = PairingVault.load(); taskDraft = TaskDraftVault.load() }
    }
    func explore() {
        generation = UUID(); demo = true; snapshot = .demo; lastSuccess = Date(); error = nil
        devices = HelmDevice.demo; services = .demo; sessions = [.demo]; deviceError = nil; connectionsError = nil; terminalError = nil; legacyBridge = false
        taskDraft.target = TaskTarget(rawValue: "computer")
        taskSnapshot = .demo; taskLastSuccess = Date(); taskError = nil; taskNotice = nil
    }
    func pair(_ value: Connection) async throws {
        let fresh = try await BridgeClient(connection: value).snapshot()
        if let connection, AIConsentVault.key(connection, device: "") != AIConsentVault.key(value, device: "") {
            AIConsentVault.clear(connection); taskDraft = TaskDraft(); TaskDraftVault.clear()
        }
        if !Self.testing { try PairingVault.save(value) }
        generation = UUID(); connection = value; demo = false; snapshot = fresh; lastSuccess = Date(); error = nil; knownPending = Self.answerableIds(in: fresh)
        taskSnapshot = .empty; taskLastSuccess = nil; taskError = nil; taskNotice = nil
        await refreshTasks()
        await refreshDevices()
    }
    func refresh() async {
        guard !refreshing, !demo, let connection else { return }
        refreshing = true
        // Task API availability is independent of the monitoring snapshot.
        async let taskRefresh: Void = refreshTasks()
        async let deviceRefresh: Void = refreshDevices()
        let requestGeneration = generation
        defer { refreshing = false }
        do {
            let value = try await BridgeClient(connection: connection).snapshot()
            guard requestGeneration == generation else { return }
            snapshot = value; lastSuccess = Date(); error = nil; noteQuestions(in: value)
        } catch {
            guard requestGeneration == generation else { return }
            self.error = "Couldn’t reach your bridge. Showing the last received updates. \(error.localizedDescription)"
        }
        await taskRefresh
        await deviceRefresh
    }
    func refreshTasks() async {
        guard !taskRefreshing, !demo, let connection else { return }
        taskRefreshing = true
        let requestGeneration = generation
        defer { taskRefreshing = false }
        do {
            let value = try await BridgeClient(connection: connection).tasks()
            guard requestGeneration == generation else { return }
            taskSnapshot = value; taskLastSuccess = Date(); taskError = nil
        } catch {
            guard requestGeneration == generation else { return }
            taskError = "Task updates unavailable. Showing the last received task states. \(error.localizedDescription)"
        }
    }
    func refreshDevices() async {
        guard !deviceRefreshing, !demo, let connection else { return }
        deviceRefreshing = true
        let requestGeneration = generation
        defer { deviceRefreshing = false }
        let client = BridgeClient(connection: connection)
        do {
            let inventory = try await client.devices()
            guard generation == requestGeneration else { return }
            devices = inventory; legacyBridge = false; deviceError = nil
            if !taskTargets.contains(taskDraft.target), taskDraft.prompt.isEmpty, let first = taskTargets.first { taskDraft.target = first }
        } catch {
            guard generation == requestGeneration else { return }
            legacyBridge = error is BridgeAPIError
            deviceError = error.localizedDescription
        }
        do {
            let value = try await client.connections()
            guard generation == requestGeneration else { return }
            services = value; connectionsError = nil
        } catch {
            guard generation == requestGeneration else { return }; connectionsError = error.localizedDescription
        }
    }
    func setDiscovery(id: String, enabled: Bool) async {
        guard !demo, let connection else { return }
        let requestGeneration = generation
        do {
            let value = try await BridgeClient(connection: connection).setDiscovery(id: id, enabled: enabled)
            guard generation == requestGeneration else { return }; services = value; connectionsError = nil
        } catch { guard generation == requestGeneration else { return }; connectionsError = error.localizedDescription }
    }
    func refreshTerminals() async {
        guard !demo, let connection else { return }
        let requestGeneration = generation
        do {
            let value = try await BridgeClient(connection: connection).terminals()
            guard generation == requestGeneration else { return }; sessions = value; terminalError = nil
        } catch { guard generation == requestGeneration else { return }; terminalError = error.localizedDescription }
    }
    func startTerminal(device: HelmDevice, kind: String, id: String) async throws -> TerminalSession {
        guard !demo, let connection else { throw HelmError.message("Sample mode never opens a remote session.") }
        guard device.capabilities.terminal, kind == "shell" || (kind == "codex" && device.capabilities.codex) else { throw HelmError.message("This computer does not support that terminal.") }
        guard kind != "codex" || hasAIConsent(device.id) else { throw HelmError.message("Allow AI sharing for this computer first.") }
        let requestGeneration = generation
        let session = try await BridgeClient(connection: connection).startTerminal(.init(id: id, deviceId: device.id, kind: kind, cols: 80, rows: 24))
        guard generation == requestGeneration else { throw CancellationError() }
        await refreshTerminals(); return session
    }
    func submitTask() async throws {
        guard !demo else { throw HelmError.message("Demo tasks are samples. Pair a bridge to send real work.") }
        guard canSubmitTask, let connection else { throw HelmError.message("Reconnect and refresh tasks before sending work.") }
        guard hasAIConsent(taskDraft.target.rawValue) else { throw HelmError.message("Review and allow AI sharing for this computer before sending a Codex task.") }
        guard taskTargets.contains(taskDraft.target) else { throw HelmError.message("Choose a computer with a registered task worker.") }
        if let message = taskDraft.validationMessage { throw HelmError.message(message) }
        let submission = taskDraft.submission
        let requestGeneration = generation
        taskSending = true; taskNotice = nil
        defer { taskSending = false }
        do {
            let task = try await taskSender(connection, submission)
            guard requestGeneration == generation else { return }
            taskSnapshot = TaskSnapshot(tasks: [task] + taskSnapshot.tasks.filter { $0.id != task.id }, workers: taskSnapshot.workers)
            if taskDraft.id == submission.id { var cleared = TaskDraft(); cleared.target = submission.target; taskDraft = cleared }
            taskNotice = "Task saved for \(deviceName(task.target)). It will wait in the queue if that computer is unavailable."
            await refreshTasks()
        } catch {
            guard requestGeneration == generation else { return }
            // A lost response might follow a saved task. Keep its UUID for a safe retry.
            taskNotice = "Delivery could not be confirmed. Retry this unchanged draft to check the same task."
            throw error
        }
    }
    func answer(_ q: Question, text: String, replyId: String) async throws {
        guard !demo else { throw HelmError.message("This is a sample question. Pair your bridge to send real replies.") }
        guard let connection, !stale, error == nil else { throw HelmError.message("Reconnect and refresh before answering.") }
        guard hasAIConsent(activityDeviceID(q.projectId)) else { throw HelmError.message("Review and allow AI sharing before sending this answer.") }
        guard q.isAnswerable else { throw HelmError.message("This question has expired.") }
        try await BridgeClient(connection: connection).reply(questionId: q.id, answer: text, replyId: replyId)
        await refresh()
    }
    func disconnect() {
        if let connection { AIConsentVault.clear(connection) }
        taskDraft = TaskDraft(); TaskDraftVault.clear()
        pairingProposal = nil; selectedQuestion = nil
        devices = []; services = .empty; sessions = []; deviceError = nil; connectionsError = nil; terminalError = nil; legacyBridge = false
        generation = UUID(); if !Self.testing { PairingVault.clear() }; connection = nil; demo = false
        snapshot = .empty; lastSuccess = nil; error = nil; knownPending = nil
        taskSnapshot = .empty; taskLastSuccess = nil; taskError = nil; taskNotice = nil
    }
    func direct(_ session: AgentSession, deviceID: String, kind: String, text: String, id: String) async throws {
        guard !demo, let connection, !stale, error == nil, session.controllable == true else {
            throw HelmError.message("Connect this agent through Helm and refresh before sending directions.")
        }
        guard kind != "guide" || hasAIConsent(deviceID) else { throw HelmError.message("Review and allow AI sharing before sending directions.") }
        try await BridgeClient(connection: connection).command(sessionId: session.id, kind: kind, text: text, id: id)
        await refresh()
    }
    nonisolated static func answerableIds(in value: Snapshot) -> Set<String> { Set(value.questions.filter(\.isAnswerable).map(\.id)) }
    /// Questions that became answerable since the previous snapshot. A cold start never counts, so launching the app stays silent.
    nonisolated static func newQuestionIds(known: Set<String>?, in value: Snapshot) -> Set<String> {
        guard let known else { return [] }
        return answerableIds(in: value).subtracting(known)
    }
    private func noteQuestions(in value: Snapshot) {
        let fresh = Self.newQuestionIds(known: knownPending, in: value)
        knownPending = Self.answerableIds(in: value)
        guard !fresh.isEmpty else { return }
        #if canImport(UIKit)
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        #endif
    }
    func proposePairing(_ url: URL) {
        guard url.scheme == "helmremote", url.host == "pair", let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        func value(_ key: String) -> String? { parts.queryItems?.first { $0.name == key }?.value }
        do { pairingProposal = try Connection(url: value("url") ?? "", token: value("token") ?? "", fingerprint: value("fingerprint")) }
        catch { self.error = error.localizedDescription }
    }
}
