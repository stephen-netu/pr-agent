# Local llama.cpp Server & PR-Agent Multi-Diff Setup

This host runs llama.cpp as a launch agent and points pr-agent to it for multi-diff reviews.

## llama.cpp service (launchd)
- LaunchAgent: `~/Library/LaunchAgents/netu.llama-server.plist`
- Startup script: `/Users/netu/bin/start-llama-server.sh`
- Model: `/Users/netu/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf`
- Host/Port: `0.0.0.0:8000`
- API key: `local-qodo-secret`
- Context: `16384`
- Logs: `/Users/netu/Logs/llama-server.log`

Reload launch agent after script changes:
```bash
launchctl unload ~/Library/LaunchAgents/netu.llama-server.plist
launchctl load ~/Library/LaunchAgents/netu.llama-server.plist
```

Check status:
```bash
launchctl list netu.llama-server
```

Tail logs:
```bash
tail -n 40 /Users/netu/Logs/llama-server.log
```

## pr-agent config (repo-scoped)
- File: `.pr_agent.toml` at repo root
- Settings:
  - `[config]` model `qwen2.5-coder-7b-instruct-q4_k_m.gguf`, `max_model_tokens = 12000`, `custom_model_max_tokens = 12000`, `temperature = 0.2`
  - `[openai]` `api_base = "http://<mac-mini>:8000/v1"`, `key = "local-qodo-secret"`
  - `[pr_reviewer]` `enable_multi_diff = true`, `max_diff_calls = 2`

## Quick validation
- Unit tests: `python -m pytest tests/unittest/test_multi_diff_merge.py -v`
- Mock merge check (no LLM): `python scripts/test_multi_diff_local.py --mock`
- Real PR check: `python scripts/test_multi_diff_local.py --pr-url "<gitea_pr_url>"`

## Notes
- If you change the model or port, update both `start-llama-server.sh` and `.pr_agent.toml`.
- Multi-diff is on by default to avoid context overruns on large PRs.
