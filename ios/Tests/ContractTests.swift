import XCTest
@testable import CommandHelmRemote

final class ContractTests: XCTestCase {
    func testPairingRejectsInsecureRemoteAndEmbeddedCredentials() {
        XCTAssertThrowsError(try Connection(url: "http://remote.example.com:8746", token: "example"))
        XCTAssertThrowsError(try Connection(url: "https://user:pass@remote.example.com", token: "example"))
        XCTAssertThrowsError(try Connection(url: "https://remote.example.com?token=abc", token: "example"))
        XCTAssertNoThrow(try Connection(url: "https://remote.example.com:8746", token: "example"))
        XCTAssertNoThrow(try Connection(url: "http://localhost:8746", token: "example"))
    }
    func testExpiredQuestionIsNotAnswerableEvenBeforeRefresh() {
        let q = Question(id: "q", projectId: "p", provider: "codex", text: "Choose?", options: [], state: "pending", createdAt: 1, expiresAt: 2, answer: nil)
        XCTAssertFalse(q.isAnswerable)
    }
    func testFreshnessCannotImplyOfflineSessionIsRunning() {
        let s = AgentSession(id: "s", provider: "claude", state: "working", seen: 1)
        XCTAssertTrue(s.isStale)
    }
    func testDecodeBridgeContractWithNoAnswerYet() throws {
        let data = Data(#"{"generatedAt":1700000000,"projects":[{"id":"p","name":"Example","sessions":[]}],"updates":[],"questions":[{"id":"q","projectId":"p","provider":"codex","text":"Which option?","options":["A","B"],"state":"pending","createdAt":1700000000,"expiresAt":1700000300,"answer":null}]}"#.utf8)
        let result = try JSONDecoder().decode(Snapshot.self, from: data)
        XCTAssertEqual(result.questions.first?.options, ["A", "B"])
        XCTAssertEqual(result.projectName("p"), "Example")
    }
}
