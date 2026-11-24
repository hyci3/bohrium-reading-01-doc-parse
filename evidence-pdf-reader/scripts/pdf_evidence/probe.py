from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .case_io import atomic_write_json, error_record


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def run_to_log(command: list[str], log_path: Path, timeout: int = 120) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
        text = log_path.read_text(encoding="utf-8", errors="replace")[:65536]
        return result.returncode, text
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _failure_record(path: Path, backend: str, kind: str, message: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "backend": backend,
        "status": "failed",
        "page_count": 0,
        "pages": [],
        "properties": {},
        "errors": [error_record(kind, "probe-pdf", message, path=str(path))],
    }


def probe_with_pymupdf(path: Path, capabilities: dict[str, Any]) -> dict[str, Any]:
    try:
        import pymupdf
    except Exception as exc:
        return _failure_record(path, "pymupdf", "extractor_unavailable", str(exc))
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        return _failure_record(path, "pymupdf", "unreadable_pdf", f"{type(exc).__name__}: {exc}")
    pages: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        encrypted = bool(document.needs_pass)
        page_count = document.page_count
        if encrypted:
            for index in range(page_count):
                pages.append({
                    "page_number": index + 1,
                    "status": "encrypted",
                    "action": "skip_encrypted",
                    "width": None,
                    "height": None,
                    "rotation": None,
                    "word_count": 0,
                    "image_count": 0,
                })
            errors.append(error_record("encrypted_pdf", "probe-pdf", "PDF requires a password; no bypass was attempted"))
        else:
            for index, page in enumerate(document):
                try:
                    words = page.get_text("words")
                    images = page.get_images(full=True)
                    word_count = len(words)
                    image_count = len(images)
                    if word_count == 0 and image_count:
                        status = "scanned_candidate"
                        action = "ocr" if capabilities.get("ocr", {}).get("available") else "review_ocr_required"
                    elif word_count == 0:
                        status = "blank_or_unrecognized"
                        action = "review"
                    elif word_count < 10 and image_count:
                        status = "sparse_text_with_images"
                        action = "extract_and_review"
                    else:
                        status = "text_layer"
                        action = "extract"
                    pages.append({
                        "page_number": index + 1,
                        "status": status,
                        "action": action,
                        "width": float(page.rect.width),
                        "height": float(page.rect.height),
                        "rotation": int(page.rotation),
                        "word_count": word_count,
                        "image_count": image_count,
                    })
                except Exception as exc:
                    pages.append({
                        "page_number": index + 1,
                        "status": "failed",
                        "action": "skip_failed",
                        "width": None,
                        "height": None,
                        "rotation": None,
                        "word_count": 0,
                        "image_count": 0,
                    })
                    errors.append(error_record("page_probe_failed", "probe-pdf", str(exc), page=index + 1))
        status = "encrypted" if encrypted else "ready" if any(page["action"] in {"extract", "extract_and_review", "ocr"} for page in pages) else "review_required"
        return {
            "path": str(path),
            "backend": "pymupdf",
            "status": status,
            "page_count": page_count,
            "pages": pages,
            "properties": {"needs_pass": encrypted},
            "errors": errors,
        }
    finally:
        document.close()


def probe_with_poppler(path: Path, capabilities: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    executable = capabilities.get("tools", {}).get("pdftotext", {}).get("path")
    if not executable:
        return _failure_record(path, "poppler", "extractor_unavailable", "pdftotext not available")
    work_dir.mkdir(parents=True, exist_ok=True)
    properties: dict[str, Any] = {}
    pdfinfo = capabilities.get("tools", {}).get("pdfinfo", {}).get("path")
    if pdfinfo:
        code, output = run_to_log([pdfinfo, str(path)], work_dir / "pdfinfo.log")
        properties["pdfinfo_returncode"] = code
        if code == 0:
            properties["raw_summary"] = output[:12000]
            if re.search(r"(?im)^Encrypted:\s+yes", output):
                match = re.search(r"(?im)^Pages:\s+(\d+)", output)
                page_count = int(match.group(1)) if match else 0
                return {
                    "path": str(path),
                    "backend": "poppler",
                    "status": "encrypted",
                    "page_count": page_count,
                    "pages": [{
                        "page_number": index + 1,
                        "status": "encrypted",
                        "action": "skip_encrypted",
                        "word_count": 0,
                        "image_count": 0,
        } for index in range(page_count)],
                    "properties": properties,
                    "errors": [error_record("encrypted_pdf", "probe-pdf", "PDF is encrypted; no bypass was attempted")],
                }
    bbox_path = work_dir / "probe-bbox.html"
    code, log = run_to_log([executable, "-bbox-layout", str(path), str(bbox_path)], work_dir / "pdftotext-probe.log")
    if code != 0 or not bbox_path.is_file():
        return _failure_record(path, "poppler", "unreadable_pdf", f"pdftotext failed with code {code}: {log[-2000:]}")
    try:
        root = ET.parse(bbox_path).getroot()
    except ET.ParseError as exc:
        return _failure_record(path, "poppler", "unreadable_pdf", f"Invalid bbox XML: {exc}")
    pages: list[dict[str, Any]] = []
    ocr_available = capabilities.get("ocr", {}).get("available")
    for index, page in enumerate(element for element in root.iter() if local_name(element.tag) == "page"):
        words = [element for element in page.iter() if local_name(element.tag) == "word"]
        count = len(words)
        action = "extract" if count else "ocr" if ocr_available else "review_ocr_required"
        pages.append({
            "page_number": index + 1,
            "status": "text_layer" if count else "no_text_layer",
            "action": action,
            "width": float(page.attrib.get("width", 0)),
            "height": float(page.attrib.get("height", 0)),
            "rotation": 0,
            "word_count": count,
            "image_count": None,
        })
    if not pages:
        return _failure_record(path, "poppler", "unreadable_pdf", "No pages found in Poppler bbox output")
    return {
        "path": str(path),
        "backend": "poppler",
        "status": "ready" if any(page["action"] == "extract" for page in pages) else "review_required",
        "page_count": len(pages),
        "pages": pages,
        "properties": properties,
        "errors": [],
    }


def probe_pdf_command(
    plan_path: str | Path,
    capabilities_path: str | Path,
    output: str | Path,
    backend: str | None = None,
) -> int:
    from .case_io import read_json

    plan = read_json(plan_path)
    capabilities = read_json(capabilities_path)
    output = Path(output)
    records: list[dict[str, Any]] = []
    for pdf in plan.get("pdfs", []):
        path = Path(pdf["path"])
        if not path.is_file():
            records.append(_failure_record(path, backend or "unknown", "material_missing", "PDF does not exist"))
            continue
        selected = backend or capabilities.get("preferred_extractor")
        if selected == "pymupdf":
            records.append(probe_with_pymupdf(path, capabilities))
        elif selected == "poppler":
            records.append(probe_with_poppler(path, capabilities, output.parent / f"probe-{len(records) + 1}"))
        else:
            records.append(_failure_record(path, "none", "extractor_unavailable", "No supported extractor"))
    has_work = any(
        page.get("action") in {"extract", "extract_and_review", "ocr"}
        for record in records
        for page in record.get("pages", [])
    )
    status = "ready" if has_work else "no_extractable_content"
    report = {"schema_version": "1.0", "case_id": plan.get("case_id"), "status": status, "documents": records}
    atomic_write_json(output, report)
    print(f"probe_status={status}; documents={len(records)}; report={output}")
    return 0 if has_work else 5
