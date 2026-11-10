import SwiftUI
import XCTest
@testable import SugarDash

// MARK: - Trends decoding

final class TrendsDecodingTests: XCTestCase {

    private let fullPayload = """
    {
      "since": "2026-11-02T00:00:00Z",
      "until": "2026-11-09T00:00:00Z",
      "bucket": "day",
      "group_by": "actor",
      "buckets": [
        "2026-11-08T00:00:00Z",
        "2026-11-09T00:00:00Z"
      ],
      "series": [
        {
          "key": "claude-code",
          "points": [
            {
              "bucket": "2026-11-08T00:00:00Z",
              "tasks_created": 2,
              "tasks_claimed": 3,
              "tasks_completed": 1,
              "tasks_failed": 0,
              "events": 10,
              "tokens_in": 100,
              "tokens_out": 50,
              "cost_usd": 0.12
            },
            {
              "bucket": "2026-11-09T00:00:00Z",
              "tasks_created": 1,
              "tasks_claimed": 1,
              "tasks_completed": 2,
              "tasks_failed": 1,
              "events": 8,
              "tokens_in": 80,
              "tokens_out": 40,
              "cost_usd": 0.08
            }
          ],
          "totals": {
            "tasks_created": 3,
            "tasks_claimed": 4,
            "tasks_completed": 3,
            "tasks_failed": 1,
            "events": 18,
            "tokens_in": 180,
            "tokens_out": 90,
            "cost_usd": 0.20
          },
          "avg_cycle_time_s": 372.5
        },
        {
          "key": "dsh",
          "points": [
            {
              "bucket": "2026-11-08T00:00:00Z",
              "tasks_completed": 2,
              "tasks_failed": 0,
              "events": 4,
              "cost_usd": 0.01
            }
          ],
          "totals": {
            "tasks_completed": 2,
            "tasks_failed": 0,
            "events": 4,
            "cost_usd": 0.01
          },
          "avg_cycle_time_s": null
        }
      ],
      "totals": {
        "tasks_created": 3,
        "tasks_claimed": 4,
        "tasks_completed": 5,
        "tasks_failed": 1,
        "events": 22,
        "tokens_in": 180,
        "tokens_out": 90,
        "cost_usd": 0.21
      },
      "event_count": 22,
      "truncated": true
    }
    """

    func testFullTrendsPayloadDecodes() throws {
        let response = try SugarJSON.makeDecoder()
            .decode(TrendsResponse.self, from: Data(fullPayload.utf8))

        XCTAssertEqual(response.bucket, "day")
        XCTAssertEqual(response.groupBy, "actor")
        XCTAssertEqual(response.buckets, [
            try XCTUnwrap(SugarDate.parse("2026-11-08T00:00:00Z")),
            try XCTUnwrap(SugarDate.parse("2026-11-09T00:00:00Z")),
        ])
        XCTAssertEqual(response.eventCount, 22)
        XCTAssertTrue(response.truncated)
        XCTAssertEqual(response.series.count, 2)

        let first = try XCTUnwrap(response.series.first)
        XCTAssertEqual(first.key, "claude-code")
        XCTAssertEqual(first.points.count, 2)
        XCTAssertEqual(try XCTUnwrap(first.avgCycleTimeS), 372.5, accuracy: 0.0001)
        XCTAssertEqual(first.totals.tasksCompleted, 3)
        XCTAssertEqual(first.totals.tasksFailed, 1)
        XCTAssertEqual(first.totals.events, 18)
        XCTAssertEqual(first.totals.costUsd, 0.20, accuracy: 0.0001)
        XCTAssertEqual(first.points.last?.tasksFailed, 1)
        XCTAssertEqual(try XCTUnwrap(first.points.last?.costUsd), 0.08, accuracy: 0.0001)

        let second = try XCTUnwrap(response.series.last)
        XCTAssertNil(second.avgCycleTimeS)
        XCTAssertEqual(second.totals.tasksCreated, 0)
        XCTAssertEqual(second.totals.events, 4)

        XCTAssertEqual(response.totals.tasksCompleted, 5)
        XCTAssertEqual(response.totals.costUsd, 0.21, accuracy: 0.0001)
        XCTAssertFalse(response.isEmpty)
    }

