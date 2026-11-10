import XCTest
@testable import SugarDash

// MARK: - Presence decoding

/// Ephemeral SSE `presence` contract: {"ts","agents":[…],"counts":{…},"uptime_s"}.
final class LivePresenceDecodingTests: XCTestCase {

    private let presenceJSON = """
    {
      "ts": "2026-11-09T20:15:44Z",
      "agents": [
        {
          "actor": "claude-code",
          "task_id": "11111111-1111-1111-1111-111111111111",
          "title": "Fix flaky claim test",
          "status": "active",
          "lease_expires_at": "2026-11-09T20:19:00Z",
          "started_at": "2026-11-09T20:11:32Z",
          "attempts": 2
        },
        {
          "actor": "dsh",
          "task_id": "22222222-2222-2222-2222-222222222222",
          "title": "Write the live floor",
          "status": "active",
          "lease_expires_at": null,
          "started_at": null,
          "attempts": 0
        }
      ],
      "counts": {"active": 2, "pending": 5, "hold": 1, "total": 9},
      "uptime_s": 12.3
    }
    """

    func testPresencePayloadDecodes() throws {
        let snapshot = try SugarJSON.makeDecoder()
            .decode(PresenceSnapshot.self, from: Data(presenceJSON.utf8))

        XCTAssertEqual(snapshot.ts, SugarDate.parse("2026-11-09T20:15:44Z"))
        XCTAssertEqual(snapshot.uptimeS, 12.3, accuracy: 0.0001)
        XCTAssertEqual(snapshot.agents.count, 2)

        XCTAssertEqual(snapshot.counts.active, 2)
        XCTAssertEqual(snapshot.counts.pending, 5)
        XCTAssertEqual(snapshot.counts.hold, 1)
        XCTAssertEqual(snapshot.counts.total, 9)

        let first = try XCTUnwrap(snapshot.agents.first)
        XCTAssertEqual(first.actor, "claude-code")
        XCTAssertEqual(first.taskId, "11111111-1111-1111-1111-111111111111")
        XCTAssertEqual(first.title, "Fix flaky claim test")
        XCTAssertEqual(first.status, "active")
        XCTAssertEqual(first.leaseExpiresAt, SugarDate.parse("2026-11-09T20:19:00Z"))
        XCTAssertEqual(first.startedAt, SugarDate.parse("2026-11-09T20:11:32Z"))
        XCTAssertEqual(first.attempts, 2)
        XCTAssertEqual(first.id, first.taskId)

        let second = try XCTUnwrap(snapshot.agents.last)
        XCTAssertEqual(second.actor, "dsh")
        XCTAssertNil(second.leaseExpiresAt)
        XCTAssertNil(second.startedAt)
        XCTAssertEqual(second.attempts, 0)
    }

    func testPresenceToleratesMissingOptionalFields() throws {
        let json = """
        {
          "ts": "2026-11-09T20:15:44Z",
          "agents": [{"actor": "dsh"}]
        }
        """
        let snapshot = try SugarJSON.makeDecoder()
            .decode(PresenceSnapshot.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.agents.count, 1)
        let agent = try XCTUnwrap(snapshot.agents.first)
        XCTAssertEqual(agent.actor, "dsh")
        XCTAssertEqual(agent.taskId, "")
        XCTAssertEqual(agent.title, "")
        XCTAssertEqual(agent.status, "active")
        XCTAssertNil(agent.leaseExpiresAt)
        XCTAssertNil(agent.startedAt)
        XCTAssertEqual(agent.attempts, 0)
        XCTAssertEqual(agent.displayTitle, "(untitled task)")
        XCTAssertFalse(agent.id.isEmpty)

        // Missing counts / uptime degrade to zero rather than failing.
        XCTAssertEqual(snapshot.counts, PresenceCounts())
        XCTAssertEqual(snapshot.uptimeS, 0)
    }

    func testPresenceToleratesNullsAndEmptyAgents() throws {
        let json = """
        {
          "ts": null,
          "agents": null,
          "counts": null,
          "uptime_s": null
        }
        """
        let snapshot = try SugarJSON.makeDecoder()
            .decode(PresenceSnapshot.self, from: Data(json.utf8))

        XCTAssertNil(snapshot.ts)
        XCTAssertTrue(snapshot.agents.isEmpty)
        XCTAssertEqual(snapshot.counts, PresenceCounts())
        XCTAssertEqual(snapshot.uptimeS, 0)
        XCTAssertFalse(snapshot.isFresh(at: Date()))
    }

