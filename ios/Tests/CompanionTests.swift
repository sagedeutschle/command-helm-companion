import XCTest
@testable import CommandHelmRemote

final class CompanionTests: XCTestCase {
    func testPasteAndReturnKeepSeparateInputBoundaries() throws {
        var queue = TerminalInputQueue()
        try queue.append(Data("A pasted prompt".utf8))
        try queue.append(Data([13]))
        XCTAssertEqual(queue.take(), Data("A pasted prompt".utf8))
        XCTAssertEqual(queue.take(), Data([13]))
        XCTAssertTrue(queue.isEmpty)
        try queue.append(Data(repeating: 65, count: 16384))
        XCTAssertThrowsError(try queue.append(Data([13])))
        queue.removeAll()
        XCTAssertEqual(queue.byteCount, 0)
    }
    func testRawTerminalBytesAndCursorDoNotCorruptSplitUTF8() throws {
        var replay = TerminalReplay()
        let first = try replay.consume(TerminalOutput(data: Data([0xe2]).base64EncodedString(), cursor: 1, baseCursor: 0, state: "running", reset: false))
        let second = try replay.consume(TerminalOutput(data: Data([0x82, 0xac, 27, 91, 65]).base64EncodedString(), cursor: 6, baseCursor: 0, state: "running", reset: false))
        XCTAssertEqual(first + second, Data([0xe2, 0x82, 0xac, 27, 91, 65]))
        XCTAssertEqual(replay.cursor, 6)
        XCTAssertThrowsError(try replay.consume(TerminalOutput(data: "!bad", cursor: 6, baseCursor: 0, state: "running", reset: false)))
        XCTAssertThrowsError(try replay.consume(TerminalOutput(data: "YQ==", cursor: 9, baseCursor: 0, state: "running", reset: false)))
        XCTAssertEqual(try replay.consume(TerminalOutput(data: "YQ==", cursor: 20, baseCursor: 19, state: "running", reset: true)), Data("a".utf8))
        XCTAssertEqual(replay.cursor, 20)
        XCTAssertThrowsError(try replay.consume(TerminalOutput(data: "YQ==", cursor: 23, baseCursor: 21, state: "running", reset: true)))
        XCTAssertThrowsError(try replay.consume(TerminalOutput(data: "YQ==", cursor: 21, baseCursor: 21, state: "running", reset: false)))
    }
    func testAmbiguousTerminalInputIsNotReplayedAndAcknowledgementMustMatch() throws {
        var gate = TerminalInputGate()
        let value = try gate.begin(Data("pwd\r".utf8))
        XCTAssertEqual(value.data, Data("pwd\r".utf8).base64EncodedString())
        XCTAssertEqual(UUID(uuidString: value.id)?.uuidString.lowercased(), value.id)
        XCTAssertThrowsError(try gate.begin(Data("other".utf8)))
        gate.failed()
        XCTAssertThrowsError(try gate.begin(Data("pwd\r".utf8)))
        XCTAssertEqual(gate.pending, value)
        gate.resumeAfterInspection()
        let manual = try gate.begin(Data("pwd\r".utf8))
        XCTAssertNotEqual(value.id, manual.id)
        XCTAssertThrowsError(try gate.acknowledge(.init(accepted: true, id: value.id)))
        XCTAssertTrue(gate.uncertain)
        gate.resumeAfterInspection()
        let fresh = try gate.begin(Data([3]))
        try gate.acknowledge(.init(accepted: true, id: fresh.id))
        XCTAssertNil(gate.pending)
        XCTAssertFalse(gate.uncertain)
        XCTAssertThrowsError(try gate.begin(Data(repeating: 0, count: 16385)))
    }
    @MainActor func testTaskPickerSeparatesOfflineWorkerFromTerminalOnlyDevice() {
        let model = AppModel()
        model.devices = [
            .init(id: "offline", name: "Work computer", platform: "linux", transport: "ssh", status: "offline", lastSeen: 1, capabilities: .init(tasks: true, terminal: true, codex: true)),
            .init(id: "terminal-only", name: "Shell computer", platform: "macos", transport: "local", status: "online", lastSeen: 0, capabilities: .init(tasks: false, terminal: true, codex: false))
        ]
        XCTAssertEqual(model.taskTargets.map(\.rawValue), ["offline"])
        XCTAssertEqual(model.deviceName(.init(rawValue: "offline")), "Work computer")
        XCTAssertEqual(model.deviceName(.init(rawValue: "retired")), "retired")
        model.legacyBridge = true
        model.taskSnapshot = .init(tasks: [], workers: [.init(target: .init(rawValue: "old-worker"), seen: 1, state: "idle", taskId: "")])
        XCTAssertEqual(model.taskTargets.map(\.rawValue), ["old-worker"])
    }
    @MainActor func testDemoRefreshAndTerminalStartNeverUsePairedNetwork() async throws {
        var sent = false
        let model = AppModel(taskSender: { _, _ in sent = true; throw URLError(.badURL) })
        model.connection = try Connection(url: "https://unreachable.invalid", token: "fixture")
        model.explore()
        await model.refresh(); await model.refreshDevices(); await model.refreshTerminals(); await model.setDiscovery(id: "netbird", enabled: true)
        do { _ = try await model.startTerminal(device: HelmDevice.demo[0], kind: "codex", id: UUID().uuidString.lowercased()); XCTFail("Demo cannot start real sessions") } catch {}
        XCTAssertFalse(sent)
        XCTAssertEqual(model.devices, HelmDevice.demo)
        XCTAssertEqual(model.services, .demo)
        XCTAssertEqual(model.sessions, [.demo])
    }
    @MainActor func testPendingInputWarningSurvivesRuntimeRecreation() throws {
        let connection = try Connection(url: "https://pending-input-fixture.invalid", token: "fixture")
        let key = AIConsentVault.key(connection, device: TerminalSession.demo.deviceId) + ".terminal." + TerminalSession.demo.id
        UserDefaults.standard.set(true, forKey: key)
        UserDefaults.standard.set("00000000-0000-4000-8000-000000000001", forKey: key + ".inputID")
        defer { AIConsentVault.clear(connection) }
        let runtime = TerminalRuntime(session: .demo, connection: connection, demo: false)
        XCTAssertTrue(runtime.uncertain)
        XCTAssertTrue(runtime.inputStatus.contains("uncertain"))
        runtime.send(Data("must not be sent".utf8))
        XCTAssertTrue(runtime.uncertain)
        runtime.resumeAfterInspection()
        XCTAssertFalse(runtime.uncertain)
        XCTAssertNil(UserDefaults.standard.object(forKey: key))
        XCTAssertNil(UserDefaults.standard.object(forKey: key + ".inputID"))
        runtime.status = "Terminal reconnection unavailable"
        runtime.stop(keepStatus: true)
        XCTAssertEqual(runtime.status, "Terminal reconnection unavailable")
        runtime.stop()
        XCTAssertEqual(runtime.status, "Detached · reconnect to check session")
    }
    @MainActor func testContentDirectionsAndAnswersRequireConsentBeforeNetwork() async throws {
        let model = AppModel()
        model.connection = try Connection(url: "https://consent-gate.invalid", token: "fixture")
        model.lastSuccess = Date()
        let now = Date().timeIntervalSince1970
        let session = AgentSession(controllable: true, id: "session", provider: "claude", state: "working", seen: now)
        let project = Project(id: "project", name: "Project", sessions: [session], machineId: "computer")
        let question = Question(id: "q", projectId: "project", provider: "claude", text: "Choose?", options: [], state: "pending", createdAt: now, expiresAt: now + 300, answer: nil)
        model.snapshot = Snapshot(generatedAt: now, projects: [project], updates: [], questions: [question])
        AIConsentVault.clear(model.connection!)
        XCTAssertEqual(model.activityDeviceID("project"), "computer")
        do { try await model.answer(question, text: "Yes", replyId: "reply"); XCTFail("Consent required") }
        catch { XCTAssertTrue(error.localizedDescription.contains("AI sharing")) }
        do { try await model.direct(session, deviceID: "computer", kind: "guide", text: "Prioritize tests", id: "direction"); XCTFail("Consent required") }
        catch { XCTAssertTrue(error.localizedDescription.contains("AI sharing")) }
    }
    @MainActor func testAISharingRequiresExplicitChoiceAndUnpairClearsItAndDraft() async throws {
        let model = AppModel()
        let connection = try Connection(url: "https://consent-fixture.invalid", token: "disposable-fixture")
        model.connection = connection
        AIConsentVault.clear(connection)
        XCTAssertFalse(model.hasAIConsent("computer"))
        model.allowAI("computer")
        XCTAssertTrue(model.hasAIConsent("computer"))
        XCTAssertFalse(model.hasAIConsent("another-computer"))
        let other = try Connection(url: "https://other-fixture.invalid", token: "disposable-fixture")
        XCTAssertFalse(AIConsentVault.contains(other, device: "computer"))
        model.taskDraft.prompt = "Private local draft"
        model.disconnect()
        XCTAssertFalse(AIConsentVault.contains(connection, device: "computer"))
        XCTAssertTrue(model.taskDraft.prompt.isEmpty)
        XCTAssertNil(model.connection)
    }
}
