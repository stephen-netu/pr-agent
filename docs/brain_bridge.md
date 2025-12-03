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
mcp_enable = true
mcp_bin = "/opt/prism-rust/target/release/brain-mcp"
mcp_root = "/opt/prism-rust"  # Repo root, NOT .brain
mcp_default_slice = "runtime"
mcp_timeout_seconds = 6.0
mcp_max_modules = 5
mcp_max_risks = 8
mcp_max_jobs = 6
```

## Environment Variables

All configuration keys use the `BRAIN__MCP_` prefix for environment variables:

| Variable | Maps To | Description |
|----------|---------|-------------|
| `BRAIN__MCP_ENABLE` | `brain.mcp_enable` | Enable/disable the bridge |
| `BRAIN__MCP_BIN` | `brain.mcp_bin` | Path to the Brain MCP binary |
| `BRAIN__MCP_ROOT` | `brain.mcp_root` | Path to the repository root (containing `.brain` directory) |
| `BRAIN__MCP_DEFAULT_SLICE` | `brain.mcp_default_slice` | Default Brain slice for queries |
| `BRAIN__MCP_TIMEOUT_SECONDS` | `brain.mcp_timeout_seconds` | Timeout for Brain MCP calls |
| `BRAIN__MCP_MAX_MODULES` | `brain.mcp_max_modules` | Max modules to include in context |
| `BRAIN__MCP_MAX_RISKS` | `brain.mcp_max_risks` | Max risks to show per module |
| `BRAIN__MCP_MAX_JOBS` | `brain.mcp_max_jobs` | Max CI jobs to show in context |

## Usage

When enabled, the bridge automatically runs on PR events (PR open, reopen, synchronize). It:
1. Analyzes changed files.
2. Queries Brain MCP.
3. Writes `BRAIN_QODO_CONTEXT.md` to the repo root.
4. Appends instructions to the AI reviewer.

### Current Scope

**Auto Commands Only**: The bridge currently injects Brain context for **auto commands** triggered by PR events (`gitea.pr_commands` and `gitea.push_commands`).

**Comment Commands**: Comment-based commands (e.g., `/review` as a PR comment) do **not** currently use Brain context. This is planned for a future enhancement.
