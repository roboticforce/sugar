import SwiftUI

// MARK: - Presentation helpers

enum LivePresentation {
    static func leaseColor(_ health: LeaseHealth) -> Color {
        switch health {
        case .healthy: return .green
        case .expiring: return .orange
        case .expired: return .red
        }
    }

}

// MARK: - Pulsing dot

/// A small status dot with an expanding ring, used for presence and lease
/// health. Respects Reduce Motion by rendering a static dot.
struct LivePulseDot: View {
    let color: Color
    var size: CGFloat = 8

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle()
                .fill(color)
                .frame(width: size, height: size)

            Circle()
                .stroke(color, lineWidth: 1.4)
                .frame(width: size, height: size)
                .scaleEffect(pulse ? 2.3 : 1)
                .opacity(pulse ? 0 : 0.55)
        }
        .frame(width: size * 2.5, height: size * 2.5)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) {
                pulse = true
            }
        }
        .accessibilityHidden(true)
    }
}

// MARK: - Live floor

/// Middle pane for the "Live" sidebar row: who is working *right now*.
///
/// Re-evaluated once per second by a `TimelineView` so elapsed time, lease
/// countdowns and presence freshness tick without any network traffic. Data is
/// the ephemeral SSE `presence` snapshot when fresh, else the task-lease
/// fallback for older servers.
struct LiveFloorPane: View {
    @Environment(AppModel.self) private var model

    private static let cardColumns: [GridItem] = [
        GridItem(.adaptive(minimum: 250, maximum: 430), spacing: 10, alignment: .top)
    ]

    var body: some View {
        VStack(spacing: 0) {
            headerTimeline
            Divider()

            if model.connection != .connected {
                ReconnectingBanner()
            }

            TimelineView(.periodic(from: .now, by: 1)) { context in
                floorContent(now: context.date)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            tickerTimeline
        }
        .navigationTitle("Live")
    }

    /// The header ticks too, so "presence updated Xs ago" and the fallback
    /// switch stay honest without any network traffic.
    private var headerTimeline: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let agents = model.liveAgents(at: context.date)
            header(
                now: context.date,
                source: model.presenceSource(at: context.date),
                agentCount: Set(agents.map(\.actor)).count,
                taskCount: agents.count
            )
        }
    }

    /// The ScrollView lives inside the ticking `TimelineView` by structural
    /// identity, so its scroll offset survives the 1 s clock updates.
    @ViewBuilder
    private func floorContent(now: Date) -> some View {
        let source = model.presenceSource(at: now)
        let agents = model.liveAgents(at: now)

        if agents.isEmpty {
            emptyState(source: source)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(LiveAgent.groupedByActor(agents)) { group in
                        actorSection(group, now: now)
                    }
                }
                .padding(16)
            }
        }
    }

    private var tickerTimeline: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            ticker
        }
    }

    // MARK: Header

    private func header(
        now: Date,
        source: AppModel.PresenceSource,
        agentCount: Int,
        taskCount: Int
    ) -> some View {
        HStack(alignment: .center, spacing: 12) {
            LivePulseDot(color: headerDotColor(source: source, hasAgents: agentCount > 0), size: 10)
                .frame(width: 36, height: 36)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Agents at work")
                        .font(.title3.weight(.semibold))

                    Text("\(agentCount)")
                        .font(.system(size: 26, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(agentCount > 0 ? Color.green : Color.secondary)

                    Text(agentCount == 1 ? "agent" : "agents")
                        .font(.callout)
                        .foregroundStyle(.secondary)

                    if taskCount > agentCount {
                        Text("· \(taskCount) tasks")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                Text(headerSubtitle(now: now, source: source, taskCount: taskCount))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 8)

            if case .derived = source {
                Text("fallback")
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color.orange.opacity(0.14)))
                    .foregroundStyle(.orange)
                    .help("This server doesn't emit live presence; agents are derived from active task leases.")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    private func headerDotColor(source: AppModel.PresenceSource, hasAgents: Bool) -> Color {
        switch source {
        case .presence:
            return .green
        case .derived:
            if model.connection != .connected { return .gray }
            return hasAgents ? .orange : .gray
        }
    }

    private func headerSubtitle(
        now: Date,
        source: AppModel.PresenceSource,
        taskCount: Int
    ) -> String {
        let tasks = taskCount == 1 ? "1 task in flight" : "\(taskCount) tasks in flight"
        switch source {
        case .presence(let updatedAt):
            return "\(tasks) · presence updated \(LiveFormat.ago(now.timeIntervalSince(updatedAt)))"
        case .derived:
            switch model.connection {
            case .connected:
                return "\(tasks) · derived from task leases"
            case .offline:
                return "waiting for server · derived from task leases"
            case .idle, .connecting, .reconnecting:
                return "waiting for server"
            }
        }
    }

    // MARK: Agent cards

    private func actorSection(_ group: LiveAgentGroup, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "cpu")
                    .font(.callout)
                    .foregroundStyle(.purple)
                Text(group.actor)
                    .font(.headline)
                Text(group.agents.count == 1 ? "1 task" : "\(group.agents.count) tasks")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            LazyVGrid(columns: Self.cardColumns, alignment: .leading, spacing: 10) {
                ForEach(group.agents) { agent in
                    LiveAgentCard(agent: agent, now: now)
                }
            }
        }
    }

    // MARK: Empty state

    private func emptyState(source: AppModel.PresenceSource) -> some View {
        ContentUnavailableView {
            Label("No agents at work", systemImage: "person.2")
        } description: {
            VStack(spacing: 6) {
                Text("Start `sugar run` or connect a harness to see agents working here.")
                if case .derived = source {
                    Text("Presence isn't available on this server; the floor derives agents from active task leases.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if model.connection == .offline {
                    Text("The dashboard server is unreachable right now.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        } actions: {
            if model.connection == .offline {
                Button("Reconnect") { model.reconnect() }
            } else {
                Button("Refresh") { model.refresh() }
            }
        }
    }

    // MARK: Activity ticker

    private var recentEvents: [ActivityEvent] {
        Array(model.activityEvents.prefix(12))
    }

    private var ticker: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Image(systemName: "waveform.path.ecg")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("Recent activity")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text("\(model.activityEvents.count) events")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)

            if recentEvents.isEmpty {
                Text(model.activityUnavailable
                     ? "The activity feed isn't available on this server."
                     : "Waiting for activity…")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 10)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(recentEvents) { event in
                            LiveTickerRow(event: event)
                                .transition(.move(edge: .top).combined(with: .opacity))
                        }
                    }
                    .padding(.horizontal, 8)
                }
                .frame(maxHeight: 188)
                .animation(.easeOut(duration: 0.25), value: recentEvents.map(\.id))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }
}

