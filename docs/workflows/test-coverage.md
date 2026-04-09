# Continuous Test Coverage Improvement

Queue tasks to improve test coverage for specific modules. Sugar analyzes uncovered code paths, writes targeted tests for them, and runs the full suite to confirm nothing regresses.

This workflow is useful for paying down test debt incrementally - run it on a schedule or queue coverage tasks manually when you add new modules.

## Setup

### 1. Enable test coverage discovery in `.sugar/config.yaml`

```yaml
sugar:
  loop_interval: 300

  claude:
    executor: sdk
    timeout: 1800

  discovery:
    test_coverage:
      enabled: true
      root_path: "."
      source_dirs:
        - "src"
        - "lib"
        - "myapp"
      test_dirs:
        - "tests"
        - "test"
      excluded_dirs:
        - "node_modules"
        - ".git"
        - "__pycache__"
        - "venv"
        - ".venv"
        - "build"
        - "dist"

  quality_gates:
    run_tests: true
    require_passing_tests: true
```

With this config, Sugar automatically discovers modules with low coverage on each loop cycle and queues `test` work items for them.

### 2. Manually queue coverage tasks

For targeted coverage improvements, add tasks directly without waiting for discovery:

```bash
# Improve coverage for a specific module
sugar add \
  --title "Add tests for payment/refund.py" \
  --type test \
  --priority 3 \
  --description "The refund module has 0% test coverage. Focus on the partial refund path and the idempotency check in process_refund(). Acceptance criteria: coverage >= 80%, all edge cases in the docstring covered."

# Queue multiple modules at once
sugar add --title "Add tests for auth/tokens.py" --type test --priority 3
sugar add --title "Add tests for api/rate_limiter.py" --type test --priority 3
sugar add --title "Add tests for utils/retry.py" --type test --priority 4
```

### 3. Check the queue

```bash
sugar list --type test
```

```
ID          TYPE  PRIORITY  STATUS   TITLE
a1b2c3d4    test  3         pending  Add tests for payment/refund.py
e5f6a7b8    test  3         pending  Add tests for auth/tokens.py
c9d0e1f2    test  3         pending  Add tests for api/rate_limiter.py
```

### 4. Run Sugar

```bash
sugar run
```

## How It Works

1. Sugar's `TestCoverageAnalyzer` walks source directories and identifies modules where test files are missing or coverage metrics are low.
2. A `test` work item is queued per low-coverage module.
3. The executor reads the target module, its docstrings, and any existing tests for context.
4. Claude analyzes uncovered branches, edge cases described in docstrings, and error paths.
5. New test functions are written in the appropriate test file (or a new file is created following the project's naming convention).
6. Quality gates run the full suite. If any test fails, Ralph re-prompts Claude to fix it.
7. The final coverage delta and new test count are recorded in an `outcome` memory entry.

## Example Output

```
[sugar] TestCoverageAnalyzer: 3 modules below threshold
[sugar] Queued: "Add tests for payment/refund.py" (test, priority=3)
[sugar] Queued: "Add tests for auth/tokens.py" (test, priority=3)
[sugar] Executing: Add tests for payment/refund.py
[sugar] Ralph iteration 1/10...
[sugar] Ralph iteration 2/10 - fixing assertion in test_partial_refund...
[sugar] Quality gates: tests=PASS (198 passed, 14 new) criteria=PASS
[sugar] Committed: def8912 on branch sugar/test-coverage-refund

--- Coverage delta ---
payment/refund.py:  12% -> 83%  (+71%)
New tests added:    14
  test_process_refund_full
  test_process_refund_partial
  test_process_refund_idempotent
  test_process_refund_invalid_amount
  ... (10 more)
```
