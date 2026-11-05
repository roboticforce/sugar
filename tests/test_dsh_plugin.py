"""Structural tests for the DeepSeek Harness plugin bundle (``dsh-plugin/``).

DSH loads a plugin as an npm package whose ``package.json`` declares
``dsh.bundle.patch`` - an ordered list of cordis loader patch files. The bundle
is declarative here (no JavaScript), so the contract worth guarding is the
manifest shape and the patch rows, not behaviour.
"""

import json
from pathlib import Path

import pytest
import yaml

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "dsh-plugin"
MANIFEST = PLUGIN_DIR / "package.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(manifest):
    rows = []
    for rel in manifest["dsh"]["bundle"]["patch"]:
        rows.extend(yaml.safe_load((PLUGIN_DIR / rel).read_text(encoding="utf-8")))
    return rows


def test_manifest_is_a_publishable_package(manifest):
    assert manifest["name"] == "dsh-plugin-sugar"
    assert manifest["version"][0].isdigit()
    assert manifest["description"]
    assert manifest["license"] == "AGPL-3.0-or-later"


def test_declares_a_dsh_bundle_patch(manifest):
    patch = manifest["dsh"]["bundle"]["patch"]
    assert isinstance(patch, list) and patch, "dsh.bundle.patch must be a file list"
    for rel in patch:
        assert not rel.startswith("/"), "patch paths are relative to the package"
        assert (PLUGIN_DIR / rel).is_file(), f"missing bundle patch {rel}"


def test_declares_a_dsh_version_floor(manifest):
    # DSH's pre-install compatibility check reads engines.dsh (falling back to
    # @deepseek-ai/dsh-* peers) and refuses the install when unmet.
    assert manifest["engines"]["dsh"].startswith(">=")


def test_is_declarative_only(manifest):
    """No main/scripts: the bundle only composes entries owned by the runtime."""
    assert "main" not in manifest, "a patch-only bundle should not ship code"
    assert "scripts" not in manifest


def test_patch_adds_the_sugar_memory_mcp_server(entries):
    row = next(r for r in entries if r["id"] == "mcp-sugar")
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"
    config = row["config"]
    assert config["serverName"] == "sugar"
    assert config["transport"] == "stdio"
    assert config["command"] == "sugar"
    assert config["args"] == ["mcp", "memory"]


def test_fails_loudly_when_the_server_cannot_start(entries):
    # A memory plugin that silently ships no tools is indistinguishable from a
    # working one until the user notices the agent never remembers anything.
    row = next(r for r in entries if r["id"] == "mcp-sugar")
    assert row["config"]["failOnStartupError"] is True


def test_patch_rows_are_uniquely_identified(entries):
    ids = [row["id"] for row in entries]
    assert len(ids) == len(set(ids))


def test_readme_documents_the_mcp_extra():
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    # `sugar mcp memory` imports FastMCP, which ships in the mcp extra, not memory.
    assert "sugarai[mcp]" in readme
