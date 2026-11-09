import Foundation

// MARK: - Concurrency alias

/// Sugar's HTTP model is called `Task` (it mirrors the `work_items` rows the
/// backend serves), which shadows Swift concurrency's `Task` inside this
/// module. Use `AsyncTask` for unstructured concurrency in this target.
typealias AsyncTask<Success: Sendable, Failure: Error> = _Concurrency.Task<Success, Failure>

// MARK: - Loose JSON

/// Lossless-enough representation of the free-form JSON fields in the task
/// contract (`context`, `result`, `acceptance_criteria`, `blocked_by`).
/// The dashboard keeps their exact shape opaque and just renders them.
enum JSONValue: Codable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null:
            try container.encodeNil()
        case .bool(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        }
    }

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    /// True for `null`, `[]` and `{}` — i.e. "nothing recorded".
    var isEmptyValue: Bool {
        switch self {
        case .null:
            return true
        case .array(let value):
            return value.isEmpty
        case .object(let value):
            return value.isEmpty
        case .string(let value):
            return value.isEmpty
        default:
            return false
        }
    }

    /// Pretty-printed JSON, used in the detail pane.
    var prettyString: String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(self),
              let string = String(data: data, encoding: .utf8) else {
            return ""
        }
        return string
    }

    /// A single human-readable line: strings verbatim, everything else as JSON.
    var displayText: String {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            if value == value.rounded() {
                return String(Int(value))
            }
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        case .null:
            return "null"
        case .array, .object:
            return prettyString
        }
    }
}

// MARK: - Dates

/// Timestamps in the contract are ISO-8601 UTC with a trailing `Z`, e.g.
/// `2026-11-05T16:12:44Z`. Be liberal and also accept fractional seconds in
/// case a future backend revision emits them.
enum SugarDate {
    private static let plainFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    static func parse(_ raw: String) -> Date? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return fractionalFormatter.date(from: trimmed) ?? plainFormatter.date(from: trimmed)
    }

    static func string(from date: Date) -> String {
        plainFormatter.string(from: date)
    }
}

/// Shared JSON coders: snake_case keys on the wire, tolerant ISO-8601 dates.
enum SugarJSON {
    static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            guard let date = SugarDate.parse(raw) else {
                throw DecodingError.dataCorruptedError(
                    in: container,
                    debugDescription: "Invalid ISO-8601 timestamp: \(raw)"
                )
            }
            return date
        }
        return decoder
    }

    static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(SugarDate.string(from: date))
        }
        return encoder
    }
}

// MARK: - Tasks

/// A single `work_items` row, exactly as `GET /api/tasks` and the SSE stream
/// return it (see docs/local-dashboard.md §2.1). All timestamps are optional
/// because the column may be NULL; unknown `status` values are preserved.
struct Task: Identifiable, Codable, Hashable {
    var id: String
    var type: String
    var title: String
    var description: String
    var priority: Int
    var status: String
    var source: String?
    var sourceFile: String?
    var assignedAgent: String?
    /// Provenance (§10): who asked for the task. Kind is `human`, `agent`,
    /// `system` or nil; `createdBySession` carries the client version.
    var createdBy: String?
    var createdByKind: String?
    var createdBySession: String?
    /// Who is doing it right now, plus the lease that guards the claim.
    var claimedBy: String?
    var leaseExpiresAt: Date?
    var parentTaskId: String?
    var stage: String?
    var blockedBy: [JSONValue]
    var context: JSONValue
    var acceptanceCriteria: [JSONValue]
    var attempts: Int
    var createdAt: Date?
    var updatedAt: Date?
    var startedAt: Date?
    var completedAt: Date?
    var lastAttemptAt: Date?
    var errorMessage: String?
    var result: JSONValue
    var commitSha: String?
    var totalExecutionTime: Double
    var verificationStatus: String?
    var thinkingSummary: String?

