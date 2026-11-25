"""Tests for task cwd/project provenance (CLI, MCP, dashboard)."""

import asyncio
import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.main import cli
from sugar.storage.work_queue import WorkQueue

try:
    from sugar.mcp.task_server import create_task_mcp_server

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional MCP extra
    MCP_AVAILABLE = False


def read_tasks(db_path):
    async def go():
        queue = WorkQueue(str(db_path))
        await queue.initialize()
        return await queue.get_recent_work(limit=10)

    return asyncio.run(go())


def make_project(tmp_path):
    (tmp_path / ".sugar").mkdir()
    (tmp_path / ".sugar" / "config.yaml").write_text(
        yaml.dump(
            {
                "sugar": {
                    "storage": {"database": str(tmp_path / ".sugar" / "sugar.db")},
                    "logging": {"file": str(tmp_path / ".sugar" / "sugar.log")},
                }
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


class TestCLIProvenance:
    def test_add_records_cwd_and_project(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as cwd:
            cwd_path = Path(cwd)
            make_project(cwd_path)
            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(cwd_path / ".sugar" / "config.yaml"),
                    "add",
                    "Provenance task",
                ],
            )
            assert result.exit_code == 0, result.output
            tasks = read_tasks(cwd_path / ".sugar" / "sugar.db")
            context = tasks[0]["context"]
            assert context["added_via"] == "sugar_cli"
            assert context["cwd"] == os.getcwd()
            assert context["project"] == os.path.basename(os.getcwd())
            assert Path(context["cwd"]).is_absolute()

    def test_json_input_context_merge_preserves_provenance(self):
        runner = CliRunner()
        with runner.isolated_filesystem() as cwd:
            cwd_path = Path(cwd)
            make_project(cwd_path)
            input_file = cwd_path / "task.json"
            input_file.write_text(
                '{"context": {"custom": "kept", "priority_note": "x"}}',
                encoding="utf-8",
            )
            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(cwd_path / ".sugar" / "config.yaml"),
                    "add",
                    "Merged context task",
                    "--input-file",
                    str(input_file),
                ],
            )
            assert result.exit_code == 0, result.output
            context = read_tasks(cwd_path / ".sugar" / "sugar.db")[0]["context"]
            assert context["custom"] == "kept"
            assert context["priority_note"] == "x"
            assert context["cwd"] == os.getcwd()
            assert context["project"] == os.path.basename(os.getcwd())


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestMCPProvenance:
    def _tool(self):
        mcp = create_task_mcp_server()
        return mcp._tool_manager._tools["sugar_add_task"].fn

    @pytest.mark.asyncio
    async def test_derives_from_server_cwd(self, monkeypatch):
        from unittest.mock import AsyncMock, patch

        queue = AsyncMock()
        queue.initialize = AsyncMock()
        queue.add_work = AsyncMock(return_value="task-1")
        monkeypatch.setattr(
            "sugar.mcp.task_server.os.getcwd", lambda: "/work/derived-project"
        )
        tool = self._tool()
        with patch("sugar.mcp.task_server.get_work_queue", return_value=queue):
            result = await tool(title="Derived provenance")
        assert result["success"] is True
        context = queue.add_work.call_args[0][0]["context"]
        assert context["cwd"] == "/work/derived-project"
        assert context["project"] == "derived-project"
        assert context["added_via"] == "sugar_mcp_tasks"

    @pytest.mark.asyncio
    async def test_explicit_project_and_cwd(self):
        from unittest.mock import AsyncMock, patch

        queue = AsyncMock()
        queue.initialize = AsyncMock()
        queue.add_work = AsyncMock(return_value="task-2")
        tool = self._tool()
        with patch("sugar.mcp.task_server.get_work_queue", return_value=queue):
            result = await tool(
                title="Explicit provenance",
                project="custom-project",
                cwd="/repo/custom",
            )
        assert result["success"] is True
        context = queue.add_work.call_args[0][0]["context"]
        assert context["cwd"] == "/repo/custom"
        assert context["project"] == "custom-project"

    @pytest.mark.asyncio
    async def test_project_cwd_are_optional_tool_args(self):
        mcp = create_task_mcp_server()
        tools = await mcp.list_tools()
        add_task = next(tool for tool in tools if tool.name == "sugar_add_task")
        properties = add_task.inputSchema.get("properties", {})
        assert "project" in properties
        assert "cwd" in properties
        assert add_task.inputSchema.get("required") == ["title"]


class TestDashboardProvenance:
    def test_dashboard_task_records_server_cwd(self, tmp_path):
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
            ingest_socket="",
        )
        with TestClient(app) as client:
            response = client.post("/api/tasks", json={"title": "Dashboard provenance"})
            assert response.status_code == 201, response.text
            task = response.json()["task"]
        context = task["context"]
        assert context["added_via"] == "sugar_dashboard"
        assert context["cwd"] == os.getcwd()
        assert context["project"] == os.path.basename(os.getcwd())
        assert Path(context["cwd"]).is_absolute()
