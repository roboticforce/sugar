import AppKit
import Foundation
import Observation

// MARK: - Sidebar filter

enum StatusFilter: String, CaseIterable, Identifiable {
    case all
    case pending
    case active
    case hold
    case completed
    case failed

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: return "All Tasks"
        case .pending: return "Pending"
        case .active: return "Active"
        case .hold: return "On Hold"
        case .completed: return "Completed"
        case .failed: return "Failed"
        }
    }

    /// `nil` for "All"; otherwise the exact `status` value used by the API.
    var statusValue: String? {
        self == .all ? nil : rawValue
    }

    var systemImage: String {
        switch self {
        case .all: return "tray.full"
        case .pending: return "circle.dashed"
        case .active: return "play.circle"
        case .hold: return "pause.circle"
        case .completed: return "checkmark.circle"
        case .failed: return "xmark.octagon"
        }
    }
}

// MARK: - Sidebar selection

/// The sidebar has one selection; task filters and the Activity feed share it
/// so only one row is highlighted at a time.
enum SidebarItem: Hashable {
    case status(StatusFilter)
    case activity
}

// MARK: - Store

/// Single source of truth for the UI.
///
/// The SSE `snapshot` is authoritative: it replaces local state on connect and
/// after every reconnect. Live edits arrive as `upsert` / `delete` / `stats`
/// events. The store also applies its own write responses locally so the UI
/// reacts immediately; the next SSE event reconciles if anything differs.
@MainActor
@Observable
final class AppModel {
    enum ConnectionState: Equatable {
        case idle
        case connecting
        case connected
        case reconnecting
        case offline

        var label: String {
            switch self {
            case .idle: return "Idle"
            case .connecting: return "Connecting…"
            case .connected: return "Live"
            case .reconnecting: return "Reconnecting…"
            case .offline: return "Offline"
            }
        }
    }

    static let defaultServerURL = "http://127.0.0.1:7331"
    static let serverURLDefaultsKey = "SugarDash.serverURL"
    /// Upper bound for the in-memory activity feed (the browser client uses
    /// the same 500-event window).
    static let activityLimit = 500

    // Authoritative queue state (from the SSE snapshot + deltas).
    private(set) var tasks: [Task] = []
    private(set) var stats: Stats?
    private(set) var health: Health?
    private(set) var taskTypes: [TaskType] = []
    private(set) var taskTypesError: String?

    // Live activity feed (`GET /api/activity` + SSE `activity`). Newest first;
    // the spine is a separate, optional surface (§11), so a missing endpoint
    // only degrades this pane and never raises the global error banner.
    private(set) var activityEvents: [ActivityEvent] = []
    private(set) var activityUnavailable = false

    // Connection state.
    private(set) var connection: ConnectionState = .idle
    private(set) var hasEverConnected = false
    private(set) var lastError: String?
    private(set) var serverVersion: String?

    // UI state.
    var statusFilter: StatusFilter = .all
    var selection: Task.ID?
    var searchText: String = ""
    /// True while the sidebar's Activity row is selected; the middle pane
    /// switches from the task list to the feed. Default (false) is the task
    /// list, which keeps the existing app behaviour unchanged.
    var showsActivity = false

    // Configuration.
    private(set) var serverURLString: String
    private var client: APIClient
    private let defaults: UserDefaults

    // Stream lifecycle.
    private var streamTask: AsyncTask<Void, Never>?
    private var streamGeneration = 0
    private var backoff: Double = 1
    /// Deduplicates a start()-time load against the load issued when the
    /// stream's first event arrives (both want the same REST snapshot).
    private var activityLoadInFlight = false

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let saved = defaults.string(forKey: Self.serverURLDefaultsKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let initialURL = (saved?.isEmpty == false ? saved! : Self.defaultServerURL)
        self.serverURLString = initialURL
        self.client = APIClient(baseURL: URL(string: initialURL) ?? URL(string: Self.defaultServerURL)!)
    }

    // MARK: - Derived state

    var pendingCount: Int { count(for: .pending) }
    var activeCount: Int { count(for: .active) }
    var holdCount: Int { count(for: .hold) }
    var completedCount: Int { count(for: .completed) }
    var failedCount: Int { count(for: .failed) }

    var isReadOnly: Bool { health?.readOnly == true }

    /// True before the first successful connection, while failing to connect.
    var isUnreachable: Bool {
        tasks.isEmpty && !hasEverConnected && connection == .offline
    }

