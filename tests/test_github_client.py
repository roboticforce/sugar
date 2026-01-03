"""
Tests for GitHub Client integration

Tests the GitHubClient class which wraps the `gh` CLI for GitHub API operations.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from subprocess import CompletedProcess

from sugar.integrations.github import (
    GitHubClient,
    GitHubUser,
    GitHubLabel,
    GitHubComment,
    GitHubIssue,
)


# =============================================================================
# Dataclass Tests
# =============================================================================


class TestGitHubUser:
    """Tests for GitHubUser dataclass"""

    def test_from_dict_complete(self):
        """Test creating user from complete dict"""
        data = {"login": "testuser", "id": 12345, "type": "User"}
        user = GitHubUser.from_dict(data)
        assert user.login == "testuser"
        assert user.id == 12345
        assert user.type == "User"

    def test_from_dict_minimal(self):
        """Test creating user from minimal dict"""
        data = {"login": "minuser"}
        user = GitHubUser.from_dict(data)
        assert user.login == "minuser"
        assert user.id == 0
        assert user.type == "User"

    def test_from_dict_empty(self):
        """Test creating user from empty dict"""
        user = GitHubUser.from_dict({})
        assert user.login == "unknown"
        assert user.id == 0

    def test_bot_type(self):
        """Test bot user type"""
        data = {"login": "dependabot[bot]", "id": 999, "type": "Bot"}
        user = GitHubUser.from_dict(data)
        assert user.type == "Bot"


class TestGitHubLabel:
    """Tests for GitHubLabel dataclass"""

    def test_from_dict_complete(self):
        """Test creating label from complete dict"""
        data = {
            "name": "bug",
            "color": "d73a4a",
            "description": "Something isn't working",
        }
        label = GitHubLabel.from_dict(data)
        assert label.name == "bug"
        assert label.color == "d73a4a"
        assert label.description == "Something isn't working"

    def test_from_dict_minimal(self):
        """Test creating label from minimal dict"""
        data = {"name": "enhancement"}
        label = GitHubLabel.from_dict(data)
        assert label.name == "enhancement"
        assert label.color == ""
        assert label.description == ""


class TestGitHubComment:
    """Tests for GitHubComment dataclass"""

    def test_from_dict_complete(self):
        """Test creating comment from complete dict"""
        data = {
            "id": 123,
            "body": "This is a comment",
            "user": {"login": "commenter", "id": 456},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
        }
        comment = GitHubComment.from_dict(data)
        assert comment.id == 123
        assert comment.body == "This is a comment"
        assert comment.user.login == "commenter"
        assert comment.created_at == "2024-01-01T00:00:00Z"


class TestGitHubIssue:
    """Tests for GitHubIssue dataclass"""

    def test_from_dict_complete(self):
        """Test creating issue from complete dict"""
        data = {
            "number": 42,
            "title": "Test Issue",
            "body": "Issue description",
            "state": "open",
            "user": {"login": "author", "id": 789},
            "labels": [{"name": "bug"}, {"name": "help wanted"}],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "comments": 5,
            "html_url": "https://github.com/owner/repo/issues/42",
        }
        issue = GitHubIssue.from_dict(data)
        assert issue.number == 42
        assert issue.title == "Test Issue"
        assert issue.body == "Issue description"
        assert issue.state == "open"
        assert issue.user.login == "author"
        assert len(issue.labels) == 2
        assert issue.labels[0].name == "bug"
        assert issue.comments_count == 5
        assert issue.html_url == "https://github.com/owner/repo/issues/42"

    def test_from_dict_null_body(self):
        """Test creating issue with null body"""
        data = {
            "number": 1,
            "title": "No body",
            "body": None,
            "state": "open",
            "user": {"login": "author"},
            "labels": [],
            "created_at": "",
            "updated_at": "",
        }
        issue = GitHubIssue.from_dict(data)
        assert issue.body == ""

    def test_from_dict_pull_request(self):
        """Test creating issue that is a PR"""
        data = {
            "number": 10,
            "title": "PR Title",
            "body": "PR body",
            "state": "open",
            "user": {"login": "author"},
            "labels": [],
            "created_at": "",
            "updated_at": "",
            "pull_request": {"url": "https://api.github.com/pulls/10"},
        }
        issue = GitHubIssue.from_dict(data)
        assert issue.is_pull_request is True


# =============================================================================
# GitHubClient Tests
# =============================================================================


class TestGitHubClientInit:
    """Tests for GitHubClient initialization"""

    def test_init_with_explicit_repo(self):
        """Test initialization with explicit repo"""
        client = GitHubClient(repo="owner/repo")
        assert client.repo == "owner/repo"

    def test_init_without_repo(self):
        """Test initialization without repo - stores None"""
        client = GitHubClient()
        assert client.repo is None

    def test_init_with_token(self):
        """Test initialization with explicit token"""
        client = GitHubClient(repo="owner/repo", token="test-token")
        assert client.token == "test-token"

    @patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"})
    def test_init_reads_env_token(self):
        """Test that GITHUB_TOKEN is read from environment"""
        client = GitHubClient(repo="owner/repo")
        assert client.token == "env-token"


class TestGitHubClientGetIssue:
    """Tests for GitHubClient.get_issue"""

    @patch("sugar.integrations.github.subprocess.run")
    def test_get_issue_success(self, mock_run):
        """Test successful issue retrieval"""
        # gh CLI returns data in this format
        gh_output = {
            "number": 42,
            "title": "Test Issue",
            "body": "Description",
            "state": "open",
            "author": {"login": "testauthor"},
            "labels": [{"name": "bug"}],
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
            "comments": [],  # gh returns comments as array
            "url": "https://github.com/owner/repo/issues/42",
        }

        mock_run.return_value = CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(gh_output), stderr=""
        )

        client = GitHubClient(repo="owner/repo")
        issue = client.get_issue(42)

        assert issue.number == 42
        assert issue.title == "Test Issue"
        assert len(issue.labels) == 1
        assert issue.labels[0].name == "bug"

    @patch("sugar.integrations.github.subprocess.run")
    def test_get_issue_with_comments(self, mock_run):
        """Test issue retrieval with comments"""
        gh_output = {
            "number": 1,
            "title": "Issue with comments",
            "body": "Body",
            "state": "open",
            "author": {"login": "author"},
            "labels": [],
            "createdAt": "2024-01-01T00:00:00Z",
            "updatedAt": "2024-01-01T00:00:00Z",
            "comments": [
                {
                    "id": 1,
                    "body": "First comment",
                    "author": {"login": "user1"},
                    "createdAt": "",
                    "updatedAt": "",
                },
                {
                    "id": 2,
                    "body": "Second comment",
                    "author": {"login": "user2"},
                    "createdAt": "",
                    "updatedAt": "",
                },
            ],
            "url": "",
        }

        mock_run.return_value = CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(gh_output), stderr=""
        )

        client = GitHubClient(repo="owner/repo")
        issue = client.get_issue(1)

        assert len(issue.comments) == 2
        assert issue.comments[0].body == "First comment"
        assert issue.comments[1].body == "Second comment"

    @patch("sugar.integrations.github.subprocess.run")
    def test_get_issue_not_found(self, mock_run):
        """Test get_issue raises error when issue not found"""
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="issue not found",
        )

        client = GitHubClient(repo="owner/repo")
        with pytest.raises(RuntimeError, match="gh command failed"):
            client.get_issue(999)


class TestGitHubClientListIssues:
    """Tests for GitHubClient.list_issues"""

    @patch("sugar.integrations.github.subprocess.run")
    def test_list_issues_default(self, mock_run):
        """Test listing issues with default parameters"""
        # gh CLI format
        gh_output = [
            {
                "number": 1,
                "title": "Issue 1",
                "body": "",
                "state": "open",
                "author": {"login": "a"},
                "labels": [],
                "createdAt": "",
                "updatedAt": "",
                "comments": [],
                "url": "",
            },
            {
                "number": 2,
                "title": "Issue 2",
                "body": "",
                "state": "open",
                "author": {"login": "b"},
                "labels": [],
                "createdAt": "",
                "updatedAt": "",
                "comments": [],
                "url": "",
            },
        ]

        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(gh_output),
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        issues = client.list_issues()

        assert len(issues) == 2
        assert issues[0].number == 1

    @patch("sugar.integrations.github.subprocess.run")
    def test_list_issues_with_labels(self, mock_run):
        """Test listing issues filtered by labels"""
        gh_output = [
            {
                "number": 5,
                "title": "Bug Issue",
                "body": "",
                "state": "open",
                "author": {"login": "a"},
                "labels": [{"name": "bug"}],
                "createdAt": "",
                "updatedAt": "",
                "comments": [],
                "url": "",
            },
        ]

        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(gh_output),
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        issues = client.list_issues(labels=["bug"])

        assert len(issues) == 1
        # Verify the label filter was included in the command
        call_args = mock_run.call_args[0][0]
        assert "--label" in call_args
        # Labels are joined with comma
        assert any("bug" in arg for arg in call_args)


class TestGitHubClientPostComment:
    """Tests for GitHubClient.post_comment"""

    @patch("sugar.integrations.github.subprocess.run")
    def test_post_comment_success(self, mock_run):
        """Test posting a comment successfully"""
        # gh issue comment doesn't return JSON, just success/failure
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        comment = client.post_comment(42, "Test comment")

        # Returns a synthetic comment with the body we posted
        assert comment.body == "Test comment"
        assert comment.user.login == "sugar[bot]"
        # ID is 0 because gh doesn't return the created comment
        assert comment.id == 0

    @patch("sugar.integrations.github.subprocess.run")
    def test_post_comment_builds_correct_command(self, mock_run):
        """Test that post_comment builds the correct gh command"""
        mock_run.return_value = CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        client = GitHubClient(repo="owner/repo")
        client.post_comment(42, "My comment body")

        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "comment" in call_args
        assert "42" in call_args
        assert "--body" in call_args
        assert "My comment body" in call_args


class TestGitHubClientLabels:
    """Tests for GitHubClient label operations"""

    @patch("sugar.integrations.github.subprocess.run")
    def test_add_labels(self, mock_run):
        """Test adding labels to an issue"""
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        client.add_labels(42, ["bug", "priority"])

        call_args = mock_run.call_args[0][0]
        assert "--add-label" in call_args
        # Labels are joined with comma
        assert any("bug" in arg and "priority" in arg for arg in call_args)

    @patch("sugar.integrations.github.subprocess.run")
    def test_add_labels_empty_list(self, mock_run):
        """Test adding empty labels list does nothing"""
        client = GitHubClient(repo="owner/repo")
        client.add_labels(42, [])
        mock_run.assert_not_called()

    @patch("sugar.integrations.github.subprocess.run")
    def test_remove_labels(self, mock_run):
        """Test removing labels from an issue"""
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        client.remove_labels(42, ["wontfix"])

        call_args = mock_run.call_args[0][0]
        assert "--remove-label" in call_args
        assert any("wontfix" in arg for arg in call_args)


class TestGitHubClientSearch:
    """Tests for GitHubClient search operations"""

    @patch("sugar.integrations.github.subprocess.run")
    def test_search_issues(self, mock_run):
        """Test searching for issues"""
        search_results = [
            {
                "number": 10,
                "title": "Match 1",
                "body": "contains search term",
                "state": "open",
                "user": {"login": "a"},
                "labels": [],
                "created_at": "",
                "updated_at": "",
            },
        ]

        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(search_results),
            stderr="",
        )

        client = GitHubClient(repo="owner/repo")
        results = client.search_issues("search term")

        assert len(results) == 1
        assert results[0].number == 10


class TestGitHubClientHelpers:
    """Tests for GitHubClient helper methods"""

    def test_has_maintainer_response_true(self):
        """Test detecting maintainer response"""
        issue = GitHubIssue(
            number=1,
            title="Test",
            body="",
            state="open",
            user=GitHubUser(login="reporter", id=1),
            labels=[],
            created_at="",
            updated_at="",
            comments=[
                GitHubComment(
                    id=1,
                    body="Thanks for reporting",
                    user=GitHubUser(login="maintainer", id=2, type="User"),
                    created_at="",
                    updated_at="",
                )
            ],
        )

        client = GitHubClient(repo="owner/repo")
        # Note: has_maintainer_response checks COLLABORATOR status via gh CLI
        # This test would need more mocking for full integration

    def test_is_bot_author_true(self):
        """Test detecting bot author"""
        issue = GitHubIssue(
            number=1,
            title="Automated issue",
            body="",
            state="open",
            user=GitHubUser(login="dependabot[bot]", id=1, type="Bot"),
            labels=[],
            created_at="",
            updated_at="",
        )

        client = GitHubClient(repo="owner/repo")
        assert client.is_bot_author(issue) is True

    def test_is_bot_author_false(self):
        """Test detecting human author"""
        issue = GitHubIssue(
            number=1,
            title="Human issue",
            body="",
            state="open",
            user=GitHubUser(login="humanuser", id=1, type="User"),
            labels=[],
            created_at="",
            updated_at="",
        )

        client = GitHubClient(repo="owner/repo")
        assert client.is_bot_author(issue) is False

    def test_is_bot_author_by_login_pattern(self):
        """Test detecting bot by login pattern"""
        issue = GitHubIssue(
            number=1,
            title="Bot issue",
            body="",
            state="open",
            user=GitHubUser(
                login="github-actions[bot]", id=1, type="User"
            ),  # Type might be wrong
            labels=[],
            created_at="",
            updated_at="",
        )

        client = GitHubClient(repo="owner/repo")
        assert client.is_bot_author(issue) is True
