# Workflow Examples

Practical, copy-pasteable guides for common Sugar use cases.

| Workflow | Description |
|----------|-------------|
| [Security Auto-Fix](./security-auto-fix.md) | Sugar watches for Snyk/Dependabot/GHAS vulnerability issues, patches the dependency or code, and opens a PR automatically |
| [Bug Triage](./bug-triage.md) | Sugar picks up GitHub issues labeled "bug" + "sugar-approved", writes a failing test, implements the fix, and opens a PR |
| [Test Coverage](./test-coverage.md) | Queue or auto-discover modules with low test coverage; Sugar writes targeted tests and verifies the full suite still passes |
| [Code Quality](./code-quality.md) | Queue refactoring and lint cleanup tasks; Sugar fixes systematically and enforces that no behavior changes are introduced |
| [Feature Development](./feature-development.md) | Describe a feature in natural language; Sugar implements it following project conventions stored in memory, writes tests, opens a PR |

## Common Patterns

All workflows share the same underlying mechanics:

1. Tasks enter the queue via discovery (GitHub watcher, code scanner) or the `sugar add` CLI command.
2. The executor (Claude Agent SDK) runs with context from the task description and recalled memories.
3. Ralph Wiggum iterates until quality gates pass or the iteration limit is reached.
4. On success, changes are committed and a PR is opened. On failure, the task is flagged for human review.

See [../ARCHITECTURE.md](../ARCHITECTURE.md) for a full description of each component.
