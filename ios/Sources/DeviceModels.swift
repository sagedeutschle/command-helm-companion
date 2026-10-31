import Foundation
import CryptoKit

struct DeviceCapabilities: Codable, Equatable {
    let tasks: Bool
    let terminal: Bool
    let codex: Bool
}
struct HelmDevice: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let platform: String
    let transport: String
    let status: String
    let lastSeen: Double
    let capabilities: DeviceCapabilities
    var target: TaskTarget { TaskTarget(rawValue: id) }
    static let demo = [
        HelmDevice(id: "computer", name: "My computer", platform: "macos", transport: "local", status: "online", lastSeen: 0, capabilities: .init(tasks: true, terminal: true, codex: true)),
        HelmDevice(id: "workstation", name: "Workstation", platform: "linux", transport: "ssh", status: "offline", lastSeen: 0, capabilities: .init(tasks: true, terminal: true, codex: true))
    ]
}
struct DeviceInventory: Decodable { let devices: [HelmDevice] }
struct ServiceConnection: Codable, Identifiable, Equatable {
    let id: String
    let enabled: Bool
    let status: String
    let account: String
    let message: String
}
struct NetworkPeer: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let address: String
    let status: String
    let os: String
}
struct ConnectionsSnapshot: Codable, Equatable {
    let connections: [ServiceConnection]
    let peers: [NetworkPeer]
    static let empty = ConnectionsSnapshot(connections: [], peers: [])
    static let demo = ConnectionsSnapshot(connections: [ServiceConnection(id: "netbird", enabled: false, status: "disabled", account: "", message: "Sample · discovery is off"), ServiceConnection(id: "github", enabled: false, status: "disabled", account: "", message: "Sample · discovery is off")], peers: [])
}

enum PublicHelp {
    static let base = "https://github.com/sagedeutschle/command-helm-companion/blob/main/docs/"
    static let setup = URL(string: base + "getting-started.md")!
    static let privacy = URL(string: base + "privacy.md")!
    static let support = URL(string: base + "support.md")!
}
/// Only a hash of bridge identity is saved; credentials never enter preferences.
enum AIConsentVault {
    static func key(_ connection: Connection, device: String) -> String {
        let identity = connection.url.absoluteString + "|" + (connection.certificateFingerprint ?? "system-trust") + "|" + connection.token
        let hash = SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
        return "commandhelm.aiConsent.v1." + hash + "." + device
    }
    static func contains(_ connection: Connection, device: String) -> Bool { UserDefaults.standard.bool(forKey: key(connection, device: device)) }
    static func grant(_ connection: Connection, device: String) { UserDefaults.standard.set(true, forKey: key(connection, device: device)) }
    static func clear(_ connection: Connection) {
        let prefix = key(connection, device: "")
        for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix(prefix) { UserDefaults.standard.removeObject(forKey: key) }
    }
}
