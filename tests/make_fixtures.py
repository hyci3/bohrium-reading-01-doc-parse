#!/usr/bin/env python3
"""Generate synthetic development fixtures; never used by the installed Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[list[tuple[str, float, float, int]]]) -> None:
    """Create a small deterministic PDF using only built-in Helvetica text."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs: list[str] = []
    next_object = 4
    for draws in pages:
        commands: list[str] = []
        for text, x, y, size in draws:
            commands.append(f"BT /F1 {size} Tf {x:g} {y:g} Td ({_pdf_escape(text)}) Tj ET")
        content = ("\n".join(commands) + "\n").encode("ascii")
        page_refs.append(f"{next_object} 0 R")
        while len(objects) < next_object - 1:
            objects.append(b"")
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {next_object + 1} 0 R >>"
        ).encode("ascii")
        content_object = (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"endstream"
        )
        objects.extend([page_object, content_object])
        next_object += 2

    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>"
    ).encode("ascii")

    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def create_basic_fixtures(root: Path) -> None:
    basic = root / "basic"
    basic.mkdir(parents=True, exist_ok=True)
    page_one = [
        ("TEST JOURNAL", 265, 758, 9),
        ("A FULL WIDTH SECTION TITLE FOR THE TEST DOCUMENT", 70, 710, 14),
        ("L1 Left column opening statement.", 55, 650, 10),
        ("L2 Left column continuing details.", 55, 610, 10),
        ("R1 Right column opening note.", 325, 650, 10),
        ("R2 Right column continuing note.", 325, 610, 10),
        ("1", 305, 30, 9),
    ]
    page_two = [
        ("TEST JOURNAL", 265, 758, 9),
        ("A SECOND FULL WIDTH SECTION FOR EXTRACTION", 83, 710, 14),
        ("S1 The following single column has one result.", 72, 650, 10),
        ("S2 The section ends with an acknowledged limitation.", 72, 610, 10),
        ("2", 305, 30, 9),
    ]
    pdf_path = basic / "two-column.pdf"
    write_pdf(pdf_path, [page_one, page_two])
    single_path = root / "single" / "single-column.pdf"
    write_pdf(
        single_path,
        [[
            ("SINGLE COLUMN TEST PAPER", 160, 710, 15),
            ("1 INTRODUCTION", 72, 660, 12),
            ("A short paragraph records the measured observation.", 72, 620, 10),
            ("A final sentence states the limitation without speculation.", 72, 580, 10),
            ("1", 305, 30, 9),
        ]],
    )
    case = {
        "case_id": "synthetic-basic-v1",
        "pdf": str(pdf_path.resolve()),
        "output_contract": {
            "fields": {
                "answer": {"type": "string", "required": True},
                "keywords": {"type": "array", "required": False},
            },
            "required": ["answer"],
        },
    }
    (basic / "case.json").write_text(json.dumps(case, indent=2), encoding="utf-8")
    (basic / "task.md").write_text(
        "# Synthetic fixture task\n\n"
        "Read the attached synthetic PDF. Report the block sequence on page 1 and "
        "preserve its evidence locations. This is a development fixture, not an official case.\n",
        encoding="utf-8",
    )
    (root / "single" / "case.json").write_text(
        json.dumps({**case, "case_id": "synthetic-single-v1", "pdf": str(single_path.resolve())}, indent=2),
        encoding="utf-8",
    )
    (root / "single" / "task.md").write_text(
        "# Synthetic single-column task\n\nExtract the section and its evidence.\n",
        encoding="utf-8",
    )


def create_advanced_fixtures(root: Path) -> list[str]:
    warnings: list[str] = []
    try:
        import pymupdf
    except Exception as exc:  # Optional only for generating local advanced fixtures.
        warnings.append(f"Advanced fixtures not generated: PyMuPDF unavailable: {exc}")
        return warnings

    advanced = root / "advanced"
    advanced.mkdir(parents=True, exist_ok=True)
    scanned = pymupdf.open()
    page = scanned.new_page(width=612, height=792)
    page.insert_text((72, 100), "SCANNED OCR TEST 123", fontsize=20)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
    image_pdf = pymupdf.open()
    image_page = image_pdf.new_page(width=612, height=792)
    image_page.insert_image(image_page.rect, pixmap=pixmap)
    image_pdf.save(advanced / "scanned.pdf")
    image_pdf.close()
    scanned.close()

    encrypted = pymupdf.open()
    page = encrypted.new_page()
    page.insert_text((72, 100), "ENCRYPTED TEST CONTENT")
    encrypted.save(
        advanced / "encrypted.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="fixture-password",
        owner_pw="fixture-owner-password",
    )
    encrypted.close()
    (advanced / "corrupt.pdf").write_bytes(b"not a valid pdf\n")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    create_basic_fixtures(root)
    warnings = create_advanced_fixtures(root)
    inventory = {
        "synthetic": True,
        "root": str(root),
        "basic_pdf": str((root / "basic" / "two-column.pdf").resolve()),
        "basic_sha256": hashlib.sha256((root / "basic" / "two-column.pdf").read_bytes()).hexdigest(),
        "warnings": warnings,
    }
    (root / "generated-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    for warning in warnings:
        print(warning)
    print(str(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
