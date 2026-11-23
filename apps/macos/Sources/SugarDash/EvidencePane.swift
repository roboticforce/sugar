import SwiftUI

// MARK: - Evidence pane

/// Mission Control parity: verification / policy / approval evidence
/// (`GET /api/evidence`). Read-only, filtered, and refreshed every 15 s only
/// while the pane is visible.
struct EvidencePane: View {
    @Environment(AppModel.self) private var model

    /// Debounced task-id search text; applied to the model after 300 ms.
    @State private var taskDraft = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            filters
            Divider()
            content
            footer
        }
        .navigationTitle("Evidence")
        // Restart the visible-only refresh loop when the applied filters change.
        .task(id: model.evidenceQueryKey) {
            await model.runEvidenceRefreshLoop()
        }
        .onAppear { taskDraft = model.evidenceTaskID }
        .onChange(of: model.evidenceTaskID) { _, value in
            if taskDraft != value { taskDraft = value }
        }
        .task(id: taskDraft) {
            do {
                try await _Concurrency.Task.sleep(for: .milliseconds(300))
            } catch {
                return
            }
            guard !_Concurrency.Task.isCancelled else { return }
            if model.evidenceTaskID != taskDraft {
                model.evidenceTaskID = taskDraft
            }
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Evidence")
                        .font(.title3.weight(.semibold))
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                if model.evidenceLoading {
                    ProgressView()
                        .controlSize(.small)
                }

                Button {
                    model.refreshEvidence()
                } label: {
                    Label("Refresh evidence", systemImage: "arrow.clockwise")
                }
                .help("Reload the evidence feed")
            }

            if let summary = model.evidence?.summary {
                summaryChips(summary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private var subtitle: String {
        let base = "Verification, policy & approval events"
        if model.evidenceUnavailable {
            return "\(base) · unavailable on this server"
        }
        if let fetchedAt = model.evidenceFetchedAt {
            return "\(base) · updated \(TaskFormat.relative(fetchedAt))"
        }
        if model.evidenceLoading {
            return "\(base) · loading…"
        }
        return base
    }

    private func summaryChips(_ summary: EvidenceSummary) -> some View {
        HStack(spacing: 6) {
            evidenceChip(count: summary.total, label: "total", color: .secondary)
            evidenceChip(count: summary.statusCount("passed"), label: "passed", color: .green)
            evidenceChip(count: summary.statusCount("failed"), label: "failed", color: .red)
            evidenceChip(count: summary.statusCount("blocked"), label: "blocked", color: .orange)

            Spacer(minLength: 0)
        }
    }

    private func evidenceChip(count: Int, label: String, color: Color) -> some View {
        HStack(spacing: 4) {
            Text("\(count)")
                .fontWeight(.semibold)
                .monospacedDigit()
                .foregroundStyle(color)
            Text(label)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 8)
        .padding(.vertical, 2)
        .background(Capsule().fill(color.opacity(0.10)))
        .overlay(Capsule().stroke(color.opacity(0.20), lineWidth: 1))
        .help("\(count) \(label) evidence events")
    }

    // MARK: Filters

    private var filters: some View {
        @Bindable var model = model

        return HStack(spacing: 8) {
            HStack(spacing: 4) {
                Image(systemName: "magnifyingglass")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                TextField("Filter by task id…", text: $taskDraft)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 150, idealWidth: 220)
                    .help("Show evidence for one task id")
            }

            Picker("Kind", selection: $model.evidenceKind) {
                ForEach(EvidenceKindFilter.allCases) { kind in
                    Text(kind.title).tag(kind)
                }
            }
            .pickerStyle(.menu)
            .fixedSize()
            .help("Filter evidence by kind")

            Picker("Status", selection: $model.evidenceStatus) {
                ForEach(EvidenceStatusFilter.allCases) { status in
                    Text(status.title).tag(status)
                }
            }
            .pickerStyle(.menu)
            .fixedSize()
            .help("Filter evidence by status")

            if filtersActive {
                Button("Clear") { clearFilters() }
                    .help("Clear all evidence filters")
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var filtersActive: Bool {
        !model.evidenceTaskID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || model.evidenceKind != .all
            || model.evidenceStatus != .all
    }

    private func clearFilters() {
        taskDraft = ""
        model.evidenceTaskID = ""
        model.evidenceKind = .all
        model.evidenceStatus = .all
    }

    // MARK: Content states

    @ViewBuilder
    private var content: some View {
        if let report = model.evidence {
            if report.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if let error = model.evidenceError {
                            inlineError(error)
                        }
                        ForEach(report.events) { event in
                            EvidenceEventRow(event: event)
                        }
                    }
                    .padding(16)
                }
            }
        } else if model.evidenceUnavailable {
            unavailableState
        } else if let error = model.evidenceError {
            errorState(error)
        } else {
            loadingState
        }
    }

    private var loadingState: some View {
        VStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading evidence…")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label(filtersActive ? "No evidence matches the filters" : "No evidence yet",
                  systemImage: "doc.text.magnifyingglass")
        } description: {
            Text(filtersActive
                 ? "Try a different task id, kind or status, or clear the filters."
                 : "Verification, policy and approval evidence appears here once harnesses record it (`sugar evidence record …`).")
        } actions: {
            if filtersActive {
                Button("Clear filters") { clearFilters() }
            }
            Button("Refresh") { model.refreshEvidence() }
        }
    }

    private var unavailableState: some View {
        ContentUnavailableView {
            Label("Evidence unavailable on this server", systemImage: "doc.text.magnifyingglass")
        } description: {
            Text("This dashboard build doesn't expose `/api/evidence` yet (or its event spine is disabled). Update Sugar and restart `sugar dashboard`, then retry.")
        } actions: {
            Button("Retry") { model.refreshEvidence() }
        }
    }

    private func errorState(_ message: String) -> some View {
        ContentUnavailableView {
            Label("Couldn't load evidence", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
                .textSelection(.enabled)
        } actions: {
            Button("Retry") { model.refreshEvidence() }
        }
    }

    private func inlineError(_ message: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            Text(message)
                .font(.caption)
                .lineLimit(2)
            Spacer()
            Button("Retry") { model.refreshEvidence() }
                .font(.caption)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.10)))
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 8) {
            if let fetchedAt = model.evidenceFetchedAt {
                Text("updated \(TaskFormat.relative(fetchedAt))")
            }
            Spacer()
            if let report = model.evidence {
                Text("showing \(report.events.count) of \(report.summary.total) · limit 100")
            }
            Text("auto-refresh 15s")
        }
        .font(.caption2)
        .foregroundStyle(.tertiary)
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }
}

