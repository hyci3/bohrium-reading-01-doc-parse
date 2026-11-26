from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def read_json(path: str | Path) -> Any:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: str | Path, value: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def error_record(kind: str, stage: str, message: str, **details: Any) -> dict[str, Any]:
    return {"kind": kind, "stage": stage, "message": message, "details": details}


METADATA_FIELDS = {"case_id", "status", "sources", "uncertainty", "change_records", "errors"}
TYPE_NAMES = {"string", "number", "integer", "boolean", "array", "object", "null"}
CONTRACT_FIELD_KEYS = ("fields", "properties", "outputs", "output_fields", "result_fields", "answers", "schema")
INFERRED_CONTRACT = {
    "fields": {"answer": {"type": "string", "required": True}},
    "required": ["answer"],
    "source": "inferred-minimal-contract",
}


def _first_mapping(container: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-empty declared value among common contract key names."""
    for key in keys:
        value = container.get(key)
        if value:
            return value
    return None


def decode_contract(contract: Any) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Return field specifications, required names, and contract errors."""
    errors: list[str] = []
    required: set[str] = set()
    fields: dict[str, dict[str, Any]] = {}
    if isinstance(contract, list) and all(isinstance(item, str) for item in contract):
        fields = {name: {"type": "null"} for name in contract}
        required.update(contract)
    elif isinstance(contract, dict):
        raw_fields = _first_mapping(contract, CONTRACT_FIELD_KEYS)
        if raw_fields is None and "type" not in contract and "required" not in contract:
            raw_fields = {key: value for key, value in contract.items() if not key.startswith("$")}
        if isinstance(raw_fields, list) and all(isinstance(item, str) for item in raw_fields):
            raw_fields = {name: {"type": "string"} for name in raw_fields}
        elif isinstance(raw_fields, list):
            declared = []
            for item in raw_fields:
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
                    declared.append(item)
                else:
                    declared.append(None)
            if all(declared):
                raw_fields = {
                    item["name"]: {"type": item.get("type", "string"), "required": item.get("required", True)}
                    for item in declared
                }
        if isinstance(raw_fields, dict):
            for name, spec in raw_fields.items():
                if not isinstance(name, str) or not name:
                    errors.append("Output field names must be non-empty strings")
                    continue
                if isinstance(spec, str):
                    spec = {"type": spec}
                if spec is None:
                    spec = {"type": "null"}
                if not isinstance(spec, dict):
                    errors.append(f"Invalid specification for output field: {name}")
                    continue
                fields[name] = dict(spec)
                if spec.get("required") is True:
                    required.add(name)
        else:
            errors.append("output_contract must expose a fields mapping, properties, or a field list")
        raw_required = contract.get("required", [])
        if isinstance(raw_required, list):
            required.update(item for item in raw_required if isinstance(item, str))
        elif raw_required:
            errors.append("output_contract.required must be a list when present")
    else:
        errors.append("Missing or unsupported output_contract")
    for name in sorted(fields):
        if name in METADATA_FIELDS - {"case_id"}:
            errors.append(f"Output field conflicts with reserved result metadata: {name}")
        field_type = fields[name].get("type", "null")
        if isinstance(field_type, str) and field_type not in TYPE_NAMES:
            errors.append(f"Unsupported type '{field_type}' for field: {name}")
    return fields, sorted(required), errors


def value_matches_type(value: Any, spec: dict[str, Any]) -> bool:
    if value is None:
        return True
    expected = spec.get("type", "null")
    if not isinstance(expected, str):
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def empty_result(case_id: Any, contract: Any, errors: list[dict[str, Any]]) -> dict[str, Any]:
    fields, _, contract_errors = decode_contract(contract)
    result: dict[str, Any] = {name: case_id if name == "case_id" else None for name in fields}
    result.update({
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_records": [{"sequence": 1, "stage": "inspect-case", "description": "Created an honest early result before PDF processing"}],
        "errors": list(errors),
    })
    for message in contract_errors:
        if all(item.get("message") != message for item in result["errors"]):
            result["errors"].append(error_record("invalid_contract", "inspect-case", message))
    return result


def iter_document_blocks(document: dict[str, Any]):
    for page in document.get("pages", []):
        page_number = page.get("page_number")
        for block in page.get("blocks", []):
            yield page_number, block


def _find_pdf_values(value: Any, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            found.extend(_find_pdf_values(child_value, str(child_key).lower()))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_pdf_values(item, key))
    elif isinstance(value, str) and value.strip().lower().endswith(".pdf"):
        found.append(value)
    return found


def _discover_pdfs(root: Path, limit: int = 20) -> list[Path]:
    """Find PDF material next to a case file when the case names no explicit path."""
    if not root.is_dir():
        return []
    found = sorted(path for path in root.glob("*.pdf") if path.is_file())
    if not found:
        nested = root / "materials"
        if nested.is_dir():
            found = sorted(path for path in nested.rglob("*.pdf") if path.is_file())
    return found[:limit]


def inspect_case_command(
    case_path: str,
    task_path: str,
    pdfs: list[str] | None,
    work: str,
    output: str,
    early_result: str,
) -> int:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    case: dict[str, Any] = {}
    case_file = Path(case_path).resolve()
    task_file = Path(task_path).resolve()
    try:
        loaded = read_json(case_file)
        if not isinstance(loaded, dict):
            raise ValueError("case.json must contain an object")
        case = loaded
    except Exception as exc:
        errors.append(error_record("invalid_case", "inspect-case", f"Cannot parse case.json: {exc}"))
    case_id = case.get("case_id")
    if case_id is None or isinstance(case_id, (dict, list)):
        fallback = case.get("id", case.get("case", case_file.stem))
        if isinstance(fallback, (dict, list)) or fallback in {None, ""}:
            errors.append(error_record("invalid_case", "inspect-case", "A scalar case_id is required"))
            case_id = None
        else:
            case_id = fallback
            warnings.append(error_record(
                "inferred_case_id", "inspect-case",
                f"case.json declares no scalar case_id; used '{fallback}' from the case file",
                source=str(case_file),
            ))
    if not task_file.is_file():
        errors.append(error_record("material_missing", "inspect-case", "task.md does not exist", path=str(task_file)))
    contract = case.get("output_contract")
    if contract is None:
        contract = {key: value for key, value in INFERRED_CONTRACT.items()}
        warnings.append(error_record(
            "inferred_contract", "inspect-case",
            "case.json declares no output_contract; result keeps an 'answer' field and preserves any extra answer fields",
            source=str(case_file),
        ))
    _, _, contract_errors = decode_contract(contract)
    for message in contract_errors:
        errors.append(error_record("invalid_contract", "inspect-case", message))

    explicit_pdfs = bool(pdfs)
    candidates = list(pdfs or []) or _find_pdf_values(case)
    if not candidates:
        discovered = _discover_pdfs(case_file.parent)
        if discovered:
            candidates = [str(path) for path in discovered]
            warnings.append(error_record(
                "inferred_pdf_material", "inspect-case",
                f"case.json names no PDF; discovered {len(candidates)} PDF file(s) next to it",
                source=str(case_file.parent),
            ))
    resolved_pdfs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = Path.cwd() / path if explicit_pdfs else case_file.parent / path
        path = path.resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        record: dict[str, Any] = {"path": key}
        if not path.is_file():
            errors.append(error_record("material_missing", "inspect-case", "PDF does not exist", path=key))
            record["exists"] = False
        else:
            record.update({"exists": True, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
        resolved_pdfs.append(record)
    if not resolved_pdfs:
        errors.append(error_record("material_missing", "inspect-case", "No explicit PDF material was found"))

    work_dir = Path(work).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    early_result_path = Path(early_result).resolve()
    plan = {
        "schema_version": "1.0",
        "case_id": case_id,
        "case_path": str(case_file),
        "case_sha256": sha256_file(case_file) if case_file.is_file() else None,
        "task_path": str(task_file),
        "task_sha256": sha256_file(task_file) if task_file.is_file() else None,
        "output_contract": contract,
        "pdfs": resolved_pdfs,
        "work_dir": str(work_dir),
        "early_result": str(early_result_path),
        "status": "failed" if errors else "ready",
        "errors": errors,
        "warnings": warnings,
    }
    atomic_write_json(output, plan)
    early = empty_result(case_id, contract, errors or [error_record("pending_processing", "inspect-case", "PDF processing has not completed")])
    early["artifacts"] = {"case_plan": str(Path(output).resolve())}
    atomic_write_json(early_result, early)
    print(f"case_id={case_id}; plan={output}; errors={len(errors)}; warnings={len(warnings)}; early_result={early_result}")
    return 4 if errors else 0
