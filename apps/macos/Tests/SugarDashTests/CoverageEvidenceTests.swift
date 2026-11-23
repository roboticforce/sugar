import XCTest
@testable import SugarDash

// MARK: - Coverage decoding

final class CoverageDecodingTests: XCTestCase {

    private let fullPayload = """
    {
      "generated_at": "2026-11-22T20:00:00Z",
      "summary": {
        "L3": ["claude-code"],
        "L2": ["dsh", "cursor"],
        "L1": [],
        "L0": ["goose"]
      },
      "harnesses": [
        {
          "harness": "claude-code",
          "display_name": "Claude Code",
          "detected": true,
          "installed": true,
          "fidelity": "L3",
          "transport": "hooks",
          "last_event_at": "2026-11-22T19:59:30Z",
          "events_24h": 42,
          "adapter_version": "1.2.0",
          "notes": "Hooks registered"
        },
        {
          "harness": "dsh",
          "display_name": "DeepSeek Harness",
          "detected": true,
          "installed": false,
          "fidelity": "L2",
          "transport": "event-spine",
          "last_event_at": null,
          "events_24h": 0,
          "adapter_version": "",
          "notes": null
        },
        {
          "harness": "goose",
          "display_name": "Goose",
          "detected": false,
          "installed": false,
          "fidelity": "not-a-level",
          "transport": "",
          "last_event_at": null,
          "events_24h": 0,
          "adapter_version": null,
          "notes": null
        }
      ]
    }
    """

    func testFullCoveragePayloadDecodes() throws {
        let report = try SugarJSON.makeDecoder()
            .decode(CoverageReport.self, from: Data(fullPayload.utf8))

        XCTAssertEqual(report.generatedAt, SugarDate.parse("2026-11-22T20:00:00Z"))
        XCTAssertEqual(report.harnesses.count, 3)

        let counts = report.counts
        XCTAssertEqual(counts.l3, 1)
        XCTAssertEqual(counts.l2, 2)
        XCTAssertEqual(counts.l1, 0)
        XCTAssertEqual(counts.l0, 1)
        XCTAssertEqual(counts.total, 4)
        XCTAssertEqual(counts.aboveL0, 3)

        let first = try XCTUnwrap(report.harnesses.first)
        XCTAssertEqual(first.harness, "claude-code")
        XCTAssertEqual(first.displayName, "Claude Code")
        XCTAssertEqual(first.title, "Claude Code")
        XCTAssertTrue(first.detected)
        XCTAssertTrue(first.installed)
        XCTAssertEqual(first.fidelityLevel, .l3)
        XCTAssertEqual(first.transport, "hooks")
        XCTAssertEqual(first.lastEventAt, SugarDate.parse("2026-11-22T19:59:30Z"))
        XCTAssertEqual(first.events24h, 42)
        XCTAssertEqual(first.adapterVersion, "1.2.0")
        XCTAssertEqual(first.notes, "Hooks registered")
        XCTAssertEqual(first.statusText, "detected · installed")

        let second = try XCTUnwrap(report.harnesses.dropFirst().first)
        XCTAssertEqual(second.fidelityLevel, .l2)
        XCTAssertNil(second.lastEventAt)
        XCTAssertEqual(second.adapterVersion, "")
        XCTAssertNil(second.notes)
        XCTAssertEqual(second.statusText, "detected · not installed")

        let third = try XCTUnwrap(report.harnesses.last)
        // Unknown fidelity degrades to L0 (web parity).
        XCTAssertEqual(third.fidelityLevel, .l0)
        XCTAssertEqual(third.statusText, "not detected")
        XCTAssertFalse(third.detected)
        XCTAssertFalse(third.installed)
        XCTAssertFalse(report.isEmpty)
    }

    func testCoverageToleratesMissingFieldsAndUnknownLevels() throws {
        let json = """
        {
          "summary": {"L3": ["a"], "L9": ["unknown-level"]},
          "harnesses": [{"harness": "minimal"}]
        }
        """
        let report = try SugarJSON.makeDecoder()
            .decode(CoverageReport.self, from: Data(json.utf8))

        XCTAssertNil(report.generatedAt)
        // Summary arrays are authoritative when present.
        XCTAssertEqual(report.counts.l3, 1)
        XCTAssertEqual(report.counts.total, 1)
        XCTAssertEqual(report.summary.harnesses(at: .l1), [])

        let harness = try XCTUnwrap(report.harnesses.first)
        XCTAssertEqual(harness.harness, "minimal")
        XCTAssertEqual(harness.title, "minimal")
        XCTAssertFalse(harness.detected)
        XCTAssertFalse(harness.installed)
        XCTAssertEqual(harness.fidelityLevel, .l0)
        XCTAssertEqual(harness.transport, "")
        XCTAssertEqual(harness.events24h, 0)
        XCTAssertNil(harness.lastEventAt)
        XCTAssertNil(harness.adapterVersion)
        XCTAssertNil(harness.notes)
    }

