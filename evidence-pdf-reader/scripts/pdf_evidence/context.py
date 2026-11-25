from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .case_io import atomic_write_json, decode_contract, error_record, read_json, sha256_file, value_matches_type


DEFAULT_PROFILE = {
    "context_budget_tokens": 256000,
    "runtime_reserve_tokens": 65536,
    "case_source_injection_budget_tokens": 96000,
    "single_selection_max_tokens": 24000,
    "response_reserve_tokens": 16384,
    "document_index_max_tokens": 12000,
    "single_tool_report_max_tokens": 2000,
    "single_image_max_count": 2,
    "case_image_max_count": 6,
    "estimated_image_tokens": 1500,
}
TEXT_SAFETY_FACTOR = 1.25


def _content_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False))


def estimate_tokens(text: str) -> int:
    """Conservative mixed-language heuristic; never advertised as tokenizer-exact."""
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    others = sum(1 for char in text if not char.isspace() and not ("\u3400" <= char <= "\u9fff"))
    return max(1, math.ceil((cjk + others / 3.0) * TEXT_SAFETY_FACTOR))


def estimate_json_tokens(value: Any) -> int:
    # Includes JSON punctuation and citation overhead, rather than text alone.
    return estimate_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def document_content_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _document_hash(document: dict[str, Any]) -> str:
    return document_content_sha256(document)


def _blocks(document: dict[str, Any], main_only: bool = True) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    sequence = 0
    for page in document.get("pages", []):
        for block in page.get("blocks", []):
            if main_only and block.get("kind") in {"header", "footer"}:
                continue
            sequence += 1
            primary_text = block.get("source_text", block.get("text", ""))
            entry = {
                "block_id": block.get("block_id"),
                "page_number": page.get("page_number"),
                "kind": block.get("kind", "unknown"),
                "section_path": block.get("section_path", []),
                # Preserve physical line breaks inside a PDF block; indexes use whitespace-normalized previews.
                "text": primary_text,
                "global_reading_order": block.get("global_reading_order", sequence),
                "_sequence": sequence,
            }
            if block.get("source_text_adjustments"):
                entry["source_text_before_adjustments"] = block.get("source_text_before_adjustments")
                entry["source_text_adjustments"] = list(block["source_text_adjustments"])
            if entry["kind"] == "table":
                # Automatic table rows are evidence candidates, not verified semantic row/cell ownership.
                entry["rows"] = block.get("rows", [])
                entry["table_warning"] = block.get("table_warning", "Unverified extractor table candidate; compare with the source page before assigning row or column ownership")
                entry["table_candidate"] = True
                entry["requires_visual_review"] = True
                if block.get("layout_barrier_excluded"):
                    entry["layout_barrier_excluded"] = True
            if block.get("warnings"):
                # Layout and recovery warnings must travel with the evidence, not only on disk.
                entry["warnings"] = list(block["warnings"])
            result.append(entry)
    return sorted(result, key=lambda item: (item["global_reading_order"], item["_sequence"]))


def _preview(block: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "block_id": block["block_id"],
        "page_number": block["page_number"],
        "kind": block["kind"],
        "section_path": block.get("section_path", [])[-3:],
        "text_preview": " ".join(block.get("text", "").split())[:100],
        "text_sha256": hashlib.sha256(block.get("text", "").encode("utf-8")).hexdigest(),
    }
    if block.get("warnings"):
        preview["warning_count"] = len(block["warnings"])
    if block.get("source_text_adjustments"):
        preview["source_text_adjustment_count"] = len(block["source_text_adjustments"])
    if block["kind"] == "table":
        rows = block.get("rows", [])
        rows = rows if isinstance(rows, list) else []
        preview.update({
            "row_count": len(rows),
            "max_column_count": max((len(row) for row in rows if isinstance(row, list)), default=0),
            "table_warning": block.get("table_warning"),
            "table_candidate": True,
            "requires_visual_review": True,
        })
        # Keep full row evidence for targeted selections, never in the compact navigation index.
    return preview


