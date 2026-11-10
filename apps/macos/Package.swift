// swift-tools-version: 6.0
//
// SugarDash — native macOS client for the Sugar local dashboard.
// Contract: docs/local-dashboard.md (frozen, contract v1).
//
// Deliberately dependency-free: the app talks to the dashboard over plain
// HTTP/SSE via Foundation, so builds never need a network connection.

import PackageDescription

let package = Package(
    name: "SugarDash",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "SugarDash", targets: ["SugarDash"])
    ],
    targets: [
        .executableTarget(
            name: "SugarDash",
            path: "Sources/SugarDash",
            swiftSettings: [
                // Swift 6 strict concurrency is not worth the friction for this
                // app; language mode v5 keeps the build warning-free.
                .swiftLanguageMode(.v5)
            ]
        ),
        .testTarget(
            name: "SugarDashTests",
            dependencies: ["SugarDash"],
            path: "Tests/SugarDashTests",
            swiftSettings: [
                .swiftLanguageMode(.v5)
            ]
        )
    ]
)
