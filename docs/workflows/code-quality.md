# Automated Code Quality Maintenance

Queue refactoring and lint cleanup tasks. Sugar fixes them systematically and verifies that no behavior changes were introduced - every cleanup is covered by the existing test suite before it is committed.

## Setup

### 1. Enable code quality discovery in `.sugar/config.yaml`

```yaml
sugar:
  loop_interval: 300

  claude:
    executor: sdk
    timeout: 1800

  discovery:
    code_quality:
      enabled: true
      root_path: "."
      file_extensions:
        - ".py"
        - ".js"
        - ".ts"
      excluded_dirs:
        - "node_modules"
        - ".git"
        - "__pycache__"
        - "venv"
        - ".venv"
        - "build"
        - "dist"
      max_files_per_scan: 50

  quality_gates:
    run_tests: true
    require_passing_tests: true
```

When enabled, `CodeQualityScanner` adds `refactor` work items automatically each cycle for files with high complexity scores or lint violations.

### 2. Queue targeted cleanup tasks manually

```bash
# Fix all lint violations in a module
sugar add \
  --title "Fix lint violations in api/handlers.py" \
  --type refactor \
  --priority 3 \
  --description "Run ruff on api/handlers.py and fix all violations. Do not change behavior - only formatting and style. All existing tests must pass unchanged."

# Refactor a high-complexity function
sugar add \
  --title "Reduce complexity of parse_config() in config/loader.py" \
  --type refactor \
  --priority 3 \
  --description "parse_config() has a cyclomatic complexity of 24. Extract the validation logic into a separate validate_config() function. Acceptance criteria: complexity below 10, all existing tests pass, no new public API."

# Remove deprecated patterns across the codebase
sugar add \
  --title "Replace deprecated subprocess.call() with subprocess.run()" \
  --type refactor \
  --priority 4 \
  --description "Find all uses of subprocess.call() and replace with subprocess.run() per our migration guide. Check return codes via returncode attribute."
```

### 3. Prioritize urgent cleanups

```bash
# Mark a task urgent (priority 1)
sugar add --title "Remove hardcoded timeouts in scheduler.py" --type refactor --urgent
```

## How It Works

1. `CodeQualityScanner` walks source files and scores each for complexity (cyclomatic, cognitive), unused imports, deprecated API usage, and formatting violations.
2. Files above the threshold are queued as `refactor` items with the violation summary as context.
3. The executor instructs Claude to fix violations without changing behavior. The prompt explicitly prohibits API surface changes and requires that all existing tests continue to pass.
4. Claude applies fixes - extracting functions, removing dead code, updating deprecated calls, running the project formatter.
5. Quality gates run the full suite. If any test breaks, the task fails and is flagged for human review rather than retried.
6. The diff is validated to confirm no public API was altered.
7. Changes are committed with a message referencing the quality issue.

## Example Output

```
[sugar] CodeQualityScanner: 2 files above complexity threshold
[sugar] Queued: "Reduce complexity: api/handlers.py (score: 31)" (refactor, priority=3)
[sugar] Executing: Reduce complexity: api/handlers.py

[sugar] Ralph iteration 1/10...
[sugar] Quality gates: tests=PASS criteria=PASS diff=PASS (no public API changes)
[sugar] Committed: 3fa19cc "Refactor api/handlers.py: extract validation helpers"

--- Quality delta ---
api/handlers.py cyclomatic complexity:  31 -> 8
Lines changed:   +47 / -12
Tests affected:  0 (all 203 passing)
```

**What Sugar will not do in a refactor task:**

- Change public function signatures
- Rename exported symbols
- Alter return types or error behavior
- Remove test files or test cases

If Claude proposes any of the above during a refactor, the truth enforcement gate blocks completion and the task is marked failed for human review.
