import SwiftUI

/// Read-only detail for one task plus the mutating actions (edit, hold,
/// release, delete-with-confirmation).
struct TaskDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let task: Task
    let onEdit: () -> Void

    @State private var isWorking = false
    @State private var confirmDelete = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                if let taskError = task.errorMessage, !taskError.isEmpty {
                    errorSection(taskError)
                }

                liveActivitySection
                facts
                descriptionSection
                acceptanceSection
                contextSection
                resultSection
                thinkingSection
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
        }
        .safeAreaInset(edge: .bottom) { actionBar }
        .navigationTitle(task.displayTitle)
        .confirmationDialog(
            "Delete task?",
            isPresented: $confirmDelete,
            titleVisibility: .visible
        ) {
            Button("Delete “\(task.displayTitle)”", role: .destructive) {
                performDelete()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This permanently removes the task from the queue. It cannot be undone.")
        }
        .alert(
            "Action failed",
            isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(task.displayTitle)
                .font(.title2.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                StatusBadge(status: task.status)
                PriorityBadge(priority: task.priority)
                Text(task.type)
                    .font(.callout)
                    .foregroundStyle(.secondary)

                if let agent = task.assignedAgent, !agent.isEmpty {
                    Label(agent, systemImage: "person.crop.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Text("#\(task.shortID)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .help(task.id)
            }
        }
    }

    // MARK: Facts

    private var facts: some View {
        GroupBox {
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 18, verticalSpacing: 7) {
                DetailRow(label: "Status", value: TaskPresentation.statusTitle(task.status))
                DetailRow(
                    label: "Priority",
                    value: "P\(task.priority) — \(TaskPresentation.priorityTitle(task.priority))"
                )
                DetailRow(label: "Type", value: task.type)
                DetailRow(label: "Source", value: task.source ?? "—")
                DetailRow(label: "Source file", value: task.sourceFile ?? "—")
                DetailRow(label: "Assigned agent", value: agentText)
                DetailRow(label: "Created by", value: createdByText, systemImage: createdBySymbol)
                if let claimedBy = claimedByText {
                    DetailRow(label: "Claimed by", value: claimedBy)
                }
                if let lease = task.leaseExpiresAt {
                    DetailRow(label: "Lease expires", value: TaskFormat.timestamp(lease))
                }
                DetailRow(label: "Stage", value: task.stage ?? "—")
                DetailRow(label: "Parent task", value: parentText)
                DetailRow(label: "Blocked by", value: blockedByText)
                DetailRow(label: "Attempts", value: "\(task.attempts)")
                DetailRow(label: "Created", value: TaskFormat.timestamp(task.createdAt))
                DetailRow(label: "Updated", value: TaskFormat.timestamp(task.updatedAt))
                DetailRow(label: "Started", value: TaskFormat.timestamp(task.startedAt))
                DetailRow(label: "Completed", value: TaskFormat.timestamp(task.completedAt))
                DetailRow(label: "Last attempt", value: TaskFormat.timestamp(task.lastAttemptAt))
                DetailRow(label: "Execution time", value: TaskFormat.duration(task.totalExecutionTime))
                DetailRow(label: "Commit", value: task.commitSha ?? "—")
                DetailRow(label: "Verification", value: task.verificationStatus ?? "—")
            }
            .padding(6)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var agentText: String {
        guard let agent = task.assignedAgent, !agent.isEmpty else { return "—" }
        return agent
    }

    /// Who asked for the task, with the session/client version appended when
    /// the backend recorded one (§10).
    private var createdByText: String {
        guard let createdBy = task.createdBy, !createdBy.isEmpty else { return "—" }
        if let session = task.createdBySession, !session.isEmpty {
            return "\(createdBy) · \(session)"
        }
        return createdBy
    }

    /// Human/agent/system marker for the "Created by" row; nil when unknown
    /// so legacy rows don't claim a kind they don't have.
    private var createdBySymbol: String? {
        guard task.createdBy != nil else { return nil }
        switch task.createdByKind?.lowercased() {
        case "human": return "person.crop.circle"
        case "agent": return "cpu"
        case "system": return "gearshape"
        default: return "questionmark.circle"
        }
    }

    private var claimedByText: String? {
        guard let claimedBy = task.claimedBy, !claimedBy.isEmpty else { return nil }
        return claimedBy
    }

    private var parentText: String {
        guard let parentID = task.parentTaskId, !parentID.isEmpty else { return "—" }
        if let parent = model.tasks.first(where: { $0.id == parentID }) {
            return "\(parent.displayTitle) (\(TaskFormat.shortID(parentID)))"
        }
        return TaskFormat.shortID(parentID)
    }

    private var blockedByText: String {
        let ids = task.blockedBy.compactMap { $0.stringValue }
        guard !ids.isEmpty else { return "—" }
        return ids.map { id in
            if let blocker = model.tasks.first(where: { $0.id == id }) {
                return blocker.displayTitle
            }
            return TaskFormat.shortID(id)
        }
        .joined(separator: ", ")
    }

    // MARK: Sections

    private var descriptionSection: some View {
        section("Description") {
            if task.description.isEmpty {
                Text("No description.")
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                Text(task.description)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var acceptanceSection: some View {
        section("Acceptance criteria") {
            if task.acceptanceCriteria.isEmpty {
                Text("None specified.")
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(Array(task.acceptanceCriteria.enumerated()), id: \.offset) { _, criterion in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Image(systemName: "checklist")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(criterionDescription(criterion))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private var contextSection: some View {
        section("Context") {
            if task.context.isEmptyValue {
                Text("No context recorded.")
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                jsonBlock(task.context.prettyString)
            }
        }
    }

    private var resultSection: some View {
        section("Result") {
            if task.result.isEmptyValue {
                Text("No result recorded.")
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                jsonBlock(task.result.prettyString)
            }
        }
    }

    private var thinkingSection: some View {
        Group {
            if let summary = task.thinkingSummary, !summary.isEmpty {
                section("Thinking summary") {
                    Text(summary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func errorSection(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Error", systemImage: "xmark.octagon.fill")
                .font(.headline)
                .foregroundStyle(.red)
            Text(message)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.red.opacity(0.08)))
    }

    private func section<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.headline)
            content()
        }
    }

    private func jsonBlock(_ text: String) -> some View {
        Text(text)
            .font(.system(.caption, design: .monospaced))
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))
    }

    private func criterionDescription(_ value: JSONValue) -> String {
        if let description = value.objectValue?["description"]?.stringValue {
            return description
        }
        return value.displayText
    }

    // MARK: Actions

    private var actionBar: some View {
        HStack(spacing: 10) {
            Button {
                onEdit()
            } label: {
                Label("Edit", systemImage: "pencil")
            }
            .disabled(model.isReadOnly || isWorking)
            .help(model.isReadOnly ? "The server is read-only" : "Edit this task")

            if task.canHold {
                Button {
                    perform { try await model.hold(task) }
                } label: {
                    Label("Hold", systemImage: "pause.circle")
                }
                .disabled(model.isReadOnly || isWorking)
            }

            if task.canRelease {
                Button {
                    perform { try await model.release(task) }
                } label: {
                    Label("Release", systemImage: "play.circle")
                }
                .disabled(model.isReadOnly || isWorking)
            }

            Spacer()

            if isWorking {
                ProgressView()
                    .controlSize(.small)
            }

            Button(role: .destructive) {
                confirmDelete = true
            } label: {
                Label("Delete", systemImage: "trash")
            }
            .disabled(model.isReadOnly || isWorking)
        }
        .padding(12)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }

    private func perform(_ operation: @escaping @MainActor () async throws -> Void) {
        isWorking = true
        errorMessage = nil
        AsyncTask<Void, Never> { @MainActor in
            defer { isWorking = false }
            do {
                try await operation()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func performDelete() {
        isWorking = true
        errorMessage = nil
        AsyncTask<Void, Never> { @MainActor in
            defer { isWorking = false }
            do {
                try await model.delete(task)
                model.selection = nil
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    // MARK: - Live activity (what the harness is doing on this task)

    private var recentActivity: [ActivityEvent] {
        model.taskActivity(forTask: task.id, limit: 5)
    }

    private var liveActivitySection: some View {
        let events = recentActivity
        return section("Live activity") {
            if events.isEmpty {
                Text("No harness activity recorded for this task yet.")
                    .font(.caption)
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(events) { event in
                        TaskActivityRow(event: event)
                    }
                }
                .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: events.map(\.id))
            }
        }
    }
}

/// One compact activity row inside the task detail's "Live activity" section.
private struct TaskActivityRow: View {
    let event: ActivityEvent

    private var style: ActivityPresentation.KindStyle {
        ActivityPresentation.style(for: event.kind)
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: style.icon)
                .font(.caption2)
                .foregroundStyle(style.color)
                .frame(width: 14)

            Text(ActivityInsight.line(from: event))
                .font(.caption)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 6)

            Text(TaskFormat.relative(event.ts))
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
                .help(TaskFormat.timestamp(event.ts))
        }
    }
}

// MARK: - Row helper

private struct DetailRow: View {
    let label: String
    let value: String
    var systemImage: String? = nil

    var body: some View {
        GridRow(alignment: .firstTextBaseline) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .gridColumnAlignment(.trailing)
                .frame(minWidth: 110, alignment: .trailing)
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                }
                Text(value)
                    .font(.callout)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
