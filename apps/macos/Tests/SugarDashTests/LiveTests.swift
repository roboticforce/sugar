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
