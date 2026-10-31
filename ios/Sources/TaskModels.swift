import Foundation

/// Stable wire ID; names come from device inventory. Old and retired IDs remain readable.
struct TaskTarget: RawRepresentable, Codable, Hashable, Identifiable {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }
    init(from decoder: Decoder) throws { rawValue = try decoder.singleValueContainer().decode(String.self) }
    func encode(to encoder: Encoder) throws { var value = encoder.singleValueContainer(); try value.encode(rawValue) }
    var id: String { rawValue }
    var title: String { rawValue }
    static let spellbook = TaskTarget(rawValue: "spellbook")
    static let spelltower = TaskTarget(rawValue: "spelltower")
}
struct HelmTask: Codable, Identifiable, Equatable {
    let id: String
    let target: TaskTarget
    let prompt: String
    let workspace: String
    let state: String
    let createdAt: Double
    let updatedAt: Double
    let deliveryPath: String
    let report: String
    let error: String
}
struct TaskWorker: Codable, Equatable {
    let target: TaskTarget
    let seen: Double
    let state: String
    let taskId: String
    func isRecent(at date: Date = Date()) -> Bool {
        let age = date.timeIntervalSince1970 - seen
        return seen > 0 && age >= -30 && age <= 90
    }
}
struct TaskSnapshot: Codable, Equatable {
    let tasks: [HelmTask]
    let workers: [TaskWorker]
    static let empty = TaskSnapshot(tasks: [], workers: [])
    static var demo: TaskSnapshot {
        let now = Date().timeIntervalSince1970
        return TaskSnapshot(tasks: [
            HelmTask(id: "sample-completed", target: TaskTarget(rawValue: "computer"), prompt: "Prepare a project checklist and save it with a short report.", workspace: "", state: "completed", createdAt: now - 600, updatedAt: now - 420, deliveryPath: "Sample delivery / checklist", report: "Sample report: the checklist is ready in CHECKLIST.md. This is demonstration data; no work was sent to a computer.", error: ""),
            HelmTask(id: "sample-queued", target: TaskTarget(rawValue: "workstation"), prompt: "Review the next implementation steps.", workspace: "", state: "queued", createdAt: now - 90, updatedAt: now - 90, deliveryPath: "", report: "", error: "")
        ], workers: [])
    }
}
struct TaskSubmission: Codable, Equatable {
    let id: String
    let target: TaskTarget
    let prompt: String
    let workspace: String
}
/// The identity belongs to the payload. Editing any field starts a different task;
/// a transport failure leaves both the payload and its identity available for retry.
struct TaskDraft: Codable, Equatable {
    private(set) var id = UUID().uuidString.lowercased()
    var target: TaskTarget = .spellbook { didSet { if target != oldValue { renewIdentity() } } }
    var prompt = "" { didSet { if prompt != oldValue { renewIdentity() } } }
    var workspace = "" { didSet { if workspace != oldValue { renewIdentity() } } }
    mutating private func renewIdentity() { id = UUID().uuidString.lowercased() }
    mutating func normalizeIdentity() {
        id = UUID(uuidString: id)?.uuidString.lowercased() ?? UUID().uuidString.lowercased()
    }
    var validationMessage: String? {
        if prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Describe the work you want Codex to complete." }
        if prompt.unicodeScalars.count > 8000 { return "Keep the task description within 8,000 characters." }
        if prompt.contains("\0") || workspace.contains("\0") { return "Remove null characters from the task description and folder." }
        let path = workspace
        if path.unicodeScalars.count > 4096 { return "Keep the project folder within 4,096 characters." }
        if !path.isEmpty && !path.hasPrefix("/") { return "Use an absolute project folder, starting with /." }
        return nil
    }
    var submission: TaskSubmission {
        TaskSubmission(id: id, target: target, prompt: prompt, workspace: workspace)
    }
}
enum TaskDraftVault {
    private static let key = "commandhelm.taskDraft.v1"
    static func load() -> TaskDraft {
        guard let data = UserDefaults.standard.data(forKey: key), var draft = try? JSONDecoder().decode(TaskDraft.self, from: data) else { return TaskDraft() }
        draft.normalizeIdentity()
        return draft
    }
    static func clear() { UserDefaults.standard.removeObject(forKey: key) }
    static func save(_ draft: TaskDraft) {
        guard let data = try? JSONEncoder().encode(draft) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }
}
