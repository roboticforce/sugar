"""
Tests for MCP Server functionality

Tests the Sugar MCP server tools without requiring full MCP SDK installation.
"""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from subprocess import CompletedProcess

# Try to import MCP components, skip tests if not available
try:
    from sugar.mcp.server import (
        ServerConfig,
        SugarMCPServer,
        create_server,
    )

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    ServerConfig = None
    SugarMCPServer = None
    create_server = None


# =============================================================================
# ServerConfig Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestServerConfig:
    """Tests for ServerConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = ServerConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.anthropic_api_key is None
        assert config.github_token is None
        assert config.default_repo is None

    def test_custom_values(self):
        """Test custom configuration values"""
        config = ServerConfig(
            host="127.0.0.1",
            port=9090,
            anthropic_api_key="test-key",
            github_token="gh-token",
            default_repo="owner/repo",
        )
        assert config.host == "127.0.0.1"
        assert config.port == 9090
        assert config.anthropic_api_key == "test-key"
        assert config.github_token == "gh-token"
        assert config.default_repo == "owner/repo"


# =============================================================================
# SugarMCPServer Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarMCPServer:
    """Tests for SugarMCPServer class"""

    def test_init_default_config(self):
        """Test server initialization with default config"""
        server = SugarMCPServer()
        assert server.config.host == "0.0.0.0"
        assert server.config.port == 8080

    def test_init_custom_config(self):
        """Test server initialization with custom config"""
        config = ServerConfig(port=3000)
        server = SugarMCPServer(config=config)
        assert server.config.port == 3000


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestCreateServer:
    """Tests for create_server factory function"""

    def test_create_server_default(self):
        """Test creating server with defaults"""
        server = create_server()
        assert server.config.host == "0.0.0.0"
        assert server.config.port == 8080

    def test_create_server_custom(self):
        """Test creating server with custom parameters"""
        server = create_server(
            host="localhost",
            port=8081,
            default_repo="test/repo",
        )
        assert server.config.host == "localhost"
        assert server.config.port == 8081
        assert server.config.default_repo == "test/repo"


# =============================================================================
# MCP Tool Handler Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestAnalyzeIssueTool:
    """Tests for the analyze_issue MCP tool"""

    @pytest.mark.asyncio
    @patch("sugar.integrations.GitHubClient")
    async def test_analyze_issue_success(self, mock_client_class):
        """Test successful issue analysis"""
        # Setup mock
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Create a mock issue with to_dict method
        mock_issue = MagicMock()
        mock_issue.number = 42
        mock_issue.title = "Bug: Something broken"
        mock_issue.body = "Description of the bug with error trace"
        mock_issue.to_dict.return_value = {
            "number": 42,
            "title": "Bug: Something broken",
            "body": "Description of the bug with error trace",
            "labels": [{"name": "bug"}],
            "user": {"login": "reporter"},
            "state": "open",
            "comments": [],
        }
        mock_client.get_issue.return_value = mock_issue

        server = SugarMCPServer()
        result = await server._analyze_issue(42, "owner/repo")

        # Result should have processed data from IssueResponderProfile
        assert "issue" in result or "analysis" in result or not result.get("error")
        mock_client.get_issue.assert_called_once_with(42)


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSearchCodebaseTool:
    """Tests for the search_codebase MCP tool"""

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_search_codebase_success(self, mock_run):
        """Test successful codebase search"""
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="file.py:10:matching line\nother.py:20:another match\n",
            stderr="",
        )

        server = SugarMCPServer()
        result = await server._search_codebase("query", "*.py")

        assert "matches" in result
        assert result["query"] == "query"
        assert len(result["matches"]) == 2
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_search_codebase_no_results(self, mock_run):
        """Test search with no results"""
        mock_run.return_value = CompletedProcess(
            args=[],
            returncode=1,  # grep returns 1 when no matches
            stdout="",
            stderr="",
        )

        server = SugarMCPServer()
        result = await server._search_codebase("nonexistent", "*.py")

        assert "matches" in result
        assert result["matches"] == [] or len(result["matches"]) == 0


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestValidateResponseTool:
    """Tests for the validate_response MCP tool"""

    @pytest.mark.asyncio
    async def test_validate_response_valid(self):
        """Test validating a valid response"""
        server = SugarMCPServer()
        result = await server._validate_response(
            "Thank you for reporting this. I'll investigate the issue.",
            42,
        )

        assert "valid" in result
        assert "response_length" in result

    @pytest.mark.asyncio
    async def test_validate_response_too_short(self):
        """Test validating a too-short response"""
        server = SugarMCPServer()
        result = await server._validate_response("OK", 42)

        # Response should still be marked as valid (implementation dependent)
        assert "response_length" in result


# =============================================================================
# CLI Integration Tests
# =============================================================================

# Check if CLI can be imported (requires claude_agent_sdk)
try:
    from sugar.main import cli

    CLI_AVAILABLE = True
except ImportError:
    CLI_AVAILABLE = False
    cli = None


@pytest.mark.skipif(not CLI_AVAILABLE, reason="CLI dependencies not installed")
class TestMCPCLI:
    """Tests for MCP CLI command"""

    def test_mcp_serve_help(self):
        """Test mcp serve --help works"""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "serve", "--help"])

        assert result.exit_code == 0
        assert "Start the Sugar MCP server" in result.output
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--repo" in result.output

    def test_mcp_group_help(self):
        """Test mcp --help works"""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--help"])

        assert result.exit_code == 0
        assert "MCP" in result.output or "Model Context Protocol" in result.output
