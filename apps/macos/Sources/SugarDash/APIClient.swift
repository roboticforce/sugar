import Foundation

// MARK: - SSE

/// One server-sent event, already split into its event name and raw data.
struct SSEEvent: Sendable {
    let event: String
    let data: Data
}

/// Incremental parser for the `text/event-stream` wire format used by
/// `/api/events`. Feed it one line at a time; it returns a complete event when
/// the blank-line separator is reached. Comments (`: keepalive`) are ignored.
struct SSEParser {
    private var eventName: String?
    private var dataLines: [String] = []

    mutating func consume(line: String) -> SSEEvent? {
        if line.isEmpty {
            defer {
                eventName = nil
                dataLines.removeAll(keepingCapacity: true)
            }
            guard let eventName, !dataLines.isEmpty else { return nil }
            let payload = Data(dataLines.joined(separator: "\n").utf8)
            return SSEEvent(event: eventName, data: payload)
        }

        if line.hasPrefix(":") {
            return nil
        }
        if let value = removingPrefix("event:", from: line) {
            // Defensive: some transports drop the blank separator, so flush a
            // pending event when the next one starts.
            var pending: SSEEvent?
            if let eventName, !dataLines.isEmpty {
                pending = SSEEvent(
                    event: eventName,
                    data: Data(dataLines.joined(separator: "\n").utf8)
                )
                dataLines.removeAll(keepingCapacity: true)
            }
            eventName = value.trimmingCharacters(in: .whitespaces)
            return pending
        } else if let value = removingPrefix("data:", from: line) {
            dataLines.append(value.trimmingCharacters(in: .whitespaces))
        }
        return nil
    }
}

/// Splits a byte stream into SSE lines.
///
/// `AsyncLineSequence` (``URLSession.AsyncBytes.lines``) silently drops the
/// empty line that terminates an event, so a parser that waits for a blank
/// separator never fires. This splitter preserves empty lines and tolerates
/// CRLF and chunks that split mid-line.
struct SSELineSplitter {
    private var buffer: [UInt8] = []

    mutating func consume(byte: UInt8) -> String? {
        guard byte == 0x0A else { // '\n'
            buffer.append(byte)
            return nil
        }
        var line = buffer
        buffer.removeAll(keepingCapacity: true)
        if line.last == 0x0D { // strip CR from CRLF
            line.removeLast()
        }
        return String(decoding: line, as: UTF8.self)
    }
}

/// Streams an HTTP response body chunk-by-chunk through a URLSession delegate.
private final class SSEDataSource: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private let lock = NSLock()
    private var finished = false
    private var failure: Error?

    func start(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            self.continuation = continuation
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 60
            configuration.timeoutIntervalForResource = 3600
            let session = URLSession(
                configuration: configuration,
                delegate: self,
                delegateQueue: nil
            )
            self.session = session
            let task = session.dataTask(with: request)
            self.task = task
            continuation.onTermination = { [weak self] _ in
                self?.cancel()
            }
            task.resume()
        }
    }

    func cancel() {
        lock.lock()
        let alreadyDone = finished
        finished = true
        lock.unlock()
        guard !alreadyDone else { return }
        task?.cancel()
        session?.invalidateAndCancel()
        continuation = nil
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            failure = APIClient.APIError.server(
                status: http.statusCode,
                code: nil,
                message: "The dashboard refused the event stream (HTTP \(http.statusCode))."
            )
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive data: Data
    ) {
        continuation?.yield(data)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        lock.lock()
        let alreadyDone = finished
        finished = true
        let pendingFailure = failure
        lock.unlock()
        guard !alreadyDone else { return }
        session.invalidateAndCancel()
        if let error, (error as? URLError)?.code != .cancelled {
            continuation?.finish(throwing: error)
        } else if let pendingFailure {
            continuation?.finish(throwing: pendingFailure)
        } else if let error {
            continuation?.finish(throwing: error)
        } else {
            continuation?.finish()
        }
        continuation = nil
    }
}

// MARK: - Write payloads

/// Body for `POST /api/tasks` (contract §2.2). Optional fields are omitted
/// when `nil`; the backend applies its normal defaults.
struct TaskCreate: Encodable {
    var title: String
    var type: String?
    var description: String?
    var priority: Int?
    var status: String?
    var assignedAgent: String?
}

/// Body for `PATCH /api/tasks/{id}`. Optional fields are emitted only when the
/// sheet actually changed them (status in particular).
struct TaskPatch: Encodable {
    var title: String?
    var description: String?
    var type: String?
    var priority: Int?
    var assignedAgent: String?
    var status: String?
}

struct HoldRequest: Encodable {
    var reason: String?
}

