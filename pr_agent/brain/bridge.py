import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pr_agent.brain.brain_client import BrainClientWrapper
from pr_agent.config_loader import get_settings

logger = logging.getLogger(__name__)

@dataclass
class PRMetadata:
    pr_number: int
    head_sha: str
    base_sha: str
    changed_files: List[str]
    # Optional fields can be added as needed

@dataclass
class BrainContextResult:
    status: Literal["ok", "partial", "unavailable"]
    extra_instructions: str
    context_file_path: Optional[Path] = None

async def prepare_brain_context(
    pr_meta: PRMetadata,
    repo_path: Path,
    task_scope: Literal["pull_request", "issue_comment"],
) -> BrainContextResult:
    """Prepare Brain MCP context for a PR using composite tools."""
    settings = get_settings()
    mcp_enable = getattr(settings.brain, "mcp_enable", False)
    if not mcp_enable:
        return BrainContextResult(status="unavailable", extra_instructions="")

    context_file = repo_path / "BRAIN_QODO_CONTEXT.md"

    try:
        async with BrainClientWrapper() as client:
            if not client.client:
                return _handle_unavailable(context_file, "Brain MCP client failed to initialize")

            default_slice = getattr(settings.brain, "mcp_default_slice", "runtime")
            max_modules = getattr(settings.brain, "mcp_max_modules", 5)
            max_risks = getattr(settings.brain, "mcp_max_risks", 8)
            usage_slice = getattr(settings.brain, "usage_slice", None) or default_slice
            hotspot_limit = getattr(settings.brain, "usage_hotspot_limit", max_modules)
            hotspot_min_score = getattr(settings.brain, "usage_min_risk_score", None)
            bundle_limit = getattr(settings.brain, "usage_bundle_limit", 5)

            status_overview = await client.get_status_overview(top_n_risks=max_risks)

            change_impact = await client._call_tool_safe(
                "get_change_impact",
                {
                    "slice": default_slice,
                    "paths": pr_meta.changed_files,
                    "dependency_depth": 1,
                    "dependents_depth": 1,
                },
            )

            impacted_modules: List[str] = []
            if change_impact:
                if "resolved_modules" in change_impact:
                    impacted_modules = [
                        mod.get("module_id")
                        for mod in change_impact.get("resolved_modules", [])
                        if mod.get("module_id")
                    ]
                elif "impacted_modules" in change_impact:
                    impacted_modules = change_impact["impacted_modules"]

            impacted_modules = impacted_modules[:max_modules]

            hotspots_overview = await client.get_hotspots_overview(
                slice_filter=usage_slice,
                limit=hotspot_limit,
                min_risk_score=hotspot_min_score,
            )

            bundle_suggestions = None
            if impacted_modules or pr_meta.changed_files:
                bundle_suggestions = await client.get_change_bundle_suggestions(
                    slice_name=usage_slice,
                    module_ids=impacted_modules or None,
                    paths=pr_meta.changed_files or None,
                    limit=bundle_limit,
                )

            markdown_content = _generate_markdown_v2(
                pr_meta.pr_number,
                status_overview,
                change_impact,
                impacted_modules,
                hotspots_overview,
                bundle_suggestions,
                max_risks,
            )

            instructions = _generate_instructions_v2(
                status_overview,
                change_impact,
                impacted_modules,
                hotspots_overview,
                bundle_suggestions,
            )

            try:
                context_file.write_text(markdown_content)
            except Exception as e:
                logger.error(f"Failed to write BRAIN_QODO_CONTEXT.md: {e}")

            status = "ok" if status_overview else "partial"

            return BrainContextResult(
                status=status,
                extra_instructions=instructions,
                context_file_path=context_file
            )

    except Exception as e:
        logger.error(f"Error in prepare_brain_context: {e}")
        return _handle_unavailable(context_file, str(e))

def _handle_unavailable(context_file: Path, reason: str) -> BrainContextResult:
    stub_content = f"""# Brain MCP Context Unavailable

Brain MCP could not be queried for this PR.
Reason: {reason}

Please rely on standard review practices.
"""
    try:
        context_file.write_text(stub_content)
    except Exception:
        pass

    instructions = """
Brain MCP context is UNAVAILABLE for this PR.
Reviewer, you MUST:
- Rely solely on the diff and standard best practices.
- NOT make claims about CI status or Brain validation.
"""
    return BrainContextResult(status="unavailable", extra_instructions=instructions)

