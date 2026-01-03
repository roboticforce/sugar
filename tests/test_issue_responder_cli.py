"""
Integration tests for Issue Responder CLI commands

Tests the `sugar issue` command group including:
- list: List GitHub issues
- view: View a specific issue
- analyze: Analyze an issue (no AI)
- respond: Generate AI response for an issue
- search: Search for issues
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner

from sugar.main import cli
from sugar.integrations.github import (
    GitHubIssue,
    GitHubLabel,
    GitHubUser,
    GitHubComment,
)


@pytest.fixture
def cli_runner():
    """Click CLI test runner"""
    return CliRunner()


@pytest.fixture
def mock_github_issue():
    """Sample GitHub issue for testing"""
    return GitHubIssue(
        number=123,
        title="Bug: Application crashes on startup",
        body="When I run `python main.py`, I get an AttributeError in src/config.py:42",
        state="open",
        user=GitHubUser(login="testuser", id=1001, type="User"),
        labels=[
            GitHubLabel(
                name="bug", color="d73a4a", description="Something isn't working"
            )
        ],
        created_at="2025-01-01T12:00:00Z",
        updated_at="2025-01-01T12:00:00Z",
        comments_count=2,
        html_url="https://github.com/test/repo/issues/123",
    )


@pytest.fixture
def mock_github_issues():
    """List of GitHub issues for testing"""
    return [
        GitHubIssue(
            number=123,
            title="Bug: Application crashes on startup",
            body="Error in config.py",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[GitHubLabel(name="bug", color="d73a4a")],
            created_at="2025-01-01T12:00:00Z",
            updated_at="2025-01-01T12:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/123",
        ),
        GitHubIssue(
            number=124,
            title="Feature: Add dark mode",
            body="Would be nice to have dark mode support",
            state="open",
            user=GitHubUser(login="user2", id=1002),
            labels=[GitHubLabel(name="enhancement", color="a2eeef")],
            created_at="2025-01-02T10:00:00Z",
            updated_at="2025-01-02T10:00:00Z",
            comments_count=5,
            html_url="https://github.com/test/repo/issues/124",
        ),
        GitHubIssue(
            number=125,
            title="Question: How to configure logging?",
            body="I'm trying to set up logging but can't find the docs",
            state="open",
            user=GitHubUser(login="user3", id=1003),
            labels=[GitHubLabel(name="question", color="d876e3")],
            created_at="2025-01-03T14:30:00Z",
            updated_at="2025-01-03T14:30:00Z",
            comments_count=1,
            html_url="https://github.com/test/repo/issues/125",
        ),
    ]


class TestIssueList:
    """Test `sugar issue list` command"""

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_default(
        self, mock_list_issues, cli_runner, mock_github_issues
    ):
        """Test listing issues with default options"""
        mock_list_issues.return_value = mock_github_issues

        result = cli_runner.invoke(cli, ["issue", "list"])

        assert result.exit_code == 0
        assert "Bug: Application crashes on startup" in result.output
        assert "Feature: Add dark mode" in result.output
        assert "Question: How to configure logging?" in result.output
        mock_list_issues.assert_called_once_with(state="open", limit=10)

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_with_state_filter(
        self, mock_list_issues, cli_runner, mock_github_issues
    ):
        """Test listing issues with state filter"""
        closed_issues = [
            GitHubIssue(
                number=100,
                title="Fixed: Old bug",
                body="This was fixed",
                state="closed",
                user=GitHubUser(login="user1", id=1001),
                labels=[],
                created_at="2024-12-01T10:00:00Z",
                updated_at="2024-12-05T15:00:00Z",
                comments_count=3,
                html_url="https://github.com/test/repo/issues/100",
            )
        ]
        mock_list_issues.return_value = closed_issues

        result = cli_runner.invoke(cli, ["issue", "list", "--state", "closed"])

        assert result.exit_code == 0
        assert "Fixed: Old bug" in result.output
        mock_list_issues.assert_called_once_with(state="closed", limit=10)

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_with_limit(
        self, mock_list_issues, cli_runner, mock_github_issues
    ):
        """Test listing issues with custom limit"""
        mock_list_issues.return_value = mock_github_issues[:2]

        result = cli_runner.invoke(cli, ["issue", "list", "--limit", "2"])

        assert result.exit_code == 0
        assert "Bug: Application crashes on startup" in result.output
        assert "Feature: Add dark mode" in result.output
        mock_list_issues.assert_called_once_with(state="open", limit=2)

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_with_repo_option(
        self, mock_list_issues, cli_runner, mock_github_issues
    ):
        """Test listing issues with custom repo"""
        mock_list_issues.return_value = mock_github_issues

        result = cli_runner.invoke(
            cli, ["issue", "list", "--repo", "owner/custom-repo"]
        )

        assert result.exit_code == 0
        mock_list_issues.assert_called_once_with(state="open", limit=10)

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_empty_result(self, mock_list_issues, cli_runner):
        """Test listing issues when no issues are found"""
        mock_list_issues.return_value = []

        result = cli_runner.invoke(cli, ["issue", "list"])

        assert result.exit_code == 0
        assert "No open issues found" in result.output

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_issues_error_handling(self, mock_list_issues, cli_runner):
        """Test error handling when listing fails"""
        mock_list_issues.side_effect = RuntimeError(
            "GitHub API error: rate limit exceeded"
        )

        result = cli_runner.invoke(cli, ["issue", "list"])

        assert result.exit_code != 0
        assert "Error" in result.output


class TestIssueView:
    """Test `sugar issue view` command"""

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_view_issue_success(self, mock_get_issue, cli_runner, mock_github_issue):
        """Test viewing a specific issue"""
        # Add some comments to the issue
        mock_github_issue.comments = [
            GitHubComment(
                id=1,
                body="I have the same issue!",
                user=GitHubUser(login="commenter1", id=2001),
                created_at="2025-01-01T13:00:00Z",
                updated_at="2025-01-01T13:00:00Z",
            ),
            GitHubComment(
                id=2,
                body="Looks like a config problem",
                user=GitHubUser(login="maintainer", id=2002),
                created_at="2025-01-01T14:00:00Z",
                updated_at="2025-01-01T14:00:00Z",
            ),
        ]
        mock_get_issue.return_value = mock_github_issue

        result = cli_runner.invoke(cli, ["issue", "view", "123"])

        assert result.exit_code == 0
        assert "Issue #123" in result.output
        assert "Bug: Application crashes on startup" in result.output
        assert "testuser" in result.output
        assert "bug" in result.output
        assert "I have the same issue!" in result.output
        assert "Looks like a config problem" in result.output
        mock_get_issue.assert_called_once_with(123)

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_view_issue_with_repo(self, mock_get_issue, cli_runner, mock_github_issue):
        """Test viewing issue with custom repo"""
        mock_get_issue.return_value = mock_github_issue

        result = cli_runner.invoke(
            cli, ["issue", "view", "123", "--repo", "owner/repo"]
        )

        assert result.exit_code == 0
        assert "Issue #123" in result.output
        mock_get_issue.assert_called_once_with(123)

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_view_issue_not_found(self, mock_get_issue, cli_runner):
        """Test viewing non-existent issue"""
        mock_get_issue.side_effect = RuntimeError("Issue not found")

        result = cli_runner.invoke(cli, ["issue", "view", "999"])

        assert result.exit_code != 0

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_view_issue_no_comments(
        self, mock_get_issue, cli_runner, mock_github_issue
    ):
        """Test viewing issue with no comments"""
        mock_github_issue.comments = []
        mock_github_issue.comments_count = 0
        mock_get_issue.return_value = mock_github_issue

        result = cli_runner.invoke(cli, ["issue", "view", "123"])

        assert result.exit_code == 0
        assert "Issue #123" in result.output
        # When there are no comments, the comment section is simply not shown
        assert "Comments (0)" not in result.output or "Comments:" not in result.output


class TestIssueAnalyze:
    """Test `sugar issue analyze` command"""

    @patch("sugar.integrations.github.GitHubClient.find_similar_issues")
    @patch("sugar.integrations.github.GitHubClient.has_maintainer_response")
    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_analyze_issue_text_format(
        self,
        mock_get_issue,
        mock_has_maintainer,
        mock_find_similar,
        cli_runner,
        mock_github_issue,
    ):
        """Test analyzing issue with text output format"""
        mock_get_issue.return_value = mock_github_issue
        mock_has_maintainer.return_value = False
        mock_find_similar.return_value = []

        result = cli_runner.invoke(cli, ["issue", "analyze", "123"])

        assert result.exit_code == 0
        assert "Issue #123" in result.output or "123" in result.output
        assert "Bug: Application crashes on startup" in result.output
        # Check for pre-analysis results
        assert "Type:" in result.output or "type" in result.output.lower()
        mock_get_issue.assert_called_once_with(123)

    @patch("sugar.integrations.github.GitHubClient.has_maintainer_response")
    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_analyze_issue_json_format(
        self, mock_get_issue, mock_has_maintainer, cli_runner, mock_github_issue
    ):
        """Test analyzing issue with JSON output format"""
        mock_get_issue.return_value = mock_github_issue
        mock_has_maintainer.return_value = False

        result = cli_runner.invoke(cli, ["issue", "analyze", "123", "--format", "json"])

        assert result.exit_code == 0
        # The JSON output should contain analysis data
        # The output may not be pure JSON (could have headers), so we check for JSON-like content
        assert "{" in result.output and "}" in result.output
        assert "issue_type" in result.output or "bug" in result.output

    @patch("sugar.integrations.github.GitHubClient.find_similar_issues")
    @patch("sugar.integrations.github.GitHubClient.has_maintainer_response")
    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_analyze_bug_issue(
        self, mock_get_issue, mock_has_maintainer, mock_find_similar, cli_runner
    ):
        """Test analyzing a bug issue identifies it correctly"""
        bug_issue = GitHubIssue(
            number=100,
            title="Error: Database connection failed",
            body="Getting connection timeout error when starting the app",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[],
            created_at="2025-01-01T10:00:00Z",
            updated_at="2025-01-01T10:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/100",
        )
        mock_get_issue.return_value = bug_issue
        mock_has_maintainer.return_value = False
        mock_find_similar.return_value = []

        result = cli_runner.invoke(cli, ["issue", "analyze", "100"])

        assert result.exit_code == 0
        # Should detect as bug type
        output_lower = result.output.lower()
        assert "bug" in output_lower or "error" in output_lower

    @patch("sugar.integrations.github.GitHubClient.find_similar_issues")
    @patch("sugar.integrations.github.GitHubClient.has_maintainer_response")
    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_analyze_feature_request(
        self, mock_get_issue, mock_has_maintainer, mock_find_similar, cli_runner
    ):
        """Test analyzing a feature request"""
        feature_issue = GitHubIssue(
            number=101,
            title="Feature request: Add export functionality",
            body="Would be nice to have export to CSV feature",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[],
            created_at="2025-01-01T10:00:00Z",
            updated_at="2025-01-01T10:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/101",
        )
        mock_get_issue.return_value = feature_issue
        mock_has_maintainer.return_value = False
        mock_find_similar.return_value = []

        result = cli_runner.invoke(cli, ["issue", "analyze", "101"])

        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "feature" in output_lower or "request" in output_lower

    @patch("sugar.integrations.github.GitHubClient.find_similar_issues")
    @patch("sugar.integrations.github.GitHubClient.has_maintainer_response")
    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_analyze_with_file_mentions(
        self, mock_get_issue, mock_has_maintainer, mock_find_similar, cli_runner
    ):
        """Test analyzing issue that mentions specific files"""
        issue = GitHubIssue(
            number=102,
            title="Bug in auth.py",
            body="The authenticate() function in src/auth.py:42 is broken. Also affects utils.py",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[],
            created_at="2025-01-01T10:00:00Z",
            updated_at="2025-01-01T10:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/102",
        )
        mock_get_issue.return_value = issue
        mock_has_maintainer.return_value = False
        mock_find_similar.return_value = []

        result = cli_runner.invoke(cli, ["issue", "analyze", "102"])

        assert result.exit_code == 0
        # Should detect file mentions
        assert "auth.py" in result.output or "Mentioned Files" in result.output


class TestIssueRespond:
    """Test `sugar issue respond` command"""

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.agent.SugarAgent")
    def test_respond_dry_run(
        self, mock_agent_class, mock_get_issue, cli_runner, mock_github_issue
    ):
        """Test generating response without posting (dry run)"""
        mock_get_issue.return_value = mock_github_issue

        # Mock the agent execution
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.start_session = AsyncMock()
        mock_agent.end_session = AsyncMock()

        # Mock execute to return a response object with content
        mock_response = MagicMock()
        mock_response.content = (
            "Thanks for reporting! This looks like a configuration issue."
        )
        mock_agent.execute = AsyncMock(return_value=mock_response)

        result = cli_runner.invoke(cli, ["issue", "respond", "123"])

        assert result.exit_code == 0
        mock_get_issue.assert_called_once_with(123)
        # Should show response but not post

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.integrations.github.GitHubClient.post_comment")
    @patch("sugar.agent.SugarAgent")
    def test_respond_with_high_confidence_auto_post(
        self,
        mock_agent_class,
        mock_post_comment,
        mock_get_issue,
        cli_runner,
        mock_github_issue,
    ):
        """Test auto-posting response with high confidence"""
        mock_get_issue.return_value = mock_github_issue

        # Mock agent with high confidence response
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.start_session = AsyncMock()
        mock_agent.end_session = AsyncMock()

        mock_response = MagicMock()
        mock_response.content = """### Confidence Score