    func testPresenceIgnoresUnknownFieldsAndUnknownCounts() throws {
        let json = """
        {
          "ts": "2026-11-09T20:15:44Z",
          "agents": [{"actor": "x", "task_id": "t", "title": "T", "future_field": 1}],
          "counts": {"active": 3, "queued": 7},
          "uptime_s": 1,
          "extra": {"nested": true}
        }
        """
        let snapshot = try SugarJSON.makeDecoder()
            .decode(PresenceSnapshot.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.agents.count, 1)
        XCTAssertEqual(snapshot.counts.active, 3)
        // Counters the client doesn't know are ignored, not fatal.
        XCTAssertEqual(snapshot.counts.pending, 0)
        XCTAssertEqual(snapshot.counts.total, 0)
    }

    func testPresenceFreshnessWindow() {
        let now = Date(timeIntervalSince1970: 100_000)
        XCTAssertTrue(PresenceSnapshot(ts: now.addingTimeInterval(-2), agents: []).isFresh(at: now))
        // Slightly-future timestamps are treated as fresh (clock skew).
        XCTAssertTrue(PresenceSnapshot(ts: now.addingTimeInterval(1), agents: []).isFresh(at: now))
        XCTAssertFalse(PresenceSnapshot(ts: now.addingTimeInterval(-11), agents: []).isFresh(at: now))
        XCTAssertFalse(PresenceSnapshot(ts: nil, agents: []).isFresh(at: now))
    }
}

// MARK: - Grouping / fallback / helpers

final class LiveAgentLogicTests: XCTestCase {
    private let epoch = Date(timeIntervalSince1970: 1_000)

    private func agent(
        actor: String,
        taskId: String,
        startedAt: Date? = nil,
        leaseExpiresAt: Date? = nil,
        attempts: Int = 0
    ) -> LiveAgent {
        LiveAgent(
            actor: actor,
            taskId: taskId,
            title: "Task \(taskId)",
            status: "active",
            leaseExpiresAt: leaseExpiresAt,
            startedAt: startedAt,
            attempts: attempts
        )
    }

    func testGroupedByActorSortsLongestWorkingFirst() {
        let agents = [
            agent(actor: "b", taskId: "b1", startedAt: epoch.addingTimeInterval(50)),
            agent(actor: "a", taskId: "a2", startedAt: epoch.addingTimeInterval(30)),
            agent(actor: "a", taskId: "a1", startedAt: epoch.addingTimeInterval(10)),
            agent(actor: "c", taskId: "c1", startedAt: nil),
        ]

        let groups = LiveAgent.groupedByActor(agents)

        XCTAssertEqual(groups.map(\.actor), ["a", "b", "c"])
        XCTAssertEqual(groups[0].agents.map(\.taskId), ["a1", "a2"])
        XCTAssertEqual(groups[0].id, "a")
        XCTAssertEqual(groups[0].agents.count, 2)
        // Missing start times sort last within/among groups.
        XCTAssertEqual(groups.last?.agents.first?.taskId, "c1")
    }

    func testWorkingOrderBreaksTiesByActorThenTask() {
        let agents = [
            agent(actor: "zed", taskId: "z1", startedAt: epoch),
            agent(actor: "amy", taskId: "a2", startedAt: epoch),
            agent(actor: "amy", taskId: "a1", startedAt: epoch),
        ]
        XCTAssertEqual(
            agents.sorted(by: LiveAgent.workingOrder).map(\.taskId),
            ["a1", "a2", "z1"]
        )
    }

