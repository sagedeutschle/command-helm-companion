import XCTest
@testable import CommandHelmRemote

final class FleetTests: XCTestCase {
    private func project(_ id: String, _ name: String, state: String = "working", seen: Double = Date().timeIntervalSince1970) -> Project {
        Project(id: id, name: name, sessions: [AgentSession(id: id + "-s", provider: "claude", state: state, seen: seen)])
    }
    func testMachineIsDerivedFromBridgePrefixNotName() {
        XCTAssertEqual(Machine.of(projectId: "spelltower-abc"), .spelltower)
        XCTAssertEqual(Machine.of(projectId: "abc"), .spellbook)
        XCTAssertEqual(project("spelltower-x", "Ygg · Spelltower").machine, .spelltower)
        XCTAssertEqual(project("x", "Site · MacBook").machine, .spellbook)
    }
    func testDirectHubMachineIdentityOverridesLegacyPrefixAndRoutesQuestions() throws {
        let now = Date().timeIntervalSince1970
        let raw = Data(#"{"id":"local-direct-id","name":"Direct project","sessions":[],"machineId":"spelltower"}"#.utf8)
        let direct = try JSONDecoder().decode(Project.self, from: raw)
        XCTAssertEqual(direct.machine, .spelltower)
        var explicit = project("spelltower-legacy", "Explicit Mac")
        explicit.machineId = "spellbook"
        XCTAssertEqual(explicit.machine, .spellbook)
        let legacy = try JSONDecoder().decode(Project.self, from: Data(#"{"id":"spelltower-old","name":"Old proxy","sessions":[]}"#.utf8))
        XCTAssertNil(legacy.machineId)
        XCTAssertEqual(legacy.machine, .spelltower)
        let snap = Snapshot(generatedAt: now, projects: [direct], updates: [], questions: [
            Question(id: "q-direct", projectId: direct.id, provider: "codex", text: "Choose?", options: [], state: "pending", createdAt: now, expiresAt: now + 600, answer: nil)
        ])
        XCTAssertEqual(snap.fleet().first { $0.machine == .spelltower }?.needsYou, 1)
        XCTAssertEqual(snap.fleet().first { $0.machine == .spellbook }?.needsYou, 0)
    }
    func testOfflineOnlyWhenBridgeSaysSo() {
        XCTAssertTrue(project("spelltower-x", "Ygg · Spelltower · Offline").isOffline)
        XCTAssertFalse(project("spelltower-x", "Ygg · Spelltower").isOffline)
    }
    func testFleetAlwaysListsBothMachinesAndNeverInventsOnline() {
        let snap = Snapshot(generatedAt: 0, projects: [project("a", "Site · MacBook")], updates: [], questions: [])
        let fleet = snap.fleet()
        XCTAssertEqual(fleet.map(\.machine), [.spellbook, .spelltower])
        XCTAssertEqual(fleet[0].state, "working")
        XCTAssertEqual(fleet[1].projects, 0)
        XCTAssertEqual(fleet[1].state, "quiet")
    }
    func testFleetCountsOfflineNeedsYouAndActive() {
        let now = Date().timeIntervalSince1970
        let snap = Snapshot(generatedAt: now, projects: [
            project("spelltower-y", "Ygg · Spelltower · Offline", state: "working", seen: now),
            project("b", "Lab · MacBook", state: "waiting", seen: now),
            project("c", "Old · MacBook", state: "finished", seen: now - 1000)
        ], updates: [], questions: [
            Question(id: "q1", projectId: "b", provider: "codex", text: "?", options: [], state: "pending", createdAt: now, expiresAt: now + 600, answer: nil),
            Question(id: "q2", projectId: "spelltower-y", provider: "claude", text: "?", options: [], state: "pending", createdAt: now, expiresAt: now - 1, answer: nil)
        ])
        let fleet = Dictionary(uniqueKeysWithValues: snap.fleet().map { ($0.machine, $0) })
        XCTAssertEqual(fleet[.spelltower]?.state, "offline")
        XCTAssertEqual(fleet[.spelltower]?.needsYou, 0)
        XCTAssertEqual(fleet[.spellbook]?.needsYou, 1)
        XCTAssertEqual(fleet[.spellbook]?.state, "waiting")
        XCTAssertEqual(fleet[.spellbook]?.projects, 2)
        XCTAssertEqual(fleet[.spellbook]?.active, 1)
    }
    func testNewQuestionDetectionIgnoresFirstSnapshot() {
        let now = Date().timeIntervalSince1970
        let q = Question(id: "q", projectId: "p", provider: "codex", text: "?", options: [], state: "pending", createdAt: now, expiresAt: now + 600, answer: nil)
        let snap = Snapshot(generatedAt: now, projects: [], updates: [], questions: [q])
        XCTAssertTrue(AppModel.newQuestionIds(known: nil, in: snap).isEmpty)
        XCTAssertEqual(AppModel.newQuestionIds(known: [], in: snap), ["q"])
        XCTAssertTrue(AppModel.newQuestionIds(known: ["q"], in: snap).isEmpty)
    }
}