// MARK: - Agent card

/// One card per task an actor is working. Tapping opens the task in the task
/// list (the floor is the map, the list/detail is the drill-down).
struct LiveAgentCard: View {
    @Environment(AppModel.self) private var model

    let agent: LiveAgent
    let now: Date

    private var health: LeaseHealth { agent.leaseHealth(at: now) }
    private var leaseColor: Color { LivePresentation.leaseColor(health) }

    var body: some View {
        Button {
            model.focusTask(id: agent.taskId)
        } label: {
            card
        }
        .buttonStyle(.plain)
        .disabled(agent.taskId.isEmpty)
        .help(agent.taskId.isEmpty
              ? "No task recorded for this agent"
              : "Open “\(agent.displayTitle)” in the task list")
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 8) {
                LivePulseDot(color: leaseColor, size: 8)
                    .padding(.top, 1)

                Text(agent.displayTitle)
                    .font(.callout.weight(.medium))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 0)

                if agent.attempts > 0 {
                    Text("attempts \(agent.attempts)")
                        .font(.system(size: 9, weight: .semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(Color.orange.opacity(0.14)))
                        .foregroundStyle(.orange)
                        .help("Attempt \(agent.attempts)")
                }
            }

            HStack(spacing: 10) {
                Label(workingText, systemImage: "clock")
                Label(leaseText, systemImage: "hourglass")
            }
            .font(.caption2)
            .foregroundStyle(.secondary)

            HStack(spacing: 5) {
                if agent.taskId.isEmpty {
                    Text("no task id")
                } else {
                    Text("#\(TaskFormat.shortID(agent.taskId))")
                    Text("·")
                    Text(TaskPresentation.statusTitle(agent.status))
                }
                Spacer()
                Text(healthLabel)
                    .foregroundStyle(leaseColor)
                    .fontWeight(.semibold)
            }
            .font(.system(size: 9))
            .foregroundStyle(.tertiary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.secondary.opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(leaseColor.opacity(0.35), lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
    }

    private var workingText: String {
        guard let duration = agent.workingDuration(at: now) else { return "working" }
        return "working for \(LiveFormat.elapsed(duration))"
    }

    private var leaseText: String {
        guard let remaining = agent.leaseRemaining(at: now) else { return "no lease" }
        if remaining <= 0 {
            return "lease expired \(LiveFormat.elapsed(-remaining)) ago"
        }
        return "lease \(LiveFormat.countdown(remaining))"
    }

    private var healthLabel: String {
        switch health {
        case .healthy: return "lease healthy"
        case .expiring: return "lease expiring"
        case .expired: return agent.leaseExpiresAt == nil ? "no lease" : "lease expired"
        }
    }
}

// MARK: - Activity ticker row

/// Compact one-line activity row for the bottom of the Live floor. Rows that
/// reference a task jump to the task list, just like the full Activity feed.
struct LiveTickerRow: View {
    @Environment(AppModel.self) private var model

    let event: ActivityEvent

    private var style: ActivityPresentation.KindStyle {
        ActivityPresentation.style(for: event.kind)
    }

    var body: some View {
        Button {
            if let taskId = event.taskId, event.hasTaskLink {
                model.focusTask(id: taskId)
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: style.icon)
                    .font(.system(size: 9))
                    .foregroundStyle(style.color)
                    .frame(width: 13)

                Text(event.actorLabel)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .frame(maxWidth: 90, alignment: .leading)

                Text(event.feedTitle)
                    .font(.caption2)
                    .lineLimit(1)
                    .truncationMode(.tail)

                Spacer(minLength: 5)

                Text(TaskFormat.relative(event.ts))
                    .font(.system(size: 9))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            .padding(.vertical, 2)
            .padding(.horizontal, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!event.hasTaskLink)
        .help(event.hasTaskLink
              ? "Show “\(event.feedTitle)” in the task list"
              : style.title)
    }
}
