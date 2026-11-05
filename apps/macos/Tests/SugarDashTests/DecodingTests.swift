import XCTest
@testable import SugarDash

/// Contract fixtures copied verbatim from docs/local-dashboard.md.
final class DecodingTests: XCTestCase {

    /// Exactly the task object from docs/local-dashboard.md §2.1.
    private let specTaskJSON = """
    {
      "id": "9a88e031-a2ae-4db1-b329-72f7b18ad4f7",
      "type": "feature",
      "title": "Add retry to the GitHub client",
      "description": "Long form text, may be empty.",
      "priority": 3,
      "status": "pending",
      "source": "mcp",
      "source_file": null,
      "assigned_agent": null,
      "parent_task_id": null,
      "stage": null,
      "blocked_by": [],
      "context": {},
      "acceptance_criteria": [{"description": "tests pass"}],
      "attempts": 0,
      "created_at": "2026-11-05T16:12:44Z",
      "updated_at": "2026-11-05T16:12:44Z",
      "started_at": null,
      "completed_at": null,
      "last_attempt_at": null,
      "error_message": null,
      "result": {},
      "commit_sha": null,
      "total_execution_time": 0.0,
      "verification_status": null,
      "thinking_summary": null
    }
    """

    func testTaskFixtureFromSpecDecodes() throws {
        let data = Data(specTaskJSON.utf8)
        let task = try SugarJSON.makeDecoder().decode(Task.self, from: data)

        XCTAssertEqual(task.id, "9a88e031-a2ae-4db1-b329-72f7b18ad4f7")
        XCTAssertEqual(task.type, "feature")
        XCTAssertEqual(task.title, "Add retry to the GitHub client")
        XCTAssertEqual(task.description, "Long form text, may be empty.")
        XCTAssertEqual(task.priority, 3)
        XCTAssertEqual(task.status, "pending")
        XCTAssertEqual(task.source, "mcp")

        // Null-heavy fields stay nil.
        XCTAssertNil(task.sourceFile)
        XCTAssertNil(task.assignedAgent)
        XCTAssertNil(task.parentTaskId)
        XCTAssertNil(task.stage)
        XCTAssertNil(task.startedAt)
        XCTAssertNil(task.completedAt)
        XCTAssertNil(task.lastAttemptAt)
        XCTAssertNil(task.errorMessage)
        XCTAssertNil(task.commitSha)
        XCTAssertNil(task.verificationStatus)
        XCTAssertNil(task.thinkingSummary)
        // Legacy rows predate provenance (§10): the keys are absent, not null.
        XCTAssertNil(task.createdBy)
        XCTAssertNil(task.createdByKind)
        XCTAssertNil(task.createdBySession)
        XCTAssertNil(task.claimedBy)
        XCTAssertNil(task.leaseExpiresAt)

        XCTAssertTrue(task.blockedBy.isEmpty)
        XCTAssertTrue(task.context.isEmptyValue)
        XCTAssertEqual(task.context.objectValue, [:])
        XCTAssertTrue(task.result.isEmptyValue)
        XCTAssertEqual(task.result.objectValue, [:])

        XCTAssertEqual(task.acceptanceCriteria.count, 1)
        XCTAssertEqual(task.acceptanceCriteria.first?.objectValue?["description"]?.stringValue, "tests pass")

        XCTAssertEqual(task.attempts, 0)
        XCTAssertEqual(task.totalExecutionTime, 0.0, accuracy: 0.0001)

        // ISO-8601 ``Z`` dates decode to the exact UTC instant.
        let createdAt = try XCTUnwrap(task.createdAt)
        let updatedAt = try XCTUnwrap(task.updatedAt)
        XCTAssertEqual(createdAt, updatedAt)

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(secondsFromGMT: 0))
        let components = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: createdAt
        )
        XCTAssertEqual(components.year, 2026)
        XCTAssertEqual(components.month, 11)
        XCTAssertEqual(components.day, 5)
        XCTAssertEqual(components.hour, 16)
        XCTAssertEqual(components.minute, 12)
        XCTAssertEqual(components.second, 44)
    }

    /// Provenance (§10.1): a task created by an agent from a recorded session,
    /// currently claimed under a lease.
    func testTaskProvenanceFieldsDecode() throws {
        let json = """
        {
          "id": "2da0b2b3-e8c9-47f7-ba91-c97bd3b706bb",
          "type": "bug_fix",
          "title": "Fix flaky claim test in tests/test_concurrency.py",
          "priority": 1,
          "status": "active",
          "created_at": "2026-11-05T15:19:06Z",
          "created_by": "dsh",
          "created_by_kind": "agent",
          "created_by_session": "0.4.2",
          "claimed_by": "claude-code",
          "lease_expires_at": "2026-11-05T18:38:14Z"
        }
        """
        let task = try SugarJSON.makeDecoder()
            .decode(Task.self, from: Data(json.utf8))

        XCTAssertEqual(task.createdBy, "dsh")
        XCTAssertEqual(task.createdByKind, "agent")
        XCTAssertEqual(task.createdBySession, "0.4.2")
        XCTAssertEqual(task.claimedBy, "claude-code")

        let lease = try XCTUnwrap(task.leaseExpiresAt)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(secondsFromGMT: 0))
        let components = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: lease
        )
        XCTAssertEqual(components.year, 2026)
        XCTAssertEqual(components.month, 11)
        XCTAssertEqual(components.day, 5)
        XCTAssertEqual(components.hour, 18)
        XCTAssertEqual(components.minute, 38)
        XCTAssertEqual(components.second, 14)
    }

    /// Provenance (§10.1): explicit nulls decode to nil rather than failing,
    /// and a partial kind-only row keeps the other fields nil.
    func testTaskProvenanceNullsDecode() throws {
        let json = """
        {
          "id": "00000000-0000-0000-0000-000000000002",
          "type": "chore",
          "title": "Legacy row",
          "status": "pending",
          "created_by": null,
          "created_by_kind": null,
          "created_by_session": null,
          "claimed_by": null,
          "lease_expires_at": null
        }
        """
        let task = try SugarJSON.makeDecoder()
            .decode(Task.self, from: Data(json.utf8))

        XCTAssertNil(task.createdBy)
        XCTAssertNil(task.createdByKind)
        XCTAssertNil(task.createdBySession)
        XCTAssertNil(task.claimedBy)
        XCTAssertNil(task.leaseExpiresAt)
    }

    /// SSE `snapshot` payload: {"tasks":[…],"stats":{…}} (contract §3).
    func testSnapshotPayloadFromSpecDecodes() throws {
        let json = """
        {
          "tasks": [\(specTaskJSON)],
          "stats": {
            "total": 1,
            "by_status": {"pending": 1},
            "by_type": {"feature": 1},
            "recent_24h": 1
          }
        }
        """
        let snapshot = try SugarJSON.makeDecoder()
            .decode(SnapshotPayload.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.tasks.count, 1)
        XCTAssertEqual(snapshot.tasks.first?.id, "9a88e031-a2ae-4db1-b329-72f7b18ad4f7")
        XCTAssertEqual(snapshot.stats.total, 1)
        XCTAssertEqual(snapshot.stats.byStatus["pending"], 1)
        XCTAssertEqual(snapshot.stats.byType["feature"], 1)
        XCTAssertEqual(snapshot.stats.recent24h, 1)
        XCTAssertEqual(snapshot.stats.count("pending"), 1)
    }

    /// Unknown statuses are passed through, and fractional-second timestamps
    /// (not in the frozen example, but tolerated by the client) still parse.
    func testUnknownStatusAndFractionalSecondsAreTolerated() throws {
        let json = """
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "type": "chore",
          "title": "Unknown status row",
          "status": "queued",
          "created_at": "2026-11-05T16:12:44.123456Z"
        }
        """
        let task = try SugarJSON.makeDecoder()
            .decode(Task.self, from: Data(json.utf8))

        XCTAssertEqual(task.status, "queued")
        XCTAssertNotNil(task.createdAt)
        XCTAssertEqual(task.context, .object([:]))
        XCTAssertTrue(task.blockedBy.isEmpty)
    }

    /// The SSE wire format from contract §3: hello, snapshot, deltas, comments.
    func testSSEParserHandlesHelloSnapshotKeepaliveAndDeltas() throws {
        let wire = [
            "event: hello",
            #"data: {"version":"3.10.6.dev0","server":"sugar-dashboard"}"#,
            "",
            ": keepalive",
            "",
            "event: snapshot",
            #"data: {"tasks":[],"stats":{"total":0,"by_status":{},"by_type":{},"recent_24h":0}}"#,
            "",
            "event: delete",
            #"data: {"id":"9a88e031-a2ae-4db1-b329-72f7b18ad4f7"}"#,
            ""
        ]

        var parser = SSEParser()
        var events: [SSEEvent] = []
        for line in wire {
            if let event = parser.consume(line: line) {
                events.append(event)
            }
        }

        XCTAssertEqual(events.map(\.event), ["hello", "snapshot", "delete"])

        let decoder = SugarJSON.makeDecoder()
        let hello = try decoder.decode(HelloPayload.self, from: events[0].data)
        XCTAssertEqual(hello.version, "3.10.6.dev0")
        XCTAssertEqual(hello.server, "sugar-dashboard")

        let snapshot = try decoder.decode(SnapshotPayload.self, from: events[1].data)
        XCTAssertTrue(snapshot.tasks.isEmpty)
        XCTAssertEqual(snapshot.stats.total, 0)

        let delete = try decoder.decode(DeletePayload.self, from: events[2].data)
        XCTAssertEqual(delete.id, "9a88e031-a2ae-4db1-b329-72f7b18ad4f7")
    }

    /// Multiple `data:` lines in one event are joined with newlines, as SSE
    /// requires, and comments never produce an event on their own.
    func testSSEParserJoinsMultiLineDataAndIgnoresComments() throws {
        let wire = [
            ": keepalive",
            "",
            "event: stats",
            #"data: {"total": 3,"#,
            #"data:  "by_status": {"pending": 3}}"#,
            ""
        ]

        var parser = SSEParser()
        var events: [SSEEvent] = []
        for line in wire {
            if let event = parser.consume(line: line) {
                events.append(event)
            }
        }

        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events.first?.event, "stats")

        let stats = try SugarJSON.makeDecoder()
            .decode(Stats.self, from: try XCTUnwrap(events.first?.data))
        XCTAssertEqual(stats.total, 3)
        XCTAssertEqual(stats.count("pending"), 3)
    }
}
