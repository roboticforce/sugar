"""
Sugar MCP Server Implementation

Provides MCP (Model Context Protocol) server for Sugar, exposing tools for:
- Issue analysis
- Response generation
- Codebase search
- Similar issue detection
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import mcp as mcp_sdk
from mcp.server import Server as MCPServer
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    """MCP server configuration"""

    host: str = "0.0.0.0"
    port: int = 8080
    anthropic_api_key: Optional[str] = None
    github_token: Optional[str] = None
    default_repo: Optional[str] = None


class SugarMCPServer:
    """
    Sugar MCP Server

    Exposes Sugar functionality via the Model Context Protocol for use with
    GitHub Copilot Custom Agents and other MCP clients.
    """

    def __init__(self, config: Optional[ServerConfig] = None):
        """Initialize the MCP server"""
        self.config = config or ServerConfig()
        self.server = MCPServer("sugar")
        self._setup_tools()

    def _setup_tools(self):
        """Register MCP tools"""

        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [
                Tool(
                    name="analyze_issue",
                    description="Analyze a GitHub issue and return insights",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_number": {
                                "type": "integer",
                                "description": "The issue number to analyze",
                            },
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (optional)",
                            },
                        },
                        "required": ["issue_number"],
                    },
                ),
                Tool(
                    name="generate_response",
                    description="Generate a response for a GitHub issue",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_number": {
                                "type": "integer",
                                "description": "The issue number to respond to",
                            },
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (optional)",
                            },
                            "context": {
                                "type": "string",
                                "description": "Additional context for the response",
                            },
                        },
                        "required": ["issue_number"],
                    },
                ),
                Tool(
                    name="search_codebase",
                    description="Search the codebase for relevant code",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query",
                            },
                            "file_pattern": {
                                "type": "string",
                                "description": "File pattern to search (e.g., '*.py')",
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="find_similar_issues",
                    description="Find issues similar to a given issue",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_number": {
                                "type": "integer",
                                "description": "The issue number to find similar issues for",
                            },
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (optional)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of similar issues to return",
                                "default": 5,
                            },
                        },
                        "required": ["issue_number"],
                    },
                ),
                Tool(
                    name="suggest_labels",
                    description="Suggest labels for an issue based on its content",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "issue_number": {
                                "type": "integer",
                                "description": "The issue number to suggest labels for",
                            },
                            "repo": {
                                "type": "string",
                                "description": "Repository in owner/repo format (optional)",
                            },
                        },
                        "required": ["issue_number"],
                    },
                ),
                Tool(
                    name="validate_response",
                    description="Validate a response before posting",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "response": {
                                "type": "string",
                                "description": "The response to validate",
                            },
                            "issue_number": {
                                "type": "integer",
                                "description": "The issue number (for context)",
                            },
                        },
                        "required": ["response"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Handle tool calls"""
            try:
                if name == "analyze_issue":
                    result = await self._analyze_issue(
                        arguments["issue_number"],
                        arguments.get("repo"),
                    )
                elif name == "generate_response":
                    result = await self._generate_response(
                        arguments["issue_number"],
                        arguments.get("repo"),
                        arguments.get("context"),
                    )
                elif name == "search_codebase":
                    result = await self._search_codebase(
                        arguments["query"],
                        arguments.get("file_pattern"),
                    )
                elif name == "find_similar_issues":
                    result = await self._find_similar_issues(
                        arguments["issue_number"],
                        arguments.get("repo"),
                        arguments.get("limit", 5),
                    )
                elif name == "suggest_labels":
                    result = await self._suggest_labels(
                        arguments["issue_number"],
                        arguments.get("repo"),
                    )
                elif name == "validate_response":
                    result = await self._validate_response(
                        arguments["response"],
                        arguments.get("issue_number"),
                    )
                else:
                    result = {"error": f"Unknown tool: {name}"}

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            except Exception as e:
                logger.error(f"Tool {name} failed: {e}")
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _analyze_issue(
        self,
        issue_number: int,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze a GitHub issue"""
        from sugar.integrations import GitHubClient
        from sugar.profiles import IssueResponderProfile

        repo = repo or self.config.default_repo
        if not repo:
            return {"error": "No repository specified"}

        github = GitHubClient(repo=repo, token=self.config.github_token)
        issue = github.get_issue(issue_number)

        profile = IssueResponderProfile()
        input_data = {"issue": issue.to_dict(), "repo": repo}
        processed = await profile.process_input(input_data)

        return {
            "issue_number": issue_number,
            "title": issue.title,
            "state": issue.state,
            "author": issue.user.login,
            "labels": [l.name for l in issue.labels],
            "analysis": processed.get("pre_analysis", {}),
            "has_maintainer_response": github.has_maintainer_response(issue),
        }

    async def _generate_response(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a response for an issue"""
        from sugar.agent import SugarAgent, SugarAgentConfig
        from sugar.integrations import GitHubClient
        from sugar.profiles import IssueResponderProfile

        repo = repo or self.config.default_repo
        if not repo:
            return {"error": "No repository specified"}

        github = GitHubClient(repo=repo, token=self.config.github_token)
        issue = github.get_issue(issue_number)

        profile = IssueResponderProfile()
        input_data = {"issue": issue.to_dict(), "repo": repo}
        processed = await profile.process_input(input_data)

        # Add extra context if provided
        prompt = processed["prompt"]
        if context:
            prompt += f"\n\nAdditional Context:\n{context}"

        # Run agent
        agent_config = SugarAgentConfig(
            model=os.environ.get("SUGAR_MODEL", "claude-sonnet-4-20250514"),
        )
        agent = SugarAgent(config=agent_config)

        try:
            response = await agent.execute(prompt, task_context=f"Repository: {repo}")
            result = await profile.process_output(
                {
                    "content": response.content,
                    "success": response.success,
                }
            )
            return result.get("response", {})
        finally:
            await agent.end_session()

    async def _search_codebase(
        self,
        query: str,
        file_pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search the codebase"""
        import re
        import subprocess

        # Validate file_pattern to prevent injection via glob argument
        if file_pattern:
            if not re.match(r"^[a-zA-Z0-9*._\-]+$", file_pattern):
                raise ValueError(
                    f"Invalid file pattern: {file_pattern}. "
                    "Only alphanumeric characters, *, ., _, and - are allowed."
                )

        # Use -- to separate options from the query argument,
        # preventing flag injection if query starts with -
        cmd = ["grep", "-r", "-n", "--", query, "."]
        if file_pattern:
            cmd = ["grep", "-r", "-n", "--include", file_pattern, "--", query, "."]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            lines = result.stdout.strip().split("\n")[:20]  # Limit results
            return {
                "query": query,
                "matches": [{"line": line} for line in lines if line],
                "total_matches": len(lines),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _find_similar_issues(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Find similar issues"""
        from sugar.integrations import GitHubClient

        repo = repo or self.config.default_repo
        if not repo:
            return {"error": "No repository specified"}

        github = GitHubClient(repo=repo, token=self.config.github_token)
        issue = github.get_issue(issue_number)
        similar = github.find_similar_issues(issue, limit=limit)

        return {
            "issue_number": issue_number,
            "similar_issues": [
                {
                    "number": i.number,
                    "title": i.title,
                    "state": i.state,
                    "url": i.html_url,
                }
                for i in similar
            ],
        }

    async def _suggest_labels(
        self,
        issue_number: int,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Suggest labels for an issue"""
        from sugar.integrations import GitHubClient
        from sugar.profiles import IssueResponderProfile

        repo = repo or self.config.default_repo
        if not repo:
            return {"error": "No repository specified"}

        github = GitHubClient(repo=repo, token=self.config.github_token)
        issue = github.get_issue(issue_number)

        profile = IssueResponderProfile()
        input_data = {"issue": issue.to_dict(), "repo": repo}
        processed = await profile.process_input(input_data)

        analysis = processed.get("pre_analysis", {})
        issue_type = analysis.get("issue_type", "question")

        # Suggest labels based on analysis
        suggested = []
        if issue_type == "bug":
            suggested.append("bug")
        elif issue_type == "feature":
            suggested.append("enhancement")
        elif issue_type == "documentation":
            suggested.append("documentation")
        else:
            suggested.append("question")

        return {
            "issue_number": issue_number,
            "suggested_labels": suggested,
            "analysis": analysis,
        }

    async def _validate_response(
        self,
        response: str,
        issue_number: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Validate a response before posting"""
        issues = []

        # Check length
        if len(response) < 50:
            issues.append("Response is too short")
        if len(response) > 5000:
            issues.append("Response is too long")

        # Check for common issues
        if "I am an AI" in response or "As an AI" in response:
            issues.append("Response contains AI self-reference")

        # Check for potential sensitive content
        sensitive_patterns = ["api_key", "password", "secret", "token"]
        for pattern in sensitive_patterns:
            if pattern.lower() in response.lower():
                issues.append(f"Response may contain sensitive content: {pattern}")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "response_length": len(response),
        }

    async def run(self):
        """Run the MCP server"""
        transport = SseServerTransport("/mcp")
        logger.info(
            f"Starting Sugar MCP server on {self.config.host}:{self.config.port}"
        )

        # Note: In production, you'd use a proper ASGI server like uvicorn
        # This is a simplified example
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Mount

        app = Starlette(
            routes=[
                Mount("/mcp", app=transport.get_asgi_app()),
            ],
        )

        await uvicorn.Server(
            uvicorn.Config(
                app,
                host=self.config.host,
                port=self.config.port,
            )
        ).serve()


def create_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    anthropic_api_key: Optional[str] = None,
    github_token: Optional[str] = None,
    default_repo: Optional[str] = None,
) -> SugarMCPServer:
    """Factory function to create a Sugar MCP server"""
    config = ServerConfig(
        host=host,
        port=port,
        anthropic_api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
        github_token=github_token or os.environ.get("GITHUB_TOKEN"),
        default_repo=default_repo or os.environ.get("SUGAR_DEFAULT_REPO"),
    )
    return SugarMCPServer(config)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    server = create_server()
    asyncio.run(server.run())