/// Body for `POST /api/events` (dashboard event-spine ingest). Used for local
/// bookkeeping only — feedback itself travels through email/GitHub, never here.
struct EventIngest: Encodable {
    var kind: String
    var actor: String?
    var actorKind: String?
    var harness: String?
    var sessionId: String?
    var taskId: String?
    var project: String?
    var payload: [String: JSONValue]?
}

// MARK: - Client

/// Talks to the local Sugar dashboard over plain HTTP; the event stream uses
/// `URLSession.bytes(for:)` so we never poll.
struct APIClient {
    enum APIError: LocalizedError {
        case invalidResponse
        case transport(String)
        case server(status: Int, code: String?, message: String)
        case decoding(String)

        var errorDescription: String? {
            switch self {
            case .invalidResponse:
                return "The dashboard returned an invalid response."
            case .transport(let message):
                return message
            case .server(_, _, let message):
                return message
            case .decoding(let message):
                return "Could not read the dashboard response: \(message)"
            }
        }

        /// True when we could not reach the server at all.
        var isUnreachable: Bool {
            if case .transport = self { return true }
            return false
        }
    }

    let baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    // MARK: Reads

    func health() async throws -> Health {
        let data = try await send("api/health")
        return try decode(Health.self, from: data)
    }

    func stats() async throws -> Stats {
        let data = try await send("api/stats")
        return try decode(Stats.self, from: data)
    }