    func testTolerantMissingFieldsDecode() throws {
        let json = """
        {
          "series": [
            {"key": "minimal", "points": [{"bucket": "2026-11-09T20:00:00Z"}]}
          ]
        }
        """
        let response = try SugarJSON.makeDecoder()
            .decode(TrendsResponse.self, from: Data(json.utf8))

        // Contract defaults for everything the payload omitted.
        XCTAssertNil(response.since)
        XCTAssertNil(response.until)
        XCTAssertEqual(response.bucket, "day")
        XCTAssertEqual(response.groupBy, "actor")
        XCTAssertTrue(response.buckets.isEmpty)
        XCTAssertEqual(response.eventCount, 0)
        XCTAssertFalse(response.truncated)
        XCTAssertEqual(response.totals, TrendTotals())
        XCTAssertTrue(response.isEmpty)

        let series = try XCTUnwrap(response.series.first)
        XCTAssertEqual(series.avgCycleTimeS, nil)
        XCTAssertEqual(series.totals, TrendTotals())
        XCTAssertEqual(series.points.count, 1)
        XCTAssertEqual(series.points.first?.bucket, SugarDate.parse("2026-11-09T20:00:00Z"))
        XCTAssertEqual(series.points.first?.events, 0)
        XCTAssertEqual(series.points.first?.costUsd, 0)
    }

    func testZeroFilledPointsAlignment() throws {
        let buckets = [
            try XCTUnwrap(SugarDate.parse("2026-11-09T18:00:00Z")),
            try XCTUnwrap(SugarDate.parse("2026-11-09T19:00:00Z")),
            try XCTUnwrap(SugarDate.parse("2026-11-09T20:00:00Z")),
            try XCTUnwrap(SugarDate.parse("2026-11-09T21:00:00Z")),
        ]
        let series = TrendSeries(
            key: "dsh",
            points: [
                TrendPoint(bucket: buckets[0], tasksCompleted: 2, events: 3, costUsd: 0.05),
                TrendPoint(bucket: buckets[2], tasksCompleted: 1, events: 7, costUsd: 0.02),
            ],
            totals: TrendTotals(tasksCompleted: 3, events: 10, costUsd: 0.07)
        )

        let aligned = series.alignedPoints(buckets: buckets)

        XCTAssertEqual(aligned.count, 4)
        XCTAssertEqual(aligned.map(\.bucket), buckets)
        XCTAssertEqual(aligned[0].tasksCompleted, 2)
        XCTAssertEqual(aligned[1].tasksCompleted, 0) // inserted zero-fill
        XCTAssertEqual(aligned[1].events, 0)
        XCTAssertEqual(aligned[2].events, 7)
        XCTAssertEqual(aligned[3].costUsd, 0) // inserted zero-fill
        XCTAssertNil(series.point(at: buckets[1])) // absent point is not synthesized
        XCTAssertEqual(series.point(at: buckets[2])?.events, 7)
    }

    func testEmptyResponse() {
        let empty = TrendsResponse()
        XCTAssertTrue(empty.isEmpty)

        let decoded = try? SugarJSON.makeDecoder().decode(
            TrendsResponse.self,
            from: Data("""
            {"buckets": [], "series": [], "totals": {}, "event_count": 0, "truncated": false}
            """.utf8)
        )
        XCTAssertEqual(decoded?.isEmpty, true)

        let noEventsButSeries = TrendsResponse(
            series: [TrendSeries(key: "x", points: [TrendPoint(bucket: Date(), tasksCompleted: 1)])],
            eventCount: 0
        )
        // A non-empty series still has something to draw, even if events are 0.
        XCTAssertFalse(noEventsButSeries.isEmpty)
    }
}

// MARK: - Range / dimensions / metrics

