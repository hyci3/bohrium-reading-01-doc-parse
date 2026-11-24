from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .case_io import atomic_write_json, error_record, read_json
from .probe import local_name, run_to_log
from .reading_order import order_pages


def _bbox(items: list[dict[str, Any]]) -> list[float]:
    return [
        min(item[0] for item in items),
        min(item[1] for item in items),
        max(item[2] for item in items),
        max(item[3] for item in items),
    ]


def _pymupdf_page(page: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") == 0:
            line_texts: list[str] = []
            font_sizes: list[float] = []
            bold = False
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                line_texts.append("".join(span.get("text", "") for span in spans))
                for span in spans:
                    if span.get("text"):
                        font_sizes.append(float(span.get("size", 0)))
                        font_name = str(span.get("font", "")).lower()
                        bold = bold or "bold" in font_name or bool(int(span.get("flags", 0)) & 16)
            text = "\n".join(value for value in line_texts if value)
            if not text.strip():
                continue
            bbox = [float(value) for value in block.get("bbox", [0, 0, 0, 0])]
            raw_id = f"r{len(blocks):04d}"
            blocks.append({
                "raw_id": raw_id,
                "bbox": bbox,
                "raw_text": text,
                "text": text,
                "source_order": len(blocks),
                "original_kind": "body",
                "raw_kind": "body",
                "font_size_max": max(font_sizes) if font_sizes else None,
                "font_bold": bold,
            })
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
    try:
        finder = page.find_tables()
        for table in getattr(finder, "tables", []):
            rows = table.extract()
            text = "\n".join(" | ".join("" if cell is None else str(cell) for cell in row) for row in rows)
            blocks.append({
                "raw_id": f"rt{len(blocks):04d}",
                "bbox": [float(value) for value in table.bbox],
                "raw_text": text,
                "text": text,
                "source_order": len(blocks),
                "original_kind": "table",
                "raw_kind": "body",
                "rows": rows,
                "table_warning": "Table structure is extractor output and still requires source verification for merged cells",
            })
    except Exception:
        # Table recovery is opportunistic; text and image evidence remain the fallback.
        pass
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


def _poppler_page(page: ET.Element) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for element in (item for item in page.iter() if local_name(item.tag) == "block"):
        lines: list[str] = []
        word_boxes: list[list[float]] = []
        for word in (item for item in element.iter() if local_name(item.tag) == "word"):
            try:
                coordinates = _element_bbox(word)
                if coordinates is not None:
                    word_boxes.append(coordinates)
            except (KeyError, ValueError):
                continue
        for line in (item for item in element.iter() if local_name(item.tag) == "line"):
            words = [(item.text or "") for item in line.iter() if local_name(item.tag) == "word"]
            if any(word.strip() for word in words):
                lines.append(" ".join(words).strip())
        if not lines:
            words = [(item.text or "") for item in element.iter() if local_name(item.tag) == "word"]
            if any(word.strip() for word in words):
                lines = [" ".join(words).strip()]
        text = "\n".join(lines)
        if not text.strip():
            continue
        element_coordinates = _element_bbox(element)
        bbox = element_coordinates if element_coordinates is not None else _bbox(word_boxes) if word_boxes else [0.0, 0.0, 0.0, 0.0]
        output.append({
            "raw_id": f"r{len(output):04d}",
            "bbox": bbox,
            "raw_text": text,
            "text": text,
            "source_order": len(output),
            "original_kind": "body",
            "raw_kind": "body",
            "font_size_max": None,
            "font_bold": None,
        })
    return output


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
    poppler_elements: list[ET.Element] = []
    if backend == "pymupdf" and record["status"] not in {"encrypted", "failed"}:
        import pymupdf

        try:
            pymupdf_document = pymupdf.open(pdf_path)
        except Exception as exc:
            errors.append(error_record("unreadable_pdf", "extract", str(exc)))
    elif backend == "poppler" and record["status"] not in {"encrypted", "failed"}:
        executable = capabilities["tools"]["pdftotext"]["path"]
        bbox_output = work / "extract-bbox.html"
        code, log = run_to_log([executable, "-bbox-layout", str(pdf_path), str(bbox_output)], work / "pdftotext-extract.log")
        if code == 0:
            try:
                poppler_root = ET.parse(bbox_output).getroot()
                poppler_elements = [element for element in poppler_root.iter() if local_name(element.tag) == "page"]
            except ET.ParseError as exc:
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
                    page["raw_blocks"] = _pymupdf_page(pymupdf_document[number - 1])
                elif backend == "poppler" and number <= len(poppler_elements):
                    page["raw_blocks"] = _poppler_page(poppler_elements[number - 1])
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
    atomic_write_json(output, document)
    _update_early_result(plan, document, output)
    print(
        f"document={output}; pages={len(pages)}; processed={processed}; failed={failed}; "
        f"skipped={skipped}; rendered={len(rendered)}"
    )
    return 0 if processed else 6