    func testCoverageCountsFallBackToHarnessFidelities() throws {
        let json = """
        {
          "harnesses": [
            {"harness": "a", "fidelity": "L3"},
            {"harness": "b", "fidelity": "L2"},
            {"harness": "c", "fidelity": "L2"},
            {"harness": "d", "fidelity": "L0"}
          ]
        }
        """
        let report = try SugarJSON.makeDecoder()
            .decode(CoverageReport.self, from: Data(json.utf8))

        XCTAssertEqual(report.counts.l3, 1)
        XCTAssertEqual(report.counts.l2, 2)
        XCTAssertEqual(report.counts.l1, 0)
        XCTAssertEqual(report.counts.l0, 1)
        XCTAssertEqual(report.counts.total, 4)
        XCTAssertEqual(report.counts.aboveL0, 3)

        // Empty payload-safe defaults.
        let empty = try SugarJSON.makeDecoder()
            .decode(CoverageReport.self, from: Data("{}".utf8))
        XCTAssertTrue(empty.isEmpty)
        XCTAssertEqual(empty.counts, CoverageCounts())
        XCTAssertNil(empty.generatedAt)
        XCTAssertEqual(empty.harnesses.count, 0)
    }
}

// MARK: - Evidence decoding

final class EvidenceDecodingTests: XCTestCase {

    private let fullPayload = """
    {
      "events": [
        {
          "id": 11,
          "ts": "2026-11-22T20:00:00Z",
          "kind": "verification.check_result",
          "actor": "claude-code",
          "actor_kind": "agent",
          "harness": "claude-code",
          "task_id": "t_1",
          "payload": {
            "status": "passed",
            "payload": {
              "status": "passed",
              "check": "pytest",
              "command": "pytest -q",
              "commit": "abc123"
            }
          },
          "ingested_at": "2026-11-22T20:00:01Z"
        },
        {
          "id": 12,
          "ts": "2026-11-22T20:01:00Z",
          "kind": "policy.decision",
          "harness": "dsh",
          "task_id": "t_2",
          "payload": {"payload": {"message": "allow read"}}
        }
      ],
      "last_id": 12,
      "summary": {
        "total": 2,
        "by_status": {"passed": 1, "blocked": 1},
        "by_kind": {"verification.check_result": 1, "policy.decision": 1}
      }
    }
    """

    func testFullEvidencePayloadDecodes() throws {
        let report = try SugarJSON.makeDecoder()
            .decode(EvidenceReport.self, from: Data(fullPayload.utf8))

        XCTAssertEqual(report.lastId, 12)
        XCTAssertEqual(report.events.count, 2)
        XCTAssertFalse(report.isEmpty)

        let first = try XCTUnwrap(report.events.first)
        XCTAssertEqual(first.id, 11)
        XCTAssertEqual(first.ts, SugarDate.parse("2026-11-22T20:00:00Z"))
        XCTAssertEqual(first.kind, "verification.check_result")
        XCTAssertEqual(first.harness, "claude-code")
        XCTAssertEqual(first.taskId, "t_1")
        XCTAssertEqual(first.actor, "claude-code")
        XCTAssertEqual(first.status, "passed")
        XCTAssertEqual(first.summaryText, "pytest")
        XCTAssertEqual(first.kindGroup, "verification")
        XCTAssertEqual(first.innerPayload?["command"]?.stringValue, "pytest -q")
        XCTAssertEqual(first.ingestedAt, SugarDate.parse("2026-11-22T20:00:01Z"))

        let second = try XCTUnwrap(report.events.last)
        XCTAssertNil(second.status)
        XCTAssertEqual(second.summaryText, "allow read")
        XCTAssertEqual(second.kindGroup, "policy")
        XCTAssertEqual(second.actorLabel, "unknown")

        XCTAssertEqual(report.summary.total, 2)
        XCTAssertEqual(report.summary.statusCount("passed"), 1)
        XCTAssertEqual(report.summary.statusCount("blocked"), 1)
        XCTAssertEqual(report.summary.statusCount("failed"), 0)
        XCTAssertEqual(report.summary.kindCount("policy.decision"), 1)
    }