final class TrendsMathTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testRangeWindowMathUsesInjectedClock() {
        for range in TrendsRange.allCases {
            let window = range.window(endingAt: now)
            XCTAssertEqual(window.until, now)
            XCTAssertEqual(
                window.since.timeIntervalSince(now),
                -range.duration,
                accuracy: 0.001,
                "range \(range.rawValue)"
            )
        }

        XCTAssertEqual(TrendsRange.day.bucket, "hour")
        XCTAssertEqual(TrendsRange.week.bucket, "day")
        XCTAssertEqual(TrendsRange.month.bucket, "day")

        let day = TrendsRange.day.window(endingAt: now)
        XCTAssertEqual(day.since.timeIntervalSince(day.until), -86_400, accuracy: 0.001)
    }

    func testDimensionAPIValuesAndLabels() {
        XCTAssertEqual(TrendsDimension.actor.groupBy, "actor")
        XCTAssertEqual(TrendsDimension.project.groupBy, "project")
        XCTAssertEqual(TrendsDimension.harness.groupBy, "harness")

        XCTAssertEqual(TrendsDimension.actor.title, "Agent")
        XCTAssertEqual(TrendsDimension.project.title, "Project")
        XCTAssertEqual(TrendsDimension.harness.title, "Harness")
    }

    func testMetricMapping() {
        let point = TrendPoint(
            bucket: now,
            tasksCreated: 9,
            tasksCompleted: 4,
            tasksFailed: 2,
            events: 11,
            costUsd: 1.25
        )
        let totals = TrendTotals(
            tasksCreated: 12,
            tasksCompleted: 8,
            tasksFailed: 3,
            events: 30,
            costUsd: 2.50
        )

        XCTAssertEqual(TrendsMetric.completed.value(for: point), 4)
        XCTAssertEqual(TrendsMetric.failed.value(for: point), 2)
        XCTAssertEqual(TrendsMetric.events.value(for: point), 11)
        XCTAssertEqual(TrendsMetric.cost.value(for: point), 1.25, accuracy: 0.0001)

        XCTAssertEqual(TrendsMetric.completed.total(in: totals), 8)
        XCTAssertEqual(TrendsMetric.failed.total(in: totals), 3)
        XCTAssertEqual(TrendsMetric.events.total(in: totals), 30)
        XCTAssertEqual(TrendsMetric.cost.total(in: totals), 2.50, accuracy: 0.0001)

        XCTAssertTrue(TrendsMetric.cost.isCurrency)
        XCTAssertFalse(TrendsMetric.completed.isCurrency)
        XCTAssertEqual(TrendsMetric.completed.title, "Tasks completed")
    }

    func testFailureRateMath() throws {
        XCTAssertEqual(
            try XCTUnwrap(TrendsMath.failureRate(TrendTotals(tasksCompleted: 3, tasksFailed: 1))),
            0.25,
            accuracy: 0.0001
        )
        XCTAssertEqual(
            try XCTUnwrap(TrendsMath.failureRate(TrendTotals(tasksCompleted: 5, tasksFailed: 0))),
            0,
            accuracy: 0.0001
        )
        XCTAssertNil(TrendsMath.failureRate(TrendTotals()))
        XCTAssertNil(TrendsMath.failureRate(TrendTotals(tasksCompleted: 0, tasksFailed: 0)))
    }

    func testAverageCycleTimeIsWeightedByCompleted() throws {
        let series = [
            TrendSeries(
                key: "a",
                totals: TrendTotals(tasksCompleted: 2),
                avgCycleTimeS: 100
            ),
            TrendSeries(
                key: "b",
                totals: TrendTotals(tasksCompleted: 3),
                avgCycleTimeS: 300
            ),
            TrendSeries(key: "missing", totals: TrendTotals(tasksCompleted: 5)),
            TrendSeries(key: "zero", totals: TrendTotals(tasksCompleted: 0), avgCycleTimeS: 999),
        ]

        let average = TrendsMath.averageCycleTime(series: series)
        XCTAssertEqual(try XCTUnwrap(average), 220, accuracy: 0.0001)

        XCTAssertNil(TrendsMath.averageCycleTime(series: []))
        XCTAssertNil(TrendsMath.averageCycleTime(series: [TrendSeries(key: "x")]))
    }

    func testFormatting() {
        XCTAssertEqual(TrendsFormat.count(1_234), "1,234")
        XCTAssertEqual(TrendsFormat.count(0), "0")
        XCTAssertEqual(TrendsFormat.count(987.6), "988")

        XCTAssertEqual(TrendsFormat.currency(0), "$0.00")
        XCTAssertEqual(TrendsFormat.currency(1.234), "$1.23")
        XCTAssertEqual(TrendsFormat.currency(1_234.5), "$1,234.50")
        XCTAssertEqual(TrendsFormat.currency(0.004), "$0.0040")

        XCTAssertEqual(TrendsFormat.percent(0.125), "12.5%")
        XCTAssertEqual(TrendsFormat.percent(0.5), "50%")
        XCTAssertEqual(TrendsFormat.percent(0), "0%")
        XCTAssertEqual(TrendsFormat.percent(nil), "—")

        XCTAssertEqual(TrendsFormat.duration(192), "3m 12s")
        XCTAssertEqual(TrendsFormat.duration(nil), "—")

        let utc = TimeZone(secondsFromGMT: 0)!
        let date = SugarDate.parse("2026-11-09T20:00:00Z")!
        XCTAssertEqual(TrendsFormat.bucketLabel(date, bucket: "hour", timeZone: utc), "20:00")
        XCTAssertEqual(TrendsFormat.bucketLabel(date, bucket: "day", timeZone: utc), "Nov 9")
        XCTAssertEqual(TrendsFormat.bucketLabel(date, bucket: "week", timeZone: utc), "Nov 9")
    }

    func testPaletteIsStable() {
        XCTAssertEqual(TrendsPalette.color(for: "claude-code"), TrendsPalette.color(for: "claude-code"))
        XCTAssertNotEqual(TrendsPalette.color(for: "claude-code"), TrendsPalette.color(for: "dsh"))
    }
}

