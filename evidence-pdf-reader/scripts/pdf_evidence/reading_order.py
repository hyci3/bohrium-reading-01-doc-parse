from __future__ import annotations

import re
from statistics import median
from typing import Any


TERMINAL_RE = re.compile(r"[.!?:;。！？；：)\]」』】]$")
CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|table|图|表)\s*([A-Za-z]?\d+(?:\.\d+)*)(?=\s*[:.．。：])", re.I)
NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+\S")
# Letter-numbered appendix headings ("A.2", "A.2.1", "Appendix B") never match NUMBERED_HEADING_RE.
APPENDIX_HEADING_RE = re.compile(r"^(?:[A-Z](?:\.\d+)+|Appendix\s+[A-Z])\b")
EQUATION_NUMBER_RE = re.compile(r"(?:\(|（)(\d+(?:\.\d+)*)(?:\)|）)\s*$")
CONTROL_CHARS_RE = re.compile(r"[\x10-\x1f]")
MATH_SYMBOL_RE = re.compile(r"[=+\-−×·*/^_∑∫∂≈≤≥±<>(){}\[\]]")
MATH_STRONG_RE = re.compile(r"[=≈≤≥±∑∫∂^_]")
CAPTION_PREFIX_RE = re.compile(r"^\s*(figure|fig\.?|table|图|表)\b", re.I)
# A heading never starts its title with a function word; prose that begins "2 of the first ..." is a
# continuation fragment whose leading superscript was split into its own text run.
HEADING_STOPWORDS = frozenset({
    "of", "and", "the", "for", "with", "in", "to", "as", "that", "which", "is", "are", "we",
    "this", "by", "from", "on", "at", "when", "where", "since", "because", "it", "its", "our",
    "their", "be", "been", "was", "were", "has", "have", "had", "not", "but", "or", "if", "then",
    "than", "so", "such", "these", "those", "an", "into", "over", "under", "between", "both",
})
HEADING_TERMINAL_PUNCTUATION = ".,;:。，；：?!！？"


def _math_fragment(text: str) -> bool:
    """Heuristic: text that is a fragment of a formula rather than prose.

    PyMuPDF emits math as several runs, sometimes with private control code points, so these
    fragments must not be trusted as headings or quoted as complete equations. Numeric data
    rows are deliberately excluded: they are long and contain no operators or control codes.
    """
    if not text or CAPTION_PREFIX_RE.match(text):
        return False
    if CONTROL_CHARS_RE.search(text):
        return True
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact:
        return False
    letters = sum(1 for ch in compact if ch.isalpha())
    digits = sum(1 for ch in compact if ch.isdigit())
    symbols = sum(1 for ch in compact if MATH_SYMBOL_RE.match(ch))
    number_tokens = re.findall(r"\d+(?:\.\d+)?", text)
    if len(compact) <= 80 and MATH_STRONG_RE.search(text) and letters / len(compact) <= 0.45:
        return True
    # A run that opens with an equation number or a stray superscript and carries operators is a
    # piece of a broken formula, e.g. "2 , WNF4) + XBF16LBF16" or "1 , ck-bit".
    if re.match(r"^\d+\s*[,，)]", text) and re.search(r"[=+()\[\]^_-]", text):
        return True
    if (
        len(compact) <= 24
        and 1 <= letters <= 3
        and symbols >= 1
        and digits + symbols >= 2
        and len(number_tokens) <= 2
    ):
        return True
    tokens = text.split()
    if (
        len(tokens) <= 3
        and re.search(r"[a-z]{3,}", text) is None
        and re.search(r"[A-Z]", text)
        and re.search(r"\d", text)
        and re.search(r"\([A-Za-z]{2,}", text) is None
        and (text[:1].isdigit() or re.search(r"[=+^_∑∫∂<>]", text) is not None)
    ):
        return True
    return False


def _heading_level(text: str) -> int:
    first = text.split()[0].strip(".,;:)")
    if re.fullmatch(r"[A-Z](?:\.\d+)+", first) or re.fullmatch(r"\d+(?:\.\d+)*", first):
        return first.count(".") + 1
    return 1


def normalized(value: str) -> str:
    return " ".join(value.split())


def _numbered_heading(block: dict[str, Any], text: str, font_size_reference: float | None) -> bool:
    if not (NUMBERED_HEADING_RE.match(text) or APPENDIX_HEADING_RE.match(text)):
        return False
    if len(text) > 120 or len(text.split()) > 16:
        return False
    if _math_fragment(text):
        return False
    words = text.split()
    # Headings are labels, not sentences: terminal punctuation betrays numbered list items and
    # prose whose leading superscript became a separate run. Short labels such as
    # "5 Conclusion." legitimately end with a period, so only phrase-length runs are rejected.
    if len(words) >= 4 and text.rstrip()[-1:] in HEADING_TERMINAL_PUNCTUATION:
        return False
    # An arithmetic expression that closes with a bracket is a figure/table label, not a section.
    if re.search(r"[)\]]\s*$", text) and re.search(r"[=+×·/]", text):
        return False
    if len(words) > 1:
        second_token = words[1].strip(".,;:)")
        if not second_token:
            return False
        if second_token.lower() in HEADING_STOPWORDS:
            return False
        if re.fullmatch(r"[A-Z]*\d+[A-Z0-9]*", second_token):
            return False
        if re.fullmatch(r"[^\w]+", second_token):
            return False
    font_size = block.get("font_size_max")
    if isinstance(font_size, (int, float)) and font_size_reference and font_size < font_size_reference * 0.85:
        return False
    return True


