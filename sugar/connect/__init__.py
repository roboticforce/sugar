"""Phase-0 harness adapters: detect, install, and forward telemetry to Sugar.

Modules stay import-light because ``sugar.connect.hook`` runs on every tool call
in some harnesses:

- :mod:`sugar.connect.detect` — read-only harness detection (2s cap);
- :mod:`sugar.connect.installers` — idempotent config merges with backups;
- :mod:`sugar.connect.registry` — atomic ``~/.sugar/adapters.json`` ledger;
- :mod:`sugar.connect.hook` — the fail-open ``sugar hook`` shim;
- :mod:`sugar.connect.doctor` — end-to-end delivery diagnostics;
- :mod:`sugar.connect.cli` — rendering/apply logic for the CLI commands.
"""

__all__: list[str] = []
