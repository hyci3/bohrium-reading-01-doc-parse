"""Strict Poppler bbox XML parsing with reversible XML 1.0 control-character repair."""
from __future__ import annotations

from bisect import bisect_right
import codecs
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

_INVALID_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_PAGE_TAG = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?page(?=[\s>])")
_ENCODING = re.compile(br"encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']", re.I)
_MARKER = "__PDF_READER_XML_RECOVERY_"


def has_illegal_xml_control(value: str | None) -> bool:
    return bool(value and _INVALID_C0.search(value))


def _decoded(raw: bytes) -> str:
    encoding = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8"
    declaration = _ENCODING.search(raw[:2048])
    if declaration and encoding == "utf-8":
        encoding = declaration.group(1).decode("ascii")
    return raw.decode(encoding)


def parse_poppler_bbox(path: str | Path) -> tuple[ET.Element, dict[str, Any] | None]:
    """Parse without altering the original XML; restore original control code points.

    This is deliberately not a general malformed-XML or invalid-UTF repair. Other
    errors still fail. A marker collision is rejected rather than corrupting text.
    """
    source_path = Path(path)
    raw = source_path.read_bytes()
    text = _decoded(raw)
    if not _INVALID_C0.search(text):
        return ET.fromstring(text), None
    if _MARKER in text:
        raise ValueError("Poppler XML contains a reserved recovery marker; refusing ambiguous text repair")
    controls: dict[str, str] = {}
    by_page: dict[int, list[int]] = defaultdict(list)
    first_occurrences: dict[int, list[dict[str, Any]]] = defaultdict(list)
    line_starts = [0] + [match.end() for match in re.finditer("\n", text)]
    page_starts = [match.start() for match in _PAGE_TAG.finditer(text)]

    def replace(match: re.Match[str]) -> str:
        character = match.group(0)
        codepoint = ord(character)
        token = f"{_MARKER}{len(controls)}__"
        controls[token] = character
        position = match.start()
        page_number = bisect_right(page_starts, position)
        by_page[page_number].append(codepoint)
        if len(first_occurrences[page_number]) < 6:
            line_index = bisect_right(line_starts, position) - 1
            first_occurrences[page_number].append({
                "codepoint": f"U+{codepoint:04X}",
                "original_xml_line": line_index + 1,
                "original_xml_column": position - line_starts[line_index] + 1,
            })
        return token

    root = ET.fromstring(_INVALID_C0.sub(replace, text))

    def restore(value: str | None) -> str | None:
        if value is None or _MARKER not in value:
            return value
        for token, character in controls.items():
            value = value.replace(token, character)
        return value

    for element in root.iter():
        element.text = restore(element.text)
        element.tail = restore(element.tail)
        element.attrib = {key: restore(value) for key, value in element.attrib.items()}
    report = {
        "kind": "poppler_xml_control_preserved",
        "source_xml_path": str(source_path.resolve()),
        "source_xml_sha256": sha256(raw).hexdigest(),
        "control_character_count": len(controls),
        "codepoints": sorted({f"U+{ord(value):04X}" for value in controls.values()}),
        "rendition_semantics": "Original Poppler code points are restored in JSON source text; printed glyph meaning is unverified, not OCR-recovered",
        "repair_scope": "Only XML 1.0-disallowed C0 controls; other XML/UTF errors remain fatal",
        "pages": [],
    }
    for page_number in sorted(by_page):
        values = by_page[page_number]
        report["pages"].append({
            "page_number": page_number if page_number else None,
            "scope": "poppler_xml_page" if page_number else "document_outside_page_nodes",
            "count": len(values),
            "codepoints": sorted({f"U+{value:04X}" for value in values}),
            "first_occurrences": first_occurrences[page_number],
        })
    return root, report


def page_control_warning(report: dict[str, Any] | None, page_number: int) -> str | None:
    if not report:
        return None
    page = next((item for item in report["pages"] if item.get("scope") == "poppler_xml_page" and item["page_number"] == page_number), None)
    if not page:
        return None
    return f"poppler_xml_controls: {page['count']} control code point(s) retained ({', '.join(page['codepoints'])}); glyph semantics and the associated page image require review"


def controls_as_dict(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Short structured warnings for probe outputs; never interprets the glyph."""
    if not report:
        return []
    return [{
        "kind": report["kind"],
        "message": "XML-disallowed controls preserved as original code points; rendered glyph meaning requires source-page verification",
        "page_number": page["page_number"],
        "count": page["count"],
        "codepoints": page["codepoints"],
        "first_occurrences": page["first_occurrences"],
        "source_xml_path": report["source_xml_path"],
        "source_xml_sha256": report["source_xml_sha256"],
    } for page in report["pages"]]
