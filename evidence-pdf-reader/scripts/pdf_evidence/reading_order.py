from __future__ import annotations

import re
from typing import Any


TERMINAL_RE = re.compile(r"[.!?:;。！？；：)\]」』】]$")
CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|table|图|表)\s*([A-Za-z]?\d+(?:\.\d+)*)", re.I)
NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+\S")
EQUATION_NUMBER_RE = re.compile(r"(?:\(|（)(\d+(?:\.\d+)*)(?:\)|）)\s*$")


def normalized(value: str) -> str:
    return " ".join(value.split())


def _classify(block: dict[str, Any]) -> dict[str, Any]:
    item = dict(block)
    text = normalized(item.get("text", ""))
    if item.get("original_kind") == "table":
        item["kind"] = "table"
    elif item.get("original_kind") == "figure":
        item["kind"] = "figure"
    else:
        caption = CAPTION_RE.match(text)
        equation = EQUATION_NUMBER_RE.search(text)
        if caption:
            prefix = caption.group(1).lower()
            item["kind"] = "table_caption" if prefix.startswith(("table", "表")) else "figure_caption"
            item["caption_number"] = caption.group(2)
        elif equation and len(text) <= 300 and re.search(r"[=+\-*/^_≤≥∑∫]", text):
            item["kind"] = "formula"
            item["formula_number"] = equation.group(1)
        elif NUMBERED_HEADING_RE.match(text) and len(text) <= 180:
            item["kind"] = "heading"
            item["heading_level"] = text.split()[0].strip(".)").count(".") + 1
        elif re.match(r"(?i)^(?:abstract|references|acknowledg\w*|appendix)\b", text) and len(text) <= 180:
            item["kind"] = "heading"
            item["heading_level"] = 1
        elif re.match(r"^\s*\[\d+\]", text):
            item["kind"] = "reference"
        else:
            item["kind"] = "body"
        if item["kind"] == "formula":
            match = EQUATION_NUMBER_RE.search(text)
            item["formula_number"] = match.group(1) if match else None
    item["text"] = text
    item.setdefault("source_text", item.get("raw_text", item.get("text", "")))
    item.setdefault("warnings", [])
    item.setdefault("links", [])
    return item


def _mark_repeated_margins(pages: list[dict[str, Any]]) -> None:
    occurrences: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for page in pages:
        width = page.get("width") or 1
        height = page.get("height") or 1
        for block in page.get("raw_blocks", []):
            text = normalized(block.get("text", ""))
            if not text or len(text) > 240:
                continue
            x0, y0, x1, y1 = block.get("bbox", [0, 0, width, height])
            region = "header" if y1 <= height * 0.15 else "footer" if y0 >= height * 0.85 else ""
            if region or re.fullmatch(r"\d{1,4}", text):
                region = region or "footer"
                occurrences.setdefault(text.lower(), []).append((page["page_number"], block, region))
    repeated = {text for text, values in occurrences.items() if len({item[0] for item in values}) >= 2}
    for text, values in occurrences.items():
        for _, block, region in values:
            if text in repeated or re.fullmatch(r"\d{1,4}", normalized(block.get("text", ""))):
                block["raw_kind"] = region
                block["kind"] = region
                block["text"] = normalized(block.get("text", ""))
                block["warnings"] = ["Classified as a repeated page number" if region == "footer" and text.isdigit() else "Classified as a repeated margin block; original text retained"]


def _full_width(block: dict[str, Any], width: float) -> bool:
    x0, _, x1, _ = block["bbox"]
    fraction = max(0.0, x1 - x0) / width
    return fraction >= 0.68 or (x0 <= width * 0.33 and x1 >= width * 0.62)


