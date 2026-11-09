import SwiftUI

// MARK: - Presentation helpers

enum TaskPresentation {
    /// Statuses the editor offers. Unknown statuses from the server are shown
    /// as-is but not selectable.
    static let statuses = ["pending", "active", "hold", "completed", "failed"]

    static func statusTitle(_ raw: String) -> String {
        switch raw {
        case "pending": return "Pending"
        case "active": return "Active"
        case "hold": return "Hold"
        case "completed": return "Completed"
        case "failed": return "Failed"
        default:
            return raw.isEmpty ? "Unknown" : raw.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func statusColor(_ raw: String) -> Color {
        switch raw {
        case "pending": return .orange
        case "active": return .green
        case "hold": return .yellow
        case "completed": return .blue
        case "failed": return .red
        default: return .gray
        }
    }

    static func priorityTitle(_ priority: Int) -> String {
        switch priority {
        case 1: return "Urgent"
        case 2: return "High"
        case 3: return "Normal"
        case 4: return "Low"
        case 5: return "Minimal"
        default: return "P\(priority)"
        }
    }

    static func priorityColor(_ priority: Int) -> Color {
        switch priority {
        case 1: return .red
        case 2: return .orange
        case 3: return .yellow
        case 4: return .blue
        default: return .gray
        }
    }
}

// MARK: - Root

struct ContentView: View {
    @Environment(AppModel.self) private var model

    @State private var showCreateTask = false
    @State private var editingTask: Task?

    var body: some View {
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 215, ideal: 245, max: 300)
        } content: {
            Group {
                if model.showsLive {
                    LiveFloorPane()
                } else if model.showsActivity {
                    ActivityFeedPane()
                } else {
                    TaskListPane(showCreateTask: $showCreateTask, editingTask: $editingTask)
                }
            }
            .navigationSplitViewColumnWidth(min: 340, ideal: 440)
        } detail: {
            DetailPane(editingTask: $editingTask)
        }
        .toolbar { toolbarContent }
        .navigationTitle("Sugar Dashboard")
        .frame(minWidth: 940, minHeight: 560)
        .task { model.start() }
        .sheet(isPresented: $showCreateTask) {
            TaskEditorView(mode: .create)
                .environment(model)
        }
        .sheet(item: $editingTask) { task in
            TaskEditorView(mode: .edit(task))
                .environment(model)
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if model.isReadOnly {
                Text("Read-only")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Color.orange.opacity(0.15)))
                    .help("The server was started with --read-only; writes are rejected.")
            }

            if model.liveAgentCount > 0 {
                Button {
                    model.showLive()
                } label: {
                    HStack(spacing: 5) {
                        LivePulseDot(color: .green, size: 6)
                        Text(model.liveAgentCount == 1
                             ? "1 agent working"
                             : "\(model.liveAgentCount) agents working")
                            .font(.caption)
                    }
                }
                .help("Show the live \"Agents at work\" floor")
            }

            Button {
                showCreateTask = true
            } label: {
                Label("New Task", systemImage: "plus")
            }
            .disabled(model.isReadOnly)
            .keyboardShortcut("n", modifiers: .command)
            .help(model.isReadOnly ? "The server is read-only" : "Create a new task")

            Button {
                model.refresh()
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .keyboardShortcut("r", modifiers: .command)
            .help("Reconnect and reload the queue")

            Button {
                model.reconnect()
            } label: {
                Label("Reconnect", systemImage: "bolt.horizontal")
            }
            .help("Reconnect to \(model.serverURLString)")

            HStack(spacing: 6) {
                ConnectionDot(state: model.connection)
                Text(model.connection.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.leading, 4)
            .help(model.lastError ?? model.connection.label)
        }
    }
}

// MARK: - Sidebar

struct SidebarView: View {
    @Environment(AppModel.self) private var model

    @State private var urlDraft = ""
    @State private var urlError: String?
    @FocusState private var urlFieldFocused: Bool

    private var sidebarSelection: Binding<SidebarItem?> {
        Binding(
            get: {
                if model.showsLive { return .live }
                if model.showsActivity { return .activity }
                return .status(model.statusFilter)
            },
            set: { newValue in
                switch newValue {
                case .live:
                    model.showLive()
                case .activity:
                    model.showActivity()
                case .status(let filter):
                    model.showsLive = false
                    model.showsActivity = false
                    model.statusFilter = filter
                case nil:
                    model.showsLive = false
                    model.showsActivity = false
                    model.statusFilter = .all
                }
            }
        )
    }