    func testLeaseHealthThresholds() {
        let now = Date(timeIntervalSince1970: 10_000)

        XCTAssertEqual(agent(actor: "a", taskId: "t", leaseExpiresAt: now.addingTimeInterval(120)).leaseHealth(at: now), .healthy)
        XCTAssertEqual(agent(actor: "a", taskId: "t", leaseExpiresAt: now.addingTimeInterval(60)).leaseHealth(at: now), .healthy)
        XCTAssertEqual(agent(actor: "a", taskId: "t", leaseExpiresAt: now.addingTimeInterval(59)).leaseHealth(at: now), .expiring)
        XCTAssertEqual(agent(actor: "a", taskId: "t", leaseExpiresAt: now.addingTimeInterval(-1)).leaseHealth(at: now), .expired)
        // Missing lease = red per contract.
        XCTAssertEqual(agent(actor: "a", taskId: "t", leaseExpiresAt: nil).leaseHealth(at: now), .expired)

        let remaining = agent(actor: "a", taskId: "t", leaseExpiresAt: now.addingTimeInterval(59))
            .leaseRemaining(at: now)
        XCTAssertEqual(remaining, 59)
        XCTAssertNil(agent(actor: "a", taskId: "t", leaseExpiresAt: nil).leaseRemaining(at: now))
    }

    func testWorkingDurationIsClampedAndNilSafe() {
        let now = Date(timeIntervalSince1970: 10_000)
        XCTAssertEqual(agent(actor: "a", taskId: "t", startedAt: now.addingTimeInterval(-192)).workingDuration(at: now), 192)
        // Clock skew must not render a negative duration.
        XCTAssertEqual(agent(actor: "a", taskId: "t", startedAt: now.addingTimeInterval(30)).workingDuration(at: now), 0)
        XCTAssertNil(agent(actor: "a", taskId: "t", startedAt: nil).workingDuration(at: now))
    }

    func testDerivedFromActiveClaimedTasksOnly() throws {
        let started = epoch.addingTimeInterval(-500)
        let lease = epoch.addingTimeInterval(300)
        let tasks = [
            try task(id: "t1", status: "active", claimedBy: "claude-code", leaseExpiresAt: lease, startedAt: started, attempts: 2),
            try task(id: "t2", status: "active", claimedBy: "dsh"),
            try task(id: "t3", status: "active"), // active but unclaimed: skip
            try task(id: "t4", status: "pending", claimedBy: "claude-code"), // not active: skip
            try task(id: "t5", status: "active", claimedBy: "   "), // blank actor: skip
        ]

        let derived = LiveAgent.derived(from: tasks)

        XCTAssertEqual(derived.map(\.taskId), ["t1", "t2"])
        XCTAssertEqual(derived.first?.actor, "claude-code")
        XCTAssertEqual(derived.first?.startedAt, started)
        XCTAssertEqual(derived.first?.leaseExpiresAt, lease)
        XCTAssertEqual(derived.first?.attempts, 2)
        XCTAssertEqual(derived.last?.actor, "dsh")
    }

    func testLiveFormatting() {
        XCTAssertEqual(LiveFormat.elapsed(45), "45s")
        XCTAssertEqual(LiveFormat.elapsed(192), "3m 12s")
        XCTAssertEqual(LiveFormat.elapsed(3_725), "1h 2m")

        XCTAssertEqual(LiveFormat.countdown(272), "4:32")
        XCTAssertEqual(LiveFormat.countdown(0), "0:00")
        XCTAssertEqual(LiveFormat.countdown(-9), "0:00")
        XCTAssertEqual(LiveFormat.countdown(3_723), "1:02:03")

        XCTAssertEqual(LiveFormat.ago(0.4), "just now")
        XCTAssertEqual(LiveFormat.ago(3.2), "3s ago")
        XCTAssertEqual(LiveFormat.ago(64), "1m 4s ago")
        XCTAssertEqual(LiveFormat.ago(7_200), "2h ago")
    }

    // MARK: Fixtures

    private func task(
        id: String,
        status: String,
        claimedBy: String? = nil,
        leaseExpiresAt: Date? = nil,
        startedAt: Date? = nil,
        attempts: Int = 0
    ) throws -> Task {
        var fields: [String] = [
            "\"id\": \"\(id)\"",
            "\"type\": \"feature\"",
            "\"title\": \"Task \(id)\"",
            "\"status\": \"\(status)\"",
            "\"attempts\": \(attempts)",
        ]
        if let claimedBy { fields.append("\"claimed_by\": \"\(claimedBy)\"") }
        if let leaseExpiresAt {
            fields.append("\"lease_expires_at\": \"\(SugarDate.string(from: leaseExpiresAt))\"")
        }
        if let startedAt {
            fields.append("\"started_at\": \"\(SugarDate.string(from: startedAt))\"")
        }
        let json = "{\(fields.joined(separator: ","))}"
        return try SugarJSON.makeDecoder().decode(Task.self, from: Data(json.utf8))
    }
}

