# Issue Responder

Analyze and respond to GitHub issues with AI assistance. Sugar helps you understand issues, search your codebase for relevant context, and generate helpful responses automatically.

## Quick Start

```bash
# List open issues in your repository
sugar issue list

# View a specific issue
sugar issue view 42

# Generate an AI response (preview only)
sugar issue respond 42

# Generate and post a response if confidence is high
sugar issue respond 42 --post

# Search for issues
sugar issue search "authentication error"
```

## Prerequisites

- **GitHub CLI (`gh`)** installed and authenticated
- **Sugar installed** (`pip install sugarai`)
- Repository access (uses current repo or specify with `--repo owner/name`)

### Setup GitHub CLI

```bash
# Install gh CLI
brew install gh  # macOS
# or download from https://cli.github.com

# Authenticate with GitHub
gh auth login
```

## CLI Commands

### `sugar issue list`

List GitHub issues with filtering options.

**Options:**
- `--state` - Filter by state: `open` (default), `closed`, or `all`
- `--limit` - Maximum number of issues to list (default: 10)
- `--repo` - Repository in `owner/repo` format (defaults to current repo)

**Examples:**
```bash
# List open issues in current repo
sugar issue list

# List last 20 closed issues
sugar issue list --state closed --limit 20

# List issues in a specific repo
sugar issue list --repo anthropics/anthropic-sdk-python
```

**Output:**
```
#      State    Title                                                        Author
-----------------------------------------------------------------------------------------------
#42    open     Authentication timeout after 30 seconds                      @user123
#41    open     Add support for custom retry logic                           @contributor

2 issues listed
```

### `sugar issue view <number>`

View detailed information about a specific issue including all comments.

**Arguments:**
- `<number>` - Issue number (required)

**Options:**
- `--repo` - Repository in `owner/repo` format

**Examples:**
```bash
# View issue #42
sugar issue view 42

# View issue in another repo
sugar issue view 15 --repo owner/repo
```

**Output:**
```
================================================================================
Issue #42: Authentication timeout after 30 seconds
================================================================================
State: open
Author: @user123
Labels: bug, needs-investigation
Created: 2024-01-15T10:30:00Z
Comments: 3
URL: https://github.com/owner/repo/issues/42

--------------------------------------------------------------------------------

When making API calls with long-running operations, the authentication
times out after 30 seconds...

--------------------------------------------------------------------------------
Comments (3):

  @maintainer (2024-01-15T11:00:00Z):
  Thanks for reporting this. Can you share more details about your setup?...
```

### `sugar issue analyze <number>`

Analyze an issue without making an AI call. Performs lightweight pre-analysis to extract key information.

**Arguments:**
- `<number>` - Issue number (required)

**Options:**
- `--repo` - Repository in `owner/repo` format
- `--format` - Output format: `text` (default) or `json`

**Examples:**
```bash
# Analyze issue and show insights
sugar issue analyze 42

# Get JSON output for scripting
sugar issue analyze 42 --format json
```

**Output (text format):**
```
================================================================================
Analysis for Issue #42: Authentication timeout after 30 seconds
================================================================================

Type: bug
Sentiment: frustrated
Key Topics: auth, timeout, api

Mentioned Files:
  - auth/client.py
  - config/settings.yaml

Mentioned Errors:
  - TimeoutError: Request timed out after 30s

Similar Issues:
  #38, #29, #15

Has Maintainer Response: No
```

**Output (JSON format):**
```json
{
  "issue_number": 42,
  "title": "Authentication timeout after 30 seconds",
  "analysis": {
    "issue_type": "bug",
    "key_topics": ["auth", "timeout", "api"],
    "mentioned_files": ["auth/client.py", "config/settings.yaml"],
    "mentioned_errors": ["TimeoutError: Request timed out after 30s"]
  },
  "has_maintainer_response": false
}
```

### `sugar issue respond <number>`

Generate an AI-powered response to an issue. Sugar analyzes the issue, searches your codebase, and creates a helpful response.

**Arguments:**
- `<number>` - Issue number (required)

**Options:**
- `--repo` - Repository in `owner/repo` format
- `--post` - Actually post the response if confidence meets threshold
- `--force-post` - Post regardless of confidence score (use with caution)
- `--confidence-threshold` - Minimum confidence to auto-post: 0.0-1.0 (default: 0.8)
- `--dry-run` - Show what would be posted without posting

