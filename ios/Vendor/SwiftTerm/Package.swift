// swift-tools-version:6.2
// Command Helm downstream iOS library packaging of SwiftTerm.
// Upstream: https://github.com/migueldeicaza/SwiftTerm
// Revision: a183c15b52858530a17de57dcc72b93eeebc9857 (MIT).
// Original Package.swift, generator and generated inputs are retained in Upstream/.
import PackageDescription

let package = Package(
    name: "SwiftTerm",
    platforms: [.iOS(.v14)],
    products: [.library(name: "SwiftTerm", targets: ["SwiftTerm"])],
    targets: [.target(name: "SwiftTerm", path: "Sources/SwiftTerm")],
    swiftLanguageModes: [.v6]
)
