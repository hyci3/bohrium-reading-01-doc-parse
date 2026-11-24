from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

from .case_io import atomic_write_json, sha256_file


VERSION_FLAGS = {
    "pdftotext": "-v",
    "pdftoppm": "-v",
    "pdfinfo": "-v",
    "tesseract": "--version",
    "ocrmypdf": "--version",
    "paddleocr": "--version",
}


def tool_version(executable: str, flag: str, work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=work_dir, prefix="tool-version-", suffix=".log", delete=False)
    log_path = Path(handle.name)
    try:
        with handle:
            result = subprocess.run(
                [executable, flag],
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=12,
            )
        text = log_path.read_text(encoding="utf-8", errors="replace")[:16384].strip()
        return {
            "status": "ok" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "summary": text.splitlines()[0] if text else "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        log_path.unlink(missing_ok=True)


def _python_pdf_libraries() -> dict[str, Any]:
    fitz_spec = importlib.util.find_spec("fitz")
    pymupdf_spec = importlib.util.find_spec("pymupdf")
    result: dict[str, Any] = {
        "import_spec_found": bool(fitz_spec or pymupdf_spec),
        "available": False,
        "version": None,
        "error": None,
    }
    try:
        result["version"] = metadata.version("PyMuPDF")
    except metadata.PackageNotFoundError:
        pass
    if fitz_spec or pymupdf_spec:
        try:
            import pymupdf

            result["version"] = result["version"] or getattr(pymupdf, "__version__", None)
            result["available"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _resource_hints(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "found": False}
    if not path.is_file():
        return {"provided": True, "found": False, "error": "Resource entry file does not exist"}
    text = path.read_text(encoding="utf-8")
    hints: dict[str, list[str]] = {}
    for name in ("pdftotext", "pdftoppm", "pdfinfo", "tesseract", "ocrmypdf", "paddleocr"):
        if re.search(rf"(?i)\b{re.escape(name)}\b", text):
            hints[name] = ["name-mentioned; executable path still requires runtime detection"]
    return {
        "provided": True,
        "found": True,
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "tool_hints": hints,
        "note": "Hints are not executable evidence; actual commands and imports are checked independently.",
    }


def detect_capabilities(resources: str | Path | None = None, work_dir: str | Path = ".work") -> dict[str, Any]:
    work = Path(work_dir).resolve()
    libraries = _python_pdf_libraries()
    tools: dict[str, Any] = {}
    for name, flag in VERSION_FLAGS.items():
        executable = shutil.which(name)
        record: dict[str, Any] = {"available": bool(executable), "path": executable}
        if executable:
            record["version_check"] = tool_version(executable, flag, work)
        else:
            record["version_check"] = {"status": "not_run"}
        tools[name] = record

    poppler = bool(tools["pdftotext"]["available"])
    renderer_poppler = bool(tools["pdftoppm"]["available"])
    ocr_records: dict[str, Any] = {"kind": None, "available": False, "lang": "eng"}
    if tools["tesseract"]["available"]:
        ocr_records = {
            "kind": "tesseract",
            "available": True,
            "path": tools["tesseract"]["path"],
            "lang": os.environ.get("TESSERACT_LANG", "eng"),
        }
    elif importlib.util.find_spec("paddleocr") is not None:
        ocr_records = {
            "kind": "paddleocr-module",
            "available": True,
            "note": "Actual model availability and a real recognition run are still required before declaring verified OCR.",
        }

    extractor = "pymupdf" if libraries["available"] else "poppler" if poppler else None
    renderer = "pymupdf" if libraries["available"] else "poppler" if renderer_poppler else None
    return {
        "schema_version": "1.0",
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "implementation": sys.implementation.name,
        },
        "pymupdf": libraries,
        "tools": tools,
        "ocr": ocr_records,
        "preferred_extractor": extractor,
        "preferred_renderer": renderer,
        "resources": _resource_hints(Path(resources).resolve() if resources else None),
        "limitations": [
            "This manifest describes one interpreter and one process environment only.",
            "OCR availability is not equivalent to verified recognition quality.",
        ],
    }


def capabilities_command(resources: str | None, output: str | Path) -> int:
    target = Path(output)
    manifest = detect_capabilities(resources=resources, work_dir=target.parent)
    manifest["status"] = "ready" if manifest["preferred_extractor"] else "extractor_unavailable"
    atomic_write_json(target, manifest)
    print(f"capabilities={target}; extractor={manifest['preferred_extractor'] or 'none'}")
    return 0 if manifest["preferred_extractor"] else 3
