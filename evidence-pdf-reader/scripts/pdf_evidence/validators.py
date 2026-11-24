from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .case_io import (
    atomic_write_json,
    decode_contract,
    iter_document_blocks,
    read_json,
    value_matches_type,
)


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PERSONAL_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|" + "/" + "Users" + "/|" + "/" + "home" + r"/[^/\s]+/)"
)


def validate_frontmatter(skill_file: Path, expected_name: str) -> list[str]:
    errors: list[str] = []
    text = skill_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ["SKILL.md must start with YAML frontmatter"]
    try:
        closing = lines[1:].index("---") + 1
    except ValueError:
        return ["SKILL.md frontmatter has no closing delimiter"]
    keys: list[str] = []
    description_lines: list[str] = []
    for line in lines[1:closing]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if match:
            key, value = match.group(1), match.group(2) or ""
            keys.append(key)
            if key == "description" and value not in (">-", ">", "|", "|-"):
                description_lines.append(value.strip().strip('"\''))
        elif line.startswith((" ", "\t")):
            description_lines.append(line.strip())
        elif line.strip():
            errors.append(f"Unsupported frontmatter line: {line}")
    if set(keys) != {"name", "description"} or len(keys) != 2:
        errors.append("Frontmatter must contain exactly name and description")
    values = [line for line in lines[1:closing] if line.startswith("name:")]
    name = values[0].split(":", 1)[1].strip().strip('"\'') if values else ""
    if not NAME_RE.fullmatch(name) or len(name) >= 64:
        errors.append("Skill name must be lowercase kebab-case and shorter than 64 characters")
    if name != expected_name:
        errors.append(f"Skill name '{name}' does not match directory '{expected_name}'")
    if not " ".join(description_lines).strip():
        errors.append("Skill description must not be empty")
    if not any(line.strip() for line in lines[closing + 1 :]):
        errors.append("SKILL.md body must contain executable instructions")
    return errors


def _markdown_reference_errors(root: Path, file_path: Path) -> list[str]:
    errors: list[str] = []
    text = file_path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = match.group(1).strip().strip("<>")
        if not target or target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        if Path(target).is_absolute():
            errors.append(f"Absolute Markdown reference in {file_path.name}: {target}")
            continue
        if not (file_path.parent / target).resolve().exists():
            errors.append(f"Missing Markdown reference in {file_path.name}: {target}")
    return errors