    var selectedTask: Task? {
        guard let selection else { return nil }
        return tasks.first { $0.id == selection }
    }

    var filteredTasks: [Task] {
        var result = tasks

        if let status = statusFilter.statusValue {
            result = result.filter { $0.status == status }
        }

        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !query.isEmpty {
            result = result.filter { task in
                task.title.lowercased().contains(query)
                    || task.description.lowercased().contains(query)
                    || task.type.lowercased().contains(query)
                    || (task.assignedAgent?.lowercased().contains(query) ?? false)
                    || task.id.lowercased().contains(query)
            }
        }

        return result.sorted { lhs, rhs in
            let left = lhs.createdAt ?? lhs.updatedAt ?? .distantPast
            let right = rhs.createdAt ?? rhs.updatedAt ?? .distantPast
            if left != right { return left > right }
            return lhs.id < rhs.id
        }
    }

    func count(for filter: StatusFilter) -> Int {
        guard let status = filter.statusValue else {
            return stats?.total ?? tasks.count
        }
        if let stats {
            return stats.count(status)
        }
        return tasks.filter { $0.status == status }.count
    }

    // MARK: - Lifecycle

    /// Idempotent: safe to call from `.task` every time the window appears.
    func start() {
        guard streamTask == nil else { return }
        connect(resetBackoff: true)
        loadActivityInBackground()
    }

    /// User-initiated reconnect: starts a fresh stream immediately.
    func reconnect() {
        connect(resetBackoff: true)
        loadMetadata()
    }

    /// Toolbar Refresh: drop the current stream so the server sends a fresh
    /// authoritative snapshot, and refresh health / task types over REST.
    func refresh() {
        connect(resetBackoff: true)
        loadMetadata()
    }

    func stop() {
        streamGeneration += 1
        streamTask?.cancel()
        streamTask = nil
        connection = .idle
    }

    // MARK: - Server URL

    @discardableResult
    func applyServerURL(_ raw: String) -> Bool {
        guard let normalized = Self.normalizeServerURL(raw),
              let url = URL(string: normalized) else {
            return false
        }

        let changed = normalized != serverURLString
        serverURLString = normalized
        defaults.set(normalized, forKey: Self.serverURLDefaultsKey)
        client = APIClient(baseURL: url)

        if changed {
            resetSnapshot()
        }
        connect(resetBackoff: true)
        loadMetadata()
        return true
    }

    static func normalizeServerURL(_ raw: String) -> String? {
        var trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        if !trimmed.contains("://") {
            trimmed = "http://" + trimmed
        }
        guard var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = components.host,
              !host.isEmpty else {
            return nil
        }
        if components.path.hasSuffix("/") {
            components.path = String(components.path.dropLast())
        }
        return components.url?.absoluteString
    }

    // MARK: - Reads

    func loadHealth() async {
        let client = self.client
        do {
            let fetched = try await client.health()
            guard self.client.baseURL == client.baseURL else { return }
            health = fetched
            if !fetched.version.isEmpty {
                serverVersion = fetched.version
            }
            if connection == .connected {
                lastError = nil
            }
        } catch {
            guard self.client.baseURL == client.baseURL else { return }
            if tasks.isEmpty {
                lastError = friendlyMessage(error)
            }
        }
    }

    func loadTaskTypesIfNeeded() async {
        guard taskTypes.isEmpty else { return }
        await loadTaskTypes()
    }

    func loadTaskTypes(force: Bool = false) async {
        guard force || taskTypes.isEmpty else { return }
        let client = self.client
        do {
            let fetched = try await client.taskTypes()
            guard self.client.baseURL == client.baseURL else { return }
            taskTypes = fetched
            taskTypesError = nil
        } catch {
            guard self.client.baseURL == client.baseURL else { return }
            taskTypesError = friendlyMessage(error)
        }
    }

    /// Loads the recent event spine, newest first. Deliberately silent on
    /// failure: the endpoint is optional (§11 ships separately from the task
    /// board), so a 404 or a transient error just marks the feed unavailable
    /// (or leaves stale events in place) and never touches `lastError`.
    func loadActivity(limit: Int = 200) async {
        let client = self.client
        do {
            let response = try await client.activity(limit: limit)
            guard self.client.baseURL == client.baseURL else { return }
            // Contract: oldest first; the feed renders newest first.
            activityEvents = Array(response.events.reversed().prefix(Self.activityLimit))
            activityUnavailable = false
        } catch {
            guard self.client.baseURL == client.baseURL else { return }
            // Keep whatever we already have: a short outage should not blank
            // the feed. When nothing was ever loaded the pane shows a quiet
            // "not available" state instead of an error banner.
            activityUnavailable = activityEvents.isEmpty
        }
    }

