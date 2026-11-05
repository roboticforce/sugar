import XCTest
@testable import SugarDash

/// Activity feed contract: one event + the `GET /api/activity` envelope.
/// Fixtures mirror a real response from docs/local-dashboard.md §11.3.
final class ActivityTests: XCTestCase {

    /// A fully populated task event, as emitted by the Sugar queue observer.
    private let completedEventJSON = """
    {
      "id": 3,
      "ts": "2026-11-05T18:36:46Z",
      "kind": "task.completed",
      "actor": "dsh",
      "actor_kind": "agent",
      "harness": "sugar",
      "machine": "Mac",
      "session_id": "sess-7f3a",
      "task_id": "7c9b4ba2-a0b9-482c-bb0f-c0ee2e1722c8",
      "run_id": "run-19",
      "project": "sugar",
      "trace_id": "trace-abc123",
      "source": "observer:sugar",
      "payload": {
        "title": "activity feed smoke",
        "status": "completed",
        "previous_status": "pending",
        "source": "dashboard"
      },
      "ingested_at": "2026-11-05T18:36:47Z"
    }
    """

    func testActivityEventDecodesEveryField() throws {
        let event = try SugarJSON.makeDecoder()
            .decode(ActivityEvent.self, from: Data(completedEventJSON.utf8))

        XCTAssertEqual(event.id, 3)
        XCTAssertEqual(event.ts, SugarDate.parse("2026-11-05T18:36:46Z"))
        XCTAssertEqual(event.kind, "task.completed")
        XCTAssertEqual(event.actor, "dsh")
        XCTAssertEqual(event.actorKind, "agent")
        XCTAssertEqual(event.harness, "sugar")
        XCTAssertEqual(event.machine, "Mac")
        XCTAssertEqual(event.sessionId, "sess-7f3a")
        XCTAssertEqual(event.taskId, "7c9b4ba2-a0b9-482c-bb0f-c0ee2e1722c8")
        XCTAssertEqual(event.runId, "run-19")
        XCTAssertEqual(event.project, "sugar")
        XCTAssertEqual(event.traceId, "trace-abc123")
        XCTAssertEqual(event.source, "observer:sugar")
        XCTAssertEqual(event.ingestedAt, SugarDate.parse("2026-11-05T18:36:47Z"))

        // `payload` stays loosely typed; the feed reads `title` out of it.
        XCTAssertEqual(event.payload.objectValue?["status"]?.stringValue, "completed")
        XCTAssertEqual(event.payload.objectValue?["previous_status"]?.stringValue, "pending")
        XCTAssertEqual(event.payloadTitle, "activity feed smoke")
        XCTAssertEqual(event.feedTitle, "activity feed smoke")
        XCTAssertTrue(event.hasTaskLink)
    }

    /// `GET /api/activity` → {"events":[…], "last_id": N}; oldest first.
    func testActivityResponseEnvelopeDecodes() throws {
        let json = """
        {
          "events": [
            {
              "id": 1,
              "ts": "2026-11-05T18:36:38Z",
              "kind": "run.started",
              "actor": "dsh",
              "actor_kind": "agent",
              "harness": "dsh",
              "machine": "Mac",
              "task_id": "t_aaaa0005",
              "source": "ingest",
              "payload": {"go": "dashboard spine smoke"},
              "ingested_at": "2026-11-05T18:36:38Z"
            },
            \(completedEventJSON)
          ],
          "last_id": 3
        }
        """
        let response = try SugarJSON.makeDecoder()
            .decode(ActivityResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.lastId, 3)
        XCTAssertEqual(response.events.count, 2)
        // The API is oldest-first; the app reverses for display.
        XCTAssertEqual(response.events.map(\.id), [1, 3])
        XCTAssertEqual(response.events.first?.kind, "run.started")
        XCTAssertEqual(response.events.first?.taskId, "t_aaaa0005")
        XCTAssertEqual(response.events.first?.feedTitle, "Task t_aaaa00")
        XCTAssertNil(response.events.first?.payloadTitle)
    }