def _index_payload(document: dict[str, Any], profile: dict[str, int], index_path: Path) -> tuple[dict[str, Any], Path]:
    blocks = _blocks(document)
    inventory_path = index_path.with_name(f"{index_path.stem}-inventory.json")
    inventory = {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "document_sha256": _document_hash(document),
        "blocks": [_preview(block) for block in blocks],
    }
    special = [entry for entry in inventory["blocks"] if entry["kind"] in {
        "heading", "table", "table_caption", "figure", "figure_caption", "formula", "reference"
    }]
    page_entries: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        page_blocks = [block for block in blocks if block["page_number"] == page.get("page_number")]
        kinds: dict[str, int] = {}
        for block in page_blocks:
            kinds[block["kind"]] = kinds.get(block["kind"], 0) + 1
        page_entries.append({
            "page_number": page.get("page_number"),
            "status": page.get("status"),
            "block_count": len(page_blocks),
            "kinds": kinds,
            "risk": bool(page.get("warnings")) or any(block.get("warnings") for block in page_blocks),
        })
    index: dict[str, Any] = {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "document_sha256": _document_hash(document),
        "page_count": document.get("page_count", 0),
        "coverage": document.get("coverage", {}),
        "status": document.get("status"),
        "title_candidates": inventory["blocks"][:4],
        "special_blocks": special,
        "pages": page_entries,
        "inventory_artifact": str(inventory_path),
        "rendered_pages": [{"page_number": item.get("page_number"), "path": item.get("path")} for item in document.get("rendered_pages", [])],
        "usage": "Read this index, call select-context with targeted filters; do not read document.json wholesale.",
        "token_count_method": "estimated",
        "estimation_method": {"name": "mixed_characters_div3", "safety_factor": TEXT_SAFETY_FACTOR, "error": "unknown"},
        "truncated": False,
    }
    if document.get("extraction_recovery"):
        index["extraction_recovery"] = document["extraction_recovery"]
        index["recovery_warning"] = "Only illegal XML-1.0 C0 controls were temporarily substituted and restored; their printed glyph meanings remain unverified."
    limit = int(profile["document_index_max_tokens"])
    omitted_special = 0
    omitted_pages = 0
    while estimate_json_tokens(index) > limit and index["special_blocks"]:
        index["special_blocks"].pop()
        omitted_special += 1
    if omitted_special:
        index["omitted_special_block_count"] = omitted_special
        index["truncated"] = True
    while estimate_json_tokens(index) > limit and index["pages"]:
        index["pages"].pop()
        omitted_pages += 1
    if omitted_pages:
        index["omitted_page_count"] = omitted_pages
        index["page_index_continuation"] = "Use --page for unlisted pages; the inventory contains stable block references."
        index["truncated"] = True
    atomic_write_json(inventory_path, inventory)
    return index, inventory_path


def _initial_state(document: dict[str, Any], index_path: Path, index_tokens: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "document_sha256": _document_hash(document),
        "profile": dict(DEFAULT_PROFILE),
        "index": {"path": str(index_path.resolve()), "estimated_tokens": index_tokens, "counted": False},
        "source_injection_used_tokens": 0,
        "token_count_method": "estimated",
        "non_text_actual_tokens": 0,
        "non_text_estimated_tokens": 0,
        "usage_events": [],
        "unknown_history": [{"kind": "initial_unmeasured_history", "conservative_factor": 0.85}],
        "new_evidence_blocks": {},
        "selection_history": [],
        "processed_images": [],
        "image_estimated_tokens": 0,
        "glossary": {},
        "full_document_segments": [],
        "full_document_completed": False,
    }


def _effective_history_tokens(state: dict[str, Any]) -> int:
    return int(state.get("non_text_actual_tokens", 0)) + int(state.get("non_text_estimated_tokens", 0)) + int(state.get("image_estimated_tokens", 0))


def _budget_view(state: dict[str, Any], requested: int | None = None) -> dict[str, Any]:
    profile = state["profile"]
    used = int(state["source_injection_used_tokens"])
    history = _effective_history_tokens(state)
    unknown_factor = 0.85 if state.get("unknown_history") else 1.0
    effective_case_limit = math.floor(int(profile["case_source_injection_budget_tokens"]) * unknown_factor)
    current_context_available = max(0, int(profile["context_budget_tokens"]) - int(profile["runtime_reserve_tokens"]) - int(profile["response_reserve_tokens"]) - used - history)
    available = max(0, min(int(profile["single_selection_max_tokens"]), requested or int(profile["single_selection_max_tokens"]), effective_case_limit - used, current_context_available))
    return {
        "profile": profile,
        "effective_case_source_budget_tokens": effective_case_limit,
        "case_source_injection_used_tokens": used,
        "case_source_injection_remaining_tokens": max(0, effective_case_limit - used),
        "non_text_history_tokens": history,
        "history_measurement": "actual_and_estimated_actual_takes_precedence",
        "unknown_history_items": len(state.get("unknown_history", [])),
        "source_budget_uncertainty_factor": unknown_factor,
        "current_context_available_tokens": current_context_available,
        "available_for_new_evidence_tokens": available,
        "image_count": len(state.get("processed_images", [])),
        "image_estimated_tokens": state.get("image_estimated_tokens", 0),
    }


