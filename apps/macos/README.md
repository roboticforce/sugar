# SugarDash — native macOS dashboard for the Sugar work queue

A small, dependency-free SwiftUI app that talks to the local Sugar dashboard
backend over REST + Server-Sent Events. It is a **client of the database**, not a
second storage layer: `sugar/dashboard/` owns all writes, and the app simply
observes and edits through it.

Contract: [`docs/local-dashboard.md`](../../docs/local-dashboard.md) (frozen,
contract v1). Any disagreement between that document and this app is a bug in
the app.

## Requirements

- macOS 14 or newer (developed against macOS 26 / Swift 6.4, Xcode 27.1).
- The dashboard backend running locally:
  `pip install 'sugarai[dashboard]'`, then from your project directory:

  ```bash
  sugar dashboard          # http://127.0.0.1:7331 by default
  ```

  (Loopback only; v1 has no authentication.) Without the backend the app shows a
  clear empty state with the configured URL and a **Reconnect** button.
- No third-party Swift dependencies, no network access at build time.

## Build

```bash
cd apps/macos
swift build -c release      # plain SwiftPM build
./build-app.sh              # builds release + assembles dist/SugarDash.app
```

`build-app.sh` produces:

```
dist/SugarDash.app/
└── Contents/
    ├── Info.plist            # io.roboticforce.sugardash, minimum macOS 14.0
    ├── MacOS/SugarDash       # release binary
    └── Resources/
```

and ad-hoc signs it (`codesign -s - --force --deep`). The bundle is launchable
with `open dist/SugarDash.app`; it is not notarized or signed with a Developer ID,
which is expected for a local/dev build.

The script also disables SwiftPM's manifest sandbox and points its caches at
`/tmp/sugar-swift-cache` (override with `SUGAR_SWIFT_CACHE=...`). Those flags make
the build reproducible in restricted environments (CI containers, agent
sandboxes) where nested sandboxes / the default clang module cache are denied.
They are harmless for normal local builds.

## Run

```bash
open dist/SugarDash.app
# or run the release binary directly (window + menu bar extra):
.build/release/SugarDash
```

The app opens one window:

- **Sidebar** — status filters (All / Pending / Active / Hold / Completed /
  Failed) with live counters, the server URL field (persisted in
  `UserDefaults`), connection state, and the server's DB path/version.
- **Task list** — priority, status, type, title, assigned agent, attempts and
  relative update time; searchable and filterable.
- **Detail** — full task facts, description, context, acceptance criteria,
  result, error, thinking summary.
- **Toolbar** — New Task (⌘N), Refresh (⌘R), Reconnect, connection dot.

Features:

- Live updates over SSE (`/api/events`); no polling loop. Reconnects with
  1 s → 10 s backoff and treats the new `snapshot` as authoritative.
- Full CRUD: create, edit (including status transitions), hold/release, delete
  with a confirmation dialog.
- Type picker populated from `GET /api/task-types`; priority 1 (Urgent) – 5
  (Minimal).
- Read-only mode (`sugar dashboard --read-only`) is detected via `/api/health`
  and disables all write actions.
- Menu bar extra shows the pending count; its menu has Open Dashboard,
  Reconnect, and Quit.
- Unreachable server → empty state showing the URL + `sugar dashboard` hint +
  Reconnect.

## Tests

Decoding is covered by XCTest fixtures copied verbatim from the contract
(`Tests/SugarDashTests/DecodingTests.swift`): a null-heavy task, the SSE snapshot
with stats, and an ISO-8601 `Z` timestamp.

```bash
swift test
```

In a sandboxed/restricted shell, use the same flags as `build-app.sh`:

```bash
export CLANG_MODULE_CACHE_PATH=/tmp/sugar-swift-cache/clang
export SWIFT_MODULECACHE_PATH=/tmp/sugar-swift-cache/swift
swift test --disable-sandbox \
  --cache-path /tmp/sugar-swift-cache/spm \
  --config-path /tmp/sugar-swift-cache/spmconf
```

## Layout

```
apps/macos/
├── Package.swift                     # swift-tools-version 6.0, macOS 14, language mode v5
├── Sources/SugarDash/
│   ├── SugarDashApp.swift            # @main, WindowGroup + MenuBarExtra
│   ├── Models.swift                  # Task / Stats / TaskType / Health + JSON & date decoding
│   ├── APIClient.swift               # REST + SSE (URLSession async bytes/lines)
│   ├── AppModel.swift                # @MainActor @Observable store
│   ├── ContentView.swift             # NavigationSplitView + list/sidebar/toolbar
│   ├── TaskDetailView.swift          # read-only detail + actions
│   └── TaskEditorView.swift          # add/edit sheet
├── Tests/SugarDashTests/
│   └── DecodingTests.swift
├── build-app.sh
└── README.md
```

## Notes / v1 limitations

- One connection per app instance; no multi-server support.
- No auth/TLS/remote access — matches the backend's loopback-only v1 scope.
- "Hold" sends a fixed reason (`Held from the SugarDash app`); the v1 UI has no
  reason prompt.
- The editor's assigned-agent field sends `""` to clear an assignment (the
  contract does not define a dedicated clear semantics).