    func testEvidenceToleratesMissingFieldsAndInnerStatus() throws {
        let json = """
        {
          "events": [
            {"id": 1, "ts": "2026-11-22T20:00:00Z", "kind": "artifact.created"},
            {
              "id": 2,
              "ts": "2026-11-22T20:01:00Z",
              "kind": "approval.granted",
              "payload": {"payload": {"status": "failed"}}
            }
          ]
        }
        """
        let report = try SugarJSON.makeDecoder()
            .decode(EvidenceReport.self, from: Data(json.utf8))

        XCTAssertEqual(report.lastId, 0)
        XCTAssertEqual(report.events.count, 2)

        let first = try XCTUnwrap(report.events.first)
        XCTAssertNil(first.actor)
        XCTAssertNil(first.harness)
        XCTAssertNil(first.taskId)
        XCTAssertEqual(first.payload, .null)
        XCTAssertNil(first.status)
        // No inner fields/status → the kind is the fallback line.
        XCTAssertEqual(first.summaryText, "artifact.created")

        let second = try XCTUnwrap(report.events.last)
        // status falls back to payload.payload.status.
        XCTAssertEqual(second.status, "failed")
        XCTAssertEqual(second.summaryText, "failed")

        XCTAssertEqual(report.summary.total, 0)
    }