def _column_clusters(blocks: list[dict[str, Any]], width: float) -> tuple[list[list[dict[str, Any]]], bool]:
    if len(blocks) < 2:
        return [blocks], False
    scroll = sorted(blocks, key=lambda item: (item["bbox"][0], item["bbox"][1], item.get("raw_id", "")))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_max = 0.0
    for block in scroll:
        x0 = block["bbox"][0]
        if current and x0 > current_max + width * 0.02:
            clusters.append(current)
            current = []
            current_max = 0.0
        current.append(block)
        current_max = max(current_max, block["bbox"][2])
    if current:
        clusters.append(current)
    if not 2 <= len(clusters) <= 3:
        return [blocks], len(clusters) > 3
    ranges = []
    for cluster in clusters:
        y0 = min(block["bbox"][1] for block in cluster)
        y1 = max(block["bbox"][3] for block in cluster)
        ranges.append((y0, y1))
    common = min(end for _, end in ranges) - max(start for start, _ in ranges)
    zone_height = max(end for _, end in ranges) - min(start for start, _ in ranges)
    if common <= 0 or common < zone_height * 0.10:
        return [blocks], False
    return clusters, False


def _order_zone(blocks: list[dict[str, Any]], width: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(blocks) <= 1:
        return blocks, {"columns": 1, "ambiguous": False, "raw_blocks": [b["raw_id"] for b in blocks]}
    divided, ambiguous = _column_clusters(blocks, width)
    if len(divided) == 1:
        if ambiguous:
            for block in blocks:
                block["warnings"].append("Unstable multi-column hypothesis; retained coordinate fallback instead of forcing column ownership")
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0], item.get("raw_id", ""))), {
            "columns": 1,
            "ambiguous": ambiguous,
            "raw_blocks": [b["raw_id"] for b in blocks],
        }
    ordered: list[dict[str, Any]] = []
    for block in blocks:
        if block not in [entry for cluster in divided for entry in cluster]:
            block["warnings"].append("Column assignment was ambiguous; retained raw coordinate evidence")
    debug_columns: list[list[str]] = []
    for column in divided:
        column.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item.get("raw_id", "")))
        ordered.extend(column)
        debug_columns.append([block["raw_id"] for block in column])
    return ordered, {"columns": len(divided), "ambiguous": False, "raw_columns": debug_columns}


