from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities import capabilities_command
from .case_io import atomic_write_json, error_record, inspect_case_command, read_json
from .coverage import coverage_report_command
from .extractors import extract_command
from .probe import probe_pdf_command

NEXT_ACTIONS = [
    "Read auto-summary.json for the artifact paths and page counts.",
    "Call select-context with a short --query taken from the case task to obtain budgeted evidence.",
    "Write answer.json (fields/status/sources/uncertainty/errors) whose quotes match block text verbatim.",
    "Run finalize-result and validate-result; a partial status with honest uncertainty is acceptable.",
]


def _run_step(name: str, function, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        code = function(*args, **kwargs)
        return {"step": name, "returncode": code, "ok": code == 0}
    except Exception as exc:  # a single failing step must not abort the pipeline
        return {
            "step": name,
            "returncode": None,
            "ok": False,
            "error": error_record("step_failed", name, f"{type(exc).__name__}: {exc}"),
        }


def auto_command(
    case_path: str | Path,
    task_path: str | Path,
    pdfs: list[str] | None,
    work: str | Path,
    output_dir: str | Path,
    resources: str | Path | None = None,
    backend: str | None = None,
    order: str = "layout",
) -> int:
    """Run capability detection, case inspection, probing, extraction and coverage in one call."""
    work_dir = Path(work).resolve()
    output_root = Path(output_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    capabilities_path = work_dir / "capabilities.json"
    plan_path = work_dir / "case-plan.json"
    probe_path = work_dir / "pdf-probe.json"
    document_path = work_dir / "document.json"
    coverage_path = work_dir / "coverage.json"
    summary_path = work_dir / "auto-summary.json"
    early_result = output_root / "result.json"
    case_path = Path(case_path)

    steps: list[dict[str, Any]] = []
    steps.append(_run_step("capabilities", capabilities_command, str(resources) if resources else None, str(capabilities_path)))
    steps.append(_run_step(
        "inspect-case", inspect_case_command,
        str(case_path), str(task_path), pdfs, str(work_dir), str(plan_path), str(early_result),
    ))

    plan_ready = plan_path.is_file() and capabilities_path.is_file()
    if plan_ready:
        steps.append(_run_step("probe-pdf", probe_pdf_command, str(plan_path), str(capabilities_path), str(probe_path), backend))
    if probe_path.is_file():
        steps.append(_run_step(
            "extract", extract_command,
            str(plan_path), str(probe_path), str(capabilities_path), str(work_dir), str(document_path), order,
        ))
    if document_path.is_file():
        steps.append(_run_step("coverage-report", coverage_report_command, str(document_path), str(coverage_path)))

    document: dict[str, Any] = {}
    if document_path.is_file():
        try:
            loaded = read_json(document_path)
            document = loaded if isinstance(loaded, dict) else {}
        except Exception:
            document = {}
    pages = document.get("pages", [])
    blocks = [block for page in pages for block in page.get("blocks", [])]
    kinds: dict[str, int] = {}
    for block in blocks:
        kind = str(block.get("kind") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    plan: dict[str, Any] = {}
    if plan_path.is_file():
        try:
            loaded_plan = read_json(plan_path)
            plan = loaded_plan if isinstance(loaded_plan, dict) else {}
        except Exception:
            plan = {}

    summary = {
        "schema_version": "1.0",
        "case_id": plan.get("case_id"),
        "status": "ready" if document and blocks else "incomplete",
        "steps": steps,
        "artifacts": {
            "capabilities": str(capabilities_path) if capabilities_path.is_file() else None,
            "case_plan": str(plan_path) if plan_path.is_file() else None,
            "pdf_probe": str(probe_path) if probe_path.is_file() else None,
            "document": str(document_path) if document_path.is_file() else None,
            "coverage": str(coverage_path) if coverage_path.is_file() else None,
            "early_result": str(early_result) if early_result.is_file() else None,
        },
        "document": {
            "page_count": len(pages),
            "block_count": len(blocks),
            "kind_counts": dict(sorted(kinds.items())),
            "title_candidates": document.get("title_candidates", [])[:3],
        },
        "case_warnings": plan.get("warnings", []),
        "next_actions": NEXT_ACTIONS if blocks else [
            "No body block was extracted: report status partial, keep the early result, and explain the failure in uncertainty.",
        ],
    }
    atomic_write_json(summary_path, summary)
    print(
        "auto_status={status}; pages={pages}; blocks={blocks}; steps={steps}; summary={summary}".format(
            status=summary["status"], pages=len(pages), blocks=len(blocks),
            steps=",".join(f"{step['step']}:{step['returncode']}" for step in steps),
            summary=summary_path,
        )
    )
    if not document_path.is_file():
        failing = [step for step in steps if not step["ok"]]
        first = failing[0] if failing else {}
        code = first.get("returncode")
        return code if isinstance(code, int) and code > 0 else 4
    return 0
