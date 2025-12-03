# Brain-Qodo Bridge

This fork implements a bridge between Qodo (PR-Agent) and Brain MCP.

## Overview

The bridge injects context from Brain MCP (CI status, change impact, module contracts, risks) into Qodo's review process.

## Components

- **`pr_agent/brain/`**: Contains the bridge logic.
  - `brain_client.py`: JSON-RPC client for `brain-mcp`.
  - `bridge.py`: Orchestrates context gathering and generates `BRAIN_QODO_CONTEXT.md`.
- **`pr_agent/servers/gitea_app.py`**: Modified to call the bridge before executing PR commands.

## Configuration

The bridge is configured via `configuration.toml` (or environment variables):

```toml
[brain]
enable = true
mcp_bin = "/path/to/brain-mcp"
mcp_root = "/path/to/repo/.brain"
default_slice = "runtime"
mcp_timeout_seconds = 6.0
max_modules = 5
max_risks = 8
```

## Environment Variables

| Variable | Maps To | Description |
|----------|---------|-------------|
| `BRAIN__MCP_BIN` | `brain.mcp_bin` | Path to the Brain MCP binary |
| `BRAIN__MCP_ROOT` | `brain.mcp_root` | Path to the repository root containing `.brain` |
| `BRAIN__ENABLE` | `brain.enable` | Enable/disable the bridge |

## Usage

When enabled, the bridge automatically runs on PR events. It:
1. Analyzes changed files.
2. Queries Brain MCP.
3. Writes `BRAIN_QODO_CONTEXT.md` to the repo root.
4. Appends instructions to the AI reviewer.
