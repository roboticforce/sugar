import SwiftUI

// MARK: - Coverage pane

/// Mission Control parity: the adapter coverage matrix (`GET /api/coverage`).
/// Read-only; auto-refreshes every 15 s only while the pane is visible.
struct CoveragePane: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
            footer
        }
        .navigationTitle("Coverage")
        .task { await model.runCoverageRefreshLoop() }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Harness coverage")
                        .font(.title3.weight(.semibold))
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                if model.coverageLoading {
                    ProgressView()
                        .controlSize(.small)
                }

                Button {
                    model.refreshCoverage()
                } label: {
                    Label("Refresh coverage", systemImage: "arrow.clockwise")
                }
                .help("Reload the adapter coverage matrix")
            }

            if let coverage = model.coverage, !coverage.isEmpty {
                summaryChips(counts: coverage.counts)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private var subtitle: String {
        if model.coverageUnavailable {
            return "Fidelity L0–L3 · unavailable on this server"
        }
        if let generated = model.coverage?.generatedAt {
            return "Fidelity L0–L3 · generated \(TaskFormat.relative(generated))"
        }
        if model.coverageLoading {
            return "Fidelity L0–L3 · loading…"
        }
        return "Fidelity L0–L3 · adapter matrix from `sugar connect` + recent telemetry"
    }

    private func summaryChips(counts: CoverageCounts) -> some View {
        HStack(spacing: 6) {
            ForEach(CoverageFidelity.allCases) { level in
                let color = CoveragePresentation.color(level)
                HStack(spacing: 4) {
                    Text("\(counts.count(level))")
                        .fontWeight(.semibold)
                        .monospacedDigit()
                    Text(level.rawValue)
                }
                .font(.caption)
                .foregroundStyle(color)
                .padding(.horizontal, 8)
                .padding(.vertical, 2)
                .background(Capsule().fill(color.opacity(0.13)))
                .help("\(level.rawValue) = \(level.help)")
            }

            Text("· \(counts.total) \(counts.total == 1 ? "harness" : "harnesses")")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Content states

    @ViewBuilder
    private var content: some View {
        if let coverage = model.coverage {
            if coverage.harnesses.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if let error = model.coverageError {
                            inlineError(error)
                        }
                        ForEach(coverage.harnesses) { harness in
                            CoverageHarnessRow(harness: harness)
                        }
                    }
                    .padding(16)
                }
            }
        } else if model.coverageUnavailable {
            unavailableState
        } else if let error = model.coverageError {
            errorState(error)
        } else {
            loadingState
        }
    }

    private var loadingState: some View {
        VStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading coverage…")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No harnesses reported", systemImage: "checkmark.shield")
        } description: {
            Text("Run `sugar connect` to register adapters, or let a harness emit telemetry to the event spine.")
        } actions: {
            Button("Refresh") { model.refreshCoverage() }
        }
    }

    private var unavailableState: some View {
        ContentUnavailableView {
            Label("Coverage unavailable on this server", systemImage: "checkmark.shield")
        } description: {
            Text("This dashboard build doesn't expose `/api/coverage` yet. Update Sugar and restart `sugar dashboard`, then retry.")
        } actions: {
            Button("Retry") { model.refreshCoverage() }
        }
    }

    private func errorState(_ message: String) -> some View {
        ContentUnavailableView {
            Label("Couldn't load coverage", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
                .textSelection(.enabled)
        } actions: {
            Button("Retry") { model.refreshCoverage() }
        }
    }

    private func inlineError(_ message: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            Text(message)
                .font(.caption)
                .lineLimit(2)
            Spacer()
            Button("Retry") { model.refreshCoverage() }
                .font(.caption)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.10)))
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 8) {
            if let fetchedAt = model.coverageFetchedAt {
                Text("updated \(TaskFormat.relative(fetchedAt))")
            }
            Spacer()
            Text("auto-refresh 15s")
        }
        .font(.caption2)
        .foregroundStyle(.tertiary)
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }
}

// MARK: - One harness row

private struct CoverageHarnessRow: View {
    let harness: CoverageHarness

    private var levelColor: Color {
        CoveragePresentation.color(harness.fidelityLevel)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(harness.title)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)

                HStack(spacing: 4) {
                    Circle()
                        .fill(harness.detected ? Color.green : Color.secondary)
                        .frame(width: 6, height: 6)
                    Text(harness.statusText)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                Text(harness.fidelityLevel.rawValue)
                    .font(.caption2.weight(.bold).monospacedDigit())
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .foregroundStyle(levelColor)
                    .background(Capsule().fill(levelColor.opacity(0.14)))
                    .overlay(Capsule().stroke(levelColor.opacity(0.35), lineWidth: 1))
                    .help("\(harness.fidelityLevel.rawValue) = \(harness.fidelityLevel.help)")
            }

            if !harness.harness.isEmpty, harness.title != harness.harness {
                Text(harness.harness)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            HStack(spacing: 12) {
                if !harness.transport.isEmpty {
                    Label(harness.transport, systemImage: "arrow.left.arrow.right")
                }
                Label("\(harness.events24h) events/24h", systemImage: "waveform.path.ecg")
                if let last = harness.lastEventAt {
                    Text("last event \(TaskFormat.relative(last))")
                        .help(TaskFormat.timestamp(last))
                } else {
                    Text("no events yet")
                }
                if let version = harness.adapterVersion,
                   !version.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text("adapter \(version)")
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
            .lineLimit(1)

            if let notes = harness.notes,
               !notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text(notes)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.06)))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(levelColor.opacity(0.22), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(harness.title), \(harness.statusText), \(harness.fidelityLevel.rawValue) fidelity, \(harness.events24h) events in 24 hours"
        )
    }
}

// MARK: - Presentation helpers

enum CoveragePresentation {
    static func color(_ level: CoverageFidelity) -> Color {
        switch level {
        case .l3: return .green
        case .l2: return .teal
        case .l1: return .yellow
        case .l0: return .red
        }
    }
}