    /// Only id/kind/ts are guaranteed: explicit nulls and omitted keys must
    /// decode to nil rather than failing the whole feed.
    func testActivityEventToleratesNullsAndOmittedFields() throws {
        let nullHeavy = """
        {
          "id": 4,
          "ts": "2026-11-05T18:36:58Z",
          "kind": "run.started",
          "actor": null,
          "actor_kind": null,
          "harness": null,
          "machine": "Mac",
          "session_id": null,
          "task_id": null,
          "run_id": null,
          "project": null,
          "trace_id": null,
          "source": "ingest",
          "payload": null,
          "ingested_at": null
        }
        """
        let event = try SugarJSON.makeDecoder()
            .decode(ActivityEvent.self, from: Data(nullHeavy.utf8))

        XCTAssertEqual(event.id, 4)
        XCTAssertEqual(event.kind, "run.started")
        XCTAssertEqual(event.machine, "Mac")
        XCTAssertNil(event.actor)
        XCTAssertNil(event.actorKind)
        XCTAssertNil(event.harness)
        XCTAssertNil(event.sessionId)
        XCTAssertNil(event.taskId)
        XCTAssertNil(event.runId)
        XCTAssertNil(event.project)
        XCTAssertNil(event.traceId)
        XCTAssertNil(event.ingestedAt)
        XCTAssertEqual(event.payload, .null)
        XCTAssertNil(event.payloadTitle)
        XCTAssertEqual(event.actorLabel, "unknown")
        XCTAssertEqual(event.feedTitle, "run.started")
        XCTAssertFalse(event.hasTaskLink)
    }

    /// Keys the app doesn't know are ignored, and a free-form kind still has a
    /// usable presentation entry.
    func testFreeFormKindAndUnknownFieldsDecode() throws {
        let json = """
        {
          "id": 99,
          "ts": "2026-11-05T19:00:00Z",
          "kind": "signal.heartbeat",
          "payload": {"title": 42, "note": "not a task event"},
          "score": 0.9,
          "experimental": {"nested": true}
        }
        """
        let event = try SugarJSON.makeDecoder()
            .decode(ActivityEvent.self, from: Data(json.utf8))

        XCTAssertEqual(event.kind, "signal.heartbeat")
        XCTAssertEqual(event.feedTitle, "signal.heartbeat")
        // payload.title is not a String, so the helper must ignore it.
        XCTAssertNil(event.payloadTitle)

        let style = ActivityPresentation.style(for: event.kind)
        XCTAssertEqual(style.title, "Signal heartbeat")
        XCTAssertEqual(style.icon, "antenna.radiowaves.left.and.right")
    }

    /// The SSE stream carries `event: activity` with the event object as data
    /// (§11.3); verify the parser + decoder path the app uses.
    func testSSEActivityEventDecodes() throws {
        let wire = [
            "event: hello",
            #"data: {"version":"3.10.0.dev0","server":"sugar-dashboard"}"#,
            "",
            "event: snapshot",
            #"data: {"tasks":[],"stats":{"total":0,"by_status":{},"by_type":{},"recent_24h":0}}"#,
            "",
            "event: activity",
            "data: \(completedEventJSON.replacingOccurrences(of: "\n", with: ""))",
            ""
        ]

        var parser = SSEParser()
        var events: [SSEEvent] = []
        for line in wire {
            if let event = parser.consume(line: line) {
                events.append(event)
            }
        }

        XCTAssertEqual(events.map(\.event), ["hello", "snapshot", "activity"])
        let activity = try XCTUnwrap(events.last)
        let decoded = try SugarJSON.makeDecoder()
            .decode(ActivityEvent.self, from: activity.data)
        XCTAssertEqual(decoded.id, 3)
        XCTAssertEqual(decoded.kind, "task.completed")
        XCTAssertEqual(decoded.payloadTitle, "activity feed smoke")
    }

    /// A kind the app has never seen must not take the feed down.
    func testUnknownKindStyleFallsBack() {
        let style = ActivityPresentation.style(for: "something.brand_new")
        XCTAssertEqual(style.title, "Something brand new")
        XCTAssertEqual(style.icon, "circle.fill")
    }

    func testActorKindIcons() {
        XCTAssertEqual(ActivityPresentation.actorKindIcon("human"), "person.fill")
        XCTAssertEqual(ActivityPresentation.actorKindIcon("agent"), "cpu")
        XCTAssertEqual(ActivityPresentation.actorKindIcon("system"), "gearshape.fill")
        XCTAssertEqual(ActivityPresentation.actorKindIcon(nil), "person.crop.circle")
        XCTAssertEqual(ActivityPresentation.actorKindIcon("other"), "person.crop.circle")
    }
}

/// Clicking an activity row that references a task must select that task and
/// switch the middle pane back to the task list.
@MainActor
final class ActivityNavigationTests: XCTestCase {
    func testFocusTaskLeavesActivityAndSelectsTask() {
        // Read-only use of the standard defaults; the model never writes here
        // in this test.
        let model = AppModel()
        model.showsActivity = true
        // A stale filter that would hide the target task must be cleared.
        model.statusFilter = .failed
        model.searchText = "no such task"

        model.focusTask(id: "task-123")

        XCTAssertFalse(model.showsActivity)
        XCTAssertEqual(model.selection, "task-123")
        XCTAssertEqual(model.statusFilter, .all)
        XCTAssertEqual(model.searchText, "")
        XCTAssertTrue(model.activityEvents.isEmpty)
        XCTAssertFalse(model.activityUnavailable)
    }
}
