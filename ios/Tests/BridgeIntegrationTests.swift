import XCTest
@testable import CommandHelmRemote
final class BridgeIntegrationTests: XCTestCase {
    func testPinnedTLSAndRealPhoneReplyContract() async throws {
        let c = try Connection(url: TestBridge.url, token: TestBridge.credential, fingerprint: TestBridge.pin)
        let client = BridgeClient(connection: c)
        let snapshot = try await client.snapshot()
        XCTAssertEqual(snapshot.projects.first?.name, "Integration project")
        let q = try XCTUnwrap(snapshot.questions.first { $0.text == "Unit integration question" })
        try await client.reply(questionId: q.id, answer: "Confirmed", replyId: "integration-unit")
        let next = try await client.snapshot()
        XCTAssertEqual(next.questions.first { $0.id == q.id }?.answer, "Confirmed")
    }
    func testTaskSubmissionAndRetryOverPinnedTLSBridge() async throws {
        let client = BridgeClient(connection: try Connection(url: TestBridge.url, token: TestBridge.credential, fingerprint: TestBridge.pin))
        var draft = TaskDraft()
        draft.target = .spelltower
        draft.prompt = "Integration fixture task; no worker should execute this."
        let submission = draft.submission
        XCTAssertEqual(submission.id, UUID(uuidString: submission.id)?.uuidString.lowercased())
        let task = try await client.submitTask(submission)
        let retry = try await client.submitTask(submission)
        XCTAssertEqual(task.id, submission.id)
        XCTAssertEqual(retry.id, submission.id)
        XCTAssertEqual(retry.target, .spelltower)
        XCTAssertEqual(retry.workspace, "")
        XCTAssertEqual(retry.state, "queued")
        let snapshot = try await client.tasks()
        XCTAssertEqual(snapshot.tasks.filter { $0.id == submission.id }.count, 1)
    }
    func testWrongCertificateCannotReceiveCredentials() async throws {
        let c = try Connection(url: TestBridge.url, token: TestBridge.credential, fingerprint: String(repeating: "0", count: 64))
        do { let _ = try await BridgeClient(connection: c).snapshot(); XCTFail("An unpaired certificate must fail") }
        catch { XCTAssertTrue(error is URLError) }
    }
}
extension BridgeIntegrationTests {
    func testDevicesConnectionsAndPersistentTerminalOverPinnedTLS() async throws {
        let client = BridgeClient(connection: try Connection(url: TestBridge.url, token: TestBridge.credential, fingerprint: TestBridge.pin))
        let devices = try await client.devices()
        XCTAssertEqual(devices.first?.id, "computer")
        XCTAssertEqual(devices.first?.name, "My computer")
        XCTAssertEqual(devices.first?.capabilities.tasks, true)
        let connections = try await client.connections()
        XCTAssertEqual(Set(connections.connections.map(\.id)), ["netbird", "github"])
        XCTAssertTrue(connections.connections.allSatisfy { !$0.enabled })
        let id = UUID().uuidString.lowercased()
        let start = TerminalStart(id: id, deviceId: "computer", kind: "shell", cols: 80, rows: 24)
        let session = try await client.startTerminal(start)
        let retry = try await client.startTerminal(start)
        XCTAssertEqual(session, retry)
        var replay = TerminalReplay()
        let first = try await client.terminalOutput(id: id, after: 0)
        XCTAssertTrue(String(decoding: try replay.consume(first), as: UTF8.self).contains("Fixture shell"))
        let input = TerminalInput(bytes: Data([0xe2, 0x82, 0xac, 27, 91, 65, 13]))
        let receipt = try await client.terminalInput(id: id, input: input)
        let repeated = try await client.terminalInput(id: id, input: input)
        XCTAssertTrue(receipt.accepted)
        XCTAssertEqual(receipt.id, repeated.id)
        let echoed = try await client.terminalOutput(id: id, after: replay.cursor)
        XCTAssertEqual(try replay.consume(echoed), Data(base64Encoded: input.data))
        try await client.resizeTerminal(id: id, cols: 91, rows: 27)
        let resized = try await client.terminalOutput(id: id, after: replay.cursor)
        XCTAssertEqual(String(decoding: try replay.consume(resized), as: UTF8.self), "size=91x27\r\n")
        let list = try await client.terminals()
        XCTAssertTrue(list.contains { $0.id == id })
        try await client.closeTerminal(id: id)
        let closed = try await client.terminals()
        XCTAssertEqual(closed.first { $0.id == id }?.state, "exited")
    }
    @MainActor func testOldBridge404LeavesTasksAndMonitoringWorking() async throws {
        let connection = try Connection(url: TestBridge.legacyURL, token: TestBridge.credential, fingerprint: TestBridge.pin)
        let model = AppModel()
        model.connection = connection
        await model.refresh()
        XCTAssertTrue(model.legacyBridge)
        XCTAssertNil(model.taskError)
        XCTAssertNil(model.error)
        XCTAssertNotNil(model.deviceError)
        XCTAssertTrue(model.canSubmitTask)
    }
}