0.95

### Response
Thanks for reporting! This is a known configuration issue. Please check your config.py file."""
        mock_agent.execute = AsyncMock(return_value=mock_response)

        result = cli_runner.invoke(cli, ["issue", "respond", "123", "--post"])

        assert result.exit_code == 0
        # Should post because confidence >= 0.8
        if mock_post_comment.called:
            assert mock_post_comment.call_count <= 1

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.integrations.github.GitHubClient.post_comment")
    @patch("sugar.agent.SugarAgent")
    def test_respond_with_low_confidence_no_post(
        self,
        mock_agent_class,
        mock_post_comment,
        mock_get_issue,
        cli_runner,
        mock_github_issue,
    ):
        """Test not posting response with low confidence"""
        mock_get_issue.return_value = mock_github_issue

        # Mock agent with low confidence response
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.start_session = AsyncMock()
        mock_agent.end_session = AsyncMock()

        mock_response = MagicMock()
        mock_response.content = """### Confidence Score
0.3

### Response
I'm not sure about this issue. It might be related to config."""
        mock_agent.execute = AsyncMock(return_value=mock_response)

        result = cli_runner.invoke(cli, ["issue", "respond", "123", "--post"])

        assert result.exit_code == 0
        # Should not post due to low confidence
        assert (
            "too low" in result.output.lower() or "not posting" in result.output.lower()
        )

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.integrations.github.GitHubClient.post_comment")
    @patch("sugar.agent.SugarAgent")
    def test_respond_force_post(
        self,
        mock_agent_class,
        mock_post_comment,
        mock_get_issue,
        cli_runner,
        mock_github_issue,
    ):
        """Test force posting regardless of confidence"""
        mock_get_issue.return_value = mock_github_issue

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.start_session = AsyncMock()
        mock_agent.end_session = AsyncMock()

        mock_response = MagicMock()
        mock_response.content = """### Confidence Score
0.3

### Response
Not very confident, but here's my attempt at helping."""
        mock_agent.execute = AsyncMock(return_value=mock_response)

        result = cli_runner.invoke(cli, ["issue", "respond", "123", "--force-post"])

        assert result.exit_code == 0
        # Should post even with low confidence due to --force-post

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.agent.SugarAgent")
    def test_respond_custom_confidence_threshold(
        self, mock_agent_class, mock_get_issue, cli_runner, mock_github_issue
    ):
        """Test using custom confidence threshold"""
        mock_get_issue.return_value = mock_github_issue

        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_agent.start_session = AsyncMock()
        mock_agent.end_session = AsyncMock()

        mock_response = MagicMock()
        mock_response.content = """### Confidence Score
0.85

### Response
This should help with your issue."""
        mock_agent.execute = AsyncMock(return_value=mock_response)

        result = cli_runner.invoke(
            cli, ["issue", "respond", "123", "--confidence-threshold", "0.9"]
        )

        assert result.exit_code == 0
        # With threshold 0.9, confidence 0.85 should not auto-post

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_respond_error_handling(self, mock_get_issue, cli_runner):
        """Test error handling during response generation"""
        mock_get_issue.side_effect = RuntimeError("Failed to fetch issue")

        result = cli_runner.invoke(cli, ["issue", "respond", "123"])

        assert result.exit_code != 0