// MARK: - AppModel integration (through the real SSE event path)

@MainActor
final class LiveAppModelTests: XCTestCase {
    private let twoAgentsJSON = """
    {"actor":"claude-code","task_id":"t1","title":"One","status":"active",
     "lease_expires_at":null,"started_at":null,"attempts":1},
    {"actor":"dsh","task_id":"t2","title":"Two","status":"active",
     "lease_expires_at":null,"started_at":null,"attempts":0}
    """

    private func presenceEvent(at ts: Date, agentsJSON: String) -> SSEEvent {
        let json = """
        {"ts":"\(SugarDate.string(from: ts))","agents":[\(agentsJSON)],
         "counts":{"active":2,"pending":1,"hold":0,"total":3},"uptime_s":42.5}
        """
        return SSEEvent(event: "presence", data: Data(json.utf8))
    }

    private func snapshotEvent(tasksJSON: String) -> SSEEvent {
        let json = """
        {"tasks":[\(tasksJSON)],
         "stats":{"total":1,"by_status":{"active":1},"by_type":{"feature":1},"recent_24h":1}}
        """
        return SSEEvent(event: "snapshot", data: Data(json.utf8))
    }

    private var claimedActiveTaskJSON: String {
        """
        {"id":"task-active-claimed","type":"feature","title":"Claimed work",
         "status":"active","claimed_by":"claude-code","attempts":1}
        """
    }

    func testPresenceEventDrivesLiveFloor() {
        let model = AppModel()
        model.handle(presenceEvent(at: Date().addingTimeInterval(-1), agentsJSON: twoAgentsJSON))

        XCTAssertEqual(model.liveAgents.count, 2)
        XCTAssertEqual(model.liveAgentCount, 2)
        XCTAssertEqual(model.liveTaskCount, 2)
        XCTAssertEqual(model.workingAgentsByActor["claude-code"]?.count, 1)
        XCTAssertEqual(model.liveAgentGroups.map(\.actor).sorted(), ["claude-code", "dsh"])

        if case .presence(let updatedAt) = model.presenceSource() {
            XCTAssertEqual(updatedAt.timeIntervalSinceNow, -1, accuracy: 2)
        } else {
            XCTFail("Expected a fresh presence source")
        }
    }

    func testLiveAgentCountDeduplicatesActors() {
        let model = AppModel()
        let sameActor = """
        {"actor":"dsh","task_id":"a","title":"A","status":"active"},
        {"actor":"dsh","task_id":"b","title":"B","status":"active"}
        """
        model.handle(presenceEvent(at: Date(), agentsJSON: sameActor))

        XCTAssertEqual(model.liveTaskCount, 2)
        XCTAssertEqual(model.liveAgentCount, 1)
        XCTAssertEqual(model.workingAgentsByActor["dsh"]?.count, 2)
    }

    func testFallbackDerivesFromTasksWithoutPresence() {
        let model = AppModel()
        model.handle(snapshotEvent(tasksJSON: claimedActiveTaskJSON))

        XCTAssertEqual(model.liveAgents.map(\.taskId), ["task-active-claimed"])
        XCTAssertEqual(model.liveAgents.first?.actor, "claude-code")
        XCTAssertEqual(model.presenceSource(), .derived)
    }

    func testStalePresenceFallsBackToTaskLeases() {
        let model = AppModel()
        model.handle(snapshotEvent(tasksJSON: claimedActiveTaskJSON))
        model.handle(presenceEvent(
            at: Date().addingTimeInterval(-60),
            agentsJSON: twoAgentsJSON
        ))

        // Presence is older than the freshness window: tasks win.
        XCTAssertEqual(model.presenceSource(), .derived)
        XCTAssertEqual(model.liveAgents.map(\.taskId), ["task-active-claimed"])
    }

    func testFocusTaskLeavesLiveFloor() {
        let model = AppModel()
        model.showLive()
        XCTAssertTrue(model.showsLive)
        XCTAssertFalse(model.showsActivity)

        model.focusTask(id: "task-123")

        XCTAssertFalse(model.showsLive)
        XCTAssertFalse(model.showsActivity)
        XCTAssertEqual(model.selection, "task-123")
    }