def _record_usage(state: dict[str, Any], event_path: str | Path | None) -> None:
    if not event_path:
        return
    path = Path(event_path)
    event = read_json(path)
    if not isinstance(event, dict):
        raise ValueError("Usage event must be an object")
    event_id = str(event.get("event_id") or hashlib.sha256(path.read_bytes()).hexdigest())
    if any(item.get("event_id") == event_id for item in state["usage_events"]):
        return
    actual = event.get("actual_tokens")
    estimated = event.get("estimated_tokens")
    if actual is None and estimated is None:
        state["unknown_history"].append({"kind": "unmeasured_usage_event", "event_id": event_id, "conservative_factor": 0.85})
    else:
        if actual is not None:
            if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0:
                raise ValueError("actual_tokens must be a non-negative integer")
            state["non_text_actual_tokens"] += actual
            method = "exact"
        else:
            method = None
        if actual is None and estimated is not None:
            if not isinstance(estimated, int) or isinstance(estimated, bool) or estimated < 0:
                raise ValueError("estimated_tokens must be a non-negative integer")
            state["non_text_estimated_tokens"] += estimated
            method = "estimated"
        if not method:
            raise ValueError("Usage event tokens were not recorded")
    state["usage_events"].append({**event, "event_id": event_id, "source_path": str(path), "recorded": True})


def _payload(block: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in block.items() if not key.startswith("_")}
    payload["estimated_tokens"] = 0
    payload["token_count_method"] = "estimated"
    payload["estimated_tokens"] = estimate_json_tokens(payload)
    return payload


def _glossary(state: dict[str, Any], path: str | Path | None) -> None:
    if not path:
        return
    source = read_json(path)
    if not isinstance(source, dict):
        raise ValueError("Glossary must be an object of compact term mappings")
    if len(state.get("glossary", {})) >= 16:
        return
    for term, translation in source.items():
        if len(state["glossary"]) >= 16:
            break
        if isinstance(term, str) and _content_size(term) <= 80 and _content_size(translation) <= 160:
            state["glossary"][term] = translation