    private func loadActivityInBackground() {
        guard !activityLoadInFlight else { return }
        activityLoadInFlight = true
        AsyncTask<Void, Never> { [weak self] in
            guard let self else { return }
            await self.loadActivity()
            self.activityLoadInFlight = false
        }
    }

    /// Records *that* feedback was sent (category, destination, message length)
    /// in the local activity spine. The message itself never leaves the app
    /// except through the email/GitHub action the user chose. Best-effort.
    func recordFeedbackSent(category: String, destination: String, messageLength: Int) async {
        let event = EventIngest(
            kind: "feedback.submitted",
            actor: NSUserName(),
            actorKind: "human",
            harness: "sugardash",
            sessionId: nil,
            taskId: nil,
            project: nil,
            payload: [
                "category": .string(category),
                "destination": .string(destination),
                "message_length": .number(Double(messageLength)),
            ]
        )
        try? await client.ingestEvent(event)
    }

    // MARK: - Writes

    func createTask(from draft: TaskDraft) async throws {
        let payload = TaskCreate(
            title: draft.trimmedTitle,
            type: draft.type.isEmpty ? nil : draft.type,
            description: draft.description,
            priority: draft.priority,
            status: nil,
            assignedAgent: draft.trimmedAssignedAgent.isEmpty ? nil : draft.trimmedAssignedAgent
        )
        let created = try await client.createTask(payload)
        upsertLocal(created)
        selection = created.id
    }

    func updateTask(_ task: Task, from draft: TaskDraft) async throws {
        var patch = TaskPatch()
        patch.title = draft.trimmedTitle
        patch.description = draft.description
        patch.type = draft.type
        patch.priority = draft.priority
        patch.assignedAgent = draft.trimmedAssignedAgent
        if let status = draft.status, status != task.status {
            patch.status = status
        }
        let updated = try await client.updateTask(id: task.id, patch: patch)
        upsertLocal(updated)
        selection = updated.id
    }

    func hold(_ task: Task) async throws {
        let updated = try await client.holdTask(
            id: task.id,
            reason: "Held from the SugarDash app"
        )
        upsertLocal(updated)
    }

    func release(_ task: Task) async throws {
        let updated = try await client.releaseTask(id: task.id)
        upsertLocal(updated)
    }

    func delete(_ task: Task) async throws {
        try await client.deleteTask(id: task.id)
        removeLocal(id: task.id)
    }




    // MARK: - Event stream

    private func connect(resetBackoff: Bool) {
        streamTask?.cancel()
        streamTask = nil
        streamGeneration += 1
        let generation = streamGeneration
        if resetBackoff {
            backoff = 1
        }
        connection = tasks.isEmpty ? .connecting : .reconnecting

        streamTask = AsyncTask<Void, Never> { [weak self] in
            guard let self else { return }
            await self.runEventStream(generation: generation)
        }
    }

    /// Keeps one SSE connection alive; reconnects with 1 s → 10 s backoff.
    private func runEventStream(generation: Int) async {
        defer {
            if generation == streamGeneration {
                streamTask = nil
            }
        }

        var delay: Double = max(backoff, 1)

        while !_Concurrency.Task.isCancelled, generation == streamGeneration {
            var connectedThisAttempt = false

            do {
                try await streamOnce(generation: generation) {
                    connectedThisAttempt = true
                    delay = 1
                    self.backoff = 1
                }
            } catch is CancellationError {
                return
            } catch {
                if _Concurrency.Task.isCancelled || generation != streamGeneration {
                    return
                }
                lastError = friendlyMessage(error)
                connection = hasEverConnected ? .reconnecting : .offline
            }

            if _Concurrency.Task.isCancelled || generation != streamGeneration {
                return
            }

            if connectedThisAttempt {
                lastError = lastError ?? "The live connection dropped; reconnecting…"
                connection = .reconnecting
            }

            do {
                try await _Concurrency.Task.sleep(for: .seconds(delay))
            } catch {
                return
            }
            delay = min(delay * 2, 10)
        }
    }