// MARK: - Persisted section / query key

@MainActor
final class TrendsAppModelTests: XCTestCase {
    func testRestoreSectionRoundTripsAndDefaultsToTasks() {
        let model = AppModel()

        model.restoreSection(raw: AppSection.trends.rawValue)
        XCTAssertTrue(model.showsTrends)
        XCTAssertFalse(model.showsLive)
        XCTAssertFalse(model.showsActivity)
        XCTAssertEqual(model.activeSection, .trends)

        model.restoreSection(raw: AppSection.live.rawValue)
        XCTAssertTrue(model.showsLive)
        XCTAssertFalse(model.showsTrends)
        XCTAssertEqual(model.activeSection, .live)

        model.restoreSection(raw: AppSection.activity.rawValue)
        XCTAssertEqual(model.activeSection, .activity)

        model.restoreSection(raw: "not-a-section")
        XCTAssertEqual(model.activeSection, .tasks)
        XCTAssertFalse(model.showsLive)
        XCTAssertFalse(model.showsActivity)
        XCTAssertFalse(model.showsTrends)
    }

    func testTrendsQueryKeyTracksRangeAndDimension() {
        let model = AppModel()
        XCTAssertEqual(model.trendsQueryKey, "7d-actor")

        model.trendsRange = .month
        model.trendsDimension = .harness
        XCTAssertEqual(model.trendsQueryKey, "30d-harness")

        model.trendsRange = .week
        model.trendsDimension = .project
        XCTAssertEqual(model.trendsQueryKey, "7d-project")
    }

    func testTrendsStateStartsEmpty() {
        let model = AppModel()
        XCTAssertNil(model.trends)
        XCTAssertNil(model.trendsError)
        XCTAssertFalse(model.trendsLoading)
        XCTAssertFalse(model.trendsUnavailable)
        XCTAssertEqual(model.trendsMetric, .completed)
    }
}
