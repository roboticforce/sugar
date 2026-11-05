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