    // Manual decoding keeps the client tolerant of NULLs and of newer/older
    // server revisions while staying strict about the fields we key on.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        type = try container.decodeIfPresent(String.self, forKey: .type) ?? "unknown"
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        description = try container.decodeIfPresent(String.self, forKey: .description) ?? ""
        priority = try container.decodeIfPresent(Int.self, forKey: .priority) ?? 3
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "pending"
        source = try container.decodeIfPresent(String.self, forKey: .source)
        sourceFile = try container.decodeIfPresent(String.self, forKey: .sourceFile)
        assignedAgent = try container.decodeIfPresent(String.self, forKey: .assignedAgent)
        createdBy = try container.decodeIfPresent(String.self, forKey: .createdBy)
        createdByKind = try container.decodeIfPresent(String.self, forKey: .createdByKind)
        createdBySession = try container.decodeIfPresent(String.self, forKey: .createdBySession)
        claimedBy = try container.decodeIfPresent(String.self, forKey: .claimedBy)
        leaseExpiresAt = try container.decodeIfPresent(Date.self, forKey: .leaseExpiresAt)
        parentTaskId = try container.decodeIfPresent(String.self, forKey: .parentTaskId)
        stage = try container.decodeIfPresent(String.self, forKey: .stage)
        blockedBy = try container.decodeIfPresent([JSONValue].self, forKey: .blockedBy) ?? []
        context = try container.decodeIfPresent(JSONValue.self, forKey: .context) ?? .object([:])
        acceptanceCriteria = try container.decodeIfPresent([JSONValue].self, forKey: .acceptanceCriteria) ?? []
        attempts = try container.decodeIfPresent(Int.self, forKey: .attempts) ?? 0
        createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
        startedAt = try container.decodeIfPresent(Date.self, forKey: .startedAt)
        completedAt = try container.decodeIfPresent(Date.self, forKey: .completedAt)
        lastAttemptAt = try container.decodeIfPresent(Date.self, forKey: .lastAttemptAt)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        result = try container.decodeIfPresent(JSONValue.self, forKey: .result) ?? .object([:])
        commitSha = try container.decodeIfPresent(String.self, forKey: .commitSha)
        totalExecutionTime = try container.decodeIfPresent(Double.self, forKey: .totalExecutionTime) ?? 0
        verificationStatus = try container.decodeIfPresent(String.self, forKey: .verificationStatus)
        thinkingSummary = try container.decodeIfPresent(String.self, forKey: .thinkingSummary)
    }

    var displayTitle: String {
        title.isEmpty ? "(untitled task)" : title
    }

    var shortID: String {
        String(id.prefix(8))
    }

    var updatedOrCreatedAt: Date? {
        updatedAt ?? createdAt
    }

    /// Hold and release are only meaningful for these statuses.
    var canHold: Bool {
        status == "pending" || status == "active" || status == "failed"
    }

    var canRelease: Bool {
        status == "hold"
    }
}

// MARK: - Activity (event spine, §11)

