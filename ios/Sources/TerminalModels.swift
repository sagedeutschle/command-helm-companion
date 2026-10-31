import Foundation

struct TerminalSession: Codable, Identifiable, Equatable {
    let id: String
    let deviceId: String
    let kind: String
    let state: String
    let createdAt: Double
    let title: String
    static let demo = TerminalSession(id: "sample-shell", deviceId: "computer", kind: "shell", state: "running", createdAt: 0, title: "My computer · Sample shell")
}
struct TerminalInventory: Decodable { let sessions: [TerminalSession] }
struct TerminalOutput: Codable {
    let data: String
    let cursor: Int
    let baseCursor: Int
    let state: String
    let reset: Bool
}
struct TerminalStart: Encodable {
    let id: String
    let deviceId: String
    let kind: String
    let cols: Int
    let rows: Int
}
struct TerminalInput: Codable, Equatable {
    let id: String
    let data: String
    init(bytes: Data) { id = UUID().uuidString.lowercased(); data = bytes.base64EncodedString() }
}
struct TerminalInputReceipt: Decodable { let accepted: Bool; let id: String }
/// Preserve raw UTF-8 and escape-sequence boundaries; never decode chunks into Strings.
struct TerminalReplay {
    private(set) var cursor = 0
    mutating func consume(_ output: TerminalOutput) throws -> Data {
        guard let bytes = Data(base64Encoded: output.data), bytes.count <= 65536,
              output.cursor >= 0, output.baseCursor >= 0, output.baseCursor <= output.cursor,
              output.cursor >= bytes.count,
              output.reset ? output.cursor == output.baseCursor + bytes.count : (output.baseCursor <= cursor && output.cursor == cursor + bytes.count) else {
            throw HelmError.message("Terminal replay was incomplete. Reconnect to rebuild the screen.")
        }
        cursor = output.cursor
        return bytes
    }
}
/// Fail closed after any ambiguous request. No queued key is automatically replayed.
struct TerminalInputGate {
    private(set) var pending: TerminalInput?
    private(set) var uncertain = false
    mutating func begin(_ bytes: Data) throws -> TerminalInput {
        guard pending == nil, !uncertain, !bytes.isEmpty, bytes.count <= 16384 else {
            throw HelmError.message("Input paused. Inspect the terminal before sending more input.")
        }
        let value = TerminalInput(bytes: bytes); pending = value; return value
    }
    mutating func acknowledge(_ receipt: TerminalInputReceipt) throws {
        guard receipt.accepted, receipt.id == pending?.id else { uncertain = true; throw HelmError.message("Input acknowledgement did not match. Inspect the terminal.") }
        pending = nil
    }
    mutating func failed() { uncertain = true }
    mutating func resumeAfterInspection() { pending = nil; uncertain = false }
}
/// Keep renderer event boundaries: pasting text and pressing Return are separate
/// deliveries, even while an earlier input acknowledgement is in flight.
struct TerminalInputQueue {
    private var chunks: [Data] = []
    private(set) var byteCount = 0
    var isEmpty: Bool { chunks.isEmpty }
    mutating func append(_ bytes: Data) throws {
        guard !bytes.isEmpty, byteCount + bytes.count <= 16384 else { throw HelmError.message("Unsent input exceeded the limit. Inspect the terminal before continuing.") }
        chunks.append(bytes); byteCount += bytes.count
    }
    mutating func take() -> Data? {
        guard !chunks.isEmpty else { return nil }
        let bytes = chunks.removeFirst(); byteCount -= bytes.count; return bytes
    }
    mutating func removeAll() { chunks.removeAll(); byteCount = 0 }
}
