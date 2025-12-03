# Upstream Base

- **Upstream URL:** https://github.com/Codium-ai/pr-agent
- **Base Commit:** ede3f82143b7e539b44670967685fd8b4e7bc297

## Local Changes

- **Brain-Qodo Bridge:**
  - Added `pr_agent/brain/` package for Brain MCP integration.
  - Implemented `prepare_brain_context` hook in `gitea_app.py`.
  - Added `[brain]` configuration section.
- **Gitea Integration:**
  - Enhanced `gitea_app.py` to inject Brain context into `extra_instructions`.