/// One row of the append-only event spine served by `GET /api/activity` and
/// pushed over SSE as `event: activity` (docs/local-dashboard.md §11.3).
///
/// Only `id`, `kind` and `ts` are guaranteed: observability columns are filled
/// on a best-effort basis, so everything else is optional and older servers
/// simply omit the keys. `payload` stays loosely typed (`JSONValue`) because
/// its shape depends on the kind (`title`/`status`/`attempts`/…).
struct ActivityEvent: Decodable, Identifiable, Sendable, Hashable {
    var id: Int
    var ts: Date
    var kind: String
    var actor: String?
    var actorKind: String?
    var harness: String?
    var machine: String?
    var sessionId: String?
    var taskId: String?
    var runId: String?
    var project: String?
    var traceId: String?
    var source: String?
    var payload: JSONValue
    var ingestedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case id
        case ts
        case kind
        case actor
        case actorKind
        case harness
        case machine
        case sessionId
        case taskId
        case runId
        case project
        case traceId
        case source
        case payload
        case ingestedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // The spine's identity: an event without these is unusable.
        id = try container.decode(Int.self, forKey: .id)
        kind = try container.decode(String.self, forKey: .kind)
        ts = try container.decode(Date.self, forKey: .ts)
        actor = try container.decodeIfPresent(String.self, forKey: .actor)
        actorKind = try container.decodeIfPresent(String.self, forKey: .actorKind)
        harness = try container.decodeIfPresent(String.self, forKey: .harness)
        machine = try container.decodeIfPresent(String.self, forKey: .machine)
        sessionId = try container.decodeIfPresent(String.self, forKey: .sessionId)
        taskId = try container.decodeIfPresent(String.self, forKey: .taskId)
        runId = try container.decodeIfPresent(String.self, forKey: .runId)
        project = try container.decodeIfPresent(String.self, forKey: .project)
        traceId = try container.decodeIfPresent(String.self, forKey: .traceId)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        payload = try container.decodeIfPresent(JSONValue.self, forKey: .payload) ?? .null
        ingestedAt = try container.decodeIfPresent(Date.self, forKey: .ingestedAt)
    }

    /// `payload["title"]` when the observer recorded one (`task.*` events do).
    var payloadTitle: String? {
        guard let raw = payload.objectValue?["title"]?.stringValue else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// What the feed's title line shows: the task title when we have it,
    /// otherwise a stable short reference to the task / run.
    var feedTitle: String {
        if let payloadTitle { return payloadTitle }
        if let taskId, !taskId.isEmpty { return "Task \(String(taskId.prefix(8)))" }
        if let runId, !runId.isEmpty { return "Run \(String(runId.prefix(8)))" }
        return kind
    }

    /// Short label for the actor column; the spine allows anonymous events.
    var actorLabel: String {
        guard let actor, !actor.isEmpty else { return "unknown" }
        return actor
    }

    var hasTaskLink: Bool {
        guard let taskId else { return false }
        return !taskId.isEmpty
    }
}

/// `GET /api/activity` envelope (contract §11.3). `events` is oldest first;
/// `lastId` is the watermark for paging forward (`since_id`).
struct ActivityResponse: Decodable, Sendable {
    var events: [ActivityEvent]
    var lastId: Int

    private enum CodingKeys: String, CodingKey {
        case events
        case lastId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        events = try container.decodeIfPresent([ActivityEvent].self, forKey: .events) ?? []
        lastId = try container.decodeIfPresent(Int.self, forKey: .lastId) ?? 0
    }
}

// MARK: - Live presence (ephemeral SSE `presence` event)

/// Lease health for a live agent card. The app's rule of thumb: more than a
/// minute left is healthy (green), under a minute is expiring (amber), and an
/// expired or missing lease is treated as a dead claim (red).
enum LeaseHealth: String, Hashable, Sendable {
    case healthy
    case expiring
    case expired
}

/// One agent currently working a task, as carried by the ephemeral SSE
/// `presence` event:
///
/// ```json
/// {"actor":"claude-code","task_id":"…","title":"…","status":"active",
///  "lease_expires_at":"…Z"|null,"started_at":"…Z"|null,"attempts":2}
/// ```
///
/// Presence is not persisted server-side and older servers never emit it, so
/// every field except `actor` is decoded tolerantly and the app has a
/// task-derived fallback (``LiveAgent/derived(from:)``).
struct LiveAgent: Decodable, Identifiable, Hashable, Sendable {
    var actor: String
    var taskId: String
    var title: String
    var status: String
    var leaseExpiresAt: Date?
    var startedAt: Date?
    var attempts: Int

    /// One card per task; an actor may hold several tasks. The composite key
    /// only matters for pathological payloads without a task id.
    var id: String {
        if !taskId.isEmpty { return taskId }
        return "\(actor)|\(title)|\(startedAt?.timeIntervalSince1970 ?? 0)"
    }

    var displayTitle: String {
        title.isEmpty ? "(untitled task)" : title
    }

