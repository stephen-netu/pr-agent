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
    """
    Prepare Brain MCP context for a PR.

    1. Identify impacted modules.
    2. Query Brain MCP for CI status, validation status, contracts, and risks.
    3. Write BRAIN_QODO_CONTEXT.md.
    4. Return extra_instructions.
    """
    settings = get_settings()
    if not settings.brain.enable:
        return BrainContextResult(status="unavailable", extra_instructions="")

    context_file = repo_path / "BRAIN_QODO_CONTEXT.md"

    try:
        async with BrainClientWrapper() as client:
            if not client.client:
                return _handle_unavailable(context_file, "Brain MCP client failed to initialize")

            # 1. Get Change Impact (to find modules)
            # We assume changed_files are relative to repo root.
            # Brain MCP expects paths relative to repo root.
            # We use a heuristic: map files to modules via get_change_impact
            # Note: get_change_impact takes module_ids OR paths. We use paths if available.
            # But the spec says "Determine impacted modules... by calling Brain MCP tools".
            # The current brain_client.py get_change_impact helper expects module_ids.
            # We should probably update brain_client.py or call the tool directly if we want to pass paths.
            # Let's call the tool directly via _call_tool_safe to pass 'paths'.

            change_impact = await client._call_tool_safe("get_change_impact", {
                "slice": settings.brain.default_slice,
                "paths": pr_meta.changed_files,
                "dependency_depth": 1,
                "dependents_depth": 1
            })

            impacted_modules = []
            if change_impact and "impacted_modules" in change_impact:
                impacted_modules = change_impact["impacted_modules"]

            # Limit modules
            max_modules = settings.brain.max_modules
            top_modules = impacted_modules[:max_modules]

            # 2. Get CI and Validation Status
            ci_summary = await client.get_ci_run_summary()
            validation_status = await client.get_brain_validation_status()

            # 3. Get Details for Top Modules
            module_details = []
            for mod_id in top_modules:
                contract = await client.get_module_contract(mod_id, settings.brain.default_slice)
                risks = await client.get_module_risks(mod_id, settings.brain.default_slice)
                module_details.append({
                    "id": mod_id,
                    "contract": contract,
                    "risks": risks
                })

            # 4. Generate Content
            markdown_content = _generate_markdown(
                pr_meta.pr_number,
                ci_summary,
                validation_status,
                module_details,
                settings.brain.max_risks
            )

            instructions = _generate_instructions(
                ci_summary,
                validation_status,
                module_details,
                settings.brain.max_risks
            )

            # Write file
            try:
                context_file.write_text(markdown_content)
            except Exception as e:
                logger.error(f"Failed to write BRAIN_QODO_CONTEXT.md: {e}")
                # We continue, but status might be partial if we couldn't write file?
                # Actually if we can't write file, Qodo won't see it.
                # But we can still return instructions.

            status = "ok"
            if not ci_summary or not validation_status or (top_modules and not module_details):
                status = "partial"

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

def _generate_markdown(
    pr_number: int,
    ci_summary: Optional[Dict[str, Any]],
    validation_status: Optional[Dict[str, Any]],
    module_details: List[Dict[str, Any]],
    max_risks: int
) -> str:
    # Helpers
    ci_status = "UNKNOWN"
    failing_jobs = []
    if ci_summary:
        ci_status = ci_summary.get("overall_status", "UNKNOWN")
        for job in ci_summary.get("jobs", []):
            if job.get("last_run_status") != "success":
                failing_jobs.append(job.get("name", "unknown"))

    brain_status = "UNKNOWN"
    failing_validators = []
    if validation_status:
        brain_status = validation_status.get("overall_status", "UNKNOWN")
        for slice_entry in validation_status.get("slices", []):
            if slice_entry.get("status") != "passed":
                failing_validators.append(slice_entry.get("slice", "unknown"))

    overall_status = "OK"
    if ci_status != "success" or brain_status != "passed":
        overall_status = "FAIL" if (ci_status == "failure" or brain_status == "failed") else "PARTIAL"

    md = f"# Brain MCP snapshot for PR #{pr_number}\n\n"
    md += f"- CI / Brain overall status: **{overall_status}**\n"

    if failing_jobs:
        md += f"- Notable failing jobs: {', '.join(failing_jobs)}\n"
    else:
        md += "- Notable failing jobs: None\n"

    if failing_validators:
        md += f"- Notable failing Brain validators: {', '.join(failing_validators)}\n"
    else:
        md += "- Notable failing Brain validators: None\n"

    md += "\n## Impacted modules\n\n"
    if not module_details:
        md += "No high-impact modules identified or Brain data unavailable.\n"
    else:
        for mod in module_details:
            mod_id = mod["id"]
            contract = mod["contract"]
            risks_data = mod["risks"]

            contract_summary = "No contract available."
            if contract and "summary" in contract:
                contract_summary = contract.get("summary", "")

            md += f"- **{mod_id}**\n"
            md += f"  - Contract: {contract_summary}\n"

            risks_list = []
            if risks_data and "risks" in risks_data:
                all_risks = risks_data["risks"]
                # Filter/sort risks? For now take top N
                risks_list = all_risks[:max_risks]

            if risks_list:
                md += "  - Known critical risks:\n"
                for r in risks_list:
                    rid = r.get("id", "unknown")
                    title = r.get("title", "No title")
                    md += f"    - [{rid}] {title}\n"
            else:
                md += "  - Known critical risks: None\n"
            md += "\n"

    md += "## Notes\n\n"
    md += "- This snapshot is generated automatically by the Brain–Qodo bridge.\n"
    md += "- If it is missing or incomplete, Qodo should state this limitation explicitly.\n"

    return md

def _generate_instructions(
    ci_summary: Optional[Dict[str, Any]],
    validation_status: Optional[Dict[str, Any]],
    module_details: List[Dict[str, Any]],
    max_risks: int
) -> str:
    # Summarize for instructions
    ci_status = "UNKNOWN"
    failed_jobs_count = 0
    if ci_summary:
        ci_status = ci_summary.get("overall_status", "UNKNOWN")
        failed_jobs_count = len([j for j in ci_summary.get("jobs", []) if j.get("last_run_status") != "success"])

    brain_status = "UNKNOWN"
    failed_slices = []
    if validation_status:
        brain_status = validation_status.get("overall_status", "UNKNOWN")
        failed_slices = [s.get("slice") for s in validation_status.get("slices", []) if s.get("status") != "passed"]

    impacted_ids = [m["id"] for m in module_details]

    all_risk_ids = []
    for m in module_details:
        if m["risks"] and "risks" in m["risks"]:
            for r in m["risks"]["risks"][:max_risks]:
                all_risk_ids.append(r.get("id"))

    # Construct string
    lines = ["Brain MCP context for this PR:"]
    lines.append(f"- Overall Brain status: {brain_status} (Failing slices: {', '.join(failed_slices) if failed_slices else 'None'})")
    lines.append(f"- CI summary: {ci_status} ({failed_jobs_count} failing jobs)")
    lines.append(f"- Impacted modules: {', '.join(impacted_ids) if impacted_ids else 'None'}")
    lines.append(f"- Critical risks in scope: {', '.join(all_risk_ids) if all_risk_ids else 'None'}")

    lines.append("\nReviewer, you MUST:")
    lines.append("- Prioritize issues that affect these modules and risks.")
    lines.append("- Explicitly mention if you rely on this Brain snapshot.")
    lines.append("- If Brain MCP is unavailable, clearly say so and limit your claims.")

    return "\n".join(lines)