**Examples:**
```bash
# Generate response (preview only, no posting)
sugar issue respond 42

# Generate and post if confidence >= 0.8
sugar issue respond 42 --post

# Post with lower confidence threshold
sugar issue respond 42 --post --confidence-threshold 0.6

# Force post regardless of confidence (use carefully)
sugar issue respond 42 --force-post

# Preview what would be posted
sugar issue respond 42 --post --dry-run
```

**Output:**
```
🔍 Analyzing issue #42: Authentication timeout after 30 seconds
------------------------------------------------------------

🤖 Generating AI response...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated Response (Confidence: 0.85)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Thanks for reporting this issue! I've analyzed the codebase and found
the timeout configuration.

The timeout is currently hardcoded to 30 seconds in `auth/client.py:45`.
For long-running operations, you can increase this by setting the
`AUTH_TIMEOUT` environment variable:

```bash
export AUTH_TIMEOUT=300  # 5 minutes
```

Or configure it in your `config/settings.yaml`:

```yaml
auth:
  timeout: 300
```

See the [configuration guide](docs/config.md) for more details.

---
*Sugar (AI Assistant) • [Learn more](https://github.com/roboticforce/sugar)*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Suggested Labels: bug, documentation

✅ Confidence score 0.85 meets threshold of 0.80
✅ Response posted to issue #42
```

**Behavior:**
- **Without `--post`**: Shows generated response but doesn't post
- **With `--post`**: Posts only if confidence >= threshold
- **With `--force-post`**: Posts regardless of confidence
- **Already responded**: Won't post if issue has responses (use `--force-post` to override)

### `sugar issue search <query>`

Search for issues matching a query across the repository.

**Arguments:**
- `<query>` - Search query (required)

**Options:**
- `--repo` - Repository in `owner/repo` format
- `--limit` - Maximum results (default: 10)

**Examples:**
```bash
# Search for authentication issues
sugar issue search "authentication"

# Search for open bugs
sugar issue search "is:open label:bug"

# Search with GitHub query syntax
sugar issue search "timeout in:title"

# Limit results
sugar issue search "error" --limit 5
```

**Output:**
```
Search results for: authentication

#      State    Title
--------------------------------------------------------------------------------
#42    open     Authentication timeout after 30 seconds
#38    closed   Add OAuth authentication support
#29    open     Authentication fails with special characters

3 issues found
```

**Query Syntax:**
The search supports GitHub's advanced query syntax:
- `is:open` or `is:closed` - Filter by state
- `label:bug` - Filter by label
- `in:title` or `in:body` - Search in specific fields
- `author:username` - Filter by author
- Combine multiple terms: `auth is:open label:bug`

## Options and Flags Reference

### Global Options

**`--repo owner/repo`**
- Specify repository in `owner/repo` format
- If omitted, uses the current repository (detected via git remote)
- Example: `--repo anthropics/anthropic-sdk-python`

### Response Options

**`--post`**
- Post the response to GitHub if confidence meets threshold
- Default threshold: 0.8 (configurable with `--confidence-threshold`)
- Won't post if issue already has maintainer responses (override with `--force-post`)

**`--force-post`**
- Post the response regardless of confidence score
- Bypasses maintainer response check
- Use carefully - low confidence responses may not be helpful

**`--confidence-threshold <value>`**
- Set minimum confidence for auto-posting (0.0-1.0)
- Default: 0.8 (80% confidence)
- Only applies when using `--post`
- Example: `--confidence-threshold 0.7` for 70% threshold

**`--dry-run`**
- Show what would be posted without actually posting
- Useful for testing and review
- Shows the generated response and posting decision

## Custom Prompt Configuration

Customize how Sugar responds to issues by creating a custom prompt configuration file.

### Configuration File

Create `.sugar/prompts/issue_responder.json` in your repository:

```json
{
  "name": "my-project-responder",
  "description": "Custom issue responder for my project",

  "persona": {
    "role": "Senior Developer",
    "goal": "Help users resolve issues quickly and thoroughly",
    "expertise": ["Python", "TypeScript", "REST APIs"]
  },

  "instructions": "You are a helpful assistant for this project. Be friendly and professional. When discussing APIs, reference our documentation at /docs. For authentication issues, point users to the auth guide.",

  "guidelines": [
    "Always search the codebase before responding",
    "Include relevant file paths in your response",
    "Provide code examples when helpful",
    "Ask clarifying questions if the issue is ambiguous"
  ],

  "constraints": [
    "Never share API keys or secrets",
    "Don't promise specific release dates",
    "Don't close issues automatically"
  ]
}
```

