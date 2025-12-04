"""Chat-style Brain query handlers."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pr_agent.brain.brain_client import BrainClientWrapper
from pr_agent.config_loader import get_settings

logger = logging.getLogger(__name__)


async def handle_status_query() -> Dict[str, Any]:
    """Answer "what's the repo state?" questions via Brain MCP."""
    settings = get_settings()
    if not settings.brain.get("mcp_enable", False):
        return {
            "status": "unavailable",
            "headline": "Brain MCP is not enabled",
            "summary_text": "Brain MCP integration is disabled. Enable with `brain.mcp_enable=true`.",
        }

    try:
        async with BrainClientWrapper() as client:
            if not client.client:
                return {
                    "status": "unavailable",
                    "headline": "Brain MCP client failed to initialize",
                    "summary_text": "Could not connect to Brain MCP.",
                }

            top_n = settings.brain.get("mcp_max_risks", 5)
            overview = await client.get_status_overview(top_n_risks=top_n)
            if not overview:
                return {
                    "status": "unavailable",
                    "headline": "Brain MCP returned no data",
                    "summary_text": "Brain query failed.",
                }

            headline = _build_headline(overview)
            summary_text = _format_status_overview(overview)
            return {
                "status": "ok",
                "headline": headline,
                "by_slice": overview.get("by_slice", []),
                "top_risks": overview.get("top_risks", []),
                "ci_drift": overview.get("ci_drift"),
                "quality_gate": overview.get("quality_gate"),
                "top_actions": overview.get("top_actions", []),
                "summary_text": summary_text,
            }
    except Exception as exc:  # pragma: no cover - logging path
        logger.error("Error in handle_status_query", exc_info=exc)
        return {
            "status": "unavailable",
            "headline": f"Brain query failed: {exc}",
            "summary_text": f"Error: {exc}",
        }


async def handle_next_actions_query(
    slice_filter: Optional[str] = None,
    max_actions: int = 5,
) -> Dict[str, Any]:
    """Answer "what should I fix next?" questions via Brain MCP."""
    settings = get_settings()
    if not settings.brain.get("mcp_enable", False):
        return {
            "status": "unavailable",
            "summary_text": "Brain MCP is not enabled.",
        }

    try:
        async with BrainClientWrapper() as client:
            if not client.client:
                return {
                    "status": "unavailable",
                    "summary_text": "Could not connect to Brain MCP.",
                }

            next_actions = await client.get_next_actions(
                slice_filter=slice_filter, max_actions=max_actions
            )
            if not next_actions:
                return {
                    "status": "unavailable",
                    "summary_text": "Brain query failed.",
                }

            summary_text = _format_next_actions(next_actions)
            return {
                "status": "ok",
                "actions": next_actions.get("actions", []),
                "reasoning": next_actions.get("reasoning", []),
                "slice_filter": next_actions.get("slice_filter"),
                "summary_text": summary_text,
            }
    except Exception as exc:  # pragma: no cover - logging path
        logger.error("Error in handle_next_actions_query", exc_info=exc)
        return {
            "status": "unavailable",
            "summary_text": f"Error: {exc}",
        }


def _build_headline(overview: Dict[str, Any]) -> str:
    """Summarize the overview into a short headline."""
    overall = overview.get("overall_status", "unknown").upper()
    quality_gate = overview.get("quality_gate") or {}
    qg_state = quality_gate.get("state")
    failing_slices = [
        s.get("slice", "?")
        for s in overview.get("by_slice", [])
        if s.get("validation_status") not in {"passed", "success"}
    ]

    headline = f"Brain status: {overall}"
    if qg_state == "failed":
        headline += " — quality gate FAILING"
    elif qg_state == "success":
        headline += " — quality gate passing"

    if failing_slices:
        headline += f", {len(failing_slices)} slice(s) failing validation ({', '.join(failing_slices)})"

    return headline


def _format_status_overview(overview: Dict[str, Any]) -> str:
    """Render the status overview as markdown."""
    md = "# Codebase Health Overview\n\n"
    md += f"**Overall Status**: {overview.get('overall_status', 'unknown').upper()}\n\n"

    quality_gate = overview.get("quality_gate")
    if quality_gate:
        md += f"**Quality Gate (main)**: {quality_gate.get('state', 'unknown')}\n"
        failed_jobs = quality_gate.get("failed_jobs", [])
        if failed_jobs:
            md += f"- Failed jobs: {', '.join(failed_jobs)}\n"
        md += "\n"

    md += "## Health by Slice\n\n"
    for slice_info in overview.get("by_slice", []):
        slice_name = slice_info.get("slice", "unknown")
        val_status = slice_info.get("validation_status", "unknown")
        risk_count = slice_info.get("risk_count", 0)
        severity = slice_info.get("top_risk_severity", "N/A")
        status_emoji = "✅" if val_status == "passed" else "❌"
        md += (
            f"- {status_emoji} **{slice_name}**: {val_status} | "
            f"{risk_count} risk(s) | max severity: {severity}\n"
        )
    md += "\n"

    md += "## Top Risks (by severity)\n\n"
    top_risks = overview.get("top_risks", [])
    if top_risks:
        for idx, risk in enumerate(top_risks[:5], start=1):
            priority = risk.get("priority", "P2")
            risk_id = risk.get("id", "unknown")
            severity = risk.get("severity", 0)
            action = risk.get("recommended_action", "")
            estimate = risk.get("estimate", "")
            line = f"{idx}. **{priority}** [{risk_id}] (severity={severity}): {action}"
            if estimate:
                line += f" — est: {estimate}"
            md += line + "\n"
    else:
        md += "No critical risks identified.\n"
    md += "\n"

    ci_drift = overview.get("ci_drift")
    if ci_drift:
        degraded = ci_drift.get("degraded_count", 0)
        if degraded:
            md += "## CI Drift\n\n"
            md += f"{degraded} job(s) with failures or performance degradation:\n"
            for job in ci_drift.get("jobs_with_drift", [])[:5]:
                md += f"- {job}\n"
            md += "\n"

    top_actions = overview.get("top_actions", [])
    if top_actions:
        md += "## Top Actions\n\n"
        for action in top_actions[:5]:
            priority = action.get("priority", "P2")
            risk_id = action.get("risk_id", "unknown")
            summary = action.get("summary", "")
            estimate = action.get("estimate", "")
            md += f"- **{priority}** [{risk_id}]: {summary}"
            if estimate:
                md += f" (est: {estimate})"
            md += "\n"
        md += "\n"

    return md


def _format_next_actions(next_actions: Dict[str, Any]) -> str:
    """Render next actions as markdown."""
    md = "# Recommended Next Actions\n\n"

    slice_filter = next_actions.get("slice_filter")
    if slice_filter:
        md += f"_Filtered to slice: {slice_filter}_\n\n"

    reasoning = next_actions.get("reasoning", [])
    if reasoning:
        md += "**Ranking logic**:\n"
        for line in reasoning:
            md += f"- {line}\n"
        md += "\n"

    actions = next_actions.get("actions", [])
    if actions:
        md += "## Actions (ranked)\n\n"
        for idx, action in enumerate(actions, start=1):
            priority = action.get("priority", "P2")
            risk_id = action.get("risk_id", "unknown")
            summary = action.get("summary", "")
            estimate = action.get("estimate", "")
            slice_name = action.get("slice", "")
            line = f"{idx}. **{priority}** [{risk_id}]"
            if slice_name:
                line += f" ({slice_name})"
            line += f": {summary}"
            if estimate:
                line += f" — est: {estimate}"
            md += line + "\n"
    else:
        md += "No open actions found.\n"

    return md
