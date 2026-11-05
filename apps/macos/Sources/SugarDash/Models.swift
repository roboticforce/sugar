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