    /// Consumes one `/api/events` response. Returns when the stream ends.
    private func streamOnce(
        generation: Int,
        onFirstEvent: @escaping @MainActor () -> Void
    ) async throws {
        var sawFirstEvent = false
        for try await event in client.eventStream() {
            if _Concurrency.Task.isCancelled || generation != streamGeneration {
                return
            }
            if !sawFirstEvent {
                sawFirstEvent = true
                hasEverConnected = true
                connection = .connected
                lastError = nil
                onFirstEvent()
                loadMetadata()
            }
            handle(event)
        }
    }

    private func loadMetadata() {
        AsyncTask<Void, Never> { [weak self] in
            guard let self else { return }
            await self.loadHealth()
            await self.loadTaskTypes()
        }
        loadActivityInBackground()
    }

    private func handle(_ event: SSEEvent) {
        let decoder = SugarJSON.makeDecoder()

        switch event.event {
        case "hello":
            if let payload = try? decoder.decode(HelloPayload.self, from: event.data) {
                serverVersion = payload.version
            }
        case "snapshot":
            guard let payload = try? decoder.decode(SnapshotPayload.self, from: event.data) else {
                return
            }
            tasks = payload.tasks
            stats = payload.stats
            reconcileSelection()
        case "upsert":
            guard let payload = try? decoder.decode(UpsertPayload.self, from: event.data) else {
                return
            }
            upsertLocal(payload.task)
        case "delete":
            guard let payload = try? decoder.decode(DeletePayload.self, from: event.data) else {
                return
            }
            removeLocal(id: payload.id)
        case "stats":
            guard let payload = try? decoder.decode(Stats.self, from: event.data) else {
                return
            }
            stats = payload
        case "activity":
            // §11.3: `data: <Event>` — one append-only spine event. The feed is
            // best-effort; a malformed event is dropped, never surfaced.
            guard let activity = try? decoder.decode(ActivityEvent.self, from: event.data) else {
                return
            }
            prependActivity(activity)
        default:
            break
        }
    }

    // MARK: - Activity helpers

    private func prependActivity(_ event: ActivityEvent) {
        // Reconnect races can replay an event we already loaded over REST.
        activityEvents.removeAll { $0.id == event.id }
        activityEvents.insert(event, at: 0)
        if activityEvents.count > Self.activityLimit {
            activityEvents.removeLast(activityEvents.count - Self.activityLimit)
        }
        activityUnavailable = false
    }

    /// Activity row → task list: select the referenced task and leave the feed.
    /// Only resets the status filter / search when the task would otherwise be
    /// hidden, so the user's filters survive normal jumps.
    func focusTask(id: String) {
        showsActivity = false
        selection = id
        if !filteredTasks.contains(where: { $0.id == id }) {
            statusFilter = .all
            searchText = ""
        }
    }

    // MARK: - Local state helpers

    private func upsertLocal(_ task: Task) {
        if let index = tasks.firstIndex(where: { $0.id == task.id }) {
            tasks[index] = task
        } else {
            tasks.append(task)
        }
        recomputeStatsLocally()
    }

    private func removeLocal(id: String) {
        tasks.removeAll { $0.id == id }
        if selection == id {
            selection = nil
        }
        recomputeStatsLocally()
    }

    /// Keeps counters sane between the first upsert and the server's `stats`
    /// event (the contract says stats follows every batch).
    private func recomputeStatsLocally() {
        var byStatus: [String: Int] = [:]
        var byType: [String: Int] = [:]
        for task in tasks {
            byStatus[task.status, default: 0] += 1
            byType[task.type, default: 0] += 1
        }
        stats = Stats(
            total: tasks.count,
            byStatus: byStatus,
            byType: byType,
            recent24h: stats?.recent24h ?? 0
        )
    }

    private func reconcileSelection() {
        guard let selection else { return }
        if !tasks.contains(where: { $0.id == selection }) {
            self.selection = nil
        }
    }

    private func resetSnapshot() {
        tasks = []
        stats = nil
        health = nil
        taskTypes = []
        taskTypesError = nil
        activityEvents = []
        activityUnavailable = false
        selection = nil
        hasEverConnected = false
        serverVersion = nil
        lastError = nil
        connection = .idle
    }

    private func friendlyMessage(_ error: Error) -> String {
        if let apiError = error as? APIClient.APIError {
            return apiError.errorDescription ?? "The dashboard request failed."
        }
        if let urlError = error as? URLError {
            return "Cannot reach \(serverURLString): \(urlError.localizedDescription)"
        }
        return error.localizedDescription
    }
}