// MARK: - One evidence row

private struct EvidenceEventRow: View {
    let event: EvidenceEvent

    private var kindColor: Color {
        EvidencePresentation.kindColor(event.kindGroup)
    }

    private var statusColor: Color? {
        event.status.map(EvidencePresentation.statusColor)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(event.kind)
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundStyle(kindColor)
                    .lineLimit(1)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(kindColor.opacity(0.10)))
                    .overlay(Capsule().stroke(kindColor.opacity(0.35), lineWidth: 1))

                if let status = event.status {
                    Text(status.capitalized)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(statusColor ?? .secondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Capsule().fill((statusColor ?? .secondary).opacity(0.10)))
                        .overlay(Capsule().stroke((statusColor ?? .secondary).opacity(0.30), lineWidth: 1))
                }

                Spacer(minLength: 6)

                Text(TaskFormat.relative(event.timestamp))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .help(TaskFormat.timestamp(event.timestamp))
            }

            Text(event.summaryText)
                .font(.callout)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .help(event.summaryText)

            HStack(spacing: 10) {
                if let harness = event.harness, !harness.isEmpty {
                    Label(harness, systemImage: "cpu")
                }
                if let taskId = event.taskId, !taskId.isEmpty {
                    Text("task \(String(taskId.prefix(8)))")
                        .textSelection(.enabled)
                        .help(taskId)
                }
                if let actor = event.actor, actor != event.harness {
                    Label(actor, systemImage: "person.crop.circle")
                }
                Spacer(minLength: 0)
                Text("id \(event.id)")
                    .help("Evidence event \(event.id)")
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.06)))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(kindColor.opacity(0.18), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        var parts = [event.kind]
        if let status = event.status { parts.append(status) }
        parts.append(event.summaryText)
        if let harness = event.harness, !harness.isEmpty { parts.append(harness) }
        if let taskId = event.taskId, !taskId.isEmpty { parts.append("task \(taskId)") }
        parts.append(TaskFormat.relative(event.timestamp))
        return parts.joined(separator: ", ")
    }
}

// MARK: - Presentation helpers

enum EvidencePresentation {
    static func kindColor(_ group: String) -> Color {
        switch group {
        case "verification": return .teal
        case "policy": return .purple
        case "approval": return .yellow
        case "artifact": return .accentColor
        case "commit": return .orange
        default: return .secondary
        }
    }

    static func statusColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "passed", "ok", "granted", "success":
            return .green
        case "failed", "error", "denied":
            return .red
        case "blocked":
            return .orange
        case "pending", "warning":
            return .yellow
        case "skipped", "unknown":
            return .secondary
        default:
            return .secondary
        }
    }
}