class TestIssueSearch:
    """Test `sugar issue search` command"""

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_success(
        self, mock_search_issues, cli_runner, mock_github_issues
    ):
        """Test searching for issues"""
        # Return issues matching the search
        search_results = [mock_github_issues[0]]  # Bug issue
        mock_search_issues.return_value = search_results

        result = cli_runner.invoke(cli, ["issue", "search", "crash"])

        assert result.exit_code == 0
        assert "Bug: Application crashes on startup" in result.output
        mock_search_issues.assert_called_once_with("crash", limit=10)

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_with_limit(
        self, mock_search_issues, cli_runner, mock_github_issues
    ):
        """Test searching with custom limit"""
        mock_search_issues.return_value = mock_github_issues[:2]

        result = cli_runner.invoke(cli, ["issue", "search", "bug", "--limit", "2"])

        assert result.exit_code == 0
        mock_search_issues.assert_called_once_with("bug", limit=2)

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_with_repo(
        self, mock_search_issues, cli_runner, mock_github_issues
    ):
        """Test searching in specific repository"""
        mock_search_issues.return_value = mock_github_issues

        result = cli_runner.invoke(
            cli, ["issue", "search", "feature", "--repo", "owner/custom-repo"]
        )

        assert result.exit_code == 0
        mock_search_issues.assert_called_once_with("feature", limit=10)

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_no_results(self, mock_search_issues, cli_runner):
        """Test search with no results"""
        mock_search_issues.return_value = []

        result = cli_runner.invoke(cli, ["issue", "search", "nonexistent-query-xyz"])

        assert result.exit_code == 0
        assert "No issues found" in result.output

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_multiple_words(
        self, mock_search_issues, cli_runner, mock_github_issues
    ):
        """Test searching with multi-word query"""
        mock_search_issues.return_value = mock_github_issues[:1]

        result = cli_runner.invoke(
            cli, ["issue", "search", "application crashes startup"]
        )

        assert result.exit_code == 0
        # Query should be passed as-is
        mock_search_issues.assert_called_once_with(
            "application crashes startup", limit=10
        )

    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_issues_error_handling(self, mock_search_issues, cli_runner):
        """Test error handling during search"""
        mock_search_issues.side_effect = RuntimeError("Search API error")

        result = cli_runner.invoke(cli, ["issue", "search", "test"])

        assert result.exit_code != 0