    func testShowLiveAndShowActivityAreMutuallyExclusive() {
        let model = AppModel()
        model.showActivity()
        XCTAssertTrue(model.showsActivity)
        XCTAssertFalse(model.showsLive)

        model.showLive()
        XCTAssertTrue(model.showsLive)
        XCTAssertFalse(model.showsActivity)
    }
}

// MARK: - Current action (what each agent is doing)

final class LiveActionTests: XCTestCase {
    private let agent = LiveAgent(actor: "claude-code", taskId: "task-A", title: "Task A")

    func testLatestActionPrefersTaskMatchOverNewerActorEvent() throws {
        let taskEvent = try makeActivityEvent(id: 1, kind: "tool.invoked", age: 40, taskId: "task-A")
        let newerActorEvent = try makeActivityEvent(id: 2, kind: "tool.invoked", age: 2, taskId: "task-B")

        let action = ActivityInsight.latestAction(for: agent, in: [newerActorEvent, taskEvent], now: liveTestNow)

        XCTAssertEqual(action?.id, 1)
        XCTAssertEqual(action?.taskId, "task-A")
    }

    func testLatestActionFallsBackToActor() throws {
        let otherActor = try makeActivityEvent(id: 1, kind: "tool.invoked", age: 10, actor: "dsh", taskId: "task-X")
        let matchingActor = try makeActivityEvent(id: 2, kind: "tool.invoked", age: 5, taskId: "task-Y")

        let action = ActivityInsight.latestAction(for: agent, in: [otherActor, matchingActor], now: liveTestNow)

        XCTAssertEqual(action?.id, 2)
    }

    func testLatestActionRespectsFreshnessWindow() throws {
        let staleTask = try makeActivityEvent(id: 1, kind: "tool.invoked", age: 121, taskId: "task-A")
        let staleActor = try makeActivityEvent(id: 2, kind: "tool.invoked", age: 200, taskId: "task-Z")

        XCTAssertNil(ActivityInsight.latestAction(for: agent, in: [staleTask, staleActor], now: liveTestNow))

        let freshTask = try makeActivityEvent(id: 3, kind: "tool.invoked", age: 119, taskId: "task-A")
        XCTAssertEqual(
            ActivityInsight.latestAction(for: agent, in: [staleTask, freshTask], now: liveTestNow)?.id,
            3
        )

        // A stale task match must not shadow a fresh actor fallback.
        let freshActor = try makeActivityEvent(id: 4, kind: "tool.invoked", age: 10, taskId: "task-B")
        XCTAssertEqual(
            ActivityInsight.latestAction(for: agent, in: [staleTask, freshActor], now: liveTestNow)?.id,
            4
        )
    }