def _block_list_hash(blocks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for block in blocks:
        digest.update(block["block_id"].encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(block.get("text", "").encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _update_early_result(plan: dict[str, Any], selection: dict[str, Any]) -> None:
    if not plan.get("early_result"):
        return
    path = Path(plan["early_result"])
    try:
        early = read_json(path)
    except Exception:
        return
    early["context"] = {
        "status": selection["status"],
        "selected_blocks": len(selection["selected_blocks"]),
        "remaining_blocks": selection["remaining_blocks"],
        "next_cursor": selection.get("next_cursor"),
        "budget": selection["budget"],
        "token_count_method": "estimated",
    }
    non_budget = [error for error in early.get("errors", []) if error.get("kind") not in {"pending_processing", "budget_exhausted", "context_budget_exhausted"}]
    early["errors"] = non_budget + selection.get("errors", [])
    changes = early.get("change_records", [])
    changes.append({"sequence": len(changes) + 1, "stage": "select-context", "description": "Recorded actual evidence selection and remaining scope"})
    early["change_records"] = changes
    atomic_write_json(path, early)


def _record_segment(
    document: dict[str, Any], segments_dir: str | Path, selection: dict[str, Any], blocks: list[dict[str, Any]], state: dict[str, Any]
) -> dict[str, Any]:
    directory = Path(segments_dir)
    manifest_path = directory / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "document_sha256": _document_hash(document),
        "expected_block_ids": [block["block_id"] for block in _blocks(document)],
        "segments": [],
    }
    if manifest["document_sha256"] != _document_hash(document) or manifest["case_id"] != document.get("case_id"):
        raise ValueError("Segment manifest belongs to another case or document revision")
    number = len(manifest["segments"]) + 1
    segment_id = f"segment-{number:04d}"
    segment = {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "segment_id": segment_id,
        "sequence": number,
        "document_sha256": _document_hash(document),
        "input_sha256": _block_list_hash(blocks),
        "block_ids": [block["block_id"] for block in blocks],
        "pages": sorted({block["page_number"] for block in blocks}),
        "status": "ready_for_agent",
        "glossary": state.get("glossary", {}),
        "token_count_method": "estimated",
        "next_cursor": selection.get("next_cursor"),
        "blocks": blocks,
        "page_images": selection.get("page_images", []),
    }
    segment_path = directory / f"{segment_id}.input.json"
    atomic_write_json(segment_path, segment)
    manifest["segments"].append({
        "segment_id": segment_id,
        "sequence": number,
        "input_path": str(segment_path),
        "expected_result_path": str(directory / f"{segment_id}.segment-result.json"),
        "input_sha256": segment["input_sha256"],
        "block_ids": segment["block_ids"],
    })
    manifest["remaining_block_ids"] = selection["omitted_block_ids"]
    manifest["next_cursor"] = selection.get("next_cursor")
    manifest["glossary"] = state.get("glossary", {})
    manifest["status"] = "complete_input_segmentation" if selection["remaining_blocks"] == 0 else "partial"
    atomic_write_json(manifest_path, manifest)
    selection["segment_artifact"] = str(segment_path)
    selection["segment_manifest"] = str(manifest_path)
    state["full_document_segments"] = manifest["segments"]
    state["next_full_document_cursor"] = selection.get("next_cursor")
    state["full_document_completed"] = selection["remaining_blocks"] == 0
    return segment


def _cursor_scope(query: str | None, pages: list[int] | None, sections: list[str] | None, block_ids: list[str] | None) -> dict[str, Any]:
    normalized_lists = [sorted(set(values)) if values else None for values in (pages, sections, block_ids)]
    return {"query": query.strip() if query else None, "pages": normalized_lists[0], "sections": normalized_lists[1], "block_ids": normalized_lists[2]}


def select_context_command(
    plan_path: str | Path,
    document_path: str | Path,
    output: str | Path,
    index_output: str | Path,
    budget_state: str | Path,
    max_tokens: int | None = None,
    query: str | None = None,
    pages: list[int] | None = None,
    sections: list[str] | None = None,
    block_ids: list[str] | None = None,
    cursor: str | None = None,
    usage_event: str | Path | None = None,
    full_document: bool = False,
    segments_dir: str | Path | None = None,
    glossary: str | Path | None = None,
) -> int:
    plan = read_json(plan_path)
    document = read_json(document_path)
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError("--max-tokens must be positive")
    index_path = Path(index_output)
    state_path = Path(budget_state)
    state = read_json(state_path) if state_path.exists() else None
    current_hash = _document_hash(document)
    if state and state.get("document_sha256") != current_hash:
        raise ValueError("Budget state belongs to another document revision; use a separate state file")
    if state:
        _record_usage(state, usage_event)
    reused_index = bool(state and state.get("index", {}).get("counted") and Path(state["index"]["path"]).exists() and state.get("index", {}).get("document_sha256", current_hash) == current_hash)
    if reused_index:
        index = read_json(state["index"]["path"])
        if Path(state["index"]["path"]).resolve() != index_path.resolve() or not index_path.exists():
            atomic_write_json(index_path, index)
    else:
        index, _ = _index_payload(document, DEFAULT_PROFILE, index_path)
        atomic_write_json(index_path, index)
    index_tokens = estimate_json_tokens(index)
    if state is None:
        state = _initial_state(document, index_path, index_tokens)
        _record_usage(state, usage_event)
    if state["case_id"] != document.get("case_id") or state["case_id"] != plan.get("case_id"):
        raise ValueError("Budget state case_id does not match case-plan and document")
    if cursor is not None and not full_document and "filters" in state.get("last_selection", {}):
        previous = state["last_selection"]["filters"]
        if not isinstance(previous, dict):
            raise ValueError("Previous selection filters are not a valid JSON object")
        query = query if query is not None else previous.get("query")
        pages = pages if pages is not None else previous.get("pages")
        sections = sections if sections is not None else previous.get("sections")
        block_ids = block_ids if block_ids is not None else previous.get("block_ids")
        current_scope = _cursor_scope(query, pages, sections, block_ids)
        previous_scope = _cursor_scope(previous.get("query"), previous.get("pages"), previous.get("sections"), previous.get("block_ids"))
        if current_scope != previous_scope:
            raise ValueError("Cursor selection scope changed; keep query/page/section/block filters identical or use a separate budget state and a cursor valid for the new scope")
    index_tokens_new = 0 if state["index"]["counted"] else index_tokens
    state["source_injection_used_tokens"] += index_tokens_new
    state["index"].update({"estimated_tokens": index_tokens, "counted": True, "document_sha256": current_hash})
    _glossary(state, glossary)
    all_blocks = _blocks(document)
    by_id = {block["block_id"]: block for block in all_blocks}
    if full_document:
        candidates = all_blocks
        if cursor is None:
            cursor = state.get("next_full_document_cursor")
    else:
        query_terms = [term.casefold() for term in re.findall(r"[a-zA-Z0-9_.+/-]+|[\u3400-\u9fff]+", query or "") if len(term) > 1]
        candidates = []
        for block in all_blocks:
            if pages and block["page_number"] not in set(pages):
                continue
            if sections and not any(section.casefold() in " ".join(block.get("section_path", [])).casefold() for section in sections):
                continue
            if block_ids and block["block_id"] not in set(block_ids):
                continue
            if query_terms:
                haystack = f"{block['text']} {' '.join(block.get('section_path', []))}".casefold()
                score = sum(haystack.count(term) for term in query_terms)
                if score <= 0:
                    continue
                block = dict(block)
                block["match_score"] = score
            candidates.append(block)
        if not candidates and query_terms:
            candidates = []
        candidates = sorted(candidates, key=lambda item: item["_sequence"])
    start = next((index for index, block in enumerate(candidates) if block["block_id"] == cursor), -1) + 1 if cursor else 0
    if cursor and start == 0:
        raise ValueError(f"Unknown cursor for current selection: {cursor}")
    pending = candidates[start:]
    budget_before = _budget_view(state, max_tokens)
    available = budget_before["available_for_new_evidence_tokens"]
    chosen: list[dict[str, Any]] = []
    used_for_selection = 0
    reused_count = 0
    next_index = start
    for block in pending:
        identifier = block["block_id"]
        next_index += 1
        previous = state["new_evidence_blocks"].get(identifier)
        if previous:
            reused = dict(previous)
            reused["reuse_only"] = True
            reused["reuse_of"] = previous["selection_artifact"]
            reused.pop("text", None)
            chosen.append(reused)
            reused_count += 1
            continue
        payload = _payload(block)
        if used_for_selection + payload["estimated_tokens"] > available:
            next_index -= 1
            break
        chosen.append(payload)
        used_for_selection += payload["estimated_tokens"]
    selected_ids = {block["block_id"] for block in chosen}
    omitted = [block["block_id"] for block in pending[next_index - start:]]
    new_chosen = [block for block in chosen if not block.get("reuse_only")]
    if new_chosen:
        next_cursor = new_chosen[-1]["block_id"]
    elif chosen:
        next_cursor = chosen[-1]["block_id"]
    else:
        next_cursor = cursor
    remaining = len(omitted)
    errors: list[dict[str, Any]] = []
    code = 0
    if pending and not new_chosen and not chosen:
        errors.append(error_record("context_budget_exhausted", "select-context", "The next whole evidence block exceeds the available budget; no text was cut", next_block=pending[0]["block_id"], available_tokens=available, required_tokens=_payload(pending[0])["estimated_tokens"]))
        code = 11
    elif omitted and available <= 0:
        errors.append(error_record("budget_exhausted", "select-context", "Source or context budget is exhausted; remaining blocks were not processed", remaining_blocks=remaining, first_omitted=omitted[0]))
        code = 11
    images: list[dict[str, Any]] = []
    selected_pages = {block["page_number"] for block in chosen}
    wanted_images = full_document or bool(pages)
    profile = state["profile"]
    if wanted_images:
        for image in document.get("rendered_pages", []):
            if len(images) >= profile["single_image_max_count"] or len(state["processed_images"]) >= profile["case_image_max_count"]:
                break
            if image.get("page_number") not in selected_pages or image.get("path") in state["processed_images"]:
                continue
            record = {**image, "estimated_tokens": profile["estimated_image_tokens"], "token_count_method": "estimated", "cost_verified": False}
            images.append(record)
            state["processed_images"].append(image.get("path"))
            state["image_estimated_tokens"] += profile["estimated_image_tokens"]
    selection: dict[str, Any] = {
        "schema_version": "1.0",
        "case_id": document.get("case_id"),
        "document_sha256": current_hash,
        "status": "budget_exhausted" if code else "ready" if remaining else "complete",
        "mode": "full_document" if full_document else "targeted",
        "filters": {"query": query, "pages": pages, "sections": sections, "block_ids": block_ids, "cursor": cursor},
        "scope": "main_blocks_excluding_headers_and_footers" if full_document else "filtered_evidence",
        "selected_blocks": chosen,
        "selected_count": len(chosen),
        "new_evidence_count": len(new_chosen),
        "reused_reference_count": reused_count,
        "new_evidence_estimated_tokens": used_for_selection,
        "index_new_estimated_tokens": index_tokens_new,
        "remaining_blocks": remaining,
        "truncated": bool(remaining),
        "next_cursor": next_cursor,
        "omitted_block_ids": omitted,
        "omitted_range": {"first": omitted[0] if omitted else None, "last": omitted[-1] if omitted else None},
        "page_images": images,
        "token_count_method": "estimated",
        "estimation_method": {"name": "mixed_characters_div3", "safety_factor": TEXT_SAFETY_FACTOR, "error": "unknown"},
        "errors": errors,
        "warnings": ["No deterministic match; formulate a different filter or inspect page ranges"] if not candidates and not full_document else [],
    }
    selection["selection_estimated_tokens"] = estimate_json_tokens({key: value for key, value in selection.items() if key not in {"selected_blocks", "page_images"}}) + used_for_selection
    if new_chosen:
        history_entry = {"sequence": len(state["selection_history"]) + 1, "artifact": str(Path(output).resolve()), "block_ids": [block["block_id"] for block in new_chosen], "estimated_tokens": used_for_selection}
        state["selection_history"].append(history_entry)
        for block in new_chosen:
            state["new_evidence_blocks"][block["block_id"]] = {**block, "selection_artifact": str(Path(output).resolve())}
    state["source_injection_used_tokens"] += used_for_selection
    selection["budget"] = _budget_view(state)
    selection["budget"]["this_call_new_source_tokens"] = index_tokens_new + used_for_selection
    selection["budget"]["total_context_estimated_tokens"] = state["profile"]["runtime_reserve_tokens"] + state["profile"]["response_reserve_tokens"] + state["source_injection_used_tokens"] + _effective_history_tokens(state)
    atomic_write_json(output, selection)
    if full_document and new_chosen:
        segment = _record_segment(document, segments_dir or Path(output).parent / "segments", selection, new_chosen, state)
        selection["segment_artifact"] = str(Path(segments_dir or Path(output).parent / "segments") / f"{segment['segment_id']}.input.json")
        selection["segment_manifest"] = str(Path(segments_dir or Path(output).parent / "segments") / "manifest.json")
        atomic_write_json(output, selection)
    if full_document and not remaining and not code:
        state["full_document_completed"] = True
    state["last_selection"] = {"path": str(Path(output).resolve()), "status": selection["status"], "next_cursor": next_cursor, "remaining_blocks": remaining, "mode": selection["mode"], "filters": dict(selection["filters"])}
    atomic_write_json(state_path, state)
    _update_early_result(plan, selection)
    print(
        f"selected={len(chosen)}; new={len(new_chosen)}; remaining={remaining}; "
        f"estimated_tokens={used_for_selection}; budget_remaining={selection['budget']['case_source_injection_remaining_tokens']}; "
        f"next_cursor={next_cursor}; selection={output}"
    )
    return code


def merge_segment_results(
    plan: dict[str, Any], document: dict[str, Any], segments_dir: str | Path, output: str | Path
) -> dict[str, Any]:
    directory = Path(segments_dir)
    manifest = read_json(directory / "manifest.json")
    if manifest.get("document_sha256") != _document_hash(document) or manifest.get("case_id") != plan.get("case_id"):
        raise ValueError("Segment manifest does not match the current plan and document")
    fields, required, _ = decode_contract(plan.get("output_contract"))
    ordered = sorted(manifest.get("segments", []), key=lambda item: item["sequence"])
    errors: list[dict[str, Any]] = []
    completed: list[str] = []
    sources: list[dict[str, Any]] = []
    values: dict[str, list[Any]] = {name: [] for name in fields}
    seen_segments: set[str] = set()
    for entry in ordered:
        identifier = entry["segment_id"]
        if identifier in seen_segments:
            errors.append(error_record("segment_duplicate", "merge-segments", "Duplicate segment sequence", segment_id=identifier))
            continue
        seen_segments.add(identifier)
        result_path = Path(entry["expected_result_path"])
        if not result_path.is_file():
            errors.append(error_record("segment_result_missing", "merge-segments", "Agent segment result is not present", segment_id=identifier, path=str(result_path)))
            continue
        segment = read_json(result_path)
        if segment.get("segment_id") != identifier or segment.get("input_sha256") != entry["input_sha256"] or segment.get("document_sha256") != manifest["document_sha256"]:
            errors.append(error_record("segment_hash_mismatch", "merge-segments", "Segment result does not match its persisted source hash", segment_id=identifier))
            continue
        block_ids = segment.get("block_ids")
        if block_ids != entry["block_ids"]:
            errors.append(error_record("segment_coverage_mismatch", "merge-segments", "Segment coverage differs from the input segment", segment_id=identifier))
            continue
        if any(block_id in completed for block_id in block_ids):
            errors.append(error_record("segment_overlap", "merge-segments", "A block was completed by more than one segment", segment_id=identifier))
        completed.extend(block_id for block_id in block_ids if block_id not in completed)
        segment_fields = segment.get("fields", {})
        if not isinstance(segment_fields, dict):
            errors.append(error_record("invalid_segment_result", "merge-segments", "Segment fields must be an object", segment_id=identifier))
            continue
        for name, value in segment_fields.items():
            if name not in fields:
                errors.append(error_record("invalid_segment_result", "merge-segments", "Segment contains a field outside the original output contract", segment_id=identifier, field=name))
                continue
            if not value_matches_type(value, fields[name]):
                errors.append(error_record("invalid_segment_result", "merge-segments", "Segment field has a different type from the contract", segment_id=identifier, field=name))
                continue
            values[name].append(value)
        if segment.get("status") != "complete":
            errors.append(error_record("segment_incomplete", "merge-segments", "Agent did not mark this segment complete", segment_id=identifier))
        for item in segment.get("errors", []):
            errors.append(item if isinstance(item, dict) else error_record("invalid_segment_result", "merge-segments", str(item), segment_id=identifier))
        sources.extend(source for source in segment.get("sources", []) if isinstance(source, dict))
    merged_fields: dict[str, Any] = {}
    for name, items in values.items():
        spec = fields[name]
        field_type = spec.get("type", "null")
        if field_type == "string":
            merged_fields[name] = "\n\n".join(item for item in items if item)
        elif field_type == "array":
            merged_fields[name] = [value for item in items for value in item]
        elif field_type == "object":
            merged: dict[str, Any] = {}
            for item in items:
                for key, value in item.items():
                    if key in merged and merged[key] != value:
                        errors.append(error_record("segment_field_conflict", "merge-segments", "Object keys have conflicting values across segments", field=name, key=key))
                    else:
                        merged[key] = value
            merged_fields[name] = merged
        elif items and any(item is not None for item in items):
            merged_fields[name] = None
            errors.append(error_record("segment_merge_unsupported", "merge-segments", "Scalar global fields require Agent synthesis rather than automatic aggregation", field=name))
        else:
            merged_fields[name] = None
    known_ids = set(manifest.get("expected_block_ids", []))
    missing = [block_id for block_id in manifest.get("expected_block_ids", []) if block_id not in set(completed)]
    unexpected = [block_id for block_id in completed if block_id not in known_ids]
    if unexpected:
        errors.append(error_record("segment_coverage_mismatch", "merge-segments", "Completed blocks are outside the declared full-document scope", block_ids=unexpected))
    for name in required:
        if name in fields and fields[name].get("type") in {"string", "array", "object"} and not merged_fields.get(name):
            errors.append(error_record("segment_required_field_missing", "merge-segments", "Merged required field is empty", field=name))
    result = {
        "schema_version": "1.0",
        "case_id": plan.get("case_id"),
        "document_sha256": manifest["document_sha256"],
        "status": "complete" if not errors and not missing else "partial",
        "complete": not errors and not missing,
        "fields": merged_fields,
        "sources": sources,
        "coverage": {"expected_blocks": len(known_ids), "completed_blocks": len(completed), "missing_block_ids": missing, "unexpected_block_ids": unexpected},
        "segment_count": len(ordered),
        "errors": errors,
        "merge_method": "deterministic_original_contract_fields",
        "order": "manifest_sequence_and_original_block_order",
    }
    atomic_write_json(output, result)
    return result
