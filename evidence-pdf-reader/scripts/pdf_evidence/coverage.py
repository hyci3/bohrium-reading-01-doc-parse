"""Deterministic structural coverage report for a parsed document.

The report answers one question for the agent: which of the structures a
scientific reading task is expected to cover are actually present in the
current document.json, which ones are only partially recovered, and which
ones need a page-image check before they may be quoted.

It never invents content.  Every item is decided from block kinds, block text,
block geometry and the reader's own section paths, and every item carries the
pages and block ids that support the verdict so the result stays auditable.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Iterable

from .case_io import atomic_write_json, iter_document_blocks, read_json

ABSTRACT_RE = re.compile(r"^(?:abstract|摘\s*要)\b", re.IGNORECASE)
REFERENCES_RE = re.compile(r"^(?:references|bibliography|参考文献)\b", re.IGNORECASE)
EQUATION_NUMBER_RE = re.compile(r"(?:\(|（)\s*[0-9]+(?:\.[0-9]+)*\s*(?:\)|）)\s*$")
INSTITUTION_RE = re.compile(
    r"(?i)\b(?:universit(?:y|ies)|institut(?:e|ion|ional)|college|laborator(?:y|ies)|department"
    r"|school\s+of|academy|research|inc\.|ltd)\b|大学|学院|研究所|实验室|研究院"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
AFFILIATION_MARK_RE = re.compile(r"^(?:\d|[*†‡§¶)\s])")
FOOTNOTE_MARK_RE = re.compile(r"^(?:\d{1,2}\s|[†‡§¶*]\s|\[\d{1,2}\])")

ITEM_ORDER = (
    "title",
    "authors_and_affiliations",
    "abstract",
    "section_hierarchy",
    "figures_and_captions",
    "tables_and_captions",
    "formula_numbers",
    "references",
    "headers_and_footers",
    "footnotes",
)

STATUSES = ("present", "partial", "uncertain", "absent")

GUIDANCE = {
    "title": "Record the printed title from page 1; if page 1 has no heading block, check the page image.",
    "authors_and_affiliations": "Copy author names and affiliations verbatim from page 1 and keep the superscript mapping.",
    "abstract": "Read the abstract block in full; do not summarize it into an invented abstract.",
    "section_hierarchy": "Walk the heading blocks by section_path; report gaps instead of merging levels.",
    "figures_and_captions": "Pair each figure with its caption; unpaired figures are page-image review targets.",
    "tables_and_captions": "Use table rows, not flattened text; merged cells and multi-row headers need a page-image check.",
    "formula_numbers": "Quote the equation number together with the formula; fragments are not equations.",
    "references": "Report the reference range actually extracted; do not synthesize missing entries.",
    "headers_and_footers": "Keep running heads out of body text; report them as layout evidence, never as content.",
    "footnotes": "Read footnote text at the page bottom; mark unresolved marks instead of guessing.",
}


def _short(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _unique(values: Iterable[Any], limit: int = 12) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def _item(
    status: str,
    count: int,
    pages: Iterable[Any],
    block_ids: Iterable[Any],
    note: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "count": count,
        "pages": _unique(pages),
        "block_ids": _unique(block_ids),
        "note": note,
    }


def _blocks(document: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    entries = [(page_number, block) for page_number, block in iter_document_blocks(document)]
    entries.sort(key=lambda entry: (entry[0], entry[1].get("page_reading_order") or 0, entry[1].get("source_order") or 0))
    return entries


def _median_font(blocks: list[dict[str, Any]]) -> float:
    sizes = [float(block.get("font_size_max") or 0) for block in blocks if block.get("font_size_max")]
    return statistics.median(sizes) if sizes else 0.0


def _title_candidate(entries: list[tuple[int, dict[str, Any]]]) -> tuple[int, dict[str, Any]] | None:
    page_one = [(page, block) for page, block in entries if page == 1]
    if not page_one:
        return None
    reference = _median_font([block for _, block in page_one])
    for page, block in page_one:
        kind = block.get("kind")
        if kind in {"footer", "header", "figure", "figure_caption", "table", "table_caption", "formula", "reference"}:
            continue
        text = _short(block.get("text"))
        if not text or len(text) < 4:
            continue
        size = float(block.get("font_size_max") or 0)
        if kind == "heading" or (block.get("font_bold") and reference and size >= reference * 1.15):
            return page, block
    return None


def _abstract_candidate(entries: list[tuple[int, dict[str, Any]]]) -> tuple[int, dict[str, Any]] | None:
    for page, block in entries:
        if block.get("kind") in {"reference", "footer", "header"}:
            continue
        if ABSTRACT_RE.match(_short(block.get("text"))):
            return page, block
        if ABSTRACT_RE.match(_short(block.get("raw_text"))):
            return page, block
    return None


def _item_title(entries) -> dict[str, Any]:
    found = _title_candidate(entries)
    if found:
        page, block = found
        return _item("present", 1, [page], [block.get("block_id")], f"title candidate: {_short(block.get('text'))}")
    return _item("absent", 0, [], [], "no bold or heading block above body size on page 1")


def _item_authors(entries) -> dict[str, Any]:
    page_one = [
        (page, block) for page, block in entries
        if page == 1
        and block.get("kind") in {"body", "heading"}
        and 2 <= len(_short(block.get("text"))) <= 400
    ]
    marked = [
        (page, block) for page, block in page_one
        if EMAIL_RE.search(str(block.get("text") or "")) or INSTITUTION_RE.search(str(block.get("text") or ""))
    ]
    if marked:
        return _item(
            "present",
            len(marked),
            [page for page, _ in marked],
            [block.get("block_id") for _, block in marked],
            f"affiliation markers on page 1: {_short(marked[0][1].get('text'), 100)}",
        )
    title = _title_candidate(entries)
    abstract = _abstract_candidate(entries)
    if title is None:
        window = page_one[:6]
    else:
        order_of = {id(block): index for index, (_, block) in enumerate(entries)}
        start = order_of[id(title[1])] + 1
        stop = order_of[id(abstract[1])] if abstract is not None else start + 6
        window = entries[start:stop]
    window = [entry for entry in window if entry[1].get("kind") in {"body", "heading"}]
    if not window:
        return _item("absent", 0, [], [], "page 1 has no author or affiliation block")
    return _item(
        "uncertain",
        len(window),
        [page for page, _ in window],
        [block.get("block_id") for _, block in window],
        f"{len(window)} candidate blocks but no affiliation marker; verify names from the page image",
    )


def _item_abstract(entries) -> dict[str, Any]:
    found = _abstract_candidate(entries)
    if found:
        page, block = found
        return _item("present", 1, [page], [block.get("block_id")], f"abstract starts: {_short(block.get('text'), 100)}")
    for page, block in entries:
        if any("abstract" in str(part).lower() for part in block.get("section_path") or []):
            return _item("partial", 1, [page], [block.get("block_id")], "section path mentions Abstract but no abstract heading block")
    return _item("absent", 0, [], [], "no abstract heading or abstract section path")


def _item_sections(entries) -> dict[str, Any]:
    headings = [(page, block) for page, block in entries if block.get("kind") == "heading"]
    if not headings:
        return _item("absent", 0, [], [], "no heading block was classified")
    depths = []
    for _, block in headings:
        depth = len(block.get("section_path") or []) or 1
        depths.append(depth)
    deepest = max(depths)
    pages = [page for page, _ in headings]
    ids = [block.get("block_id") for _, block in headings]
    if len(headings) >= 3 and deepest >= 2:
        return _item("present", len(headings), pages, ids, f"{len(headings)} headings, deepest level {deepest}")
    return _item("partial", len(headings), pages, ids, f"only {len(headings)} headings, deepest level {deepest}")


def _item_figures(entries) -> dict[str, Any]:
    figures = [(page, block) for page, block in entries if block.get("kind") == "figure"]
    captions = [(page, block) for page, block in entries if block.get("kind") == "figure_caption"]
    total = len(figures) + len(captions)
    pages = [page for page, _ in figures] + [page for page, _ in captions]
    ids = [block.get("block_id") for _, block in figures] + [block.get("block_id") for _, block in captions]
    if figures and captions:
        return _item("present", total, pages, ids, f"{len(figures)} figure blocks, {len(captions)} captions")
    if total:
        return _item("partial", total, pages, ids, f"{len(figures)} figures, {len(captions)} captions; pairing is incomplete")
    return _item("absent", 0, [], [], "no figure or figure caption block")


def _item_tables(entries) -> dict[str, Any]:
    tables = [(page, block) for page, block in entries if block.get("kind") == "table"]
    captions = [(page, block) for page, block in entries if block.get("kind") == "table_caption"]
    total = len(tables) + len(captions)
    pages = [page for page, _ in tables] + [page for page, _ in captions]
    ids = [block.get("block_id") for _, block in tables] + [block.get("block_id") for _, block in captions]
    rows = sum(int(block.get("table_row_count") or 0) for _, block in tables)
    if tables and captions:
        status = "present"
        note = f"{len(tables)} table blocks, {len(captions)} captions, {rows} extracted rows; merged cells still need a page-image check"
    elif total:
        status = "partial"
        note = f"{len(tables)} tables, {len(captions)} captions; pairing or cell structure is incomplete"
    else:
        return _item("absent", 0, [], [], "no table or table caption block")
    return _item(status, total, pages, ids, note)


def _item_formulas(entries) -> dict[str, Any]:
    formulas = [(page, block) for page, block in entries if block.get("kind") == "formula"]
    numbered = []
    for page, block in entries:
        text = _short(block.get("raw_text") or block.get("text"), 400)
        if EQUATION_NUMBER_RE.search(text):
            numbered.append((page, block))
    pages = [page for page, _ in numbered] + [page for page, _ in formulas]
    ids = [block.get("block_id") for _, block in numbered] + [block.get("block_id") for _, block in formulas]
    if numbered:
        return _item("present", len(numbered), pages, ids, f"{len(numbered)} equation-numbered blocks, {len(formulas)} formula blocks")
    if formulas:
        return _item("partial", len(formulas), pages, ids, f"{len(formulas)} formula blocks without an equation number")
    return _item("absent", 0, [], [], "no formula block or equation number found")


def _item_references(entries) -> dict[str, Any]:
    refs = [(page, block) for page, block in entries if block.get("kind") == "reference"]
    headings = [
        (page, block) for page, block in entries
        if block.get("kind") == "heading" and REFERENCES_RE.match(_short(block.get("text")))
    ]
    total = len(refs)
    pages = [page for page, _ in refs] or [page for page, _ in headings]
    ids = [block.get("block_id") for _, block in refs] or [block.get("block_id") for _, block in headings]
    if total >= 5:
        return _item("present", total, pages, ids, f"{total} reference blocks")
    if total:
        return _item("partial", total, pages, ids, f"only {total} reference blocks")
    if headings:
        return _item("partial", 0, pages, ids, "references heading found but no reference blocks were classified")
    return _item("absent", 0, [], [], "no reference block or references heading")


def _item_margins(entries) -> dict[str, Any]:
    headers = [(page, block) for page, block in entries if block.get("kind") == "header"]
    footers = [(page, block) for page, block in entries if block.get("kind") == "footer"]
    total = len(headers) + len(footers)
    pages = [page for page, _ in headers] + [page for page, _ in footers]
    ids = [block.get("block_id") for _, block in headers] + [block.get("block_id") for _, block in footers]
    if total >= 2:
        return _item("present", total, pages, ids, f"{len(headers)} running heads, {len(footers)} footers kept out of body text")
    if total == 1:
        return _item("partial", 1, pages, ids, "only one margin block was classified")
    return _item("uncertain", 0, [], [], "no margin block was classified; verify that running heads were not merged into body text")


def _item_footnotes(entries, document: dict[str, Any]) -> dict[str, Any]:
    pages_meta = {page.get("page_number"): page for page in document.get("pages", [])}
    found = []
    for page, block in entries:
        if block.get("kind") not in {"body", "reference"}:
            continue
        bbox = block.get("bbox") or []
        height = (pages_meta.get(page) or {}).get("height")
        text = _short(block.get("text"), 400)
        if not bbox or not height or len(bbox) != 4 or not text:
            continue
        if float(bbox[1]) >= float(height) * 0.82 and len(text) <= 300:
            if FOOTNOTE_MARK_RE.match(text) or "footnote" in " ".join(block.get("warnings") or []).lower():
                found.append((page, block))
    pages = [page for page, _ in found]
    ids = [block.get("block_id") for _, block in found]
    if found:
        return _item("present", len(found), pages, ids, f"{len(found)} bottom-of-page note blocks")
    return _item("uncertain", 0, [], [], "no footnote candidate at the bottom margin; verify against the page image")


def build_coverage_report(document: dict[str, Any]) -> dict[str, Any]:
    """Build the ten-item structural coverage report from document.json."""
    entries = _blocks(document)
    items = {
        "title": _item_title(entries),
        "authors_and_affiliations": _item_authors(entries),
        "abstract": _item_abstract(entries),
        "section_hierarchy": _item_sections(entries),
        "figures_and_captions": _item_figures(entries),
        "tables_and_captions": _item_tables(entries),
        "formula_numbers": _item_formulas(entries),
        "references": _item_references(entries),
        "headers_and_footers": _item_margins(entries),
        "footnotes": _item_footnotes(entries, document),
    }
    totals = {status: sum(1 for item in items.values() if item["status"] == status) for status in STATUSES}
    gaps = [
        {"item": name, "status": items[name]["status"], "action": GUIDANCE[name]}
        for name in ITEM_ORDER
        if items[name]["status"] in {"absent", "uncertain", "partial"}
    ]
    return {
        "schema_version": "1.0",
        "method": "deterministic_heuristics_from_document_json",
        "note": (
            "Presence is decided from block kinds, geometry and section paths, not from the agent's answer. "
            "A present item still needs its text to be read; an absent or uncertain item needs a page-image check "
            "before it may be reported as missing."
        ),
        "page_count": document.get("page_count"),
        "totals": totals,
        "items": items,
        "gaps": gaps,
    }


def summarize_coverage(report: dict[str, Any]) -> str:
    totals = report.get("totals") or {}
    return "coverage: present={present}, partial={partial}, uncertain={uncertain}, absent={absent}".format(
        present=totals.get("present", 0),
        partial=totals.get("partial", 0),
        uncertain=totals.get("uncertain", 0),
        absent=totals.get("absent", 0),
    )


def coverage_report_command(document_path: str | Any, output: str | Any) -> int:
    """Write the structural coverage report for an existing document.json."""
    document = read_json(document_path)
    if not isinstance(document, dict):
        raise ValueError("document.json must contain an object")
    report = build_coverage_report(document)
    atomic_write_json(output, report)
    print(summarize_coverage(report))
    for gap in report["gaps"]:
        print(f"gap: {gap['item']} ({gap['status']}) -> {gap['action']}")
    print(f"coverage_report={output}")
    return 0