    var body: some View {
        List(selection: sidebarSelection) {
            Section("Queue") {
                ForEach(StatusFilter.allCases) { filter in
                    HStack(spacing: 8) {
                        Image(systemName: filter.systemImage)
                            .frame(width: 18)
                            .foregroundStyle(.secondary)
                        Text(filter.title)
                        Spacer(minLength: 8)
                        Text("\(model.count(for: filter))")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .tag(SidebarItem.status(filter))
                }
            }

            Section("Live") {
                HStack(spacing: 8) {
                    Image(systemName: "dot.radiowaves.left.and.right")
                        .frame(width: 18)
                        .foregroundStyle(model.showsLive ? Color.accentColor : .secondary)
                    Text("Live")
                    Spacer(minLength: 8)
                    if model.liveAgentCount > 0 {
                        HStack(spacing: 4) {
                            LivePulseDot(color: .green, size: 5)
                            Text("\(model.liveAgentCount)")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .tag(SidebarItem.live)
                .accessibilityLabel(model.liveAgentCount > 0
                                    ? "Live, \(model.liveAgentCount) agents working"
                                    : "Live")

                HStack(spacing: 8) {
                    Image(systemName: "waveform.path.ecg")
                        .frame(width: 18)
                        .foregroundStyle(model.showsActivity ? Color.accentColor : .secondary)
                    Text("Activity")
                    Spacer(minLength: 8)
                    if !model.activityEvents.isEmpty {
                        Text("\(model.activityEvents.count)")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .tag(SidebarItem.activity)
            }

            if let health = model.health {
                Section("Server") {
                    LabeledContent("Version", value: model.serverVersion ?? health.version)
                    LabeledContent("Database") {
                        Text(health.db)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                            .help(health.db)
                    }
                    if health.readOnly {
                        Label("Read-only", systemImage: "lock")
                            .foregroundStyle(.orange)
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) { serverForm }
        .onAppear {
            if urlDraft.isEmpty {
                urlDraft = model.serverURLString
            }
        }
    }

    private var serverForm: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            Text("Dashboard server")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)

            HStack(spacing: 6) {
                TextField("http://127.0.0.1:7331", text: $urlDraft)
                    .textFieldStyle(.roundedBorder)
                    .focused($urlFieldFocused)
                    .onSubmit { applyURL() }
                Button("Connect") { applyURL() }
                    .disabled(urlDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            if let urlError {
                Text(urlError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 6) {
                ConnectionDot(state: model.connection)
                Text(model.connection.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Reconnect") { model.reconnect() }
                    .buttonStyle(.link)
                    .font(.caption)
            }

            if let lastError = model.lastError, model.connection != .connected {
                Text(lastError)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 10)
        .padding(.top, 6)
        .padding(.bottom, 10)
        .background(.bar)
    }

    private func applyURL() {
        if model.applyServerURL(urlDraft) {
            urlError = nil
            urlDraft = model.serverURLString
            urlFieldFocused = false
        } else {
            urlError = "Enter an http(s) URL, e.g. http://127.0.0.1:7331"
        }
    }
}

// MARK: - Task list

struct TaskListPane: View {
    @Environment(AppModel.self) private var model

    @Binding var showCreateTask: Bool
    @Binding var editingTask: Task?

    @State private var deletingTask: Task?
    @State private var actionError: String?

    var body: some View {
        @Bindable var model = model

        Group {
            if model.isUnreachable {
                unreachableState
            } else if model.tasks.isEmpty && (model.connection == .connecting || model.connection == .idle) {
                loadingState
            } else if model.tasks.isEmpty && model.connection == .connected {
                emptyQueueState
            } else if model.filteredTasks.isEmpty {
                emptyFilterState
            } else {
                taskList
            }
        }
        .searchable(text: $model.searchText, prompt: "Search tasks")
        .navigationTitle(model.statusFilter.title)
        .confirmationDialog(
            "Delete task?",
            isPresented: Binding(
                get: { deletingTask != nil },
                set: { if !$0 { deletingTask = nil } }
            ),
            titleVisibility: .visible,
            presenting: deletingTask
        ) { task in
            Button("Delete “\(task.displayTitle)”", role: .destructive) {
                performDelete(task)
            }
            Button("Cancel", role: .cancel) { deletingTask = nil }
        } message: { task in
            Text("This permanently removes “\(task.displayTitle)” from the queue. It cannot be undone.")
        }
        .alert(
            "Action failed",
            isPresented: Binding(
                get: { actionError != nil },
                set: { if !$0 { actionError = nil } }
            )
        ) {
            Button("OK", role: .cancel) { actionError = nil }
        } message: {
            Text(actionError ?? "")
        }
    }

    // MARK: States

    private var unreachableState: some View {
        ContentUnavailableView {
            Label("Can't reach the Sugar dashboard", systemImage: "bolt.horizontal.circle")
        } description: {
            VStack(spacing: 10) {
                Text(model.serverURLString)
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                Text("Start the backend in your project directory with `sugar dashboard`, then reconnect.")
                if let lastError = model.lastError {
                    Text(lastError)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        } actions: {
            VStack(spacing: 6) {
                Button("Reconnect") { model.reconnect() }
                    .buttonStyle(.borderedProminent)
                Text("Use the URL field in the sidebar if the server runs on another port.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text("Connecting to \(model.serverURLString)…")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyQueueState: some View {
        ContentUnavailableView {
            Label("Queue is empty", systemImage: "tray")
        } description: {
            Text("There are no tasks in this Sugar queue yet.")
        } actions: {
            Button("New Task…") { showCreateTask = true }
                .disabled(model.isReadOnly)
        }
    }

    private var emptyFilterState: some View {
        ContentUnavailableView {
            Label("No matching tasks", systemImage: "line.3.horizontal.decrease.circle")
        } description: {
            Text("No tasks match the current status filter and search.")
        } actions: {
            Button("Clear filters") {
                model.statusFilter = .all
                model.searchText = ""
            }
        }
    }

    // MARK: List

    private var taskList: some View {
        @Bindable var model = model

        return List(selection: $model.selection) {
            ForEach(model.filteredTasks) { task in
                TaskRow(task: task)
                    .tag(task.id)
                    .contextMenu { rowActions(for: task) }
            }
        }
        .listStyle(.inset)
        .overlay(alignment: .bottom) {
            if model.connection != .connected {
                ReconnectingBanner()
            }
        }
    }

    @ViewBuilder
    private func rowActions(for task: Task) -> some View {
        Button("Edit…") { editingTask = task }
            .disabled(model.isReadOnly)

        if task.canHold {
            Button("Hold") { perform { try await model.hold(task) } }
                .disabled(model.isReadOnly)
        }
        if task.canRelease {
            Button("Release") { perform { try await model.release(task) } }
                .disabled(model.isReadOnly)
        }

        Divider()

        Button("Delete…", role: .destructive) { deletingTask = task }
            .disabled(model.isReadOnly)
    }

    // MARK: Actions

    private func perform(_ operation: @escaping @MainActor () async throws -> Void) {
        AsyncTask<Void, Never> { @MainActor in
            do {
                try await operation()
            } catch {
                actionError = error.localizedDescription
            }
        }
    }

    private func performDelete(_ task: Task) {
        AsyncTask<Void, Never> { @MainActor in
            do {
                try await model.delete(task)
            } catch {
                actionError = error.localizedDescription
            }
            deletingTask = nil
        }
    }
}

// MARK: - Task row

struct TaskRow: View {
    let task: Task

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            PriorityBadge(priority: task.priority)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                Text(task.displayTitle)
                    .font(.callout.weight(.medium))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    StatusBadge(status: task.status)

                    Text(task.type)
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    if let agent = task.assignedAgent, !agent.isEmpty {
                        Label(agent, systemImage: "person.crop.circle")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }

                    if task.attempts > 0 {
                        Text("attempts: \(task.attempts)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                Text("updated \(TaskFormat.relative(task.updatedOrCreatedAt))")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
    }
}

// MARK: - Activity feed

/// Presentation for the event spine (§11.2). The icon communicates the
/// category (task / run / tool / memory / signal) and the colour the meaning;
/// unknown free-form kinds still get a sensible icon and a humanised label.
enum ActivityPresentation {
    struct KindStyle {
        let icon: String
        let color: Color
        let title: String
    }

    static func style(for kind: String) -> KindStyle {
        switch kind {
        case "task.created":
            return KindStyle(icon: "plus.circle.fill", color: .green, title: "Task created")
        case "task.updated":
            return KindStyle(icon: "arrow.triangle.2.circlepath", color: .blue, title: "Task updated")
        case "task.claimed":
            return KindStyle(icon: "hand.raised.fill", color: .teal, title: "Task claimed")
        case "task.held":
            return KindStyle(icon: "pause.circle.fill", color: .yellow, title: "Task held")
        case "task.released":
            return KindStyle(icon: "play.circle.fill", color: .teal, title: "Task released")
        case "task.blocked":
            return KindStyle(icon: "exclamationmark.octagon.fill", color: .red, title: "Task blocked")
        case "task.unblocked":
            return KindStyle(icon: "checkmark.octagon.fill", color: .green, title: "Task unblocked")
        case "task.assigned":
            return KindStyle(icon: "person.badge.plus", color: .indigo, title: "Task assigned")
        case "task.commented":
            return KindStyle(icon: "text.bubble.fill", color: .purple, title: "Task commented")
        case "task.completed":
            return KindStyle(icon: "checkmark.circle.fill", color: .blue, title: "Task completed")
        case "task.failed":
            return KindStyle(icon: "xmark.octagon.fill", color: .red, title: "Task failed")
        case "task.archived":
            return KindStyle(icon: "archivebox.fill", color: .gray, title: "Task archived")
        case "run.started":
            return KindStyle(icon: "play.circle.fill", color: .orange, title: "Run started")
        case "run.ended":
            return KindStyle(icon: "stop.circle.fill", color: .orange, title: "Run ended")
        case "tool.invoked":
            return KindStyle(icon: "wrench.and.screwdriver.fill", color: .orange, title: "Tool invoked")
        case "memory.recalled":
            return KindStyle(icon: "brain", color: .pink, title: "Memory recalled")
        default:
            if kind.hasPrefix("signal") {
                return KindStyle(icon: "antenna.radiowaves.left.and.right", color: .purple, title: humanized(kind))
            }
            if kind.hasPrefix("run.") {
                return KindStyle(icon: "play.circle.fill", color: .orange, title: humanized(kind))
            }
            if kind.hasPrefix("tool.") {
                return KindStyle(icon: "wrench.and.screwdriver.fill", color: .orange, title: humanized(kind))
            }
            if kind.hasPrefix("task.") {
                return KindStyle(icon: "circle.dashed", color: .blue, title: humanized(kind))
            }
            return KindStyle(icon: "circle.fill", color: .gray, title: humanized(kind))
        }
    }

    static func actorKindIcon(_ actorKind: String?) -> String {
        switch actorKind {
        case "human": return "person.fill"
        case "agent": return "cpu"
        case "system": return "gearshape.fill"
        default: return "person.crop.circle"
        }
    }

    static func actorKindColor(_ actorKind: String?) -> Color {
        switch actorKind {
        case "human": return .blue
        case "agent": return .purple
        case "system": return .gray
        default: return .secondary
        }
    }

    /// "task.completed" → "Task completed"; free-form kinds are tolerated.
    private static func humanized(_ kind: String) -> String {
        let parts = kind.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 2, !parts[0].isEmpty else {
            return kind.replacingOccurrences(of: "_", with: " ").capitalized
        }
        let action = parts[1].replacingOccurrences(of: "_", with: " ")
        return action.isEmpty ? parts[0].capitalized : "\(parts[0].capitalized) \(action)"
    }
}

/// Middle pane for the "Activity" sidebar row: the live event spine, newest
/// first. Rows that reference a task jump back to the task list on click.
struct ActivityFeedPane: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if !model.activityEvents.isEmpty {
                activityList
            } else if model.connection == .offline {
                offlineState
            } else if model.connection == .idle || model.connection == .connecting {
                loadingState
            } else if model.activityUnavailable {
                unavailableState
            } else {
                emptyState
            }
        }
        .navigationTitle("Activity")
    }

    private var activityList: some View {
        List {
            ForEach(model.activityEvents) { event in
                row(for: event)
            }
        }
        .listStyle(.inset)
        .overlay(alignment: .bottom) {
            if model.connection != .connected {
                ReconnectingBanner()
            }
        }
    }

    @ViewBuilder
    private func row(for event: ActivityEvent) -> some View {
        if event.hasTaskLink, let taskID = event.taskId {
            Button {
                model.focusTask(id: taskID)
            } label: {
                ActivityRow(event: event)
            }
            .buttonStyle(.plain)
            .help("Show task \(taskID) in the task list")
        } else {
            ActivityRow(event: event)
        }
    }

    private var offlineState: some View {
        ContentUnavailableView {
            Label("Activity needs the dashboard server", systemImage: "bolt.horizontal.circle")
        } description: {
            Text("Reconnect to \(model.serverURLString) to load the event feed.")
        } actions: {
            Button("Reconnect") { model.reconnect() }
        }
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.small)
            Text("Loading activity…")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// The endpoint is optional (§11 shipped after the task board): say so
    /// quietly instead of raising the global error banner.
    private var unavailableState: some View {
        ContentUnavailableView {
            Label("Activity feed isn't available", systemImage: "waveform.path.ecg")
        } description: {
            Text("This dashboard server doesn't expose /api/activity yet. The task board keeps working as before.")
        } actions: {
            Button("Retry") { model.refresh() }
        }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No activity yet", systemImage: "waveform.path.ecg")
        } description: {
            Text("Task transitions and harness telemetry appear here as they happen.")
        }
    }
}

/// One spine event: kind icon, title (payload title / task id), actor with an
/// actor-kind icon, relative time, and harness / project chips.
struct ActivityRow: View {
    let event: ActivityEvent

    private var style: ActivityPresentation.KindStyle {
        ActivityPresentation.style(for: event.kind)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: style.icon)
                .font(.callout)
                .foregroundStyle(style.color)
                .frame(width: 18)
                .padding(.top, 2)
                .help(style.title)

            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(event.feedTitle)
                        .font(.callout.weight(.medium))
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .help(titleHelp)

                    Spacer(minLength: 4)

                    Text(TaskFormat.relative(event.ts))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .help(TaskFormat.timestamp(event.ts))
                }

                HStack(spacing: 8) {
                    HStack(spacing: 3) {
                        Image(systemName: ActivityPresentation.actorKindIcon(event.actorKind))
                            .font(.caption2)
                            .foregroundStyle(ActivityPresentation.actorKindColor(event.actorKind))
                        Text(event.actorLabel)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .help(actorHelp)

                    Text(style.title)
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    if let harness = nonEmpty(event.harness) {
                        chip(harness, icon: "hammer")
                    }
                    if let project = nonEmpty(event.project) {
                        chip(project, icon: "folder")
                    }
                }
            }

            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
    }

    private var actorHelp: String {
        if let actorKind = event.actorKind, !actorKind.isEmpty {
            return "\(event.actorLabel) (\(actorKind))"
        }
        return "Actor: \(event.actorLabel)"
    }

    private var titleHelp: String {
        if let taskId = event.taskId, !taskId.isEmpty {
            return "\(event.feedTitle) — task \(taskId)"
        }
        return event.kind
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func chip(_ text: String, icon: String) -> some View {
        HStack(spacing: 3) {
            Image(systemName: icon)
                .font(.system(size: 8))
            Text(text)
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(Color.secondary.opacity(0.12)))
    }
}

// MARK: - Detail container

struct DetailPane: View {
    @Environment(AppModel.self) private var model

    @Binding var editingTask: Task?

    var body: some View {
        if let task = model.selectedTask {
            TaskDetailView(task: task) {
                editingTask = task
            }
        } else {
            ContentUnavailableView {
                Label("No Task Selected", systemImage: "sidebar.right")
            } description: {
                Text("Pick a task from the list to inspect it.")
            }
        }
    }
}

// MARK: - Small shared views

struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(TaskPresentation.statusTitle(status))
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Capsule().fill(TaskPresentation.statusColor(status).opacity(0.16)))
            .foregroundStyle(TaskPresentation.statusColor(status))
    }
}

struct PriorityBadge: View {
    let priority: Int

    var body: some View {
        Text("P\(priority)")
            .font(.caption2.weight(.bold).monospacedDigit())
            .frame(minWidth: 22)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(
                RoundedRectangle(cornerRadius: 5)
                    .fill(TaskPresentation.priorityColor(priority).opacity(0.16))
            )
            .foregroundStyle(TaskPresentation.priorityColor(priority))
            .help("Priority \(priority): \(TaskPresentation.priorityTitle(priority))")
    }
}

struct ConnectionDot: View {
    let state: AppModel.ConnectionState

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 8, height: 8)
            .overlay(Circle().stroke(Color.primary.opacity(0.08), lineWidth: 1))
            .help(state.label)
    }

    private var color: Color {
        switch state {
        case .connected:
            return .green
        case .connecting, .reconnecting:
            return .orange
        case .idle, .offline:
            return .red
        }
    }
}

/// Bottom-of-list banner shared by the task list and the activity feed.
struct ReconnectingBanner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 8) {
            ConnectionDot(state: model.connection)
            Text(model.connection == .offline
                 ? "Not connected to \(model.serverURLString)"
                 : "Reconnecting to \(model.serverURLString)…")
                .font(.caption)
            Spacer()
            Button("Reconnect") { model.reconnect() }
                .font(.caption)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }
}
