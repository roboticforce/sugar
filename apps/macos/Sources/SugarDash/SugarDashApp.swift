import AppKit
import SwiftUI

@main
struct SugarDashApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("Sugar Dashboard", id: WindowID.dashboard) {
            ContentView()
                .environment(model)
                .frame(minWidth: 940, minHeight: 560)
        }
        .defaultSize(width: 1180, height: 760)
        .windowResizability(.contentMinSize)
        .commands { SugarDashCommands() }

        Window("About SugarDash", id: WindowID.about) {
            AboutView()
                .environment(model)
        }
        .windowResizability(.contentSize)
        .defaultPosition(.center)

        MenuBarExtra {
            MenuBarContentView(model: model)
        } label: {
            Label("\(model.pendingCount)", systemImage: "tray.full")
        }
        .menuBarExtraStyle(.menu)
    }
}

enum WindowID {
    static let dashboard = "dashboard"
    static let about = "about"
}

/// Extra app-surface menus: a real About item (macOS convention) and a Help menu
/// that points at the docs, issue tracker, privacy/terms pages and support email.
struct SugarDashCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    private static let docs = URL(string: "https://sugar.roboticforce.io/docs")!
    private static let issues = URL(string: "https://github.com/roboticforce/sugar/issues")!
    private static let website = URL(string: "https://sugar.roboticforce.io")!
    private static let privacy = URL(string: "https://sugar.roboticforce.io/privacy")!
    private static let terms = URL(string: "https://sugar.roboticforce.io/terms")!
    private static let contact = URL(string: "mailto:contact@roboticforce.io")!

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button("About SugarDash") {
                openWindow(id: WindowID.about)
            }
        }
        CommandGroup(replacing: .help) {
            Link("SugarDash Documentation", destination: Self.docs)
            Link("Report an Issue", destination: Self.issues)
            Divider()
            Link("Sugar Website", destination: Self.website)
            Link("Privacy Policy", destination: Self.privacy)
            Link("Terms of Service", destination: Self.terms)
            Divider()
            Link("Contact RoboticForce", destination: Self.contact)
        }
    }
}

/// Menu bar extra: pending count in the menu bar, quick summary and
/// "Open Dashboard" / "Reconnect" / "Quit" actions in the menu.
struct MenuBarContentView: View {
    @Environment(\.openWindow) private var openWindow

    let model: AppModel

    var body: some View {
        Text("\(model.pendingCount) pending")
        Text("\(model.activeCount) active")
        Text("\(model.holdCount) on hold")
        Divider()
        Button("About SugarDash") {
            NSApp.activate()
            openWindow(id: WindowID.about)
        }
        Divider()
        Button("Open Dashboard") { openDashboard() }
        Button("Reconnect") { model.reconnect() }
            .disabled(model.connection == .connected)
        Divider()
        Button("Quit SugarDash") { NSApplication.shared.terminate(nil) }
    }

    private func openDashboard() {
        NSApp.activate()
        if focusExistingWindow() {
            return
        }
        openWindow(id: WindowID.dashboard)
    }

    /// `openWindow(id:)` is fine on a cold start, but reusing the existing
    /// window avoids opening duplicates when the window is merely hidden.
    private func focusExistingWindow() -> Bool {
        if let window = NSApp.windows.first(where: { $0.canBecomeMain && !$0.isMiniaturized }) {
            window.makeKeyAndOrderFront(nil)
            return true
        }
        if let window = NSApp.windows.first(where: { $0.canBecomeMain }) {
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
            return true
        }
        return false
    }
}
