import Charts
import SwiftUI

// MARK: - Trends pane

/// Middle pane for the "Trends" sidebar row. Renders `GET /api/trends` as a
/// throughput/failures/events/cost time series plus per-group totals, backed by
/// Swift Charts (system framework, no dependencies).
struct TrendsPane: View {
    @Environment(AppModel.self) private var model

    @State private var selectedBucket: Date?

    var body: some View {
        VStack(spacing: 0) {
            controls
            Divider()
            content
        }
        .navigationTitle("Trends")
        // Restarts on range/dimension change and cancels the ~30 s refresh loop
        // when the pane disappears (no polling while hidden).
        .task(id: model.trendsQueryKey) {
            await model.runTrendsRefreshLoop()
        }
    }

    // MARK: Controls

    private var controls: some View {
        @Bindable var model = model

        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Picker("Range", selection: $model.trendsRange) {
                    ForEach(TrendsRange.allCases) { range in
                        Text(range.title).tag(range)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 150)
                .help("Time range: 24 hours, 7 days or 30 days")

                Picker("Group by", selection: $model.trendsDimension) {
                    ForEach(TrendsDimension.allCases) { dimension in
                        Text(dimension.title).tag(dimension)
                    }
                }
                .pickerStyle(.menu)
                .fixedSize()
                .help("Group the series by agent, project or harness")

                Spacer(minLength: 4)

                if model.trendsLoading {
                    ProgressView()
                        .controlSize(.small)
                }

                Button {
                    model.refreshTrends()
                } label: {
                    Label("Refresh trends", systemImage: "arrow.clockwise")
                }
                .help("Reload trends now")
            }

            HStack(spacing: 8) {
                if let trends = model.trends {
                    Text("\(TrendsFormat.count(trends.eventCount)) events")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if trends.truncated {
                        Label("truncated", systemImage: "exclamationmark.triangle")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                            .help("The server limited the number of series; totals cover the returned series only.")
                    }

                    Text("· \(trends.bucket) buckets")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }

                if let error = model.trendsError, model.trends != nil {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .lineLimit(1)
                        .help(error)
                    Button("Retry") { model.refreshTrends() }
                        .font(.caption2)
                }
            }
            .frame(height: 14)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
    }

    // MARK: Content states

    @ViewBuilder
    private var content: some View {
        if let trends = model.trends {
            if trends.isEmpty {
                emptyState
            } else {
                trendsContent(trends)
            }
        } else if model.trendsUnavailable {
            unavailableState
        } else if let error = model.trendsError {
            errorState(error)
        } else {
            loadingState
        }
    }

    private var loadingState: some View {
        VStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading trends…")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var unavailableState: some View {
        ContentUnavailableView {
            Label("Trends aren't available on this server", systemImage: "chart.line.uptrend.xyaxis")
        } description: {
            Text("This dashboard build doesn't expose `/api/trends` yet. Update Sugar and restart `sugar dashboard`, then retry.")
        } actions: {
            Button("Retry") { model.refreshTrends() }
        }
    }

    private func errorState(_ message: String) -> some View {
        ContentUnavailableView {
            Label("Couldn't load trends", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
                .textSelection(.enabled)
        } actions: {
            Button("Retry") { model.refreshTrends() }
        }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No activity in this range", systemImage: "chart.bar")
        } description: {
            Text("Nothing was recorded in the selected window. Try a wider range or wait for agents to report activity.")
        } actions: {
            Button("Refresh") { model.refreshTrends() }
        }
    }

    // MARK: Content