### Configuration Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Identifier for this configuration |
| `description` | No | Human-readable description |
| `persona` | No | Agent identity configuration |
| `persona.role` | No | The role Sugar plays (e.g., "Senior Developer") |
| `persona.goal` | No | Primary objective |
| `persona.expertise` | No | List of areas of expertise |
| `instructions` | **Yes** | Core custom prompt - project-specific context and behavior |
| `guidelines` | No | List of things Sugar SHOULD do |
| `constraints` | No | List of things Sugar should NOT do |

### How It Works

When the configuration file exists:
1. Sugar loads the custom configuration
2. Validates that `instructions` is present
3. Builds a custom prompt section with persona, instructions, guidelines, and constraints
4. Appends this to the base system prompt

If the file doesn't exist or is invalid, Sugar uses the default behavior.

### Example Configurations

**Open Source Project (Friendly)**
```json
{
  "instructions": "You are a friendly assistant for this open source project. Welcome new contributors warmly. Point beginners to issues labeled 'good first issue'. Thank people for detailed bug reports.",
  "guidelines": [
    "Be encouraging and welcoming",
    "Use emoji sparingly but warmly",
    "Suggest contributing guidelines when appropriate"
  ]
}
```

**Enterprise API (Professional)**
```json
{
  "persona": {
    "role": "Technical Support Engineer",
    "expertise": ["REST APIs", "OAuth 2.0", "Rate Limiting"]
  },
  "instructions": "You are a technical support assistant for the Acme API. Be professional and precise. Reference API documentation at docs.acme.com. For billing questions, direct users to support@acme.com.",
  "constraints": [
    "Don't speculate about roadmap, licensing or commercial terms",
    "Don't speculate about roadmap or release dates",
    "Don't share internal implementation details"
  ]
}
```

**Developer Tool (Technical)**
```json
{
  "persona": {
    "role": "CLI Tool Expert",
    "expertise": ["Python", "CLI Design", "Unix"]
  },
  "instructions": "You assist users with this CLI tool. Provide command examples. Reference man pages and --help output when relevant.",
  "guidelines": [
    "Include full command examples",
    "Show expected output when helpful",
    "Mention related commands"
  ]
}
```

## Confidence Scoring

Sugar rates its confidence in generated responses on a scale from 0.0 to 1.0.

### Confidence Levels

| Score Range | Level | Meaning | Action |
|-------------|-------|---------|--------|
| 0.9-1.0 | Very High | Verified in code, high certainty | Auto-post recommended |
| 0.7-0.9 | High | Based on clear patterns and context | Safe to auto-post |
| 0.5-0.7 | Moderate | Some uncertainty, needs review | Manual review recommended |
| 0.0-0.5 | Low | Significant uncertainty | Do not auto-post |

### How Confidence is Determined

Sugar evaluates confidence based on:

1. **Code Reference Quality**
   - Found exact file and line references: Higher confidence
   - Found related code patterns: Moderate confidence
   - No code references found: Lower confidence

2. **Issue Clarity**
   - Clear, specific issue description: Higher confidence
   - Vague or incomplete information: Lower confidence
   - Includes error messages and context: Higher confidence

3. **Similar Issues**
   - Found similar resolved issues: Higher confidence
   - Novel or unique issue: Moderate confidence
   - No similar issues: Lower confidence

4. **Codebase Match**
   - Issue mentions exist in codebase: Higher confidence
   - Issue describes unfamiliar patterns: Lower confidence

### Default Auto-Post Threshold

By default, Sugar only auto-posts responses with **confidence >= 0.8**. This means:
- 80%+ confidence: Response will be posted (with `--post`)
- <80% confidence: Response shown but not posted (requires manual review)

You can adjust this with `--confidence-threshold`:

```bash
# More conservative (only very high confidence)
sugar issue respond 42 --post --confidence-threshold 0.9

# More aggressive (includes moderate confidence)
sugar issue respond 42 --post --confidence-threshold 0.6
```

