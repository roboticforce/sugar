import Foundation

/// Single source of the diagnostics string shown in About/Feedback. It must never
/// contain task titles, descriptions, file paths or code — only environment
/// facts a maintainer needs to triage a report.
enum AppDiagnostics {
    static var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }

    static var build: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }

    @MainActor
    static func summary(for model: AppModel) -> String {
        """
        SugarDash \(version) (\(build))
        macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)
        Dashboard: \(model.serverURLString)
        Connection: \(model.connection.label)
        Server: \(model.serverVersion ?? "unknown")
        Tasks: \(model.tasks.count)
        """
    }
}