def _order_page(page: dict[str, Any], naive: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    width = float(page.get("width") or 1)
    margins = [block for block in page.get("raw_blocks", []) if block.get("raw_kind") in {"header", "footer"}]
    headers = sorted((b for b in margins if b.get("raw_kind") == "header"), key=lambda b: b["bbox"][1])
    footers = sorted((b for b in margins if b.get("raw_kind") == "footer"), key=lambda b: b["bbox"][1])
    body = [_classify(block) for block in page.get("raw_blocks", []) if block.get("raw_kind") not in {"header", "footer"}]
    for block in body:
        block["bbox"] = [float(value) for value in block.get("bbox", [0, 0, width, page.get("height") or 1])]
    if naive:
        ordered = sorted(body, key=lambda item: (item["bbox"][1], item["bbox"][0], item.get("raw_id", "")))
        return [*_classify_margin(headers), *ordered, *_classify_margin(footers)], {"mode": "naive", "zones": []}

    full = sorted((b for b in body if _full_width(b, width)), key=lambda b: (b["bbox"][1], b["bbox"][0]))
    remaining = [b for b in body if b not in full]
    assigned: set[str] = set()
    zones: list[tuple[str, list[dict[str, Any]]]] = []
    for cross in full:
        before = [
            b for b in remaining
            if b["raw_id"] not in assigned and (b["bbox"][1] + b["bbox"][3]) / 2 < cross["bbox"][1]
        ]
        if before:
            zones.append(("columns", before))
            assigned.update(block["raw_id"] for block in before)
        overlapping: list[dict[str, Any]] = []
        for block in remaining:
            if block["raw_id"] in assigned:
                continue
            overlap = min(block["bbox"][3], cross["bbox"][3]) - max(block["bbox"][1], cross["bbox"][1])
            minimum_height = max(1.0, min(block["bbox"][3] - block["bbox"][1], cross["bbox"][3] - cross["bbox"][1]))
            if overlap > 0 and overlap / minimum_height >= 0.15:
                overlapping.append(block)
        group = sorted([cross, *overlapping], key=lambda item: (item["bbox"][1], item["bbox"][0], item["raw_id"]))
        zones.append(("mixed" if overlapping else "full_width", group))
        assigned.update(block["raw_id"] for block in overlapping)
    after = [b for b in remaining if b["raw_id"] not in assigned]
    if after:
        zones.append(("columns", after))
    ordered: list[dict[str, Any]] = []
    debug_zones: list[dict[str, Any]] = []
    for zone_type, blocks in zones:
        if zone_type in {"full_width", "mixed"}:
            zone_order = blocks
            debug = {"type": zone_type, "raw_blocks": [b["raw_id"] for b in blocks]}
        else:
            zone_order, debug = _order_zone(blocks, width)
            debug["type"] = zone_type
        ordered.extend(zone_order)
        debug_zones.append(debug)
    ambiguous = any(zone.get("ambiguous") for zone in debug_zones)
    if ambiguous:
        page.setdefault("warnings", []).append("layout_ambiguous: review page image before using the coordinate fallback as verified reading order")
    return [*_classify_margin(headers), *ordered, *_classify_margin(footers)], {"mode": "layout", "zones": debug_zones, "ambiguous": ambiguous}


def _classify_margin(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(block, text=normalized(block.get("text", "")), kind=block.get("raw_kind")) for block in blocks]


def _add_section_paths(pages: list[dict[str, Any]]) -> None:
    section_stack: list[tuple[int, str]] = []
    sequence = 0
    for page in pages:
        page_order = 0
        for block in page["blocks"]:
            sequence += 1
            page_order += 1
            block["global_reading_order"] = sequence
            block["page_reading_order"] = page_order
            if block["kind"] == "heading":
                level = int(block.get("heading_level", 1))
                section_stack = [(old_level, text) for old_level, text in section_stack if old_level < level]
                section_stack.append((level, block["text"][:160]))
            block["section_path"] = [text for _, text in section_stack]


def _continuity_candidate(previous: dict[str, Any], following: dict[str, Any]) -> dict[str, Any] | None:
    left = previous.get("text", "").rstrip()
    right = following.get("text", "").lstrip()
    if not left or not right:
        return None
    hyphenated = left.endswith("-") and right[:1].isalpha()
    lowercase_continuation = not TERMINAL_RE.search(left) and right[:1].islower()
    if not (hyphenated or lowercase_continuation):
        return None
    return {
        "type": "continues_candidate",
        "source": previous["block_id"],
        "target": following["block_id"],
        "reason": "hyphenated_word" if hyphenated else "unterminated_and_lowercase",
        "review_required": True,
    }


def order_pages(pages: list[dict[str, Any]], mode: str = "layout") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _mark_repeated_margins(pages)
    output: list[dict[str, Any]] = []
    layout_debug: dict[str, Any] = {"mode": mode, "pages": []}
    for page in pages:
        working = dict(page)
        working["warnings"] = list(page.get("warnings", []))
        working["raw_blocks"] = [dict(block) for block in page.get("raw_blocks", [])]
        blocks, debug = _order_page(working, naive=mode == "naive")
        page_output = {key: value for key, value in page.items() if key != "raw_blocks"}
        page_output["warnings"] = list(working.get("warnings", []))
        page_output["blocks"] = blocks
        page_output["layout"] = debug
        for index, block in enumerate(blocks, start=1):
            block["block_id"] = f"p{page['page_number']:04d}-b{index:04d}"
            block["page_number"] = page["page_number"]
            block.setdefault("extraction_method", "unknown")
            block.setdefault("warnings", [])
            block.setdefault("links", [])
        output.append(page_output)
        layout_debug["pages"].append({"page_number": page["page_number"], **debug})
    _add_section_paths(output)
    for previous_page, next_page in zip(output, output[1:]):
        previous = [b for b in previous_page["blocks"] if b["kind"] not in {"header", "footer"}]
        following = [b for b in next_page["blocks"] if b["kind"] not in {"header", "footer"}]
        if previous and following:
            link = _continuity_candidate(previous[-1], following[0])
            if link:
                previous[-1]["links"].append(link)
    return output, layout_debug
