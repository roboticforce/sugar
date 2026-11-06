import AppKit
import SwiftUI

/// The About card. macOS users expect the app menu → About item; this one carries
/// the essentials a distributed app needs: version, what it is, where the project
/// lives, how to reach RoboticForce, and the legal/privacy links.
struct AboutView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismissWindow) private var dismissWindow
    @Environment(\.openWindow) private var openWindow

    @State private var copiedDiagnostics = false

    private static let website = URL(string: "https://sugar.roboticforce.io")!
    private static let documentation = URL(string: "https://sugar.roboticforce.io/docs")!
    private static let repository = URL(string: "https://github.com/roboticforce/sugar")!
    private static let issues = URL(string: "https://github.com/roboticforce/sugar/issues")!
    private static let privacy = URL(string: "https://sugar.roboticforce.io/privacy")!
    private static let terms = URL(string: "https://sugar.roboticforce.io/terms")!
    private static let licensing = URL(string: "https://sugar.roboticforce.io/licensing")!
    private static let contact = URL(string: "mailto:contact@roboticforce.io")!

    private var version: String { AppDiagnostics.version }

    private var build: String { AppDiagnostics.build }

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "tray.full.fill")
                .font(.system(size: 44, weight: .medium))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)

            VStack(spacing: 2) {
                Text("SugarDash")
                    .font(.title2.weight(.semibold))
                Text("Mission control for your agent fleet")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Text("Version \(version) (\(build))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
            }

            Divider()

            VStack(alignment: .leading, spacing: 7) {
                Link(destination: Self.website) {
                    Label("sugar.roboticforce.io", systemImage: "globe")
                }
                Link(destination: Self.documentation) {
                    Label("Documentation", systemImage: "book")
                }
                Link(destination: Self.repository) {
                    Label("Source on GitHub", systemImage: "chevron.left.forwardslash.chevron.right")
                }
                Link(destination: Self.issues) {
                    Label("Report an issue", systemImage: "exclamationmark.bubble")
                }
            }
            .font(.callout)
            .frame(maxWidth: .infinity, alignment: .leading)

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                Text("Privacy")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(
                    "SugarDash talks only to your local Sugar dashboard on this Mac. "
                        + "No analytics, no telemetry, no cloud; your tasks never leave the machine."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(spacing: 4) {
                Text("© 2026 RoboticForce Inc. · Licensed under AGPL-3.0")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                HStack(spacing: 12) {
                    Link("Privacy", destination: Self.privacy)
                    Link("Terms", destination: Self.terms)
                    Link("Licensing", destination: Self.licensing)
                    Link("Contact", destination: Self.contact)
                }
                .font(.caption2)
            }

            HStack {
                Button("Send feedback") {
                    dismissWindow(id: WindowID.about)
                    openWindow(id: WindowID.feedback)
                }
                .help("Report a bug or suggest a feature")
                Button(copiedDiagnostics ? "Copied" : "Copy diagnostics") {
                    copyDiagnostics()
                }
                .help("Version, macOS, dashboard URL and connection state")
                Spacer()
                Button("Close") {
                    dismissWindow(id: WindowID.about)
                }
                .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 2)
        }
        .padding(24)
        .frame(width: 440)
    }

    private func copyDiagnostics() {
        let text = AppDiagnostics.summary(for: model)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copiedDiagnostics = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
            copiedDiagnostics = false
        }
    }
}
