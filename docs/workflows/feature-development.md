# AI-Assisted Feature Implementation

Describe a feature in natural language. Sugar implements it following your project's conventions (stored in memory), writes tests, and opens a PR.

This workflow works best when Sugar already has context about your project loaded into memory - file layouts, naming conventions, preferred libraries, architectural decisions. The richer the memory, the closer the first implementation is to what you would have written yourself.

## Setup

### 1. Configure `.sugar/config.yaml` for feature work

```yaml
sugar:
  loop_interval: 300

  claude:
    executor: sdk
    timeout: 1800

  ralph:
    enabled: true
    max_iterations: 10
    quality_gates_enabled: true
    require_completion_criteria: true

  discovery:
    github:
      enabled: true
      repo: "your-org/your-repo"
      auth_method: auto
      issue_labels:
        - "feature"
        - "sugar-approved"
      workflow:
        git_workflow: pull_request
        branch:
          create_branches: true
          name_pattern: "sugar/feature-{issue_number}"
          base_branch: main
        pull_request:
          auto_create: true
          auto_merge: false
          title_pattern: "feat #{issue_number}: {issue_title}"
          include_work_summary: true

  quality_gates:
    run_tests: true
    require_passing_tests: true
```

### 2. Seed project memory (run once)

Store your conventions so Sugar follows them without being told each time:

```bash
sugar memory store \
  --type preference \
  --content "All new API endpoints must have a corresponding integration test in tests/integration/. Use pytest fixtures from conftest.py rather than inline setup." \
  --summary "API testing convention"

sugar memory store \
  --type decision \
  --content "We use Pydantic v2 models for all request/response validation. Never use plain dicts for public API schemas." \
  --summary "Pydantic v2 for API schemas"

sugar memory store \
  --type file_context \
  --content "api/router.py registers all route handlers. Add new routers via include_router(). See existing handlers in api/handlers/ for patterns." \
  --summary "How to add API routes"
```

### 3. Add a feature task

```bash
sugar add \
  --title "Add rate limiting to the public API" \
  --type feature \
  --priority 2 \
  --description "Implement per-IP rate limiting on all /api/v1/ endpoints. Limit: 100 requests per minute. Return 429 with Retry-After header when exceeded. Use Redis for the counter (already in docker-compose). Follow existing middleware patterns in api/middleware/." \
  --acceptance-criteria "Rate limit enforced at 100 req/min per IP, 429 returned with Retry-After header, Redis used for counters, integration test covering limit and reset, no changes to existing endpoint behavior"
```

### 4. Use orchestration for multi-stage features

For larger features, use `--orchestrate` to break the work into stages:

```bash
sugar add \
  --title "Implement webhook delivery system" \
  --type feature \
  --priority 2 \
  --orchestrate \
  --description "Build a webhook system: endpoint registration, event dispatch, retry logic with exponential backoff, delivery log. See docs/task_orchestration.md for stage format."
```

## How It Works

1. The feature task enters the queue. The executor recalls relevant memories - conventions, file context, past decisions - and includes them in the prompt.
2. Claude reads the description and acceptance criteria, then explores the codebase to understand existing patterns before writing any code.
3. Implementation follows the stored conventions without needing them repeated in every task description.
4. Tests are written alongside the feature code, not as an afterthought.
5. Ralph iterates until all acceptance criteria are met and the test suite passes.
6. Quality gates verify the diff is scoped to the feature (no unrelated changes), tests pass, and any claimed behavior is demonstrably true.
7. A PR is opened. The work summary in the PR description explains what was built and why each design choice was made.
8. Sugar stores a new `outcome` memory entry recording what worked and what patterns were used, making future similar features faster.

## Example Output

```
[sugar] Executing: Add rate limiting to the public API
[sugar] Memory recall: 3 relevant entries found
  - "API testing convention" (preference)
  - "How to add API routes" (file_context)
  - "Redis already in docker-compose on port 6379" (file_context)
[sugar] Ralph iteration 1/10 - scaffolding middleware...
[sugar] Ralph iteration 2/10 - adding integration test...
[sugar] Ralph iteration 3/10 - fixing Redis key expiry...
[sugar] Quality gates: tests=PASS (47 passed, 6 new) criteria=PASS diff=PASS
[sugar] PR opened: https://github.com/your-org/your-repo/pull/104

--- Acceptance criteria verification ---
[x] Rate limit enforced at 100 req/min per IP
[x] 429 returned with Retry-After header
[x] Redis counters confirmed via test assertions
[x] Integration test: test_rate_limit_enforced, test_rate_limit_reset
[x] Existing endpoint tests: all 41 passing unchanged
```

## Tips for Better Results

- Write acceptance criteria as verifiable conditions, not vague goals. "Coverage >= 80%" is better than "well tested".
- Reference specific files in the description when you know where the work belongs.
- Use `sugar memory store` to add project conventions before queuing complex features. Sugar is faster when it does not have to infer your patterns from scratch.
- For features touching multiple subsystems, `--orchestrate` lets Sugar plan stages before executing, reducing rework.