def validate_package(skill_dir: str | Path) -> dict[str, Any]:
    root = Path(skill_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        return {"schema_version": "1.0", "valid": False, "errors": ["Skill directory does not exist"]}
    files = [
        path
        for path in root.rglob("*")
        if (path.is_file() or path.is_symlink())
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]
    total_bytes = 0
    for path in files:
        relative = str(path.relative_to(root))
        if path.is_symlink():
            errors.append(f"Symbolic link is not allowed: {relative}")
            continue
        size = path.stat().st_size
        total_bytes += size
        if size > 500 * 1024:
            errors.append(f"File exceeds 500KB: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"Resource is not UTF-8 text: {relative}")
            continue
        if PERSONAL_PATH_RE.search(text):
            errors.append(f"Machine-specific absolute path found: {relative}")
        if re.search(r"(?i)\b(?:conda|mamba|uv)\s+(?:activate|run|create|install)\b", text):
            errors.append(f"Environment-specific command found in Skill: {relative}")
        if path.suffix.lower() in {".md", ".markdown"}:
            errors.extend(_markdown_reference_errors(root, path))
    if len(files) > 50:
        errors.append(f"Too many resource files: {len(files)} > 50")
    if total_bytes > 2 * 1024 * 1024:
        errors.append(f"Resources exceed 2MB: {total_bytes} bytes")
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        errors.append("Missing SKILL.md")
    else:
        errors.extend(validate_frontmatter(skill_file, root.name))
    return {
        "schema_version": "1.0",
        "valid": not errors,
        "skill_dir": str(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "errors": errors,
        "warnings": warnings,
    }


def validate_package_command(skill_dir: str | Path, output: str | Path) -> int:
    report = validate_package(skill_dir)
    atomic_write_json(output, report)
    print(
        f"package_valid={report['valid']}; files={report.get('file_count', 0)}; "
        f"errors={len(report.get('errors', []))}; report={output}"
    )
    for message in report.get("errors", [])[:5]:
        print(f"ERROR: {message}")
    return 0 if report["valid"] else 10


def normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def validate_sources(sources: Any, document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(sources, list):
        return ["sources must be a list"]
    block_map = {block.get("block_id"): (page_number, block) for page_number, block in iter_document_blocks(document)}
    for index, source in enumerate(sources):
        label = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label} must be an object")
            continue
        block_id = source.get("block_id")
        page_number = source.get("page_number")
        if block_id not in block_map:
            errors.append(f"{label} has unknown block_id: {block_id}")
            continue
        actual_page, block = block_map[block_id]
        if page_number != actual_page:
            errors.append(f"{label} page {page_number} does not match block page {actual_page}")
        if source.get("quote"):
            quote = normalized_text(source["quote"])
            block_text = normalized_text(block.get("text"))
            if quote not in block_text:
                errors.append(f"{label} quote does not match source block text")
    return errors


def check_document_coverage(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pages = document.get("pages", [])
    declared = document.get("page_count")
    if declared != len(pages):
        errors.append(f"Declared page_count {declared} does not match output pages {len(pages)}")
    for page in pages:
        if "status" not in page:
            errors.append(f"Page {page.get('page_number')} has no processing status")
        for block in page.get("blocks", []):
            block_id = block.get("block_id")
            if not block_id:
                errors.append("A document block has no block_id")
            bbox = block.get("bbox")
            if bbox is not None and (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(item, (int, float)) for item in bbox)
            ):
                errors.append(f"Invalid bbox for block: {block_id}")
    return errors


def validate_result_object(
    plan: dict[str, Any], document: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    fields, required, contract_errors = decode_contract(plan.get("output_contract"))
    errors.extend(contract_errors)
    if result.get("case_id") != plan.get("case_id"):
        errors.append("case_id does not match case-plan.json")
    for name, spec in fields.items():
        if name not in result:
            errors.append(f"Missing original output field: {name}")
            continue
        if name in required and (result.get(name) is None or result.get(name) == ""):
            errors.append(f"Required output field is empty: {name}")
        if not value_matches_type(result.get(name), spec):
            errors.append(f"Output field has wrong type: {name}")
    if result.get("status") not in {"success", "partial", "unable_to_judge", "refused", "failed"}:
        errors.append(f"Unsupported result status: {result.get('status')}")
    if not isinstance(result.get("sources"), list):
        errors.append("sources must be a list")
    if not isinstance(result.get("uncertainty"), list):
        errors.append("uncertainty must be a list")
    if not isinstance(result.get("change_records"), list) or not result.get("change_records"):
        errors.append("change_records must be a non-empty list")
    if not isinstance(result.get("errors"), list):
        errors.append("errors must be a list")
    errors.extend(validate_sources(result.get("sources", []), document))
    coverage_errors = check_document_coverage(document)
    errors.extend(coverage_errors)
    if result.get("status") == "success":
        if not result.get("sources"):
            errors.append("A successful evidence-linked result must contain sources")
        if result.get("errors"):
            errors.append("A successful result cannot retain unresolved error records")
        if any(page.get("status") in {"failed", "pending", "skipped"} for page in document.get("pages", [])):
            errors.append("A successful result cannot contain failed, pending, or skipped pages")
        context = result.get("context", {})
        if isinstance(context, dict) and context.get("segmented_result_complete") is False:
            errors.append("A successful result cannot have incomplete segmented coverage")
    return {
        "schema_version": "1.0",
        "valid": not errors,
        "case_id": plan.get("case_id"),
        "contract_field_count": len(fields),
        "source_count": len(result.get("sources", [])) if isinstance(result.get("sources"), list) else 0,
        "errors": errors,
    }


def validate_result_command(
    plan_path: str | Path,
    document_path: str | Path,
    result_path: str | Path,
    output: str | Path,
) -> int:
    try:
        plan = read_json(plan_path)
        document = read_json(document_path)
        result = read_json(result_path)
        report = validate_result_object(plan, document, result)
    except Exception as exc:
        report = {"schema_version": "1.0", "valid": False, "errors": [f"Cannot load validation inputs: {exc}"]}
    atomic_write_json(output, report)
    print(f"result_valid={report['valid']}; errors={len(report['errors'])}; report={output}")
    for message in report["errors"][:5]:
        print(f"ERROR: {message}")
    return 0 if report["valid"] else 9


def evaluate_order_object(document: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    page_reports: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    duplicate: list[dict[str, Any]] = []
    total_pairs = 0
    consistent_pairs = 0
    complete_pages = 0
    for page_number_text, expected in gold.get("pages", {}).items():
        if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
            raise ValueError("Gold pages must map page numbers to lists of exact block texts")
        page_number = int(page_number_text)
        matching_page = next((page for page in document.get("pages", []) if page.get("page_number") == page_number), None)
        if matching_page is None:
            raise ValueError(f"Document is missing gold page {page_number}")
        blocks = [block for block in matching_page.get("blocks", []) if block.get("kind") not in {"header", "footer"}]
        actual_texts = [normalized_text(block.get("text")) for block in blocks]
        positions: list[int] = []
        for wanted in expected:
            normalized_wanted = normalized_text(wanted)
            indices = [index for index, text in enumerate(actual_texts) if text == normalized_wanted]
            if not indices:
                missing.append({"page_number": page_number, "text": wanted})
            elif len(indices) > 1:
                duplicate.append({"page_number": page_number, "text": wanted})
            else:
                positions.append(indices[0] + 1)
        if missing or duplicate:
            continue
        inversions = sum(
            1 for left in range(len(positions)) for right in range(left + 1, len(positions))
            if positions[left] > positions[right]
        )
        pairs = len(positions) * (len(positions) - 1) // 2
        page_reports.append({
            "page_number": page_number,
            "expected": expected,
            "actual_positions": positions,
            "inversions": inversions,
            "comparable_pairs": pairs,
            "order_accuracy": 1.0 if pairs == 0 else (pairs - inversions) / pairs,
            "exact_match": inversions == 0,
        })
        total_pairs += pairs
        consistent_pairs += pairs - inversions
        complete_pages += int(inversions == 0)
    errors: list[str] = []
    if missing:
        errors.append(f"{len(missing)} gold blocks are missing from the extracted document")
    if duplicate:
        errors.append(f"{len(duplicate)} gold block texts are ambiguous duplicates")
    page_gold_count = len(gold.get("pages", {}))
    return {
        "schema_version": "1.0",
        "valid_gold_match": not errors,
        "case_id": gold.get("case_id"),
        "document_exact_match": not errors and complete_pages == page_gold_count,
        "order_accuracy": (consistent_pairs / total_pairs) if total_pairs else (1.0 if not errors else 0.0),
        "comparable_pairs": total_pairs,
        "consistent_pairs": consistent_pairs,
        "exact_page_count": complete_pages,
        "pages": page_reports,
        "missing": missing,
        "duplicate": duplicate,
        "errors": errors,
    }


def evaluate_order_command(document_path: str | Path, gold_path: str | Path, output: str | Path) -> int:
    try:
        document = read_json(document_path)
        gold = read_json(gold_path)
        report = evaluate_order_object(document, gold)
    except Exception as exc:
        report = {"schema_version": "1.0", "valid_gold_match": False, "errors": [f"Cannot evaluate order: {exc}"]}
    atomic_write_json(output, report)
    print(
        f"gold_match={report.get('valid_gold_match', False)}; exact={report.get('document_exact_match', False)}; "
        f"accuracy={report.get('order_accuracy', 0):.6f}; report={output}"
    )
    return 0 if report.get("valid_gold_match") else 7