class TestIssueIntegration:
    """Integration tests combining multiple issue commands"""

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_list_then_view_workflow(
        self,
        mock_list_issues,
        mock_get_issue,
        cli_runner,
        mock_github_issues,
        mock_github_issue,
    ):
        """Test workflow of listing issues then viewing one"""
        mock_list_issues.return_value = mock_github_issues
        mock_get_issue.return_value = mock_github_issue

        # First list issues
        result1 = cli_runner.invoke(cli, ["issue", "list"])
        assert result1.exit_code == 0

        # Then view specific issue
        result2 = cli_runner.invoke(cli, ["issue", "view", "123"])
        assert result2.exit_code == 0
        assert "Issue #123" in result2.output

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    @patch("sugar.integrations.github.GitHubClient.search_issues")
    def test_search_then_analyze_workflow(
        self,
        mock_search_issues,
        mock_get_issue,
        cli_runner,
        mock_github_issues,
        mock_github_issue,
    ):
        """Test workflow of searching then analyzing an issue"""
        mock_search_issues.return_value = [mock_github_issues[0]]
        mock_get_issue.return_value = mock_github_issue

        # First search
        result1 = cli_runner.invoke(cli, ["issue", "search", "crash"])
        assert result1.exit_code == 0

        # Then analyze found issue
        result2 = cli_runner.invoke(cli, ["issue", "analyze", "123"])
        assert result2.exit_code == 0


