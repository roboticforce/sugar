import Foundation
import SwiftUI

// MARK: - Streaming console

/// The "anti-static" panel of the Live floor: a monospaced, streaming log of
/// activity events, oldest at the top / newest at the bottom. It follows new
/// events automatically until the user scrolls up, then offers "Jump to
/// latest". Capped at the app's 500-event activity window.
struct LiveConsolePane: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @Binding var category: ConsoleCategory

    @State private var autoScroll = true

    private static let bottomAnchor = "console-bottom"
    private static let scrollSpace = "console-scroll"

    private var lines: [ConsoleLine] {
        LiveConsole.lines(from: model.activityEvents, category: category)
    }

    var body: some View {
        VStack(spacing: 0) {
            filterRow
            Divider()
            GeometryReader { outer in
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 1) {
                            if lines.isEmpty {
                                emptyState
                            } else {
                                ForEach(lines) { line in
                                    ConsoleRow(line: line)
                                        .transition(.opacity)
                                }
                            }

                            Color.clear
                                .frame(height: 1)
                                .id(Self.bottomAnchor)
                                .background(
                                    GeometryReader { geo in
                                        Color.clear.preference(
                                            key: ConsoleBottomPreferenceKey.self,
                                            value: geo.frame(in: .named(Self.scrollSpace)).maxY
                                        )
                                    }
                                )
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .animation(consoleAnimation, value: animationKey)
                    }
                    .defaultScrollAnchor(.bottom)
                    .coordinateSpace(name: Self.scrollSpace)
                    .onPreferenceChange(ConsoleBottomPreferenceKey.self) { maxY in
                        guard maxY.isFinite else { return }
                        // The marker only sits below the viewport when the user
                        // has scrolled up, so this is our "pinned to bottom" bit.
                        let pinned = maxY <= outer.size.height + 8
                        if pinned != autoScroll {
                            autoScroll = pinned
                        }
                    }
                    .onAppear {
                        DispatchQueue.main.async {
                            scrollToBottom(proxy, animated: false)
                        }
                    }
                    .onChange(of: model.activityEvents.first?.id) { _, _ in
                        guard autoScroll else { return }
                        // Wait for the new line to be laid out before following it.
                        DispatchQueue.main.async {
                            scrollToBottom(proxy, animated: !reduceMotion)
                        }
                    }
                    .onChange(of: category) { _, _ in
                        autoScroll = true
                        DispatchQueue.main.async {
                            scrollToBottom(proxy, animated: false)
                        }
                    }
                    .overlay(alignment: .bottomTrailing) {
                        if !autoScroll && !lines.isEmpty {
                            Button {
                                autoScroll = true
                                scrollToBottom(proxy, animated: !reduceMotion)
                            } label: {
                                Label("Jump to latest", systemImage: "arrow.down.to.line")
                                    .font(.system(size: 10, weight: .medium))
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 4)
                                    .background(Capsule().fill(.regularMaterial))
                            }
                            .buttonStyle(.plain)
                            .padding(8)
                            .help("Follow new events again")
                        }
                    }
                }
            }
        }
    }

    private var filterRow: some View {
        HStack(spacing: 5) {
            ForEach(ConsoleCategory.allCases) { candidate in
                Button {
                    category = candidate
                } label: {
                    Text(candidate.title)
                        .font(.system(size: 10, weight: .medium))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(
                            Capsule().fill(
                                candidate == category
                                    ? Color.accentColor.opacity(0.20)
                                    : Color.secondary.opacity(0.08)
                            )
                        )
                        .foregroundStyle(
                            candidate == category ? Color.accentColor : Color.secondary
                        )
                }
                .buttonStyle(.plain)
                .help("Show \(candidate.title) events")
            }

            Spacer(minLength: 6)

            Text("\(lines.count) / \(LiveConsole.lineLimit) lines")
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(.tertiary)
                .help("The console keeps the newest \(LiveConsole.lineLimit) activity events")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
    }

    private var emptyState: some View {
        HStack {
            Text(model.activityUnavailable
                 ? "The activity feed isn't available on this server."
                 : "Waiting for harness events… start a run to see the stream.")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.vertical, 8)
    }

    private var consoleAnimation: Animation? {
        reduceMotion ? nil : .easeOut(duration: 0.2)
    }

    /// Changes whenever a line is added/removed, which animates insertions.
    private var animationKey: String {
        "\(category.rawValue)-\(lines.count)-\(lines.last?.id ?? 0)"
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool) {
        if animated {
            withAnimation(.easeOut(duration: 0.18)) {
                proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
            }
        } else {
            proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
        }
    }
}

// MARK: - One console line

/// `HH:mm:ss  actor  task/tool  message`, monospaced and tinted by event kind.
/// Lines that reference a task jump to it, like the Activity feed.
struct ConsoleRow: View {
    @Environment(AppModel.self) private var model

    let line: ConsoleLine

    private var style: ActivityPresentation.KindStyle {
        ActivityPresentation.style(for: line.kind)
    }

    private var hasTaskLink: Bool {
        guard let taskId = line.taskId else { return false }
        return !taskId.isEmpty
    }

    var body: some View {
        Button {
            if let taskId = line.taskId, hasTaskLink {
                model.focusTask(id: taskId)
            }
        } label: {
            HStack(spacing: 6) {
                Text(LiveConsole.timeString(line.ts))
                    .foregroundStyle(.tertiary)
                    .frame(width: 58, alignment: .leading)

                Text(line.actor)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(width: 84, alignment: .leading)

                Text(line.context)
                    .foregroundStyle(style.color)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(width: 96, alignment: .leading)

                Image(systemName: style.icon)
                    .foregroundStyle(style.color)
                    .frame(width: 12)

                Text(line.message)
                    .lineLimit(1)
                    .truncationMode(.tail)

                Spacer(minLength: 0)
            }
            .font(.system(size: 10.5, design: .monospaced))
            .padding(.horizontal, 4)
            .padding(.vertical, 1)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!hasTaskLink)
        .help(line.message)
    }
}

// MARK: - Preference plumbing

private struct ConsoleBottomPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = .greatestFiniteMagnitude
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}
