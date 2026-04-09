# Automated Bug Triage and Resolution

Sugar watches for GitHub issues labeled "bug", reads the reproduction steps, writes a failing test to confirm the problem, implements the fix, runs quality gates, and opens a PR - all before a developer has had a chance to triage.

This workflow uses a two-label gate: an issue must carry both "bug" and "sugar-approved" before Sugar acts on it. This gives your team control over which bugs are safe to hand off autonomously.

## Setup

### 1. Configure `.sugar/config.yaml`

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

  discovery:
    github:
      enabled: true
      repo: "your-org/your-repo"
      auth_method: auto
      # Both labels must be present - filter in issue_labels, enforce in triage
      issue_labels:
        - "bug"
        - "sugar-approved"
      check_interval_minutes: 30
      workflow:
        git_workflow: pull_request
        branch:
          create_branches: true
          name_pattern: "sugar/bug-{issue_number}"
          base_branch: main
        pull_request:
          auto_create: true
          auto_merge: false
          title_pattern: "Fix #{issue_number}: {issue_title}"
          include_work_summary: true
        commit:
          include_issue_ref: true
          message_pattern: "Fix #{issue_number}: {work_summary}"

  quality_gates:
    run_tests: true
    require_passing_tests: true
```

**Two-label gate pattern:** Add "sugar-approved" only to bugs that are well-specified and safe to fix autonomously. Poorly described bugs stay in queue until a human adds the label.

### 2. Create GitHub label

```bash
gh label create "sugar-approved" --color "0075ca" --description "Safe for autonomous resolution"
```

### 3. Start Sugar

```bash
sugar run
```

## How It Works

1. A developer files a bug report on GitHub and adds labels "bug" and "sugar-approved".
2. Sugar's `GitHubWatcher` detects the issue on next poll.
3. A `bug_fix` work item is queued. The full issue body (title, description, reproduction steps, environment info) is stored as context.
4. The executor sends context to Claude with instructions to write a failing test first (TDD approach), then implement the fix.
5. Claude reads the reproduction steps, identifies the affected code paths, and writes a test that reproduces the failure.
6. Claude implements the fix and runs the test to confirm it now passes.
7. Quality gates verify the full suite still passes - no regressions.
8. A PR is opened. Sugar posts a comment on the original issue linking the PR.
9. The fix and approach are stored as an `error_pattern` memory entry so future similar bugs are handled faster.

## Example

**Issue #77:**
```
Title: [bug] User session expires immediately after login on Safari
Labels: bug, sugar-approved

Steps to reproduce:
1. Open the app in Safari 17
2. Log in with valid credentials
3. Session expires within 5 seconds

Expected: session lasts 24 hours
Actual: session is invalid immediately

Environment: Safari 17.2, macOS 14.3
```

**Terminal output:**

```
[sugar] GitHub: Found issue #77 "User session expires immediately after login on Safari"
[sugar] Queued: bug_fix priority=2
[sugar] Executing: User session expires immediately after login on Safari
[sugar] Ralph iteration 1/10 - writing failing test...
[sugar] Ralph iteration 2/10 - implementing fix...
[sugar] Quality gates: tests=PASS (312 passed) criteria=PASS diff=PASS
[sugar] PR opened: https://github.com/your-org/your-repo/pull/91
[sugar] Comment posted on issue #77
```

**PR description:**

```
Fix #77: user session expires immediately on Safari

## Root cause
Safari sends a SameSite=Strict cookie header that was rejected by the
session middleware. The middleware was not falling back to the session
token in the Authorization header.

## Changes
- auth/session.py: add Authorization header fallback in session validation
- tests/test_session.py: add regression test for Safari SameSite behavior

## Verification
- New test `test_session_safari_samesite` added and passing
- Full suite: 312 passed, 0 failed

Closes #77
```