    init(
        actor: String,
        taskId: String = "",
        title: String = "",
        status: String = "active",
        leaseExpiresAt: Date? = nil,
        startedAt: Date? = nil,
        attempts: Int = 0
    ) {
        self.actor = actor
        self.taskId = taskId
        self.title = title
        self.status = status
        self.leaseExpiresAt = leaseExpiresAt
        self.startedAt = startedAt
        self.attempts = attempts
    }

    private enum CodingKeys: String, CodingKey {
        case actor
        case taskId
        case title
        case status
        case leaseExpiresAt
        case startedAt
        case attempts
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawActor = try container.decodeIfPresent(String.self, forKey: .actor) ?? ""
        let trimmedActor = rawActor.trimmingCharacters(in: .whitespacesAndNewlines)
        actor = trimmedActor.isEmpty ? "unknown" : trimmedActor
        taskId = try container.decodeIfPresent(String.self, forKey: .taskId) ?? ""
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "active"
        leaseExpiresAt = try container.decodeIfPresent(Date.self, forKey: .leaseExpiresAt)
        startedAt = try container.decodeIfPresent(Date.self, forKey: .startedAt)
        attempts = try container.decodeIfPresent(Int.self, forKey: .attempts) ?? 0
    }

    // MARK: Time helpers (pure; the UI feeds them a TimelineView date)

    /// Seconds until the lease expires; negative when already expired, `nil`
    /// when the server did not record a lease at all.
    func leaseRemaining(at now: Date = Date()) -> TimeInterval? {
        guard let leaseExpiresAt else { return nil }
        return leaseExpiresAt.timeIntervalSince(now)
    }

    func leaseHealth(at now: Date = Date()) -> LeaseHealth {
        guard let remaining = leaseRemaining(at: now) else { return .expired }
        if remaining <= 0 { return .expired }
        return remaining < 60 ? .expiring : .healthy
    }

    /// How long the agent has been on the task, clamped at zero for clock
    /// skew; `nil` when the server did not record a start time.
    func workingDuration(at now: Date = Date()) -> TimeInterval? {
        guard let startedAt else { return nil }
        return max(0, now.timeIntervalSince(startedAt))
    }

    // MARK: Fallback derivation

    /// Fallback for servers that predate the presence event: an active task
    /// that recorded who claimed it is, by definition, being worked on.
    static func derived(from tasks: [Task]) -> [LiveAgent] {
        tasks.compactMap { task in
            guard task.status == "active" else { return nil }
            guard let claimedBy = task.claimedBy?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !claimedBy.isEmpty else { return nil }
            return LiveAgent(
                actor: claimedBy,
                taskId: task.id,
                title: task.title,
                status: task.status,
                leaseExpiresAt: task.leaseExpiresAt,
                startedAt: task.startedAt,
                attempts: task.attempts
            )
        }
        .sorted(by: LiveAgent.workingOrder)
    }

    /// Longest-working first (missing start times last), then by actor/task for
    /// a stable order across presence ticks.
    static func workingOrder(_ lhs: LiveAgent, _ rhs: LiveAgent) -> Bool {
        let left = lhs.startedAt ?? .distantFuture
        let right = rhs.startedAt ?? .distantFuture
        if left != right { return left < right }
        if lhs.actor != rhs.actor {
            return lhs.actor.localizedCaseInsensitiveCompare(rhs.actor) == .orderedAscending
        }
        return lhs.taskId < rhs.taskId
    }

    /// Groups cards under their actor, with the longest-working actor first.
    static func groupedByActor(_ agents: [LiveAgent]) -> [LiveAgentGroup] {
        Dictionary(grouping: agents, by: \.actor)
            .map { actor, agents in
                LiveAgentGroup(actor: actor, agents: agents.sorted(by: LiveAgent.workingOrder))
            }
            .sorted { lhs, rhs in
                let left = lhs.agents.compactMap(\.startedAt).min() ?? .distantFuture
                let right = rhs.agents.compactMap(\.startedAt).min() ?? .distantFuture
                if left != right { return left < right }
                return lhs.actor.localizedCaseInsensitiveCompare(rhs.actor) == .orderedAscending
            }
    }
}