    func testActionLineExtractionPriority() throws {
        let allFields = try makeActivityEvent(
            id: 1,
            kind: "tool.invoked",
            age: 1,
            payload: #"{"message":"Reading the diff","command":"git diff","tool":"Bash","file":"x.swift","title":"T"}"#
        )
        XCTAssertEqual(ActivityInsight.line(from: allFields), "Reading the diff")

        let command = try makeActivityEvent(
            id: 2,
            kind: "tool.invoked",
            age: 1,
            payload: #"{"command":"swift test","tool":"Bash","file":"x.swift","title":"T"}"#
        )
        XCTAssertEqual(ActivityInsight.line(from: command), "swift test")

        let toolAndFile = try makeActivityEvent(
            id: 3,
            kind: "tool.invoked",
            age: 1,
            payload: #"{"tool":"Edit","file":"AppModel.swift","title":"T"}"#
        )
        XCTAssertEqual(ActivityInsight.line(from: toolAndFile), "Edit · AppModel.swift")

        let toolOnly = try makeActivityEvent(id: 4, kind: "tool.invoked", age: 1, payload: #"{"tool":"Read"}"#)
        XCTAssertEqual(ActivityInsight.line(from: toolOnly), "Read")

        let titleOnly = try makeActivityEvent(id: 5, kind: "task.updated", age: 1, payload: #"{"title":"Fix flaky test"}"#)
        XCTAssertEqual(ActivityInsight.line(from: titleOnly), "Fix flaky test")

        let blankMessage = try makeActivityEvent(
            id: 6,
            kind: "tool.invoked",
            age: 1,
            payload: #"{"message":"   ","command":"echo hi"}"#
        )
        XCTAssertEqual(ActivityInsight.line(from: blankMessage), "echo hi")

        let kindOnly = try makeActivityEvent(id: 7, kind: "tool.invoked", age: 1, payload: "{}")
        XCTAssertEqual(ActivityInsight.line(from: kindOnly), "tool.invoked")
    }

    func testActionCarriesMetadataForAnimation() throws {
        let event = try makeActivityEvent(
            id: 42,
            kind: "tool.invoked",
            age: 3,
            taskId: "task-A",
            payload: #"{"message":"Editing AppModel.swift"}"#
        )
        let action = ActivityInsight.latestAction(for: agent, in: [event], now: liveTestNow)

        XCTAssertEqual(action?.id, 42)
        XCTAssertEqual(action?.kind, "tool.invoked")
        XCTAssertEqual(action?.actor, "claude-code")
        XCTAssertEqual(action?.ts, event.ts)
        XCTAssertEqual(action?.line, "Editing AppModel.swift")
    }
}

// MARK: - Streaming console

final class LiveConsoleTests: XCTestCase {

    func testConsoleCategoryFiltering() throws {
        let events = [
            try makeActivityEvent(id: 1, kind: "tool.invoked", age: 1, taskId: "task-A"),
            try makeActivityEvent(id: 2, kind: "run.started", age: 2),
            try makeActivityEvent(id: 3, kind: "task.completed", age: 3, taskId: "task-B"),
            try makeActivityEvent(id: 4, kind: "memory.recalled", age: 4),
            try makeActivityEvent(id: 5, kind: "usage.reported", age: 5),
            try makeActivityEvent(id: 6, kind: "agent.activity", age: 6),
        ]

        XCTAssertEqual(LiveConsole.lines(from: events, category: .all).count, 6)
        XCTAssertEqual(LiveConsole.lines(from: events, category: .tool).map(\.id), [1])
        XCTAssertEqual(LiveConsole.lines(from: events, category: .run).map(\.id), [2])
        XCTAssertEqual(LiveConsole.lines(from: events, category: .task).map(\.id), [3])
        XCTAssertEqual(LiveConsole.lines(from: events, category: .memory).map(\.id), [4])
    }

    func testConsoleLinesAreOldestFirstAndFormatted() throws {
        // Feed is newest-first; the console puts the newest at the bottom.
        let newest = try makeActivityEvent(
            id: 3,
            kind: "tool.invoked",
            age: 1,
            taskId: "abcdef12-3456-7890",
            payload: #"{"tool":"Edit","file":"AppModel.swift"}"#
        )
        let middle = try makeActivityEvent(
            id: 2,
            kind: "run.started",
            age: 5,
            actor: "dsh",
            payload: #"{"message":"run started"}"#
        )
        let oldest = try makeActivityEvent(
            id: 1,
            kind: "task.completed",
            age: 9,
            taskId: "12345678-3456-7890",
            payload: #"{"title":"Done"}"#
        )

        let lines = LiveConsole.lines(from: [newest, middle, oldest])
        XCTAssertEqual(lines.map(\.id), [1, 2, 3])

        let utc = try XCTUnwrap(TimeZone(secondsFromGMT: 0))
        XCTAssertEqual(LiveConsole.timeString(oldest.ts, timeZone: utc), "20:14:51")

        let newestLine = try XCTUnwrap(lines.last)
        XCTAssertEqual(newestLine.context, "task abcdef12")
        XCTAssertEqual(newestLine.message, "Edit · AppModel.swift")
        XCTAssertEqual(newestLine.actor, "claude-code")
        XCTAssertEqual(newestLine.taskId, "abcdef12-3456-7890")

        let middleLine = try XCTUnwrap(lines.first(where: { $0.id == 2 }))
        XCTAssertEqual(middleLine.actor, "dsh")
        XCTAssertEqual(middleLine.context, "run.started")
        XCTAssertEqual(middleLine.message, "run started")
        XCTAssertNil(middleLine.taskId)

        XCTAssertEqual(lines.first?.context, "task 12345678")
        XCTAssertEqual(lines.first?.message, "Done")
    }

    func testEventsPerMinuteUsesLastSixtySeconds() throws {
        let events = [
            try makeActivityEvent(id: 1, kind: "tool.invoked", age: 5),
            try makeActivityEvent(id: 2, kind: "tool.invoked", age: 30),
            try makeActivityEvent(id: 3, kind: "tool.invoked", age: 59),
            try makeActivityEvent(id: 4, kind: "tool.invoked", age: 60),
            try makeActivityEvent(id: 5, kind: "tool.invoked", age: 61),
            try makeActivityEvent(id: 6, kind: "tool.invoked", age: 120),
            try makeActivityEvent(id: 7, kind: "tool.invoked", age: -10), // future / skew
        ]

        XCTAssertEqual(ActivityInsight.eventsPerMinute(events, now: liveTestNow), 4)
    }

    func testTaskActivitySelectionNewestFirstAndLimited() throws {
        let matching = try (1...7).map { id in
            try makeActivityEvent(
                id: id,
                kind: "tool.invoked",
                age: TimeInterval(id),
                taskId: "task-A"
            )
        }
        let otherTask = try makeActivityEvent(id: 99, kind: "tool.invoked", age: 1, taskId: "task-B")

        let recent = ActivityInsight.recentEvents(forTask: "task-A", in: matching + [otherTask], limit: 5)

        XCTAssertEqual(recent.map(\.id), [1, 2, 3, 4, 5])
        XCTAssertTrue(recent.allSatisfy { $0.taskId == "task-A" })

        XCTAssertTrue(ActivityInsight.recentEvents(forTask: "missing", in: matching).isEmpty)
        XCTAssertEqual(LiveConsole.lineLimit, 500)
    }
}

// MARK: - AppModel activity integration

@MainActor
final class LiveActivityModelTests: XCTestCase {
    func testSSEActivityUpdatesActionsTaskStripPulseAndRate() throws {
        let model = AppModel()
        let agent = LiveAgent(actor: "claude-code", taskId: "task-A", title: "Task A")
        let now = Date()

        let first = activityEventJSON(
            id: 1,
            kind: "tool.invoked",
            age: 30,
            taskId: "task-A",
            payload: #"{"tool":"Read","file":"Models.swift"}"#,
            now: now
        )
        let second = activityEventJSON(
            id: 2,
            kind: "tool.invoked",
            age: 10,
            taskId: "task-A",
            payload: #"{"message":"Editing AppModel.swift"}"#,
            now: now
        )

        model.handle(SSEEvent(event: "activity", data: Data(first.utf8)))
        model.handle(SSEEvent(event: "activity", data: Data(second.utf8)))

        XCTAssertEqual(model.activityPulse, 2)
        XCTAssertEqual(model.taskActivity(forTask: "task-A").map(\.id), [2, 1])
        XCTAssertEqual(model.latestAction(for: agent)?.id, 2)
        XCTAssertEqual(model.latestAction(for: agent)?.line, "Editing AppModel.swift")
        XCTAssertEqual(model.activityRate(), 2)
    }
}

// MARK: - Shared fixtures

/// Fixed "now" for deterministic relative timestamps (2026-11-09T20:15:00Z).
private let liveTestNow = SugarDate.parse("2026-11-09T20:15:00Z")!

private func activityEventJSON(
    id: Int,
    kind: String,
    age: TimeInterval,
    actor: String? = "claude-code",
    taskId: String? = nil,
    payload: String = "{}",
    now: Date = liveTestNow
) -> String {
    let ts = SugarDate.string(from: now.addingTimeInterval(-age))
    var fields = [
        "\"id\": \(id)",
        "\"kind\": \"\(kind)\"",
        "\"ts\": \"\(ts)\"",
        "\"payload\": \(payload)",
    ]
    if let actor { fields.append("\"actor\": \"\(actor)\"") }
    if let taskId { fields.append("\"task_id\": \"\(taskId)\"") }
    return "{\(fields.joined(separator: ","))}"
}

private func makeActivityEvent(
    id: Int,
    kind: String,
    age: TimeInterval,
    actor: String? = "claude-code",
    taskId: String? = nil,
    payload: String = "{}",
    now: Date = liveTestNow
) throws -> ActivityEvent {
    let json = activityEventJSON(
        id: id,
        kind: kind,
        age: age,
        actor: actor,
        taskId: taskId,
        payload: payload,
        now: now
    )
    return try SugarJSON.makeDecoder().decode(ActivityEvent.self, from: Data(json.utf8))
}