def _bottom_margin_numbered_prose(block: dict[str, Any], text: str, page_height: float | None) -> bool:
    if not isinstance(page_height, (int, float)) or page_height <= 0:
        return False
    bbox = block.get("bbox") or []
    if len(bbox) < 4 or not isinstance(bbox[1], (int, float)):
        return False
    return (
        bbox[1] >= page_height * 0.85
        and len(text.split()) >= 9
        and re.search(r"[.!?。！？]$", text) is not None
    )


def _source_defaults(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("source_text") is None:
        item["source_text"] = item.get("raw_text") if item.get("raw_text") is not None else item.get("text", "")
    item.setdefault("warnings", [])
    item.setdefault("links", [])
    return item


def _classify(
    block: dict[str, Any], font_size_reference: float | None = None, page_height: float | None = None
) -> dict[str, Any]:
    item = dict(block)
    text = normalized(item.get("text", ""))
    if item.get("original_kind") == "table":
        item["kind"] = "table"
        rows = item.get("rows")
        if isinstance(rows, list) and rows:
            # Keep physical rows on their own lines: a single flattened line invites row/column
            # misreads (e.g. reading a neighbouring row's cell as the target model's score).
            item["table_row_count"] = len(rows)
            item["table_column_count"] = max((len(row) for row in rows), default=0)
            item["text"] = "\n".join(
                " | ".join("" if cell is None else str(cell) for cell in row) for row in rows
            )
            item["table_warning"] = (
                "Table text keeps one line per detected row; cells are extractor output, so merged "
                "cells and row spans still require source-page verification"
            )
            return _source_defaults(item)
    if item.get("original_kind") == "figure":
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
        elif _math_fragment(text):
            item["kind"] = "formula"
            item["warnings"] = list(item.get("warnings") or []) + [
                "formula_fragment_unverified: text is a fragment of a formula, not a complete equation; "
                "review the page image before quoting it"
            ]
        elif (
            (NUMBERED_HEADING_RE.match(text) or APPENDIX_HEADING_RE.match(text))
            and _bottom_margin_numbered_prose(item, text, page_height)
        ):
            item["kind"] = "body"
            item["warnings"] = list(item.get("warnings") or []) + [
                "Numbered prose in the bottom margin treated as body rather than a verified heading; review possible footnote"
            ]
        elif _numbered_heading(item, text, font_size_reference):
            item["kind"] = "heading"
            item["heading_level"] = _heading_level(text)
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
    return _source_defaults(item)


def _mark_repeated_margins(pages: list[dict[str, Any]]) -> None:
    occurrences: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    prefixed_headers: dict[str, list[tuple[int, dict[str, Any], bool]]] = {}
    numeric_margin_counts: dict[tuple[int, str], int] = {}
    for page in pages:
        height = page.get("height") or 1
        for block in page.get("raw_blocks", []):
            if block.get("original_kind") == "table":
                continue
            text = normalized(block.get("text", ""))
            if not re.fullmatch(r"\d{1,4}", text):
                continue
            _, y0, _, y1 = block.get("bbox", [0, 0, 0, 0])
            region = "header" if y1 <= height * 0.15 else "footer" if y0 >= height * 0.85 else ""
            if region:
                key = (page["page_number"], region)
                numeric_margin_counts[key] = numeric_margin_counts.get(key, 0) + 1
    for page in pages:
        width = page.get("width") or 1
        height = page.get("height") or 1
        for block in page.get("raw_blocks", []):
            # A repeated short table label is not necessarily a running page margin.
            if block.get("original_kind") == "table":
                continue
            text = normalized(block.get("text", ""))
            if not text or len(text) > 240:
                continue
            x0, y0, x1, y1 = block.get("bbox", [0, 0, width, height])
            region = "header" if y1 <= height * 0.15 else "footer" if y0 >= height * 0.85 else ""
            numeric = re.fullmatch(r"\d{1,4}", text) is not None
            if numeric and not region:
                block["raw_kind"] = "body"
            if numeric and region and numeric_margin_counts.get((page["page_number"], region), 0) > 1:
                block["raw_kind"] = "body"
                block["warnings"] = list(block.get("warnings") or []) + [
                    "Multiple isolated numeric blocks in this page margin; retained in the main reading flow instead of automatic page-number classification"
                ]
                continue
            if region and len(text.split()) < 2 and not numeric:
                continue
            if region:
                occurrences.setdefault(text.lower(), []).append((page["page_number"], block, region))
            if region == "header":
                prefix = re.match(r"^(\d{1,4})\s+(.+)$", text)
                matching_prefix = bool(
                    prefix
                    and int(prefix.group(1)) == page["page_number"]
                    and re.search(r"[^\W\d_]", prefix.group(2))
                )
                header_key = prefix.group(2) if matching_prefix else text
                prefixed_headers.setdefault(header_key.lower(), []).append(
                    (page["page_number"], block, matching_prefix)
                )
    repeated = {text for text, values in occurrences.items() if len({item[0] for item in values}) >= 2}
    for text, values in occurrences.items():
        for _, block, region in values:
            if text in repeated or re.fullmatch(r"\d{1,4}", normalized(block.get("text", ""))):
                block["raw_kind"] = region
                block["kind"] = region
                block["text"] = normalized(block.get("text", ""))
                block["warnings"] = ["Classified as a repeated page number" if region == "footer" and text.isdigit() else "Classified as a repeated margin block; original text retained"]
    # Preserve exact matching above. A second, stricter pass handles running headers
    # whose physical page number was extracted in the same text block as the title.
    repeated_prefixed = {
        text for text, values in prefixed_headers.items()
        if len({number for number, _, matching_prefix in values if matching_prefix}) >= 3
    }
    for text, values in prefixed_headers.items():
        if text not in repeated_prefixed:
            continue
        for _, block, _ in values:
            block["raw_kind"] = "header"
            block["kind"] = "header"
            block["text"] = normalized(block.get("text", ""))
            block["warnings"] = [
                "Classified as a repeated running header after matching a physical page-number prefix across at least three pages; original text retained"
            ]


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
    assigned = {id(entry) for cluster in divided for entry in cluster}
    for block in blocks:
        if id(block) not in assigned:
            block["warnings"].append("Column assignment was ambiguous; retained raw coordinate evidence")
    debug_columns: list[list[str]] = []
    for column in divided:
        column.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item.get("raw_id", "")))
        ordered.extend(column)
        debug_columns.append([block["raw_id"] for block in column])
    return ordered, {"columns": len(divided), "ambiguous": False, "raw_columns": debug_columns}