## GitHub Action Integration

Automate issue responses in your CI/CD pipeline using GitHub Actions.

### Basic Workflow

Create `.github/workflows/issue-responder.yml`:

```yaml
name: AI Issue Responder

on:
  issues:
    types: [opened, labeled]

permissions:
  issues: write
  contents: read

jobs:
  respond:
    runs-on: ubuntu-latest

    # Only respond to issues with specific labels
    if: contains(github.event.issue.labels.*.name, 'needs-help')

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Sugar
        run: pip install sugarai

      - name: Install GitHub CLI
        run: |
          type gh >/dev/null 2>&1 || {
            (type sudo >/dev/null 2>&1 && sudo apt-get update && sudo apt-get install -y gh) || \
            echo "gh not installed"
          }

      - name: Generate and post response
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          sugar issue respond ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --post \
            --confidence-threshold 0.8
```

### Advanced Workflow with Multiple Triggers

```yaml
name: Advanced Issue Responder

on:
  issues:
    types: [opened, labeled, reopened]
  issue_comment:
    types: [created]

permissions:
  issues: write
  contents: read

jobs:
  respond:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install sugarai
          type gh >/dev/null 2>&1 || {
            (type sudo >/dev/null 2>&1 && sudo apt-get update && sudo apt-get install -y gh) || \
            echo "gh not installed"
          }

      - name: Check if should respond
        id: check
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          # Skip if issue author is a bot
          if [[ "${{ github.event.issue.user.type }}" == "Bot" ]]; then
            echo "skip=true" >> $GITHUB_OUTPUT
            exit 0
          fi

          # Skip if already has maintainer responses
          comments=$(gh issue view ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --json comments --jq '.comments | length')

          if [[ $comments -gt 0 ]]; then
            echo "skip=true" >> $GITHUB_OUTPUT
          else
            echo "skip=false" >> $GITHUB_OUTPUT
          fi

      - name: Generate response
        if: steps.check.outputs.skip == 'false'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          sugar issue respond ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --post \
            --confidence-threshold 0.8

      - name: Add labels based on analysis
        if: steps.check.outputs.skip == 'false'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          # Analyze issue and extract suggested labels
          analysis=$(sugar issue analyze ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --format json)

          # Parse and add labels (requires jq)
          # This is a placeholder - implement based on your needs
          echo "$analysis"
```

### Selective Response Workflow

Only respond to specific types of issues:

```yaml
name: Selective Issue Responder

on:
  issues:
    types: [labeled]

permissions:
  issues: write
  contents: read

jobs:
  respond-to-bugs:
    if: github.event.label.name == 'bug'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install Sugar
        run: pip install sugarai
      - name: Respond to bug
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          sugar issue respond ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --post \
            --confidence-threshold 0.85

  respond-to-questions:
    if: github.event.label.name == 'question'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install Sugar
        run: pip install sugarai
      - name: Respond to question
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          sugar issue respond ${{ github.event.issue.number }} \
            --repo ${{ github.repository }} \
            --post \
            --confidence-threshold 0.7
```

### Required Secrets

Add these secrets to your repository settings (Settings → Secrets and variables → Actions):

- `ANTHROPIC_API_KEY` - Your Anthropic API key for Claude access
- `GITHUB_TOKEN` - Automatically provided by GitHub Actions (no setup needed)

### Workflow Permissions

The workflow requires these permissions (set in the workflow file):

```yaml
permissions:
  issues: write        # Post comments and add labels
  contents: read       # Read repository code for analysis
```

### Testing the Workflow

1. **Create the workflow file** in `.github/workflows/issue-responder.yml`
2. **Add required secrets** in repository settings
3. **Test with a new issue**:
   - Create a test issue
   - Add the trigger label (e.g., `needs-help`)
   - Check the Actions tab to see workflow execution
4. **Review the response** on the issue

## Examples

### Example 1: Daily Issue Triage

Review new issues each morning:

```bash
# List new issues from last 24 hours
sugar issue list --state open --limit 20

# Analyze high-priority ones
sugar issue analyze 42
sugar issue analyze 43

# Generate responses for review
sugar issue respond 42
sugar issue respond 43

# Post responses after review
sugar issue respond 42 --post
```

### Example 2: Batch Processing

Process multiple issues with a script:

