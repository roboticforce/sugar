# Automated Security Vulnerability Resolution

Other tools find vulnerabilities and open issues. Sugar fixes them.

When Snyk, Dependabot, or GitHub Advanced Security opens a vulnerability issue, Sugar picks it up, reads the advisory, updates the affected dependency or patches the code, runs the full test suite, and opens a PR that links back to the original issue - all without human involvement.

## Setup

### 1. Configure `.sugar/config.yaml`

```yaml
sugar:
  loop_interval: 300

  claude:
    executor: sdk
    timeout: 1800

  discovery:
    github:
      enabled: true
      repo: "your-org/your-repo"
      auth_method: auto
      issue_labels:
        - "security"
        - "vulnerability"
        - "dependabot"
      check_interval_minutes: 15
      workflow:
        git_workflow: pull_request
        branch:
          create_branches: true
          name_pattern: "sugar/security-{issue_number}"
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

### 2. Set credentials

```bash
export GITHUB_TOKEN=your_token_here
# Or authenticate via gh CLI: gh auth login
```

### 3. Start Sugar

```bash
sugar run
# Or run one cycle only:
sugar run --once
```

## How It Works

1. GitHub Advanced Security (or Dependabot) opens an issue on your repo labeled "security" with advisory details.
2. Sugar's `GitHubWatcher` polls the repo every 15 minutes and finds the new issue.
3. A `bug_fix` work item is added to the queue with priority 2 (high) and the full issue body as context.
4. The executor sends the issue context to Claude, which reads the advisory, identifies the vulnerable package or code path, and applies the fix.
5. If it is a dependency update, Claude edits `requirements.txt` / `package.json` / `Cargo.toml` and runs the appropriate lock-file update.
6. If it is a code-level patch, Claude modifies the affected module and adds a regression test.
7. Quality gates run the full test suite. If tests fail, Ralph Wiggum re-prompts Claude with the failure output up to 10 times.
8. Once gates pass, Sugar commits to branch `sugar/security-42`, opens a PR titled "Fix #42: CVE-2024-XXXX in lodash", and adds a comment to the original issue linking the PR.
9. An `outcome` memory entry records what was done for future similar advisories.

## Example Output

**PR description created by Sugar:**

```
Fix #42: prototype pollution vulnerability in lodash (CVE-2024-1234)

## What changed
Updated lodash from 4.17.20 to 4.17.21 in package.json and package-lock.json.
The vulnerable `_.merge` codepath was not used directly, but the advisory
recommends upgrading as a precaution.

Closes #42

## Verification
- Full test suite: 847 passed, 0 failed
- No behavior changes detected in diff review
- Dependency audit clean after update
```

**Terminal output during execution:**

```
[sugar] Starting cycle at 2025-06-10T14:32:00Z
[sugar] GitHub: Found 1 new issue(s) matching labels ['security', 'dependabot']
[sugar] Queued: "CVE-2024-1234: lodash prototype pollution" (priority=2, type=bug_fix)
[sugar] Executing: CVE-2024-1234: lodash prototype pollution
[sugar] Ralph iteration 1/10...
[sugar] Quality gates: tests=PASS criteria=PASS truth=PASS diff=PASS
[sugar] Committed: abc1234 on branch sugar/security-42
[sugar] PR created: https://github.com/your-org/your-repo/pull/88
[sugar] Cycle completed in 187.3s, sleeping 112.7s
```
