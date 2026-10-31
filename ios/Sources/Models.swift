import Foundation

enum HelmError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}
struct Connection: Codable {
    let url: URL
    let token: String
    let certificateFingerprint: String?
    init(url: String, token: String, fingerprint: String? = nil) throws {
        guard let parts = URLComponents(string: url.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.scheme == "https" || (parts.scheme == "http" && ["localhost", "127.0.0.1", "::1"].contains(host)),
              let parsed = parts.url, !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw HelmError.message("Use an HTTPS bridge address and its pairing key. HTTP is allowed only for a local simulator.")
        }
        let pin = fingerprint?.lowercased().replacingOccurrences(of: ":", with: "")
        if let pin, !pin.isEmpty, (pin.count != 64 || pin.contains(where: { !$0.isHexDigit })) {
            throw HelmError.message("The certificate fingerprint must have 64 hexadecimal characters.")
        }
        self.url = parsed
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        self.certificateFingerprint = (pin?.isEmpty == false) ? pin : nil
    }
}
struct AgentSession: Codable, Identifiable {
    var controllable: Bool? = nil
    var commands: [AgentCommand]? = nil
    let id: String; let provider: String; let state: String; let seen: Double
    var isStale: Bool { Date().timeIntervalSince1970 - seen > 180 }
}
struct AgentCommand: Codable, Identifiable {
    let id: String
    let kind: String
    let text: String
    let state: String
}
struct Project: Codable, Identifiable {
    let id: String; let name: String; let sessions: [AgentSession]
    var machineId: String? = nil
    var lastSeen: Double { sessions.map(\.seen).max() ?? 0 }
    var state: String {
        let current = sessions.filter { !$0.isStale }
        if current.contains(where: { $0.state == "waiting" }) { return "waiting" }
        if current.contains(where: { $0.state == "working" }) { return "working" }
        if let latest = sessions.max(by: { $0.seen < $1.seen }) {
            return latest.isStale && !["finished", "failed"].contains(latest.state) ? "stale" : latest.state
        }
        return "idle"
    }
}
struct Update: Codable, Identifiable {
    let id: String; let projectId: String; let provider: String; let text: String; let state: String; let createdAt: Double
}
struct Question: Codable, Identifiable {
    let id: String; let projectId: String; let provider: String; let text: String; let options: [String]; let state: String; let createdAt: Double; let expiresAt: Double; let answer: String?
    var isAnswerable: Bool { state == "pending" && expiresAt > Date().timeIntervalSince1970 }
    var displayState: String {
        switch state {
        case "pending": return isAnswerable ? "Needs you" : "Expired"
        case "answered": return "Saved · awaiting agent"
        case "delivered": return "Sent to agent connection"
        default: return state.capitalized
        }
    }
}
struct Snapshot: Codable {
    let generatedAt: Double; let projects: [Project]; let updates: [Update]; let questions: [Question]
    func projectName(_ id: String) -> String { projects.first { $0.id == id }?.name ?? "Project" }
    static let empty = Snapshot(generatedAt: 0, projects: [], updates: [], questions: [])
    static var demo: Snapshot {
        let now = Date().timeIntervalSince1970
        return Snapshot(generatedAt: now, projects: [
            Project(id: "beta", name: "Launchpad", sessions: [AgentSession(id: "d1", provider: "codex", state: "waiting", seen: now)], machineId: "computer"),
            Project(id: "studio", name: "Studio website", sessions: [AgentSession(id: "d2", provider: "claude", state: "working", seen: now)], machineId: "computer"),
            Project(id: "library", name: "Project Atlas", sessions: [AgentSession(id: "d3", provider: "codex", state: "finished", seen: now - 420)], machineId: "workstation")
        ], updates: [
            Update(id: "u1", projectId: "studio", provider: "claude", text: "The mobile layout is ready. Checking navigation and accessibility next.", state: "working", createdAt: now - 45),
            Update(id: "u2", projectId: "beta", provider: "codex", text: "Beta candidate built. Waiting on your choice of testers.", state: "waiting", createdAt: now - 120),
            Update(id: "u3", projectId: "library", provider: "codex", text: "The import checks passed. The candidate is ready for review.", state: "finished", createdAt: now - 420)
        ], questions: [Question(id: "q1", projectId: "beta", provider: "codex", text: "Keep the beta private or invite the team?", options: ["Keep it private", "Invite the team"], state: "pending", createdAt: now - 120, expiresAt: now + 3600, answer: nil)])
    }
}

// MARK: - Fleet (machine-aware view of the bridge snapshot)
enum Machine: String, CaseIterable, Identifiable {
    case spellbook = "Bridge computer"
    case spelltower = "Remote computer"
    var id: String { rawValue }
    /// The Mac bridge prefixes every Spelltower project, session, update and question id.
    static let remotePrefix = "spelltower-"
    static func of(projectId id: String) -> Machine { id.hasPrefix(remotePrefix) ? .spelltower : .spellbook }
}
extension Project {
    var machine: Machine {
        switch machineId {
        case "spellbook": return .spellbook
        case "spelltower": return .spelltower
        default: return Machine.of(projectId: id)
        }
    }
    /// The bridge appends " · Offline" to Spelltower projects while the Mac cannot reach Spelltower.
    var isOffline: Bool { name.hasSuffix(" · Offline") }
}
struct FleetStatus: Identifiable {
    let machine: Machine
    let projects: Int
    let active: Int
    let needsYou: Int
    let offline: Bool
    let lastSeen: Double
    var id: String { machine.id }
    /// Honest state: "offline" only when the bridge reported it; no projects means no signal, never "online".
    var state: String {
        if projects == 0 { return "quiet" }
        if offline { return "offline" }
        if needsYou > 0 { return "waiting" }
        if active > 0 { return "working" }
        return "idle"
    }
}
extension Snapshot {
    func fleet() -> [FleetStatus] {
        Machine.allCases.map { machine in
            let mine = projects.filter { $0.machine == machine }
            let answerable = questions.filter { question in
                guard question.isAnswerable else { return false }
                let owner = projects.first { $0.id == question.projectId }?.machine ?? Machine.of(projectId: question.projectId)
                return owner == machine
            }
            return FleetStatus(
                machine: machine,
                projects: mine.count,
                active: mine.filter { ["working", "waiting"].contains($0.state) }.count,
                needsYou: answerable.count,
                offline: mine.contains { $0.isOffline },
                lastSeen: mine.map(\.lastSeen).max() ?? 0)
        }
    }
}