    func tasks(
        status: String? = nil,
        type: String? = nil,
        priority: Int? = nil,
        query: String? = nil,
        limit: Int = 200,
        offset: Int = 0,
        order: String = "created_at_desc"
    ) async throws -> [Task] {
        var queryItems = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "order", value: order)
        ]
        if let status { queryItems.append(URLQueryItem(name: "status", value: status)) }
        if let type { queryItems.append(URLQueryItem(name: "type", value: type)) }
        if let priority { queryItems.append(URLQueryItem(name: "priority", value: String(priority))) }
        if let query, !query.isEmpty { queryItems.append(URLQueryItem(name: "q", value: query)) }

        var request = makeRequest(path: "api/tasks", method: "GET")
        request.url = url(path: "api/tasks", queryItems: queryItems)
        let data = try await perform(request)
        return try decode(TaskListResponse.self, from: data).tasks
    }

    func taskTypes() async throws -> [TaskType] {
        let data = try await send("api/task-types")
        return try decode(TaskTypesResponse.self, from: data).types
    }

    /// `GET /api/activity?limit=N` — the event spine, oldest event first
    /// (contract §11.3). Returns 404 on servers that predate the spine; the
    /// caller treats that as "no activity feed" rather than an error.
    func activity(limit: Int = 200) async throws -> ActivityResponse {
        var queryItems = [URLQueryItem]()
        if limit > 0 {
            queryItems.append(URLQueryItem(name: "limit", value: String(limit)))
        }
        var request = makeRequest(path: "api/activity", method: "GET")
        request.url = url(path: "api/activity", queryItems: queryItems)
        let data = try await perform(request)
        return try decode(ActivityResponse.self, from: data)
    }

    /// `GET /api/trends` — zero-filled buckets and per-group series over a
    /// range. Servers that predate trends return 404; the app renders a clear
    /// "not available on this server" state for that.
    func trends(
        range: TrendsRange,
        dimension: TrendsDimension,
        now: Date = Date()
    ) async throws -> TrendsResponse {
        let window = range.window(endingAt: now)
        // No `limit`: that query parameter bounds the *event scan* server-side
        // (default 50k), not the number of returned series.
        let queryItems = [
            URLQueryItem(name: "since", value: SugarDate.string(from: window.since)),
            URLQueryItem(name: "until", value: SugarDate.string(from: window.until)),
            URLQueryItem(name: "bucket", value: range.bucket),
            URLQueryItem(name: "group_by", value: dimension.groupBy),
        ]
        var request = makeRequest(path: "api/trends", method: "GET")
        request.url = url(path: "api/trends", queryItems: queryItems)
        let data = try await perform(request)
        return try decode(TrendsResponse.self, from: data)
    }

    // MARK: Writes

    func createTask(_ body: TaskCreate) async throws -> Task {
        let data = try await send("api/tasks", method: "POST", body: body)
        return try decode(TaskResponse.self, from: data).task
    }

    func updateTask(id: String, patch: TaskPatch) async throws -> Task {
        let data = try await send("api/tasks/\(escaped(id))", method: "PATCH", body: patch)
        return try decode(TaskResponse.self, from: data).task
    }

    func holdTask(id: String, reason: String?) async throws -> Task {
        let data = try await send(
            "api/tasks/\(escaped(id))/hold",
            method: "POST",
            body: HoldRequest(reason: reason)
        )
        return try decode(TaskResponse.self, from: data).task
    }

    func releaseTask(id: String) async throws -> Task {
        let data = try await send("api/tasks/\(escaped(id))/release", method: "POST")
        return try decode(TaskResponse.self, from: data).task
    }

    func deleteTask(id: String) async throws {
        let data = try await send("api/tasks/\(escaped(id))", method: "DELETE")
        _ = try decode(DeleteResponse.self, from: data)
    }

    /// Records a local event in the dashboard's activity spine. Best-effort:
    /// callers ignore failures so this can never block a user action.
    func ingestEvent(_ event: EventIngest) async throws {
        _ = try await send("api/events", method: "POST", body: event)
    }

    // MARK: Event stream

    /// Opens `GET /api/events` and yields decoded SSE events until the stream
    /// ends or the consumer cancels. Reconnection is the caller's job.
    ///
    /// Uses a `URLSessionDataDelegate` rather than `URLSession.bytes(for:)`:
    /// in release builds `AsyncBytes` was observed to buffer a live
    /// `text/event-stream` response indefinitely (all bytes arrived at the
    /// socket, none reached the loop) even though the same code streamed fine
    /// in debug. The delegate delivers each chunk as it lands.
    func eventStream() -> AsyncThrowingStream<SSEEvent, Error> {
        let streamURL = url(path: "api/events")
        var request = URLRequest(url: streamURL)
        request.httpMethod = "GET"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 60
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")

        let source = SSEDataSource()
        let chunks = source.start(request)

        return AsyncThrowingStream { continuation in
            let task = _Concurrency.Task { [source] in
                _ = source // keep the URLSession delegate alive for the stream
                var parser = SSEParser()
                var splitter = SSELineSplitter()
                do {
                    for try await chunk in chunks {
                        for byte in chunk {
                            if _Concurrency.Task.isCancelled {
                                continuation.finish()
                                return
                            }
                            guard let line = splitter.consume(byte: byte) else { continue }
                            if let event = parser.consume(line: line) {
                                continuation.yield(event)
                            }
                        }
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                source.cancel()
                task.cancel()
            }
        }
    }

    // MARK: - Request plumbing

    private func escaped(_ id: String) -> String {
        id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
    }

    private func url(path: String, queryItems: [URLQueryItem]? = nil) -> URL {
        let base = baseURL.appendingPathComponent(path)
        guard let queryItems, !queryItems.isEmpty else { return base }
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)
        components?.queryItems = queryItems
        return components?.url ?? base
    }

    private func makeRequest(path: String, method: String) -> URLRequest {
        var request = URLRequest(url: url(path: path))
        request.httpMethod = method
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func send(_ path: String, method: String = "GET") async throws -> Data {
        try await perform(makeRequest(path: path, method: method))
    }

    private func send<Body: Encodable>(
        _ path: String,
        method: String,
        body: Body
    ) async throws -> Data {
        var request = makeRequest(path: path, method: method)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            request.httpBody = try SugarJSON.makeEncoder().encode(body)
        } catch {
            throw APIError.decoding(error.localizedDescription)
        }
        return try await perform(request)
    }

    private func perform(_ request: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch let error as URLError {
            throw APIError.transport(Self.describe(error, url: request.url))
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw APIError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw Self.serverError(status: http.statusCode, data: data)
        }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try SugarJSON.makeDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error.localizedDescription)
        }
    }

    private static func describe(_ error: URLError, url: URL?) -> String {
        let target = url?.absoluteString ?? "the dashboard"
        switch error.code {
        case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return "Cannot reach \(target). Is `sugar dashboard` running?"
        case .timedOut:
            return "The dashboard at \(target) did not respond in time."
        case .networkConnectionLost, .notConnectedToInternet:
            return "The connection to \(target) was lost."
        default:
            return error.localizedDescription
        }
    }

    private static func serverError(status: Int, data: Data) -> APIError {
        struct ErrorBody: Decodable {
            var error: String?
            var detail: String?
        }
        let body = try? JSONDecoder().decode(ErrorBody.self, from: data)
        let message: String
        switch body?.error {
        case "validation_error":
            message = body?.detail ?? "The server rejected the request as invalid."
        case "not_found":
            message = "That task no longer exists."
        case "read_only":
            message = "The dashboard is read-only; writes are disabled by the server."
        default:
            message = body?.detail ?? body?.error ?? "The dashboard returned HTTP \(status)."
        }
        return .server(status: status, code: body?.error, message: message)
    }
}

// MARK: - Small helpers

private func removingPrefix(_ prefix: String, from line: String) -> String? {
    guard line.hasPrefix(prefix) else { return nil }
    return String(line.dropFirst(prefix.count))
}