    func testEvidenceSummaryTextPriority() throws {
        func event(payload: String) throws -> EvidenceEvent {
            let json = """
            {"id": 1, "ts": "2026-11-22T20:00:00Z", "kind": "verification.review", "payload": \(payload)}
            """
            return try SugarJSON.makeDecoder()
                .decode(EvidenceEvent.self, from: Data(json.utf8))
        }

        XCTAssertEqual(
            try event(payload: #"{"payload": {"check": "lint", "command": "make lint", "title": "T"}}"#).summaryText,
            "lint"
        )
        XCTAssertEqual(
            try event(payload: #"{"payload": {"command": "make lint", "title": "T"}}"#).summaryText,
            "make lint"
        )
        XCTAssertEqual(
            try event(payload: #"{"payload": {"title": "Review passed", "message": "M"}}"#).summaryText,
            "Review passed"
        )
        XCTAssertEqual(
            try event(payload: #"{"payload": {"message": "Looks good"}}"#).summaryText,
            "Looks good"
        )
        XCTAssertEqual(
            try event(payload: #"{"payload": {"commit": "deadbeef"}}"#).summaryText,
            "deadbeef"
        )
        // Outer tool_name is the web panel's fallback after the inner object.
        XCTAssertEqual(
            try event(payload: #"{"tool_name": "bash", "payload": {}}"#).summaryText,
            "bash"
        )
        // Blank inner values are ignored.
        XCTAssertEqual(
            try event(payload: #"{"payload": {"check": "   ", "command": "swift test"}}"#).summaryText,
            "swift test"
        )
    }
}

// MARK: - Evidence query construction / filters

final class EvidenceQueryTests: XCTestCase {
    private let base = URL(string: "http://127.0.0.1:7331")!

    func testURLConstructionPercentEncodesFilters() {
        let query = EvidenceQuery(
            taskID: " task id&x ",
            kind: "policy.decision",
            status: "failed",
            limit: 100,
            offset: 0
        )
        let url = query.url(relativeTo: base)

        XCTAssertEqual(
            url.absoluteString,
            "http://127.0.0.1:7331/api/evidence?limit=100&offset=0&task_id=task%20id%26x&kind=policy.decision&status=failed"
        )
    }

    func testURLConstructionOmitsEmptyFiltersAndClampsLimit() {
        let query = EvidenceQuery(limit: 0, offset: -5)
        XCTAssertEqual(
            query.url(relativeTo: base).absoluteString,
            "http://127.0.0.1:7331/api/evidence?limit=1&offset=0"
        )

        let huge = EvidenceQuery(limit: 9_999)
        XCTAssertEqual(huge.limit, 500)
        XCTAssertEqual(huge.taskID, nil)
        XCTAssertEqual(huge.kind, nil)
        XCTAssertEqual(huge.status, nil)
        XCTAssertEqual(
            huge.queryItems.map(\.name),
            ["limit", "offset"]
        )
    }

    func testFilterEnumsMapToQueryValues() {
        XCTAssertNil(EvidenceKindFilter.all.queryValue)
        XCTAssertEqual(EvidenceKindFilter.all.title, "All kinds")
        XCTAssertEqual(EvidenceKindFilter.checkResult.queryValue, "verification.check_result")
        XCTAssertEqual(EvidenceKindFilter.commitLinked.queryValue, "commit.linked")

        XCTAssertNil(EvidenceStatusFilter.all.queryValue)
        XCTAssertEqual(EvidenceStatusFilter.all.title, "All statuses")
        XCTAssertEqual(EvidenceStatusFilter.passed.queryValue, "passed")
        XCTAssertEqual(EvidenceStatusFilter.error.queryValue, "error")
    }
}

// MARK: - Coverage / evidence presentation helpers

final class CoveragePresentationTests: XCTestCase {
    func testFidelityNormalizationAndHelp() {
        XCTAssertEqual(CoverageFidelity.normalize("l3"), .l3)
        XCTAssertEqual(CoverageFidelity.normalize(" L2 "), .l2)
        XCTAssertEqual(CoverageFidelity.normalize("nope"), .l0)
        XCTAssertEqual(CoverageFidelity.normalize(nil), .l0)

        XCTAssertEqual(CoverageFidelity.l3.help, "tool args/results")
        XCTAssertEqual(CoverageFidelity.l2.help, "tool names/status")
        XCTAssertEqual(CoverageFidelity.l1.help, "session/turn/run")
        XCTAssertEqual(CoverageFidelity.l0.help, "board/leases only")
    }
}

// MARK: - AppSection migration / AppModel state

@MainActor
final class CoverageEvidenceAppModelTests: XCTestCase {
    func testRestoreSectionHandlesLegacyNewAndUnknownValues() {
        let model = AppModel()

        for section in AppSection.allCases {
            model.restoreSection(raw: section.rawValue)
            XCTAssertEqual(model.activeSection, section)
        }

        // Legacy persisted values from earlier builds still restore.
        model.restoreSection(raw: "live")
        XCTAssertEqual(model.activeSection, .live)
        model.restoreSection(raw: "trends")
        XCTAssertEqual(model.activeSection, .trends)

        // Future/unknown values must not crash; they fall back to Tasks.
        model.restoreSection(raw: "not-a-section")
        XCTAssertEqual(model.activeSection, .tasks)
        XCTAssertFalse(model.showsCoverage)
        XCTAssertFalse(model.showsEvidence)
    }

    func testNewSectionsAreMutuallyExclusive() {
        let model = AppModel()

        model.selectSection(.coverage)
        XCTAssertTrue(model.showsCoverage)
        XCTAssertFalse(model.showsEvidence)
        XCTAssertFalse(model.showsTrends)
        XCTAssertEqual(model.activeSection, .coverage)

        model.showEvidence()
        XCTAssertTrue(model.showsEvidence)
        XCTAssertFalse(model.showsCoverage)
        XCTAssertEqual(model.activeSection, .evidence)

        model.selectSection(.tasks)
        XCTAssertFalse(model.showsCoverage)
        XCTAssertFalse(model.showsEvidence)
        XCTAssertEqual(model.activeSection, .tasks)
    }

    func testEvidenceQueryKeyTracksFilters() {
        let model = AppModel()
        let initial = model.evidenceQueryKey
        XCTAssertEqual(initial, "|all|all")

        model.evidenceTaskID = " t_1 "
        model.evidenceKind = .checkResult
        model.evidenceStatus = .failed
        XCTAssertEqual(model.evidenceQueryKey, "t_1|verification.check_result|failed")

        let query = model.evidenceQuery
        XCTAssertEqual(query.taskID, "t_1")
        XCTAssertEqual(query.kind, "verification.check_result")
        XCTAssertEqual(query.status, "failed")
        XCTAssertEqual(query.limit, 100)
    }

    func testUnavailableEndpointStatusMapping() {
        XCTAssertTrue(AppModel.isUnavailableEndpointError(
            APIClient.APIError.server(status: 404, code: "not_found", message: "missing")
        ))
        XCTAssertTrue(AppModel.isUnavailableEndpointError(
            APIClient.APIError.server(status: 405, code: nil, message: "not allowed")
        ))
        XCTAssertTrue(AppModel.isUnavailableEndpointError(
            APIClient.APIError.server(status: 503, code: "spine_unavailable", message: "no spine")
        ))
        XCTAssertFalse(AppModel.isUnavailableEndpointError(
            APIClient.APIError.server(status: 500, code: "internal", message: "boom")
        ))
        XCTAssertFalse(AppModel.isUnavailableEndpointError(
            APIClient.APIError.transport("offline")
        ))
        XCTAssertFalse(AppModel.isUnavailableEndpointError(URLError(.timedOut)))
    }

    func testCoverageAndEvidenceStartEmpty() {
        let model = AppModel()
        XCTAssertNil(model.coverage)
        XCTAssertNil(model.evidence)
        XCTAssertNil(model.coverageError)
        XCTAssertNil(model.evidenceError)
        XCTAssertFalse(model.coverageLoading)
        XCTAssertFalse(model.evidenceLoading)
        XCTAssertFalse(model.coverageUnavailable)
        XCTAssertFalse(model.evidenceUnavailable)
        XCTAssertEqual(model.evidenceKind, .all)
        XCTAssertEqual(model.evidenceStatus, .all)
    }
}
