import Foundation
import Security
import CryptoKit

final class PinnedSession: NSObject, URLSessionDelegate, URLSessionTaskDelegate, @unchecked Sendable {
    let connection: Connection
    init(_ connection: Connection) { self.connection = connection }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard let pin = connection.certificateFingerprint else {
            completionHandler(.performDefaultHandling, nil); return
        }
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == connection.url.host,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let certificate = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let data = SecCertificateCopyData(certificate) as Data
        let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard actual == pin else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // Never forward pairing credentials to a redirected origin.
    }
}
enum BridgeAPIError: LocalizedError {
    case upgradeRequired
    var errorDescription: String? { "Update the bridge on your computer to use Devices, Connections and Terminal. Tasks and monitoring remain available." }
}
private struct BridgeFailure: Decodable { let error: String }
struct BridgeClient {
    let connection: Connection
    func request<T: Decodable>(_ path: String, body: Data? = nil) async throws -> T {
        let delegate = PinnedSession(connection)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = path.hasPrefix("v1/terminals") ? 45 : 12
        configuration.timeoutIntervalForResource = path.hasPrefix("v1/terminals") ? 60 : 15
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        guard let url = URL(string: path, relativeTo: connection.url.appendingPathComponent("/")) else { throw HelmError.message("Invalid bridge route.") }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
        #if targetEnvironment(simulator)
        request.setValue("simulator", forHTTPHeaderField: "X-Helm-Client")
        #else
        request.setValue("iphone", forHTTPHeaderField: "X-Helm-Client")
        #endif
        if let body {
            request.httpMethod = "POST"; request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw HelmError.message("The bridge did not respond.") }
        guard response.statusCode == 200 else {
            if response.statusCode == 404 { throw BridgeAPIError.upgradeRequired }
            if response.statusCode == 401 { throw HelmError.message("This pairing key was rejected. Pair this phone again.") }
            if path.hasPrefix("v1/terminals") {
                let detail = (try? JSONDecoder().decode(BridgeFailure.self, from: data))?.error ?? "Check the computer and reconnect."
                throw HelmError.message("Terminal request failed (\(response.statusCode)). \(String(detail.prefix(500))) Input will not be replayed.")
            }
            if response.statusCode == 409 {
                throw HelmError.message(path == "v1/tasks" ? "This task identity was rejected. Refresh task history to inspect the existing task. Your draft and retry ID have been kept." : "That question was already answered or has expired. Refresh the inbox.")
            }
            throw HelmError.message("The bridge returned an error (\(response.statusCode)).")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
    func devices() async throws -> [HelmDevice] { let value: DeviceInventory = try await request("v1/devices"); return value.devices }
    func connections() async throws -> ConnectionsSnapshot { try await request("v1/connections") }
    func setDiscovery(id: String, enabled: Bool) async throws -> ConnectionsSnapshot {
        struct OptIn: Encodable { let id: String; let enabled: Bool }
        return try await request("v1/connections", body: JSONEncoder().encode(OptIn(id: id, enabled: enabled)))
    }
    func terminals() async throws -> [TerminalSession] { let value: TerminalInventory = try await request("v1/terminals"); return value.sessions }
    func startTerminal(_ start: TerminalStart) async throws -> TerminalSession {
        struct Receipt: Decodable { let session: TerminalSession }
        let value: Receipt = try await request("v1/terminals", body: JSONEncoder().encode(start)); return value.session
    }
    func terminalOutput(id: String, after: Int) async throws -> TerminalOutput { try await request("v1/terminals/\(id)/output?after=\(after)") }
    func terminalInput(id: String, input: TerminalInput) async throws -> TerminalInputReceipt { try await request("v1/terminals/\(id)/input", body: JSONEncoder().encode(input)) }
    func resizeTerminal(id: String, cols: Int, rows: Int) async throws {
        struct Receipt: Decodable { let ok: Bool }
        let _: Receipt = try await request("v1/terminals/\(id)/resize", body: JSONEncoder().encode(["cols": max(2, min(300, cols)), "rows": max(2, min(200, rows))]))
    }
    func closeTerminal(id: String) async throws {
        struct Receipt: Decodable { let ok: Bool }
        let _: Receipt = try await request("v1/terminals/\(id)/close", body: Data("{}".utf8))
    }
    func snapshot() async throws -> Snapshot { try await request("v1/snapshot") }
    func tasks() async throws -> TaskSnapshot { try await request("v1/tasks") }
    func submitTask(_ submission: TaskSubmission) async throws -> HelmTask {
        struct Receipt: Decodable { let task: HelmTask }
        let body = try JSONEncoder().encode(submission)
        let receipt: Receipt = try await request("v1/tasks", body: body)
        return receipt.task
    }
    func command(sessionId: String, kind: String, text: String, id: String) async throws {
        struct Receipt: Decodable { let state: String }
        let body = try JSONEncoder().encode(["kind": kind, "text": text, "commandId": id])
        let _: Receipt = try await request("v1/sessions/\(sessionId)/commands", body: body)
    }
    func reply(questionId: String, answer: String, replyId: String) async throws {
        struct Receipt: Decodable { let state: String }
        let body = try JSONEncoder().encode(["answer": answer, "replyId": replyId])
        let _: Receipt = try await request("v1/questions/\(questionId)/reply", body: body)
    }
}

enum PairingVault {
    static let service = "com.sagedeutschle.commandhelm.remote.pairing"
    static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "bridge"] }
    static func load() -> Connection? {
        var q = query; q[kSecReturnData as String] = true
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(Connection.self, from: data)
    }
    static func save(_ connection: Connection) throws {
        let data = try JSONEncoder().encode(connection)
        let updates = [kSecValueData as String: data]
        var status = SecItemUpdate(query as CFDictionary, updates as CFDictionary)
        if status == errSecItemNotFound {
            var q = query
            q[kSecValueData as String] = data
            q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            status = SecItemAdd(q as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw HelmError.message("Could not save this pairing securely.") }
    }
    static func clear() { SecItemDelete(query as CFDictionary) }
}