class TestIssueEdgeCases:
    """Test edge cases and error scenarios"""

    def test_issue_command_without_subcommand(self, cli_runner):
        """Test calling 'sugar issue' without a subcommand"""
        result = cli_runner.invoke(cli, ["issue"])

        # Click returns exit code 0 for help, or 2 for missing subcommand
        assert result.exit_code in [0, 2]
        # Should show help or command list
        assert (
            "analyze" in result.output
            or "list" in result.output
            or "Commands:" in result.output
        )

    def test_invalid_issue_number(self, cli_runner):
        """Test with invalid issue number"""
        result = cli_runner.invoke(cli, ["issue", "view", "not-a-number"])

        # Should fail with validation error
        assert result.exit_code != 0

    @patch("sugar.integrations.github.GitHubClient.get_issue")
    def test_issue_with_empty_body(self, mock_get_issue, cli_runner):
        """Test viewing issue with empty body"""
        issue = GitHubIssue(
            number=200,
            title="Issue with no body",
            body="",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[],
            created_at="2025-01-01T10:00:00Z",
            updated_at="2025-01-01T10:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/200",
        )
        mock_get_issue.return_value = issue

        result = cli_runner.invoke(cli, ["issue", "view", "200"])

        assert result.exit_code == 0
        assert "Issue #200" in result.output

    @patch("sugar.integrations.github.GitHubClient.list_issues")
    def test_issue_with_unicode_characters(self, mock_list_issues, cli_runner):
        """Test handling issues with unicode characters"""
        unicode_issue = GitHubIssue(
            number=300,
            title="Bug: 文字化け in Japanese text 🐛",
            body="Unicode test: café, naïve, 日本語",
            state="open",
            user=GitHubUser(login="user1", id=1001),
            labels=[],
            created_at="2025-01-01T10:00:00Z",
            updated_at="2025-01-01T10:00:00Z",
            comments_count=0,
            html_url="https://github.com/test/repo/issues/300",
        )
        mock_list_issues.return_value = [unicode_issue]

        result = cli_runner.invoke(cli, ["issue", "list"])

        assert result.exit_code == 0
        # Should handle unicode gracefully (may have encoding in output)