    private func trendsContent(_ trends: TrendsResponse) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                statTiles(trends)
                mainChartCard(trends)
                barChartCard(trends)
                footnote(trends)
            }
            .padding(16)
        }
    }

    private func statTiles(_ trends: TrendsResponse) -> some View {
        let totals = trends.totals
        let failureRate = TrendsMath.failureRate(totals)
        let cycleTime = TrendsMath.averageCycleTime(series: trends.series)

        return LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 145), spacing: 10, alignment: .top)],
            alignment: .leading,
            spacing: 10
        ) {
            TrendsStatTile(
                title: "Completed",
                value: TrendsFormat.count(totals.tasksCompleted),
                icon: "checkmark.circle.fill",
                color: .green,
                help: "Tasks completed in this range"
            )
            TrendsStatTile(
                title: "Failure rate",
                value: TrendsFormat.percent(failureRate),
                icon: "xmark.octagon.fill",
                color: (failureRate ?? 0) > 0.25 ? .red : .orange,
                help: "Failed ÷ (completed + failed) in this range"
            )
            TrendsStatTile(
                title: "Avg cycle",
                value: TrendsFormat.duration(cycleTime),
                icon: "clock.arrow.circlepath",
                color: .blue,
                help: "Completed-weighted average cycle time across the returned series"
            )
            TrendsStatTile(
                title: "Cost",
                value: TrendsFormat.currency(totals.costUsd),
                icon: "dollarsign.circle.fill",
                color: .mint,
                help: "Harness-reported cost in this range"
            )
            TrendsStatTile(
                title: "Events",
                value: TrendsFormat.count(totals.events),
                icon: "waveform.path.ecg",
                color: .purple,
                help: "Activity-spine events in this range"
            )
        }
    }

    // MARK: Main chart

    private func mainChartCard(_ trends: TrendsResponse) -> some View {
        @Bindable var model = model

        let keys = trends.series.map(\.key)
        let colors = keys.map(TrendsPalette.color(for:))
        let buckets = chartBuckets(trends)
        let snappedSelection = selectedBucket.flatMap { snappedBucket($0, in: buckets) }

        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Over time")
                    .font(.headline)

                Spacer(minLength: 8)

                Text(model.trendsMetric.title)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Picker("Metric", selection: $model.trendsMetric) {
                ForEach(TrendsMetric.allCases) { metric in
                    Text(metric.shortTitle).tag(metric)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .help("Metric plotted on the main chart")

            if trends.series.isEmpty {
                Text("No series in this range.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Chart {
                    ForEach(trends.series) { series in
                        ForEach(series.alignedPoints(buckets: buckets)) { point in
                            LineMark(
                                x: .value("Bucket", point.bucket),
                                y: .value(model.trendsMetric.title, model.trendsMetric.value(for: point))
                            )
                            .foregroundStyle(by: .value("Group", series.key))
                            .interpolationMethod(.monotone)
                            .lineStyle(StrokeStyle(lineWidth: 2))

                            AreaMark(
                                x: .value("Bucket", point.bucket),
                                y: .value(model.trendsMetric.title, model.trendsMetric.value(for: point)),
                                stacking: .unstacked
                            )
                            .foregroundStyle(by: .value("Group", series.key))
                            .interpolationMethod(.monotone)
                            .opacity(0.08)
                        }
                    }

                    if let snappedSelection {
                        RuleMark(x: .value("Bucket", snappedSelection))
                            .foregroundStyle(.secondary.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                            .annotation(position: .top, alignment: .leading, spacing: 6) {
                                tooltip(trends, bucket: snappedSelection)
                            }
                    }
                }
                .chartForegroundStyleScale(domain: keys, range: colors)
                .chartLegend(position: .bottom, alignment: .leading, spacing: 6)
                .chartXSelection(value: $selectedBucket)
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 6)) { value in
                        AxisGridLine()
                        AxisTick()
                        AxisValueLabel {
                            if let date = value.as(Date.self) {
                                Text(TrendsFormat.bucketLabel(date, bucket: trends.bucket))
                                    .font(.system(size: 9))
                            }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let raw = value.as(Double.self) {
                                Text(formattedValue(raw))
                                    .font(.system(size: 9))
                            }
                        }
                    }
                }
                .frame(height: 300)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
    }

    // MARK: Secondary chart

    private func barChartCard(_ trends: TrendsResponse) -> some View {
        let rows = trends.series
            .map { TrendsGroupTotal(key: $0.key, value: model.trendsMetric.total(in: $0.totals)) }
            .sorted { $0.value > $1.value }
            .prefix(10)
            .map { $0 }

        let keys = trends.series.map(\.key)
        let colors = keys.map(TrendsPalette.color(for:))

        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Totals by \(model.trendsDimension.title.lowercased())")
                    .font(.headline)
                Spacer()
                Text(model.trendsMetric.shortTitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            if rows.isEmpty || rows.allSatisfy({ $0.value == 0 }) {
                Text("No \(model.trendsMetric.shortTitle.lowercased()) recorded in this range.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(height: 80, alignment: .leading)
            } else {
                Chart(rows) { row in
                    BarMark(
                        x: .value(model.trendsMetric.title, row.value),
                        y: .value("Group", row.key)
                    )
                    .foregroundStyle(by: .value("Group", row.key))
                    .cornerRadius(4)
                }
                .chartForegroundStyleScale(domain: keys, range: colors)
                .chartLegend(.hidden)
                .chartXAxis {
                    AxisMarks { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let raw = value.as(Double.self) {
                                Text(formattedValue(raw))
                                    .font(.system(size: 9))
                            }
                        }
                    }
                }
                .frame(height: max(140, CGFloat(rows.count) * 28 + 30))
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
    }

    private func footnote(_ trends: TrendsResponse) -> some View {
        HStack(spacing: 8) {
            if let since = trends.since, let until = trends.until {
                Text("\(TaskFormat.timestamp(since)) → \(TaskFormat.timestamp(until))")
            }
            Spacer()
            Text("\(trends.series.count) series")
            if trends.truncated {
                Text("· server-truncated")
            }
        }
        .font(.caption2)
        .foregroundStyle(.tertiary)
        .padding(.horizontal, 4)
    }

    // MARK: Helpers

    private func formattedValue(_ value: Double) -> String {
        model.trendsMetric.isCurrency
            ? TrendsFormat.currency(value)
            : TrendsFormat.count(value)
    }

    private func chartBuckets(_ trends: TrendsResponse) -> [Date] {
        if !trends.buckets.isEmpty {
            return trends.buckets.sorted()
        }
        return Set(trends.series.flatMap { $0.points.map(\.bucket) }).sorted()
    }

    private func snappedBucket(_ date: Date, in buckets: [Date]) -> Date? {
        buckets.min { lhs, rhs in
            abs(lhs.timeIntervalSince(date)) < abs(rhs.timeIntervalSince(date))
        }
    }

    @ViewBuilder
    private func tooltip(_ trends: TrendsResponse, bucket: Date) -> some View {
        let rows = trends.series.compactMap { series -> TrendsTooltipRow? in
            guard let point = series.point(at: bucket) else { return nil }
            return TrendsTooltipRow(
                key: series.key,
                value: model.trendsMetric.value(for: point),
                color: TrendsPalette.color(for: series.key)
            )
        }
        .sorted { $0.value > $1.value }

        let shown = Array(rows.prefix(6))

        VStack(alignment: .leading, spacing: 3) {
            Text(TrendsFormat.bucketLabel(bucket, bucket: trends.bucket))
                .font(.caption2.weight(.semibold))

            ForEach(shown) { row in
                HStack(spacing: 5) {
                    Circle()
                        .fill(row.color)
                        .frame(width: 6, height: 6)
                    Text(row.key)
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(formattedValue(row.value))
                        .font(.system(size: 9, weight: .medium).monospacedDigit())
                }
            }

            if rows.count > shown.count {
                Text("+\(rows.count - shown.count) more")
                    .font(.system(size: 8))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(7)
        .frame(maxWidth: 220, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 7).fill(.regularMaterial))
        .overlay(
            RoundedRectangle(cornerRadius: 7)
                .stroke(Color.secondary.opacity(0.2), lineWidth: 1)
        )
    }
}

// MARK: - Supporting views / data

private struct TrendsStatTile: View {
    let title: String
    let value: String
    let icon: String
    let color: Color
    let help: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.caption)
                    .foregroundStyle(color)
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Text(value)
                .font(.system(size: 22, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(color.opacity(0.18), lineWidth: 1))
        .help(help)
    }
}

private struct TrendsGroupTotal: Identifiable {
    let key: String
    let value: Double
    var id: String { key }
}

private struct TrendsTooltipRow: Identifiable {
    let key: String
    let value: Double
    let color: Color
    var id: String { key }
}

/// Stable auto-colouring for group series, shared by both charts and the
/// tooltip so a group keeps the same colour everywhere.
enum TrendsPalette {
    static func color(for key: String) -> Color {
        var hash: UInt64 = 1_469_598_103_934_665_603
        for byte in key.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 1_099_511_628_211
        }
        let hue = Double(hash % 360) / 360
        return Color(hue: hue, saturation: 0.62, brightness: 0.92)
    }
}
