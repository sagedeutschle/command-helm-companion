import XCTest
import SwiftTerm

final class VendoredTerminalTests: XCTestCase {
    func testIOSRejectsLocalMediaEvenWithPermissiveHostPolicy() throws {
        let delegate = TerminalProbe()
        let options = TerminalOptions(kittyGraphics: .init(storageLimitBytesPerScreen: 1024, localMediaPolicy: .all))
        let terminal = Terminal(delegate: delegate, options: options)
        let fixture = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(UUID().uuidString)
        let pixels = Data([1, 2, 3])
        try pixels.write(to: fixture)
        defer { try? FileManager.default.removeItem(at: fixture) }
        for (index, medium) in ["f", "t", "s"].enumerated() {
            delegate.sent.removeAll()
            let payload = Data(fixture.path.utf8).base64EncodedString()
            terminal.feed(text: "\u{1b}_Ga=t,t=\(medium),f=24,s=1,v=1,i=\(index + 1);\(payload)\u{1b}\\")
            XCTAssertEqual(String(decoding: delegate.sent.flatMap { $0 }, as: UTF8.self), "\u{1b}_Gi=\(index + 1);EINVAL: unsupported medium\u{1b}\\")
            XCTAssertEqual(try Data(contentsOf: fixture), pixels, "A remote medium must not read/delete an iOS file")
        }
        delegate.sent.removeAll()
        terminal.feed(text: "\u{1b}_Ga=t,t=d,f=24,s=1,v=1,i=4;AQID\u{1b}\\")
        XCTAssertEqual(String(decoding: delegate.sent.flatMap { $0 }, as: UTF8.self), "\u{1b}_Gi=4;OK\u{1b}\\", "Direct byte payload decoding remains intact")
    }
    func testPinnedUpstreamIdentityAndUsedVTBehaviorRemainIntact() {
        XCTAssertEqual(SwiftTermBuildInfo.commit, "a183c15b52858530a17de57dcc72b93eeebc9857")
        XCTAssertEqual(SwiftTermBuildInfo.tag, "v1.99.2")
        XCTAssertEqual(SwiftTermBuildInfo.hasUncommittedChanges, true)
        XCTAssertEqual(SwiftTermBuildInfo.version, "v1.99.2-modified")
        let delegate = TerminalProbe()
        let terminal = Terminal(delegate: delegate, options: TerminalOptions(cols: 80, rows: 24))
        terminal.feed(text: "\u{1b}[2J\u{1b}[HReady\u{1b}[2;3H!")
        XCTAssertEqual(terminal.getCharacter(col: 0, row: 0), "R")
        XCTAssertEqual(terminal.getCharacter(col: 2, row: 1), "!")
        terminal.resize(cols: 91, rows: 27)
        XCTAssertEqual(terminal.getDims().cols, 91)
        XCTAssertEqual(terminal.getDims().rows, 27)
    }
}

private final class TerminalProbe: TerminalDelegate {
    var sent: [[UInt8]] = []
    func showCursor(source: Terminal) {}
    func hideCursor(source: Terminal) {}
    func setTerminalTitle(source: Terminal, title: String) {}
    func setTerminalIconTitle(source: Terminal, title: String) {}
    func windowCommand(source: Terminal, command: Terminal.WindowManipulationCommand) -> [UInt8]? { nil }
    func sizeChanged(source: Terminal) {}
    func bufferActivated(source: Terminal) {}
    func bell(source: Terminal) {}
    func send(source: Terminal, data: ArraySlice<UInt8>) { sent.append(Array(data)) }
}