def _table_barrier_conflict(block: dict[str, Any], body: list[dict[str, Any]], width: float, height: float) -> list[str]:
    if block.get("original_kind") != "table" or not _full_width(block, width):
        return []
    x0, y0, x1, y1 = block["bbox"]
    conflicts = []
    for caption in body:
        if caption.get("kind") != "figure_caption":
            continue
        cx0, cy0, cx1, cy1 = caption["bbox"]
        horizontal = max(0.0, min(x1, cx1) - max(x0, cx0))
        center_y = (cy0 + cy1) / 2
        if y0 <= center_y <= y1 and horizontal >= (cx1 - cx0) * 0.5 and y1 > cy1 + height * 0.05:
            conflicts.append(str(caption.get("caption_number", "unknown")))
    return conflicts


def _order_page(page: dict[str, Any], naive: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    width = float(page.get("width") or 1)
    margins = [block for block in page.get("raw_blocks", []) if block.get("raw_kind") in {"header", "footer"}]
    headers = sorted((b for b in margins if b.get("raw_kind") == "header"), key=lambda b: b["bbox"][1])
    footers = sorted((b for b in margins if b.get("raw_kind") == "footer"), key=lambda b: b["bbox"][1])
    # Weight by text length: many tiny axis labels must not define body-font size.
    font_sizes = []
    for block in page.get("raw_blocks", []):
        size = block.get("font_size_max")
        if not isinstance(size, (int, float)) or size <= 0:
            continue
        weight = max(1, len(" ".join(str(block.get("text", "")).split())))
        font_sizes.extend([float(size)] * weight)
    reference_font = median(font_sizes) if font_sizes else None
    body = [
        _classify(block, reference_font, page_height=float(page.get("height") or 1))
        for block in page.get("raw_blocks", [])
        if block.get("raw_kind") not in {"header", "footer"}
    ]
    for block in body:
        block["bbox"] = [float(value) for value in block.get("bbox", [0, 0, width, page.get("height") or 1])]
    if naive:
        ordered = sorted(body, key=lambda item: (item["bbox"][1], item["bbox"][0], item.get("raw_id", "")))
        return [*_classify_margin(headers), *ordered, *_classify_margin(footers)], {"mode": "naive", "zones": []}

    full = []
    for candidate in sorted((b for b in body if _full_width(b, width)), key=lambda b: (b["bbox"][1], b["bbox"][0])):
        conflicts = _table_barrier_conflict(candidate, body, width, float(page.get("height") or 1))
        if conflicts:
            candidate["layout_barrier_excluded"] = True
            warning = "Table candidate bbox overlaps figure caption(s) " + ", ".join(conflicts) + " and extends more than 5% of page height below them; candidate text retained, not used as a full-width barrier"
            candidate["table_warning"] = (candidate.get("table_warning", "") + "; " + warning).strip("; ")
            page.setdefault("warnings", []).append(f"table_barrier_excluded: {candidate['raw_id']} requires visual review because its bbox captures later figure-caption content")
            continue
        full.append(candidate)
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
    return [
        _source_defaults(dict(block, text=normalized(block.get("text", "")), kind=block.get("raw_kind")))
        for block in blocks
    ]


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
