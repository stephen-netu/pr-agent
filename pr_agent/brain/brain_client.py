import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from pr_agent.config_loader import get_settings

logger = logging.getLogger(__name__)

class BrainMCPClient:
    def __init__(self, binary_path: Path, mcp_root: Path):
        self.binary_path = binary_path
        self.mcp_root = mcp_root
        self.brain_root = mcp_root / ".brain"
        self.proc: Optional[subprocess.Popen] = None
        self._next_id = 1
        self._start()

    def _start(self):
        env = os.environ.copy()
        env["BRAIN_ROOT"] = str(self.brain_root)  # Points to mcp_root/.brain

        try:
            self.proc = subprocess.Popen(
                [str(self.binary_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,  # Line buffered
            )
            # Initialize
            self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pr-agent-brain-bridge", "version": "0.1.0"}
            })
            self._send_notification("notifications/initialized", {})
        except Exception as e:
            logger.error(f"Failed to start Brain MCP binary at {self.binary_path}: {e}")
            raise

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write_line(payload)

    def _send_request(self, method: str, params: Dict[str, Any]) -> Any:
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("Brain MCP process is not running")

        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self._write_line(payload)

        if self.proc.stdout is None:
            raise RuntimeError("Brain MCP stdout is closed")

        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(
                    "Brain MCP exited before replying to request." + (f" stderr: {stderr}" if stderr else "")
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue

            if response.get("id") == request_id:
                if response.get("error"):
                    raise RuntimeError(response["error"].get("message", "Unknown Brain MCP error"))
                return response.get("result")

    def _write_line(self, payload: Dict[str, Any]) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("Brain MCP stdin is closed")
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Call a Brain MCP tool and return structured JSON when available."""
        raw_result = self._send_request(
            "tools/call", {"name": name, "arguments": arguments}
        )

        if not isinstance(raw_result, dict):
            return raw_result

        content = raw_result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw_text": text}

        return raw_result

    def close(self) -> None:
        if self.proc:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self.proc.terminate()
            self.proc.wait()


class BrainClientWrapper:
    """Async wrapper for BrainMCPClient with settings integration."""

    def __init__(self):
        self.settings = get_settings()
        self.mcp_bin = Path(self.settings.brain.get('mcp_bin', '/opt/prism-rust/target/release/brain-mcp'))
        self.mcp_root = Path(self.settings.brain.get('mcp_root', '/opt/prism-rust'))
        self.timeout = self.settings.brain.get('mcp_timeout_seconds', 6.0)
        self.client: Optional[BrainMCPClient] = None

    async def __aenter__(self):
        try:
            self.client = await asyncio.to_thread(BrainMCPClient, self.mcp_bin, self.mcp_root)
        except Exception as e:
            logger.warning(f"Could not initialize Brain MCP client: {e}")
            self.client = None
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await asyncio.to_thread(self.client.close)

    async def _call_tool_safe(self, name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        if not self.client:
            return None

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.client.call_tool, name, arguments),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Brain MCP tool '{name}' timed out after {self.timeout}s")
            return None
        except Exception as e:
            logger.warning(f"Brain MCP tool '{name}' failed: {e}")
            return None

    async def get_change_impact(self, module_ids: List[str], slice_name: str, dependency_depth: int = 2, dependents_depth: int = 2) -> Optional[Dict[str, Any]]:
        return await self._call_tool_safe("get_change_impact", {
            "slice": slice_name,
            "module_ids": module_ids,
            "dependency_depth": dependency_depth,
            "dependents_depth": dependents_depth
        })

    async def get_ci_run_summary(self) -> Optional[Dict[str, Any]]:
        return await self._call_tool_safe("get_ci_run_summary", {})

    async def get_brain_validation_status(self) -> Optional[Dict[str, Any]]:
        return await self._call_tool_safe("get_brain_validation_status", {})

    async def get_module_contract(self, module_id: str, slice_name: str) -> Optional[Dict[str, Any]]:
        return await self._call_tool_safe("get_module_contract", {
            "slice": slice_name,
            "module_id": module_id
        })

    async def get_module_risks(self, module_id: str, slice_name: str) -> Optional[Dict[str, Any]]:
        return await self._call_tool_safe("get_module_risks", {
            "slice": slice_name,
            "module_id": module_id
        })

    async def get_hotspots_overview(
        self,
        slice_filter: Optional[str] = None,
        limit: Optional[int] = None,
        min_risk_score: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        args: Dict[str, Any] = {}
        if slice_filter:
            args["slice"] = slice_filter
        if limit is not None:
            args["limit"] = limit
        if min_risk_score is not None:
            args["min_risk_score"] = min_risk_score

        return await self._call_tool_safe("get_hotspots_overview", args)

    async def get_change_bundle_suggestions(
        self,
        slice_name: str,
        module_ids: Optional[List[str]] = None,
        paths: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        args: Dict[str, Any] = {"slice": slice_name}
        if module_ids:
            args["module_ids"] = module_ids
        if paths:
            args["paths"] = paths
        if limit is not None:
            args["limit"] = limit

        return await self._call_tool_safe("get_change_bundle_suggestions", args)

    async def get_status_overview(self, top_n_risks: int = 5) -> Optional[Dict[str, Any]]:
        """Fetch overall Brain MCP status snapshot."""
        return await self._call_tool_safe("get_status_overview", {
            "top_n_risks": top_n_risks
        })

    async def get_next_actions(
        self,
        slice_filter: Optional[str] = None,
        max_actions: int = 5
    ) -> Optional[Dict[str, Any]]:
        """Fetch prioritized next actions from Brain MCP."""
        args: Dict[str, Any] = {"max_actions": max_actions}
        if slice_filter:
            args["slice"] = slice_filter

        return await self._call_tool_safe("get_next_actions", args)
