from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import merge_segment_results
from .coverage import build_coverage_report, summarize_coverage
from .case_io import (
    METADATA_FIELDS,
    atomic_write_json,
    decode_contract,
    error_record,
    iter_document_blocks,
    read_json,
)
from .validators import validate_result_object


def _enrich_sources(sources: Any, document: dict[str, Any]) -> list[Any]:
    block_map = {block.get("block_id"): (page, block) for page, block in iter_document_blocks(document)}
    enriched: list[Any] = []
    for source in sources:
        if not isinstance(source, dict):
            enriched.append(source)
            continue
        item = dict(source)
        if item.get("block_id") in block_map:
            page_number, block = block_map[item["block_id"]]
            item.setdefault("page_number", page_number)
            item.setdefault("kind", block.get("kind"))
        enriched.append(item)
    return enriched



def _as_list(value: Any, field: str) -> list[Any]:
    """Accept a scalar or mapping where a list is expected, and say so in the result."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, int, float, dict)) and not isinstance(value, bool):
        return [value]
    if isinstance(value, bool):
        return [value]
    return [value]



def finalize_result_command(
    plan_path: str | Path,
    document_path: str | Path,
    answer_path: str | Path,
    output: str | Path,
    segments_dir: str | Path | None = None,
    merge_output: str | Path | None = None,
) -> int:
    plan = read_json(plan_path)
    document = read_json(document_path)
    answer = read_json(answer_path)
    if not isinstance(answer, dict):
        raise ValueError("answer.json must contain an object")
    fields, _, contract_errors = decode_contract(plan.get("output_contract"))
    output = Path(output)
    if output.exists():
        try:
            loaded = read_json(output)
            load_error = f"Existing result at {output} is not a JSON object"
        except Exception as exc:
            loaded = None
            load_error = f"Existing result at {output} is not readable JSON: {exc}"
        if not isinstance(loaded, dict):
            validation = {
                "schema_version": "1.0",
                "valid": False,
                "case_id": plan.get("case_id"),
                "errors": [load_error],
            }
            validation_path = output.with_name(f"{output.stem}.finalize-validation.json")
            atomic_write_json(validation_path, validation)
            print(f"finalize rejected; errors=1; report={validation_path}")
            return 8
        result = loaded
    else:
        result = {
            "case_id": plan.get("case_id"),
            "status": "partial",
            "sources": [],
            "uncertainty": [],
            "change_records": [],
            "errors": [],
        }
    merged = None
    merged_path = None
    if segments_dir:
        merged_path = Path(merge_output) if merge_output else output.with_name(f"{output.stem}.merged-segments.json")
        merged = merge_segment_results(plan, document, segments_dir, merged_path)
    provided_fields = answer.get("fields")
    if provided_fields is None:
        provided_fields = {name: answer[name] for name in fields if name in answer}
    if not isinstance(provided_fields, dict):
        raise ValueError("answer.fields must be an object")
    provided_fields = dict(provided_fields)
    if merged:
        for name, value in merged["fields"].items():
            if name in provided_fields and provided_fields[name] != value:
                raise ValueError(f"Agent field {name} conflicts with the deterministic segment merge")
            provided_fields[name] = value
    for name in fields:
        if name in provided_fields:
            result[name] = provided_fields[name]
    extra_fields = {
        name: value
        for name, value in provided_fields.items()
        if name not in fields and name not in METADATA_FIELDS and name != "fields"
    }
    if extra_fields:
        for name, value in extra_fields.items():
            if result.get(name) is None:
                result[name] = value
        result["extra_answer_fields"] = sorted(extra_fields)
    result["case_id"] = plan.get("case_id")
    default_status = "success" if merged and merged["complete"] else "partial"
    result["status"] = answer.get("status", default_status)
    coerced_fields: list[str] = []
    sources = answer.get("sources", answer.get("evidence"))
    if sources is None:
        sources = merged["sources"] if merged else []
    if not isinstance(sources, list):
        sources = _as_list(sources, "sources")
        coerced_fields.append("sources")
    result["sources"] = _enrich_sources(sources, document)
    uncertainty = answer.get("uncertainty", [])
    if not isinstance(uncertainty, list):
        uncertainty = _as_list(uncertainty, "uncertainty")
        coerced_fields.append("uncertainty")
    result["uncertainty"] = uncertainty
    answer_errors = answer.get("errors", [])
    if not isinstance(answer_errors, list):
        answer_errors = _as_list(answer_errors, "errors")
        coerced_fields.append("errors")
    prior_errors = result.get("errors", [])
    result["errors"] = [
        item for item in prior_errors if isinstance(item, dict) and item.get("kind") != "pending_processing"
    ] + answer_errors + (merged.get("errors", []) if merged else [])
    previous_changes = result.get("change_records", [])
    if not isinstance(previous_changes, list):
        previous_changes = []
    result["change_records"] = previous_changes + [{
        "sequence": len(previous_changes) + 1,
        "stage": "finalize-result",
        "description": "Merged actual answer fields and evidence without modifying the original contract",
        "answer_artifact": str(Path(answer_path).resolve()),
    }]
    prior_artifacts = result.get("artifacts")
    if not isinstance(prior_artifacts, dict):
        prior_artifacts = {}
    result["artifacts"] = {
        **prior_artifacts,
        "case_plan": str(Path(plan_path).resolve()),
        "document": str(Path(document_path).resolve()),
        "answer": str(Path(answer_path).resolve()),
    }
    if "coverage" in document:
        result["coverage"] = document["coverage"]
    result["coverage_report"] = build_coverage_report(document)
    if coerced_fields:
        result["coerced_answer_fields"] = sorted(coerced_fields)
    if result.get("status") == "success" and result.get("errors"):
        result["status"] = "partial"
        result["change_records"].append({
            "sequence": len(result["change_records"]) + 1,
            "stage": "finalize-result",
            "description": "Downgraded status from success to partial because the answer still reports unresolved errors",
        })
    if merged:
        result["context"] = {
            **result.get("context", {}),
            "segmented_result_complete": merged["complete"],
            "segment_coverage": merged["coverage"],
            "segment_count": merged["segment_count"],
            "merge_artifact": str(Path(merged_path).resolve()),
        }
        result["artifacts"]["merged_segments"] = str(Path(merged_path).resolve())
    if "context" in answer:
        result["context"] = {**result.get("context", {}), **answer["context"]}
    validation = validate_result_object(plan, document, result)
    if contract_errors:
        validation["errors"].extend(contract_errors)
        validation["valid"] = False
    if not validation["valid"]:
        validation_path = output.with_name(f"{output.stem}.finalize-validation.json")
        atomic_write_json(validation_path, validation)
        print(f"finalize rejected; errors={len(validation['errors'])}; report={validation_path}")
        return 8
    atomic_write_json(output, result)
    print(f"result={output}; status={result['status']}; sources={len(result['sources'])}")
    print(summarize_coverage(result["coverage_report"]))
    return 0