def _generate_markdown_v2(
    pr_number: int,
    status_overview: Optional[Dict[str, Any]],
    change_impact: Optional[Dict[str, Any]],
    impacted_modules: List[str],
    max_risks: int,
) -> str:
    if not status_overview:
        return f"""# Brain MCP snapshot for PR #{pr_number}

Brain MCP is unavailable. Using standard review practices.
"""

    overall_status = status_overview.get("overall_status", "unknown")
    quality_gate = status_overview.get("quality_gate")
    ci_drift = status_overview.get("ci_drift")

    md = f"# Brain MCP snapshot for PR #{pr_number}\n\n"
    md += f"- **Overall codebase status**: {overall_status.upper()}\n"

    if quality_gate:
        qg_state = quality_gate.get("state", "unknown")
        failed_jobs = quality_gate.get("failed_jobs", [])
        md += f"- **Quality gate (main)**: {qg_state}\n"
        if failed_jobs:
            md += f"  - Failed jobs: {', '.join(failed_jobs)}\n"

    if ci_drift:
        degraded_count = ci_drift.get("degraded_count", 0)
        md += f"- **CI drift detected**: {degraded_count} jobs with failures/drift\n"

    md += "\n## Codebase Health by Slice\n\n"
    for slice_status in status_overview.get("by_slice", []):
        slice_name = slice_status.get("slice", "unknown")
        val_status = slice_status.get("validation_status", "unknown")
        risk_count = slice_status.get("risk_count", 0)
        top_severity = slice_status.get("top_risk_severity")
        md += f"- **{slice_name}**: validation={val_status}, risks={risk_count}"
        if top_severity is not None:
            md += f", max_severity={top_severity}"
        md += "\n"

    md += "\n## PR-Specific Impact\n\n"
    if impacted_modules:
        md += f"This PR impacts {len(impacted_modules)} module(s):\n"
        for mod in impacted_modules:
            md += f"- `{mod}`\n"
    else:
        md += "No high-impact modules identified.\n"

    md += "\n## Risks Relevant to This PR\n\n"
    if change_impact and "risks" in change_impact:
        pr_risks = change_impact.get("risks", [])
        if pr_risks:
            for risk_summary in pr_risks[:max_risks]:
                mod_id = risk_summary.get("module_id", "unknown")
                risks = risk_summary.get("risks", [])
                if risks:
                    md += f"### Module: `{mod_id}`\n\n"
                    for risk in risks[:3]:
                        md += (
                            f"- **[{risk.get('id', 'unknown')}]** (severity={risk.get('severity', 'N/A')}): "
                            f"{risk.get('recommended_action', 'No recommended action')}\n"
                        )
                    md += "\n"
        else:
            md += "No critical risks found in impacted modules.\n"
    else:
        md += "Risk data unavailable.\n"

    md += "\n## Top Actions (Global)\n\n"
    top_actions = status_overview.get("top_actions", [])
    if top_actions:
        for action in top_actions[:5]:
            priority = action.get("priority", "P2")
            risk_id = action.get("risk_id", "unknown")
            summary = action.get("summary", "")
            estimate = action.get("estimate", "")
            md += f"- **{priority}** [{risk_id}]: {summary}"
            if estimate:
                md += f" (est: {estimate})"
            md += "\n"
    else:
        md += "No recommended actions.\n"

    md += "\n---\n\n"
    md += "_This snapshot is generated automatically by the Brain–Qodo bridge._\n"

    return md


def _generate_instructions_v2(
    status_overview: Optional[Dict[str, Any]],
    change_impact: Optional[Dict[str, Any]],
    impacted_modules: List[str],
) -> str:
    if not status_overview:
        return """Brain MCP context is UNAVAILABLE for this PR.\nReviewer: rely solely on the diff and standard best practices."""

    overall_status = status_overview.get("overall_status", "unknown")
    quality_gate = status_overview.get("quality_gate", {})
    qg_state = quality_gate.get("state", "unknown")

    lines = ["Brain MCP context for this PR:"]
    lines.append(f"- Overall codebase status: {overall_status}")
    lines.append(f"- Quality gate (main): {qg_state}")
    lines.append(f"- PR impacts {len(impacted_modules)} module(s): {', '.join(impacted_modules[:3]) if impacted_modules else 'None'}")

    p0_count = 0
    if change_impact and "risks" in change_impact:
        for risk_summary in change_impact.get("risks", []):
            for risk in risk_summary.get("risks", []):
                if risk.get("priority") == "P0" or risk.get("severity", 0) >= 4:
                    p0_count += 1

    if p0_count > 0:
        lines.append(f"- **WARNING**: {p0_count} P0/critical risk(s) in impacted modules")

    lines.append("\nReviewer, you MUST:")
    lines.append("- Prioritize issues that affect the impacted modules and their risks")
    lines.append("- Explicitly mention if you rely on this Brain snapshot")
    lines.append("- If data is incomplete, clearly state limitations")

    if overall_status == "fail" or qg_state == "failed":
        lines.append("\n**CRITICAL**: Quality gate is FAILING. Extra scrutiny required.")

    return "\n".join(lines)
