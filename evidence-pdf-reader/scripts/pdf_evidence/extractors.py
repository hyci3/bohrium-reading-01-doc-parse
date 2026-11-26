from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from math import isfinite
from statistics import median
from typing import Any

from .case_io import atomic_write_json, error_record, read_json
from .probe import local_name, run_to_log
from .poppler_xml import has_illegal_xml_control, page_control_warning, parse_poppler_bbox
from .reading_order import _bbox_union, _has_inline_sentence, _vertical_overlap_ratio, order_pages


def _bbox(items: list[dict[str, Any]]) -> list[float]:
    return [
        min(item[0] for item in items),
        min(item[1] for item in items),
        max(item[2] for item in items),
        max(item[3] for item in items),
    ]


def _span_texts(spans: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (plain text, text with script markers, recorded script spans).

    A superscript or subscript is only marked when the span is smaller than the
    line font *and* its baseline is shifted; small capitals keep the same
    baseline and therefore stay plain text.
    """
    plain = "".join(str(span.get("text") or "") for span in spans)
    sizes = [float(span.get("size") or 0) for span in spans if span.get("text")]
    base = max(sizes) if sizes else 0.0
    baselines = [
        float((span.get("origin") or [0, 0])[1])
        for span in spans
        if span.get("text") and float(span.get("size") or 0) >= base * 0.95
    ]
    reference = median(baselines) if baselines else None
    parts: list[str] = []
    scripts: list[dict[str, Any]] = []
    for span in spans:
        text = str(span.get("text") or "")
        if not text:
            continue
        size = float(span.get("size") or 0)
        baseline = float((span.get("origin") or [0, 0])[1])
        core = text.strip()
        script = None
        if reference is not None and base and size < base * 0.85 and 0 < len(core) <= 12 and " " not in core:
            if baseline <= reference - base * 0.12:
                script = "superscript"
            elif baseline >= reference + base * 0.12:
                script = "subscript"
        if script == "superscript":
            parts.append("^{" + core + "}" + text[len(text.rstrip()):])
        elif script == "subscript":
            parts.append("_{" + core + "}" + text[len(text.rstrip()):])
        else:
            parts.append(text)
        if script:
            scripts.append({"text": core, "script": script, "size": round(size, 2)})
    return plain, "".join(parts), scripts


def _pymupdf_page(page: Any, table_diagnostics: list[str] | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") == 0:
            plain_lines: list[str] = []
            marked_lines: list[str] = []
            block_scripts: list[dict[str, Any]] = []
            font_sizes: list[float] = []
            bold = False
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                plain, marked, scripts = _span_texts(spans)
                plain_lines.append(plain)
                marked_lines.append(marked)
                block_scripts.extend(scripts)
                for span in spans:
                    if span.get("text"):
                        font_sizes.append(float(span.get("size", 0)))
                        font_name = str(span.get("font", "")).lower()
                        bold = bold or "bold" in font_name or bool(int(span.get("flags", 0)) & 16)
            text = "\n".join(value for value in plain_lines if value)
            marked_text = "\n".join(value for value in marked_lines if value)
            if not text.strip():
                continue
            bbox = [float(value) for value in block.get("bbox", [0, 0, 0, 0])]
            raw_id = f"r{len(blocks):04d}"
            entry = {
                "raw_id": raw_id,
                "bbox": bbox,
                "raw_text": text,
                "text": text,
                "source_order": len(blocks),
                "original_kind": "body",
                "raw_kind": "body",
                "font_size_max": max(font_sizes) if font_sizes else None,
                "font_bold": bold,
            }
            if block_scripts:
                # Prose keeps plain text so quotes stay copy-pasteable; formulas switch to the marked
                # text during classification, where superscripts carry meaning.
                entry["text_with_scripts"] = marked_text
                entry["scripts"] = block_scripts
            blocks.append(entry)
        elif block.get("type") == 1:
            blocks.append({
                "raw_id": f"r{len(blocks):04d}",
                "bbox": [float(value) for value in block.get("bbox", [0, 0, 0, 0])],
                "raw_text": "",
                "text": "",
                "source_order": len(blocks),
                "original_kind": "figure",
                "raw_kind": "figure",
                "has_image": True,
                "font_size_max": None,
                "font_bold": False,
            })
    table_candidates = 0
    try:
        finder = page.find_tables()
        for table in getattr(finder, "tables", []):
            rows = [
                ["" if cell is None else " ".join(str(cell).split()) for cell in row]
                for row in table.extract()
            ]
            text = "\n".join(" | ".join(row) for row in rows)
            header: list[str] = []
            external_header = False
            try:
                names = getattr(getattr(table, "header", None), "names", None) or []
                header = [" ".join(str(name).split()) for name in names if name is not None]
                external_header = bool(getattr(table.header, "external", False))
            except Exception:
                header = []
                external_header = False
            blocks.append({
                "raw_id": f"rt{len(blocks):04d}",
                "bbox": [float(value) for value in table.bbox],
                "raw_text": text,
                "text": text,
                "source_order": len(blocks),
                "original_kind": "table",
                "raw_kind": "body",
                "rows": rows,
                "table_header": header,
                "table_header_external": external_header,
                "table_empty_cells": sum(1 for row in rows for cell in row if not cell),
                "table_warning": "Table structure is extractor output and still requires source verification for merged cells",
                "table_candidate": True,
                "requires_visual_review": True,
            })
            table_candidates += 1
        if table_diagnostics is not None and table_candidates:
            table_diagnostics.append(f"table_candidates_unverified: {table_candidates} automatic table regions require source-page review")
    except Exception as exc:
        # Table recovery is opportunistic; preserve its failure reason and original text/image evidence.
        if table_diagnostics is not None:
            table_diagnostics.append(f"table_recovery_unavailable: {type(exc).__name__}: {str(exc)[:300]}")
    return blocks


def _element_bbox(element: ET.Element) -> list[float] | None:
    names = (("xmin", "xMin"), ("ymin", "yMin"), ("xmax", "xMax"), ("ymax", "yMax"))
    values: list[float] = []
    for lower, camel in names:
        raw = element.attrib.get(lower, element.attrib.get(camel))
        if raw is None:
            return None
        values.append(float(raw))
    return values


def _join_line_words(words: list[ET.Element]) -> tuple[str, list[dict[str, Any]]]:
    """Join only geometrically supported subscripts and immediately following punctuation."""
    fragments: list[str] = []
    adjustments: list[dict[str, Any]] = []
    previous_was_subscript = False
    for index, word in enumerate(words):
        text = word.text or ""
        joined = False
        if index:
            previous = words[index - 1]
            left_text = previous.text or ""
            try:
                left_bbox = _element_bbox(previous)
                right_bbox = _element_bbox(word)
                left_height = left_bbox[3] - left_bbox[1] if left_bbox else 0.0
                right_height = right_bbox[3] - right_bbox[1] if right_bbox else 0.0
                gap = right_bbox[0] - left_bbox[2] if left_bbox and right_bbox else 1.0
                touching = -0.05 <= gap <= 0.05
                lower = right_bbox[1] >= left_bbox[1] + left_height * 0.20
                bounded_bottom = right_bbox[3] <= left_bbox[3] + left_height * 0.25
                joins_subscript = (
                    touching and left_height > 0 and 0 < right_height <= left_height * 0.75
                    and lower and bounded_bottom and bool(left_text) and left_text[-1].isalpha()
                    and text.isascii() and (text.isdigit() or text.isalpha())
                )
                if joins_subscript:
                    fragments[-1] += text
                    adjustments.append({
                        "kind": "numeric_subscript_geometry" if text.isdigit() else "alphabetic_subscript_geometry",
                        "raw_word_texts": [left_text, text],
                        "raw_word_bboxes": [left_bbox, right_bbox],
                        "gap_points": round(gap, 6),
                        "scope": "same_poppler_xml_line",
                    })
                    previous_was_subscript = True
                    joined = True
                elif previous_was_subscript and text in {",", ".", ";", ":", "!", "?", ")", "]", "}"} and 0 <= gap <= min(0.75, left_height * 0.10):
                    fragments[-1] += text
                    adjustments.append({
                        "kind": "post_subscript_punctuation_geometry",
                        "raw_word_texts": [left_text, text],
                        "raw_word_bboxes": [left_bbox, right_bbox],
                        "gap_points": round(gap, 6),
                        "threshold_points": round(min(0.75, left_height * 0.10), 6),
                        "scope": "same_poppler_xml_line_immediately_after_subscript",
                        "requires_visual_review": True,
                    })
                    previous_was_subscript = False
                    joined = True
                else:
                    previous_was_subscript = False
            except (KeyError, ValueError, TypeError):
                previous_was_subscript = False
        if not joined:
            fragments.append(text)
    return " ".join(fragments), adjustments


def _wrapped_text_partition(
    element: ET.Element,
    page_width: float | None,
    source_block_index: int,
) -> tuple[list[list[ET.Element]], list[list[ET.Element]], dict[str, Any]] | None:
    """Split only a conservative two-line floating-text band inside one XML block.

    Geometry governs the partition. Word counts and letter proportions reject weak
    prose-span candidates; they do not establish semantic rows, cells, or captions.
    """
    line_nodes = [item for item in element.iter() if local_name(item.tag) == "line"]
    all_words = [item for item in element.iter() if local_name(item.tag) == "word"]
    if len(line_nodes) < 3 or len(all_words) < 40 or page_width is None or page_width <= 0:
        return None
    try:
        parent_bbox = _element_bbox(element)
        if parent_bbox is None or not all(isfinite(value) for value in parent_bbox) or parent_bbox[2] - parent_bbox[0] < page_width * 0.55:
            return None
        prose_count = sum(any(character.isalpha() for character in (word.text or "")) for word in all_words)
        if prose_count < len(all_words) * 0.75:
            return None
        lines: list[list[tuple[ET.Element, list[float]]]] = []
        ordinary_gaps: list[float] = []
        for line in line_nodes:
            entries: list[tuple[ET.Element, list[float]]] = []
            for word in (item for item in line.iter() if local_name(item.tag) == "word"):
                coordinates = _element_bbox(word)
                if coordinates is None or not all(isfinite(value) for value in coordinates) or coordinates[0] > coordinates[2] or coordinates[1] >= coordinates[3]:
                    return None
                entries.append((word, coordinates))
            lines.append(entries)
            ordinary_gaps.extend(right[1][0] - left[1][2] for left, right in zip(entries, entries[1:]) if 0 <= right[1][0] - left[1][2] < 8)
        ordinary_median = median(ordinary_gaps) if ordinary_gaps else None
        threshold = max(8.0, 2.5 * ordinary_median) if ordinary_median is not None else 8.0
        split_rows = []
        for line_index, entries in enumerate(lines):
            if any(coordinates[0] < parent_bbox[0] - 2 or coordinates[2] > parent_bbox[2] + 2 for _, coordinates in entries):
                return None
            possible = []
            for index, gap in enumerate(right[1][0] - left[1][2] for left, right in zip(entries, entries[1:])):
                if gap >= threshold and index >= 2 and len(entries) - index - 1 >= 3:
                    possible.append((index, gap))
            if not possible:
                continue
            index, gap = max(possible, key=lambda item: item[1])
            left = entries[:index + 1]
            right = entries[index + 1:]
            split_rows.append({
                "line_index": line_index,
                "left": [word for word, _ in left],
                "right": [word for word, _ in right],
                "left_bbox": _bbox([coordinates for _, coordinates in left]),
                "right_bbox": _bbox([coordinates for _, coordinates in right]),
                "gap": gap,
            })
        for previous, current in zip(split_rows, split_rows[1:]):
            if current["line_index"] != previous["line_index"] + 1:
                continue
            # XML line keys are not printed-row numbers: require two descending text lines.
            if not all(0 < current[side][1] - previous[side][1] <= 4 * max(previous[side][3] - previous[side][1], current[side][3] - current[side][1]) for side in ("left_bbox", "right_bbox")):
                continue
            if abs(previous["left_bbox"][0] - current["left_bbox"][0]) > 12 or abs(previous["right_bbox"][0] - current["right_bbox"][0]) > 12:
                continue
            gap_start = max(previous["left_bbox"][2], current["left_bbox"][2])
            gap_end = min(previous["right_bbox"][0], current["right_bbox"][0])
            if gap_end - gap_start < threshold:
                continue
            selected = {previous["line_index"], current["line_index"]}
            right_words = previous["right"] + current["right"]
            right_boxes = [_element_bbox(word) for word in right_words]
            right_width = max(item[2] for item in right_boxes) - min(item[0] for item in right_boxes)
            if len(right_words) < 6 or right_width > (parent_bbox[2] - parent_bbox[0]) * 0.45:
                continue
            if any(
                coordinates[2] > gap_start + 2
                for line_index, entries in enumerate(lines) if line_index not in selected
                for _, coordinates in entries
            ):
                continue
            boundaries = []
            for row in (previous, current):
                left_word, right_word = row["left"][-1], row["right"][0]
                boundaries.append({
                    "line_index": row["line_index"],
                    "raw_word_texts": [left_word.text or "", right_word.text or ""],
                    "raw_word_bboxes": [_element_bbox(left_word), _element_bbox(right_word)],
                    "gap_points": round(row["gap"], 6),
                })
            left_lines = [
                next(row["left"] for row in (previous, current) if row["line_index"] == line_index)
                if line_index in selected else [word for word, _ in entries]
                for line_index, entries in enumerate(lines)
            ]
            metadata = {
                "kind": "poppler_wrapped_text_partition",
                "scope": "two_adjacent_lines_inside_one_poppler_xml_block",
                "source_xml_block_index": source_block_index,
                "source_block_bbox": parent_bbox,
                "source_block_line_count": len(lines),
                "source_block_word_count": len(all_words),
                "ordinary_median_gap_points": round(ordinary_median, 6) if ordinary_median is not None else None,
                "threshold_points": round(threshold, 6),
                "common_gap_points": round(gap_end - gap_start, 6),
                "right_width_fraction_of_parent": round(right_width / (parent_bbox[2] - parent_bbox[0]), 6),
                "partition_line_indices": [previous["line_index"], current["line_index"]],
                "line_boundaries": boundaries,
                "pre_partition_text": "block.source_text_before_adjustments",
            }
            return left_lines, [previous["right"], current["right"]], metadata
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    return None


def _rejoin_split_numbering(blocks: list[dict[str, Any]], page_width: float | None) -> list[dict[str, Any]]:
    """Rejoin a heading whose number Poppler emitted as its own block.

    Poppler breaks a block wherever the font changes, so ``1`` and ``Introduction`` arrive as two
    blocks on one baseline. Left unjoined, the heading is classified as body text and every block
    after it inherits the wrong section path.
    """
    if page_width is None or len(blocks) < 2:
        return blocks
    numbering = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)+)$")
    heading_start = re.compile(r"^[A-Z][A-Za-z]")
    output: list[dict[str, Any]] = []
    index = 0
    while index < len(blocks):
        current = blocks[index]
        if index + 1 < len(blocks):
            following = blocks[index + 1]
            left = " ".join(str(current.get("text", "")).split())
            right = " ".join(str(following.get("text", "")).split())
            same_line = _vertical_overlap_ratio(current, following) >= 0.6
            gap = float(following["bbox"][0]) - float(current["bbox"][2])
            narrow = float(current["bbox"][2]) - float(current["bbox"][0]) <= page_width * 0.08
            if (
                same_line
                and numbering.match(left)
                and heading_start.match(right)
                and narrow
                and -1.0 <= gap <= page_width * 0.03
                and len(right) <= 120
                and len(right.split()) <= 12
                and "://" not in right
                and not _has_inline_sentence(right)
                and not right.endswith((".", "。", "!", "?", ":", "：", ";", "；"))
            ):
                merged = dict(current)
                merged["text"] = f"{left} {right}"
                merged["raw_text"] = merged["text"]
                merged["bbox"] = _bbox_union(current["bbox"], following["bbox"])
                merged["source_order"] = min(current["source_order"], following["source_order"])
                merged["rejoined_from"] = [current["raw_id"], following["raw_id"]]
                merged.setdefault("warnings", []).append(
                    "poppler_split_numbering_rejoined: the heading number and its title were separate"
                    " layout blocks on one baseline; verify the heading on the page image"
                )
                output.append(merged)
                index += 2
                continue
        output.append(current)
        index += 1
    for position, block in enumerate(output):
        block["source_order"] = position
    return output


def _poppler_page(page: ET.Element) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    try:
        raw_width = page.attrib.get("width", page.attrib.get("Width"))
        page_width = float(raw_width) if raw_width is not None else None
    except (ValueError, TypeError, OverflowError):
        page_width = None
    page_blocks = [item for item in page.iter() if local_name(item.tag) == "block"]
    for source_block_index, element in enumerate(page_blocks):
        line_nodes = [item for item in element.iter() if local_name(item.tag) == "line"]
        all_lines = [[item for item in line.iter() if local_name(item.tag) == "word"] for line in line_nodes]
        if not all_lines:
            all_lines = [[item for item in element.iter() if local_name(item.tag) == "word"]]
        parent_before_lines = [
            " ".join(word.text or "" for word in words).strip(" \t\r\n")
            for words in all_lines
            if any((word.text or "").strip() or has_illegal_xml_control(word.text or "") for word in words)
        ]
        parent_before = "\n".join(parent_before_lines)
        partition = _wrapped_text_partition(element, page_width, source_block_index)
        if partition:
            left_lines, right_lines, partition_metadata = partition
            fragments = [
                (left_lines, {**partition_metadata, "fragment": "left_original_words", "source_line_indices": list(range(len(all_lines)))}),
                (right_lines, {**partition_metadata, "fragment": "right_original_words", "source_line_indices": partition_metadata["partition_line_indices"]}),
            ]
        else:
            fragments = [(all_lines, None)]
        for fragment_lines, adjustment in fragments:
            lines: list[str] = []
            lines_before_adjustments: list[str] = []
            adjustments: list[dict[str, Any]] = [adjustment] if adjustment else []
            word_boxes: list[list[float]] = []
            fragment_words = [word for words in fragment_lines for word in words]
            for word in fragment_words:
                try:
                    coordinates = _element_bbox(word)
                    if coordinates is not None:
                        word_boxes.append(coordinates)
                except (KeyError, ValueError):
                    continue
            for elements in fragment_lines:
                words = [item.text or "" for item in elements]
                if any(word.strip() or has_illegal_xml_control(word) for word in words):
                    lines_before_adjustments.append(" ".join(words).strip(" \t\r\n"))
                    if line_nodes:
                        line_text, line_adjustments = _join_line_words(elements)
                        lines.append(line_text.strip(" \t\r\n"))
                        adjustments.extend(line_adjustments)
                    else:
                        lines.append(" ".join(words).strip(" \t\r\n"))
            text = "\n".join(lines)
            if not text.strip() and not has_illegal_xml_control(text):
                continue
            element_coordinates = _element_bbox(element)
            bbox = _bbox(word_boxes) if adjustment and word_boxes else element_coordinates if element_coordinates is not None else _bbox(word_boxes) if word_boxes else [0.0, 0.0, 0.0, 0.0]
            block = {
                "raw_id": f"r{len(output):04d}",
                "bbox": bbox,
                "raw_text": text,
                "text": text,
                "source_order": len(output),
                "original_kind": "body",
                "raw_kind": "body",
                "font_size_max": None,
                "font_bold": None,
            }
            has_numeric_adjustments = any(entry["kind"] == "numeric_subscript_geometry" for entry in adjustments)
            has_alphabetic_adjustments = any(entry["kind"] == "alphabetic_subscript_geometry" for entry in adjustments)
            has_punctuation_adjustments = any(entry["kind"] == "post_subscript_punctuation_geometry" for entry in adjustments)
            if adjustment or has_punctuation_adjustments:
                block["requires_visual_review"] = True
            if adjustment or has_numeric_adjustments or has_alphabetic_adjustments or has_punctuation_adjustments:
                block["source_text_before_adjustments"] = parent_before if adjustment else "\n".join(lines_before_adjustments)
                block["source_text_adjustments"] = adjustments
            warnings = ["Poppler wrapped-text partition from two-line geometry; parent text and word coordinates retained; review the page"] if adjustment else []
            if has_numeric_adjustments:
                warnings.append("Numeric subscript words joined from touching same-line geometry; original words and coordinates retained")
            if has_alphabetic_adjustments:
                warnings.append("Alphabetic subscript words joined from touching same-line geometry; original words and coordinates retained")
            if has_punctuation_adjustments:
                warnings.append("Post-subscript punctuation joined from a small measured gap; original words and coordinates retained; review typography")
            if warnings:
                block["warnings"] = warnings
            output.append(block)
    return _rejoin_split_numbering(output, page_width)


def _render_page(
    backend: str,
    path: Path,
    number: int,
    capabilities: dict[str, Any],
    work: Path,
) -> tuple[Path | None, str | None]:
    destination = work / f"page-{number:04d}.png"
    if backend == "pymupdf":
        try:
            import pymupdf

            document = pymupdf.open(path)
            try:
                pixmap = document[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                pixmap.save(destination)
            finally:
                document.close()
            return destination, None
        except Exception as exc:
            return None, str(exc)
    executable = capabilities.get("tools", {}).get("pdftoppm", {}).get("path")
    if not executable:
        return None, "No page renderer available"
    target = work / f"page-{number:04d}"
    code, log = run_to_log([
        executable, "-f", str(number), "-l", str(number), "-singlefile", "-r", "120", "-png", str(path), str(target)
    ], work / f"render-{number:04d}.log")
    return (destination, None) if code == 0 and destination.is_file() else (None, f"pdftoppm failed: {log[-1000:]}")


def _tesseract_page(
    path: Path,
    number: int,
    capabilities: dict[str, Any],
    work: Path,
) -> tuple[str | None, str | None]:
    renderer = capabilities.get("preferred_renderer") or "poppler"
    image, error = _render_page(renderer, path, number, capabilities, work)
    if image is None:
        return None, error
    tools = capabilities.get("tools", {})
    executable = tools.get("tesseract", {}).get("path") or capabilities.get("ocr", {}).get("path")
    if not executable:
        return None, "OCR executable not available"
    output_base = work / f"ocr-{number:04d}"
    language = capabilities.get("ocr", {}).get("lang", "eng")
    code, log = run_to_log([executable, str(image), str(output_base), "-l", language], work / f"ocr-{number:04d}.log")
    text_path = output_base.with_suffix(".txt")
    if code != 0 or not text_path.is_file():
        return None, f"Tesseract failed with code {code}: {log[-1000:]}"
    return text_path.read_text(encoding="utf-8", errors="replace"), None


def _update_early_result(plan: dict[str, Any], document: dict[str, Any], document_path: Path) -> None:
    path = plan.get("early_result")
    if not path:
        return
    result_path = Path(path)
    result = read_json(result_path) if result_path.exists() else {
        "case_id": plan.get("case_id"), "status": "partial", "sources": [], "uncertainty": [],
        "change_records": [], "errors": [],
    }
    prior = [error for error in result.get("errors", []) if error.get("kind") != "pending_processing"]
    result["errors"] = prior + document.get("errors", [])
    result["coverage"] = document["coverage"]
    result["status"] = "partial"
    result["artifacts"] = {**result.get("artifacts", {}), "document": str(document_path.resolve())}
    changes = result.get("change_records", [])
    changes.append({
        "sequence": len(changes) + 1,
        "stage": "extract",
        "description": "Updated page coverage and extraction limitations from actual processing",
    })
    result["change_records"] = changes
    atomic_write_json(result_path, result)


def extract_command(
    plan_path: str | Path,
    probe_path: str | Path,
    capabilities_path: str | Path,
    work: str | Path,
    output: str | Path,
    mode: str = "layout",
) -> int:
    plan = read_json(plan_path)
    probe = read_json(probe_path)
    capabilities = read_json(capabilities_path)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output = Path(output)
    if mode not in {"layout", "naive"}:
        raise ValueError("--order must be layout or naive")
    if len(probe.get("documents", [])) != 1:
        raise ValueError("This artifact supports exactly one PDF; process multiple PDFs into separate evidence artifacts")
    record = probe["documents"][0]
    pdf_path = Path(record["path"])
    backend = record["backend"]
    errors = list(record.get("errors", []))
    pymupdf_document = None
    poppler_root = None
    poppler_recovery = None
    poppler_elements: list[ET.Element] = []
    if backend == "pymupdf" and record["status"] not in {"encrypted", "failed"}:
        try:
            import pymupdf

            pymupdf_document = pymupdf.open(pdf_path)
        except ImportError as exc:
            errors.append(error_record("extractor_unavailable", "extract", f"PyMuPDF is not importable: {exc}"))
        except Exception as exc:
            errors.append(error_record("unreadable_pdf", "extract", str(exc)))
    elif backend == "poppler" and record["status"] not in {"encrypted", "failed"}:
        executable = capabilities["tools"]["pdftotext"]["path"]
        bbox_output = work / "extract-bbox.html"
        code, log = run_to_log([executable, "-bbox-layout", str(pdf_path), str(bbox_output)], work / "pdftotext-extract.log")
        if code == 0:
            try:
                poppler_root, poppler_recovery = parse_poppler_bbox(bbox_output)
                poppler_elements = [element for element in poppler_root.iter() if local_name(element.tag) == "page"]
            except (ET.ParseError, UnicodeError, LookupError, ValueError) as exc:
                errors.append(error_record("unreadable_pdf", "extract", f"Invalid bbox XML: {exc}"))
        else:
            errors.append(error_record("unreadable_pdf", "extract", f"pdftotext code {code}: {log[-2000:]}"))
    input_pages: list[dict[str, Any]] = []
    processed = 0
    failed = 0
    skipped = 0
    for probed in record.get("pages", []):
        number = probed["page_number"]
        action = probed.get("action")
        page: dict[str, Any] = {
            "page_number": number,
            "width": probed.get("width") or 1.0,
            "height": probed.get("height") or 1.0,
            "rotation": probed.get("rotation") or 0,
            "status": "pending",
            "probe_status": probed.get("status"),
            "raw_blocks": [],
            "warnings": [],
        }
        try:
            if action in {"extract", "extract_and_review"}:
                if backend == "pymupdf" and pymupdf_document is not None:
                    table_diagnostics: list[str] = []
                    page["raw_blocks"] = _pymupdf_page(pymupdf_document[number - 1], table_diagnostics)
                    page["warnings"].extend(table_diagnostics)
                elif backend == "poppler" and number <= len(poppler_elements):
                    page["raw_blocks"] = _poppler_page(poppler_elements[number - 1])
                    control_warning = page_control_warning(poppler_recovery, number)
                    if control_warning:
                        page["warnings"].append(control_warning)
                else:
                    raise ValueError("Selected extractor did not yield this page")
                page["extraction_method"] = backend
                page["status"] = "complete"
                processed += 1
                if action == "extract_and_review":
                    page["warnings"].append("Sparse text with images requires visual review")
            elif action == "ocr":
                text, error = _tesseract_page(pdf_path, number, capabilities, work)
                if error:
                    raise ValueError(error)
                page["raw_blocks"] = [{
                    "raw_id": "r0000", "bbox": [0.0, 0.0, page["width"], page["height"]],
                    "raw_text": text or "", "text": text or "", "source_order": 0,
                    "original_kind": "body", "raw_kind": "body", "font_size_max": None, "font_bold": None,
                }]
                page["extraction_method"] = "tesseract"
                page["warnings"].append("OCR text and its confidence require source-image verification")
                page["status"] = "complete"
                processed += 1
            else:
                kind = "encrypted_pdf" if probed.get("status") == "encrypted" else "ocr_unavailable" if action == "review_ocr_required" else "page_not_processed"
                page["status"] = "skipped" if kind == "encrypted_pdf" else "failed"
                page["warnings"].append(f"{kind}: {action}")
                errors.append(error_record(kind, "extract", f"Page was not extracted: {action}", page=number))
                if page["status"] == "failed":
                    failed += 1
                else:
                    skipped += 1
        except Exception as exc:
            page["status"] = "failed"
            page["warnings"].append(str(exc))
            errors.append(error_record("page_extract_failed", "extract", str(exc), page=number))
            failed += 1
        input_pages.append(page)
    pages, layout = order_pages(input_pages, mode=mode)
    rendered: list[dict[str, Any]] = []
    renderer_work = work / "rendered"
    renderer_work.mkdir(parents=True, exist_ok=True)
    for page in pages:
        needs_review = bool(page.get("warnings")) and len(rendered) < 6
        if needs_review and pdf_path.is_file():
            image, error = _render_page(backend, pdf_path, page["page_number"], capabilities, renderer_work)
            if image:
                rendered.append({"page_number": page["page_number"], "path": str(image), "reason": list(page["warnings"])})
            elif error:
                page["warnings"].append(f"render_failed: {error}")
    document = {
        "schema_version": "1.0",
        "case_id": plan.get("case_id"),
        "source": record,
        "page_count": record.get("page_count", len(pages)),
        "pages": pages,
        "layout_analysis": layout,
        "rendered_pages": rendered,
        "coverage": {
            "declared_pages": record.get("page_count", len(pages)),
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "unaccounted": record.get("page_count", len(pages)) - processed - failed - skipped,
        },
        "errors": errors,
        "status": "partial" if errors or failed or skipped or record.get("page_count", len(pages)) != processed else "complete",
    }
    if poppler_recovery:
        document["extraction_recovery"] = poppler_recovery
    atomic_write_json(output, document)
    _update_early_result(plan, document, output)
    print(
        f"document={output}; pages={len(pages)}; processed={processed}; failed={failed}; "
        f"skipped={skipped}; rendered={len(rendered)}"
    )
    return 0 if processed else 6
