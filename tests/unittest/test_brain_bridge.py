import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pr_agent.brain.bridge import BrainContextResult, PRMetadata, prepare_brain_context

@pytest.fixture
def mock_settings():
    with patch("pr_agent.brain.bridge.get_settings") as mock:
        mock.return_value.brain.mcp_enable = True
        mock.return_value.brain.mcp_default_slice = "runtime"
        mock.return_value.brain.mcp_max_modules = 2
        mock.return_value.brain.mcp_max_risks = 2
        yield mock

@pytest.fixture
def mock_brain_client():
    with patch("pr_agent.brain.bridge.BrainClientWrapper") as mock:
        client_instance = AsyncMock()
        mock.return_value.__aenter__.return_value = client_instance
        yield client_instance

@pytest.mark.asyncio
async def test_prepare_brain_context_happy_path(mock_settings, mock_brain_client, tmp_path):
    # Setup mocks
    mock_brain_client.client = True # Simulate connected
    mock_brain_client._call_tool_safe.return_value = {
        "impacted_modules": ["crate::mod1", "crate::mod2", "crate::mod3"]
    }
    mock_brain_client.get_ci_run_summary.return_value = {
        "overall_status": "success",
        "jobs": [{"name": "job1", "last_run_status": "success"}]
    }
    mock_brain_client.get_brain_validation_status.return_value = {
        "overall_status": "passed",
        "slices": [{"slice": "runtime", "status": "passed"}]
    }
    mock_brain_client.get_module_contract.return_value = {"summary": "Do stuff"}
    mock_brain_client.get_module_risks.return_value = {
        "risks": [{"id": "R1", "title": "Risk 1"}]
    }

    pr_meta = PRMetadata(
        pr_number=123,
        head_sha="abc",
        base_sha="def",
        changed_files=["src/mod1.rs"]
    )

    result = await prepare_brain_context(pr_meta, tmp_path, "pull_request")

    assert result.status == "ok"
    assert "CI summary: success" in result.extra_instructions
    assert "crate::mod1" in result.extra_instructions

    context_file = tmp_path / "BRAIN_QODO_CONTEXT.md"
    assert context_file.exists()
    content = context_file.read_text()
    assert "Brain MCP snapshot for PR #123" in content
    assert "CI / Brain overall status: **OK**" in content
    assert "crate::mod1" in content
    assert "Risk 1" in content

@pytest.mark.asyncio
async def test_prepare_brain_context_unavailable(mock_settings, mock_brain_client, tmp_path):
    # Simulate client init failure
    mock_brain_client.client = None

    pr_meta = PRMetadata(1, "a", "b", [])
    result = await prepare_brain_context(pr_meta, tmp_path, "pull_request")

    assert result.status == "unavailable"
    assert "Brain MCP context is UNAVAILABLE" in result.extra_instructions

    context_file = tmp_path / "BRAIN_QODO_CONTEXT.md"
    assert context_file.exists()
    assert "Brain MCP could not be queried" in context_file.read_text()

@pytest.mark.asyncio
async def test_prepare_brain_context_partial(mock_settings, mock_brain_client, tmp_path):
    mock_brain_client.client = True
    # Change impact works
    mock_brain_client._call_tool_safe.return_value = {"impacted_modules": ["crate::mod1"]}
    # CI fails (returns None)
    mock_brain_client.get_ci_run_summary.return_value = None
    # Validation works
    mock_brain_client.get_brain_validation_status.return_value = {"overall_status": "passed", "slices": []}

    pr_meta = PRMetadata(1, "a", "b", [])
    result = await prepare_brain_context(pr_meta, tmp_path, "pull_request")

    assert result.status == "partial"
    assert "CI summary: UNKNOWN" in result.extra_instructions

    content = (tmp_path / "BRAIN_QODO_CONTEXT.md").read_text()
    assert "CI / Brain overall status: **PARTIAL**" in content

@pytest.mark.asyncio
async def test_disabled(mock_settings, tmp_path):
    mock_settings.return_value.brain.mcp_enable = False

    pr_meta = PRMetadata(1, "a", "b", [])
    result = await prepare_brain_context(pr_meta, tmp_path, "pull_request")

    assert result.status == "unavailable"
    assert result.extra_instructions == ""
    assert not (tmp_path / "BRAIN_QODO_CONTEXT.md").exists()