/// One actor heading plus the cards for the tasks it currently holds.
struct LiveAgentGroup: Identifiable, Hashable, Sendable {
    let actor: String
    let agents: [LiveAgent]

    var id: String { actor }
}

/// Queue counters carried by the presence event.
struct PresenceCounts: Decodable, Hashable, Sendable {
    var active: Int
    var pending: Int
    var hold: Int
    var total: Int

    init(active: Int = 0, pending: Int = 0, hold: Int = 0, total: Int = 0) {
        self.active = active
        self.pending = pending
        self.hold = hold
        self.total = total
    }

    private enum CodingKeys: String, CodingKey {
        case active
        case pending
        case hold
        case total
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        active = try container.decodeIfPresent(Int.self, forKey: .active) ?? 0
        pending = try container.decodeIfPresent(Int.self, forKey: .pending) ?? 0
        hold = try container.decodeIfPresent(Int.self, forKey: .hold) ?? 0
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? 0
    }
}

/// Payload of the ephemeral SSE `presence` event (cadence ~2 s, not
/// persisted). The snapshot is authoritative while fresh; the app falls back
/// to task leases when it is absent or stale.
struct PresenceSnapshot: Decodable, Hashable, Sendable {
    /// How long a presence snapshot is trusted before the app falls back to
    /// deriving agents from task leases. The server emits every ~2 s.
    static let staleAfter: TimeInterval = 10

    var ts: Date?
    var agents: [LiveAgent]
    var counts: PresenceCounts
    var uptimeS: Double

    init(
        ts: Date? = nil,
        agents: [LiveAgent] = [],
        counts: PresenceCounts = PresenceCounts(),
        uptimeS: Double = 0
    ) {
        self.ts = ts
        self.agents = agents
        self.counts = counts
        self.uptimeS = uptimeS
    }

    private enum CodingKeys: String, CodingKey {
        case ts
        case agents
        case counts
        case uptimeS
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ts = try container.decodeIfPresent(Date.self, forKey: .ts)
        agents = try container.decodeIfPresent([LiveAgent].self, forKey: .agents) ?? []
        counts = try container.decodeIfPresent(PresenceCounts.self, forKey: .counts) ?? PresenceCounts()
        uptimeS = try container.decodeIfPresent(Double.self, forKey: .uptimeS) ?? 0
    }

    func isFresh(at now: Date = Date()) -> Bool {
        guard let ts else { return false }
        // Tolerate a little clock skew: a slightly future timestamp is fresh.
        return now.timeIntervalSince(ts) <= Self.staleAfter
    }
}

/// Formatting for the live floor. Kept pure so the ticking UI and the tests
/// agree on the exact strings.
enum LiveFormat {
    /// How long an agent has been working: "45s", "3m 12s", "1h 2m".
    static func elapsed(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        if total < 60 { return "\(total)s" }
        let minutes = total / 60
        let secs = total % 60
        if minutes < 60 { return "\(minutes)m \(secs)s" }
        let hours = minutes / 60
        return "\(hours)h \(minutes % 60)m"
    }

    /// Lease countdown (rounded up so a lease is never shown as expired a
    /// fraction early): "4:32", "0:00", "1:02:03".
    static func countdown(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.up)))
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, secs)
        }
        return String(format: "%d:%02d", minutes, secs)
    }

    /// Presence freshness: "just now", "3s ago", "1m 4s ago", "2h ago".
    static func ago(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        if total < 1 { return "just now" }
        if total < 60 { return "\(total)s ago" }
        let minutes = total / 60
        let secs = total % 60
        if minutes < 60 {
            return secs == 0 ? "\(minutes)m ago" : "\(minutes)m \(secs)s ago"
        }
        let hours = minutes / 60
        let mins = minutes % 60
        return mins == 0 ? "\(hours)h ago" : "\(hours)h \(mins)m ago"
    }
}

