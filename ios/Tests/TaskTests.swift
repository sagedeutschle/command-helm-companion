import XCTest
@testable import CommandHelmRemote

final class TaskTests: XCTestCase {
    func testUnknownHistoricalDeviceIDRemainsDecodable() throws {
        let data = Data(#"{"id":"history","target":"retired-laptop","prompt":"Report","workspace":"","state":"completed","createdAt":0,"updatedAt":0,"deliveryPath":"","report":"","error":""}"#.utf8)
        let task = try JSONDecoder().decode(HelmTask.self, from: data)
        XCTAssertEqual(task.target.rawValue, "retired-laptop")
    }

    func testDecodesExactQueueContractWithoutInventingWorkerAvailability() throws {
        let data = Data(#"{"tasks":[{"id":"00000000-0000-4000-8000-000000000001","target":"spellbook","prompt":"Write a report","workspace":"","state":"queued","createdAt":1700000000,"updatedAt":1700000001,"deliveryPath":"","report":"","error":""}],"workers":[{"target":"spelltower","seen":1700000000,"state":"idle","taskId":""}]}"#.utf8)
        let value = try JSONDecoder().decode(TaskSnapshot.self, from: data)
        XCTAssertEqual(value.tasks.first?.target, .spellbook)
        XCTAssertEqual(value.tasks.first?.state, "queued")
        XCTAssertEqual(value.tasks.first?.deliveryPath, "")
        XCTAssertFalse(value.workers[0].isRecent(at: Date(timeIntervalSince1970: 1700000200)))
        XCTAssertTrue(value.workers[0].isRecent(at: Date(timeIntervalSince1970: 1700000010)))
    }
    func testRetryIdentitySurvivesEncodingAndEveryPayloadEditChangesIdentity() throws {
        var draft = TaskDraft()
        draft.prompt = "Create a checklist"
        let first = draft.submission
        XCTAssertNotNil(UUID(uuidString: first.id))
        XCTAssertEqual(first.id, UUID(uuidString: first.id)?.uuidString.lowercased())
        XCTAssertEqual(draft.submission, first)
        draft = try JSONDecoder().decode(TaskDraft.self, from: JSONEncoder().encode(draft))
        XCTAssertEqual(draft.submission, first, "Restoring a failed draft must preserve the idempotency key")
        draft.prompt = "Create a checklist and report"
        XCTAssertNotEqual(draft.id, first.id)
        let promptID = draft.id
        draft.target = .spelltower
        XCTAssertNotEqual(draft.id, promptID)
        let targetID = draft.id
        draft.workspace = "/home/sage/project"
        XCTAssertNotEqual(draft.id, targetID)
        let final = draft.submission
        draft.workspace = "/home/sage/project"
        XCTAssertEqual(draft.submission, final, "An unchanged retry must keep the ID")
    }
    func testLegacyPersistedUppercaseIdentityIsCanonicalizedWithoutChangingUUID() throws {
        var draft = TaskDraft()
        draft.prompt = "Restore old draft"
        let original = draft.id
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as? [String: Any])
        object["id"] = original.uppercased()
        draft = try JSONDecoder().decode(TaskDraft.self, from: JSONSerialization.data(withJSONObject: object))
        draft.normalizeIdentity()
        XCTAssertEqual(draft.id, original)
    }
    func testSubmissionUsesExactWireKeysAndAbsoluteOptionalWorkspace() throws {
        var draft = TaskDraft()
        XCTAssertNotNil(draft.validationMessage)
        draft.prompt = "  Write REPORT.md  "
        XCTAssertNil(draft.validationMessage)
        draft.workspace = "~/project"
        XCTAssertNotNil(draft.validationMessage)
        draft.workspace = "/Users/sage/project "
        XCTAssertNil(draft.validationMessage)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(draft.submission)) as? [String: String])
        XCTAssertEqual(Set(object.keys), ["id", "target", "prompt", "workspace"])
        XCTAssertEqual(object["target"], "spellbook")
        XCTAssertEqual(object["prompt"], "  Write REPORT.md  ")
        XCTAssertEqual(object["workspace"], "/Users/sage/project ")
    }
    func testValidationMatchesServerUnicodeScalarAndNullLimits() {
        var draft = TaskDraft()
        draft.prompt = String(repeating: "👩‍💻", count: 3000)
        XCTAssertLessThan(draft.prompt.count, 8000)
        XCTAssertNotNil(draft.validationMessage)
        draft.prompt = " " + String(repeating: "a", count: 8000)
        XCTAssertNotNil(draft.validationMessage, "The full raw prompt must fit the server bound")
        draft.prompt = "Create a report"
        draft.workspace = "/tmp/" + String(repeating: "a", count: 4096)
        XCTAssertNotNil(draft.validationMessage)
        draft.workspace = "/" + String(repeating: "a", count: 4095) + " "
        XCTAssertNotNil(draft.validationMessage, "Trailing whitespace still counts toward the raw path bound")
        draft.workspace = " /tmp/project"
        XCTAssertNotNil(draft.validationMessage, "Leading whitespace must not be trimmed into a different valid path")
        draft.workspace = ""
        draft.prompt = "Report\0"
        XCTAssertNotNil(draft.validationMessage)
    }
    @MainActor func testDemoCannotSubmitEvenWithFreshTaskData() async {
        let model = AppModel()
        model.explore()
        model.taskDraft.prompt = "Do not send this"
        XCTAssertFalse(model.canSubmitTask)
        let before = model.taskSnapshot
        do { try await model.submitTask(); XCTFail("Demo must never submit") } catch {}
        XCTAssertEqual(model.taskSnapshot, before)
    }
    @MainActor func testFailedTransportRetriesSameTaskAndEditedPayloadGetsNewIdentity() async throws {
        var sent: [TaskSubmission] = []
        let model = AppModel(taskSender: { _, submission in
            sent.append(submission)
            throw URLError(.networkConnectionLost)
        })
        model.connection = try Connection(url: "http://localhost:8746", token: "fixture")
        model.taskLastSuccess = Date()
        model.taskDraft = TaskDraft()
        model.taskDraft.prompt = "Prepare a report"
        model.legacyBridge = true
        model.allowAI(model.taskDraft.target.rawValue)
        defer { if let connection = model.connection { AIConsentVault.clear(connection) } }
        let original = model.taskDraft.id
        for _ in 0..<2 {
            do { try await model.submitTask(); XCTFail("Fixture must fail") } catch {}
            XCTAssertFalse(model.taskSending)
        }
        XCTAssertEqual(sent.map(\.id), [original, original])
        XCTAssertEqual(sent[0], sent[1])
        model.taskDraft.prompt = "Prepare a different report"
        do { try await model.submitTask(); XCTFail("Fixture must fail") } catch {}
        XCTAssertNotEqual(sent[2].id, original)
    }
    @MainActor func testTaskErrorsAndFreshnessDoNotInvalidateMonitoringState() throws {
        let model = AppModel()
        model.connection = try Connection(url: "http://localhost:8746", token: "fixture")
        model.lastSuccess = Date()
        model.error = nil
        model.taskLastSuccess = Date()
        XCTAssertTrue(model.canSubmitTask)
        model.taskError = "Task API unavailable"
        XCTAssertFalse(model.canSubmitTask)
        XCTAssertNil(model.error)
        XCTAssertFalse(model.stale)
        model.taskError = nil
        model.taskLastSuccess = Date(timeIntervalSinceNow: -60)
        XCTAssertFalse(model.canSubmitTask)
    }
}