```bash
#!/bin/bash
# respond-to-issues.sh

# Get list of open issues with 'needs-response' label
issue_numbers=$(gh issue list --label "needs-response" \
  --state open --json number --jq '.[].number')

for issue in $issue_numbers; do
  echo "Processing issue #$issue..."

  # Generate response with high confidence threshold
  sugar issue respond "$issue" \
    --post \
    --confidence-threshold 0.85

  # Remove the label after processing
  gh issue edit "$issue" --remove-label "needs-response"

  # Rate limit friendly delay
  sleep 2
done
```

### Example 3: Cross-Repository Support

Help users across multiple repositories:

```bash
# Main project repo
sugar issue list --repo myorg/main-project

# Respond to issue in SDK repo
sugar issue respond 15 --repo myorg/python-sdk --post

# Search for similar issues across repos
sugar issue search "timeout error" --repo myorg/main-project
sugar issue search "timeout error" --repo myorg/python-sdk
```

### Example 4: Emergency Bug Response

Quickly respond to critical bugs:

```bash
# Find the bug report
sugar issue search "critical is:open label:bug"

# View full details
sugar issue view 99

# Generate immediate response
sugar issue respond 99 --force-post
```

### Example 5: Question Answering

Respond to user questions about your project:

```bash
# Find unanswered questions
sugar issue list --state open | grep "How to"

# Analyze the question
sugar issue analyze 50

# Generate helpful response
sugar issue respond 50 --post --confidence-threshold 0.7
```

## Best Practices

### Do's

- **Review before auto-posting**: Start without `--post` to review responses
- **Set appropriate thresholds**: Use higher thresholds (0.85+) for auto-posting
- **Monitor responses**: Check posted responses regularly for accuracy
- **Use labels**: Label issues to trigger selective responses
- **Test with dry-run**: Use `--dry-run` to preview behavior
- **Provide context**: Richer issue descriptions lead to better responses

### Don'ts

- **Don't force-post blindly**: Low confidence responses may confuse users
- **Don't respond to everything**: Some issues need human judgment
- **Don't ignore feedback**: Monitor issue responses and user reactions
- **Don't skip review**: Always review auto-posted responses periodically
- **Don't forget rate limits**: GitHub API has rate limits

### Security Considerations

- **Protect API keys**: Never commit API keys to version control
- **Use environment variables**: Store secrets in env vars or secret management
- **Review permissions**: Limit workflow permissions to minimum required
- **Audit responses**: Review auto-posted responses for sensitive information
- **Monitor usage**: Track API usage to avoid unexpected costs

## Troubleshooting

### "gh: command not found"

Install GitHub CLI:
```bash
# macOS
brew install gh

# Linux
sudo apt-get install gh

# Or download from https://cli.github.com
```

### "gh command failed: HTTP 401"

Authenticate with GitHub:
```bash
gh auth login
```

For GitHub Actions, ensure `GITHUB_TOKEN` is set:
```yaml
env:
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### "Failed to detect current repository"

Specify repository explicitly:
```bash
sugar issue list --repo owner/repo
```

Or ensure you're in a git repository with GitHub remote:
```bash
git remote -v
```

### "Confidence score too low, not posting"

This is expected behavior. Options:
- Review the response manually and post if appropriate
- Lower threshold: `--confidence-threshold 0.6`
- Force post (carefully): `--force-post`
- Improve issue description for better AI analysis

### "Issue already has responses"

By design, Sugar won't auto-respond to issues with existing responses. Options:
- Use `--force-post` to override (if you're sure it's appropriate)
- Manually review and decide if additional response is needed

### Workflow not triggering

Check:
- Workflow file syntax (use YAML validator)
- Trigger conditions (`if:` statements)
- Repository permissions (Settings → Actions → General)
- Secret configuration (`ANTHROPIC_API_KEY`)

## Related Documentation

- [GitHub Integration Guide](user/github-integration.md) - Full GitHub integration details
- [CLI Reference](user/cli-reference.md) - Complete command reference
- [Configuration](user/configuration-best-practices.md) - Advanced configuration

## Need Help?

- Check the [FAQ](user/faq.md) for common questions
- See [Troubleshooting](user/troubleshooting.md) for more solutions
- Open an issue on [GitHub](https://github.com/roboticforce/sugar/issues)