// MARK: - Stats / health / task types

struct Stats: Codable, Hashable {
    var total: Int
    var byStatus: [String: Int]
    var byType: [String: Int]
    var recent24h: Int

    private enum CodingKeys: String, CodingKey {
        case total
        case byStatus
        case byType
        // `convertFromSnakeCase` maps "recent_24h" to "recent24H" (the digit
        // stops the usual capitalisation rule), so map it explicitly and keep
        // a readable Swift property name.
        case recent24h = "recent24H"
    }

    init(
        total: Int = 0,
        byStatus: [String: Int] = [:],
        byType: [String: Int] = [:],
        recent24h: Int = 0
    ) {
        self.total = total
        self.byStatus = byStatus
        self.byType = byType
        self.recent24h = recent24h
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        total = try container.decodeIfPresent(Int.self, forKey: .total) ?? 0
        byStatus = try container.decodeIfPresent([String: Int].self, forKey: .byStatus) ?? [:]
        byType = try container.decodeIfPresent([String: Int].self, forKey: .byType) ?? [:]
        recent24h = try container.decodeIfPresent(Int.self, forKey: .recent24h) ?? 0
    }

    func count(_ status: String) -> Int {
        byStatus[status] ?? 0
    }
}

struct Health: Codable, Hashable {
    var ok: Bool
    var version: String
    var db: String
    var readOnly: Bool
    var uptimeS: Double
}

struct TaskType: Identifiable, Codable, Hashable {
    var id: String
    var name: String
    var description: String?
}

// MARK: - API envelopes (contract §2.2 / §3)

struct TaskListResponse: Codable {
    var tasks: [Task]
    var count: Int?
}

struct TaskResponse: Codable {
    var task: Task
}

struct TaskTypesResponse: Codable {
    var types: [TaskType]
}

struct DeleteResponse: Codable {
    var deleted: Bool
    var id: String
}

struct SnapshotPayload: Codable {
    var tasks: [Task]
    var stats: Stats
}

struct UpsertPayload: Codable {
    var task: Task
}

struct DeletePayload: Codable {
    var id: String
}

struct HelloPayload: Codable {
    var version: String?
    var server: String?
}

// MARK: - Editor draft

/// What the add/edit sheet collects. `status == nil` means "unchanged"
/// (status is only editable when editing an existing task).
struct TaskDraft {
    var title: String = ""
    var description: String = ""
    var type: String = ""
    var priority: Int = 3
    var assignedAgent: String = ""
    var status: String?

    init(
        title: String = "",
        description: String = "",
        type: String = "",
        priority: Int = 3,
        assignedAgent: String = "",
        status: String? = nil
    ) {
        self.title = title
        self.description = description
        self.type = type
        self.priority = priority
        self.assignedAgent = assignedAgent
        self.status = status
    }

    init(task: Task) {
        title = task.title
        description = task.description
        type = task.type
        priority = task.priority
        assignedAgent = task.assignedAgent ?? ""
        status = nil
    }

    var trimmedTitle: String {
        title.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var trimmedAssignedAgent: String {
        assignedAgent.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Formatting

enum TaskFormat {
    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter
    }()

    static func timestamp(_ date: Date?) -> String {
        guard let date else { return "—" }
        return date.formatted(date: .abbreviated, time: .standard)
    }

    static func relative(_ date: Date?) -> String {
        guard let date else { return "—" }
        return relativeFormatter.localizedString(for: date, relativeTo: Date())
    }

    static func duration(_ seconds: Double) -> String {
        guard seconds > 0 else { return "—" }
        if seconds < 60 {
            return String(format: "%.1fs", seconds)
        }
        let total = Int(seconds.rounded())
        let minutes = total / 60
        let remainingSeconds = total % 60
        if minutes < 60 {
            return "\(minutes)m \(remainingSeconds)s"
        }
        return "\(minutes / 60)h \(minutes % 60)m"
    }

    static func shortID(_ id: String) -> String {
        String(id.prefix(8))
    }
}
