import AppKit
import SwiftUI

/// Explicit, user-initiated feedback. Nothing is collected automatically: the
/// user reviews the text and picks a destination (public GitHub issue or a
/// private email). The only thing recorded locally is *that* feedback was sent —
/// category, destination and length — in the dashboard's activity spine.
struct FeedbackView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismissWindow) private var dismissWindow

    enum Category: String, CaseIterable, Identifiable {
        case bug = "Bug"
        case feature = "Feature request"
        case other = "Other"

        var id: String { rawValue }
    }

    enum Destination: String {
        case github
        case email
        case clipboard
    }

    @State private var category: Category = .bug
    @State private var message = ""
    @State private var includeDiagnostics = true
    @State private var copied = false
    @State private var note: String?

    /// Keep prefilled URLs within what browsers/mail clients handle comfortably.
    private static let maxMessageInURL = 1_500

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Send feedback")
                .font(.title2.weight(.semibold))
            Text(
                "Sugar is early-release. Bug reports and ideas are genuinely useful. "
                    + "Nothing is sent automatically — you review the text and choose how to send it."
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            Picker("Type", selection: $category) {
                ForEach(Category.allCases) { category in
                    Text(category.rawValue).tag(category)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            ZStack(alignment: .topLeading) {
                TextEditor(text: $message)
                    .font(.body)
                    .frame(height: 130)
                    .padding(4)
                    .background(Color(nsColor: .textBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                    )
                if message.isEmpty {
                    Text("What happened, or what would you like to see?")
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 12)
                        .allowsHitTesting(false)
                }
            }

            Toggle(
                "Include diagnostics (version, macOS, dashboard URL, connection state, task counts)",
                isOn: $includeDiagnostics
            )
            .font(.callout)

            Text("Diagnostics never contain task titles, code, file paths or credentials.")
                .font(.caption2)
                .foregroundStyle(.tertiary)

            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            HStack {
                Button("Open GitHub Issue") { send(to: .github) }
                    .help("Opens a public issue on github.com/roboticforce/sugar")
                Button("Send via Email") { send(to: .email) }
                    .help("Opens your mail client, addressed to contact@roboticforce.io")
                Spacer()
                Button(copied ? "Copied" : "Copy") { copyText() }
                Button("Cancel") { dismissWindow(id: WindowID.feedback) }
                    .keyboardShortcut(.cancelAction)
            }
            .disabled(message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(24)
        .frame(width: 560)
    }

    // MARK: - Actions

    private func composedText() -> String {
        var text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        if includeDiagnostics {
            text += "\n\n---\nDiagnostics\n" + AppDiagnostics.summary(for: model)
        }
        return text
    }

    private func send(to destination: Destination) {
        let text = composedText()
        guard let url = destination == .github ? githubURL(for: text) : emailURL(for: text)
        else {
            note = "Could not build the link; the text has been copied instead."
            copyRaw(text)
            return
        }
        note = nil
        let length = message.trimmingCharacters(in: .whitespacesAndNewlines).count
        AsyncTask { await model.recordFeedbackSent(
            category: category.rawValue,
            destination: destination.rawValue,
            messageLength: length
        ) }
        NSWorkspace.shared.open(url)
        dismissWindow(id: WindowID.feedback)
    }

    private func copyText() {
        copyRaw(composedText())
        let length = message.trimmingCharacters(in: .whitespacesAndNewlines).count
        AsyncTask { await model.recordFeedbackSent(
            category: category.rawValue,
            destination: Destination.clipboard.rawValue,
            messageLength: length
        ) }
    }

    private func copyRaw(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { copied = false }
    }

    /// URLs have practical length limits; if the message is long we put the first
    /// part in the URL and copy the full text so nothing is lost.
    private func urlSafe(_ text: String) -> String {
        guard text.count > Self.maxMessageInURL else { return text }
        copyRaw(text)
        note = "Message is long: the full text was copied to the clipboard, a shortened version opens."
        return String(text.prefix(Self.maxMessageInURL)) + "… (full text copied to clipboard)"
    }

    private func githubURL(for text: String) -> URL? {
        var components = URLComponents()
        components.scheme = "https"
        components.host = "github.com"
        components.path = "/roboticforce/sugar/issues/new"
        let firstLine = message
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(separator: "\n", maxSplits: 1)
            .first
            .map(String.init) ?? ""
        let title = "[\(category.rawValue)] " + String(firstLine.prefix(80))
        components.queryItems = [
            URLQueryItem(name: "title", value: title),
            URLQueryItem(name: "labels", value: "feedback"),
            URLQueryItem(name: "body", value: urlSafe(text)),
        ]
        return components.url
    }

    private func emailURL(for text: String) -> URL? {
        var components = URLComponents()
        components.scheme = "mailto"
        components.path = "contact@roboticforce.io"
        components.queryItems = [
            URLQueryItem(name: "subject", value: "[SugarDash feedback] \(category.rawValue)"),
            URLQueryItem(name: "body", value: urlSafe(text)),
        ]
        return components.url
    }
}
