from __future__ import annotations

import json
from hashlib import sha256
import builtins
import time
import uuid
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from pathlib import Path

from tests._support import FIXTURES, ROOT, TEST_WORK
from pdf_evidence.case_io import atomic_write_json, inspect_case_command, read_json
from pdf_evidence.capabilities import capabilities_command
from pdf_evidence.context import select_context_command
from pdf_evidence.extractors import extract_command, _tesseract_page, _poppler_page
from pdf_evidence.poppler_xml import parse_poppler_bbox
from pdf_evidence.probe import probe_pdf_command
from pdf_evidence.reading_order import normalized


class PdfIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TEST_WORK / "m3-integration"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.capabilities = cls.root / "capabilities.json"
        assert capabilities_command(None, cls.capabilities) == 0
        cls.base_case = read_json(FIXTURES / "basic" / "case.json")
        cls.task = FIXTURES / "basic" / "task.md"

    def run_pipeline(self, pdf: Path, label: str, backend: str):
        case = dict(self.base_case)
        case["case_id"] = f"synthetic-{label}"
        case["pdf"] = str(pdf.resolve())
        case_path = self.root / f"{label}-case.json"
        atomic_write_json(case_path, case)
        plan = self.root / f"{label}-plan.json"
        early = self.root / f"{label}-result.json"
        code = inspect_case_command(
            str(case_path), str(self.task), None, str(self.root / label), str(plan), str(early)
        )
        self.assertEqual(code, 0)
        probe = self.root / f"{label}-probe.json"
        probe_code = probe_pdf_command(plan, self.capabilities, probe, backend=backend)
        document = self.root / f"{label}-document.json"
        extract_code = extract_command(plan, probe, self.capabilities, self.root / label, document)
        return probe_code, probe, extract_code, document, early

    def test_pymupdf_extracts_real_two_page_fixture(self) -> None:
        probe_code, _, extract_code, path, early = self.run_pipeline(
            FIXTURES / "basic" / "two-column.pdf", "basic-fit", "pymupdf"
        )
        self.assertEqual((probe_code, extract_code), (0, 0))
        document = read_json(path)
        self.assertEqual(document["page_count"], 2)
        self.assertEqual(document["coverage"]["processed"], 2)
        self.assertEqual(document["coverage"]["unaccounted"], 0)
        texts = {block["text"] for page in document["pages"] for block in page["blocks"]}
        self.assertIn("L1 Left column opening statement.", texts)
        self.assertIn("A SECOND FULL WIDTH SECTION FOR EXTRACTION", texts)
        self.assertTrue(read_json(early)["coverage"]["processed"] == 2)
        for page in document["pages"]:
            for block in page["blocks"]:
                self.assertEqual(len(block["bbox"]), 4)
                self.assertGreater(block["bbox"][2], block["bbox"][0])
                self.assertGreater(block["bbox"][3], block["bbox"][1])
                self.assertRegex(block["block_id"], r"^p\d{4}-b\d{4}$")

    def test_poppler_extracts_real_two_page_fixture(self) -> None:
        probe_code, _, extract_code, path, _ = self.run_pipeline(
            FIXTURES / "basic" / "two-column.pdf", "basic-poppler", "poppler"
        )
        self.assertEqual((probe_code, extract_code), (0, 0))
        document = read_json(path)
        self.assertEqual(document["coverage"]["processed"], 2)
        texts = {block["text"] for page in document["pages"] for block in page["blocks"]}
        self.assertIn("R2 Right column continuing note.", texts)
        self.assertTrue(all(page.get("extraction_method") == "poppler" for page in document["pages"]))
        self.assertTrue(all(
            block["bbox"][2] > block["bbox"][0] and block["bbox"][3] > block["bbox"][1]
            for page in document["pages"] for block in page["blocks"]
        ))

    def test_scanned_pdf_without_ocr_is_honest_failure(self) -> None:
        case = dict(self.base_case)
        pdf = FIXTURES / "advanced" / "scanned.pdf"
        probe_code, probe_path, extract_code, document_path, early = self.run_pipeline(pdf, "scanned", "pymupdf")
        self.assertEqual(probe_code, 5)
        probe = read_json(probe_path)
        self.assertEqual(probe["documents"][0]["pages"][0]["status"], "scanned_candidate")
        self.assertEqual(extract_code, 6)
        document = read_json(document_path)
        self.assertEqual(document["coverage"]["failed"], 1)
        self.assertTrue(any(error["kind"] == "ocr_unavailable" for error in document["errors"]))
        self.assertNotIn("SCANNED OCR TEST 123", json.dumps(document))
        self.assertEqual(read_json(early)["status"], "partial")

    def test_encrypted_pdf_is_not_bypassed(self) -> None:
        probe_code, probe_path, extract_code, document_path, _ = self.run_pipeline(
            FIXTURES / "advanced" / "encrypted.pdf", "encrypted", "pymupdf"
        )
        self.assertEqual(probe_code, 5)
        self.assertEqual(read_json(probe_path)["documents"][0]["status"], "encrypted")
        self.assertEqual(extract_code, 6)
        document = read_json(document_path)
        self.assertEqual(document["coverage"]["skipped"], 1)
        self.assertNotIn("ENCRYPTED TEST CONTENT", json.dumps(document))

    def test_corrupt_pdf_returns_distinct_error(self) -> None:
        case = dict(self.base_case)
        pdf = FIXTURES / "advanced" / "corrupt.pdf"
        probe_code, probe_path, _, _, _ = self.run_pipeline(pdf, "corrupt", "pymupdf")
        self.assertEqual(probe_code, 5)
        record = read_json(probe_path)["documents"][0]
        self.assertEqual(record["status"], "failed")
        self.assertTrue(any(error["kind"] == "unreadable_pdf" for error in record["errors"]))

    def test_real_research_pdf_pymupdf_end_to_end(self) -> None:
        real = ROOT / ".work" / "fixtures" / "real" / "attention.pdf"
        source = ROOT / ".work" / "fixtures" / "real" / "source.json"
        self.assertTrue(real.is_file(), "Required real research fixture is missing")
        provenance = read_json(source)
        self.assertNotEqual(provenance.get("sha256"), "")
        probe_code, _, extract_code, path, _ = self.run_pipeline(real, "real-attention", "pymupdf")
        self.assertEqual((probe_code, extract_code), (0, 0))
        document = read_json(path)
        self.assertEqual(document["page_count"], 15)
        self.assertEqual(document["coverage"]["processed"], 15)
        self.assertEqual(document["coverage"]["unaccounted"], 0)
        text = normalized(" ".join(block["text"] for page in document["pages"] for block in page["blocks"]))
        self.assertIn("Attention Is All You Need", text)
        self.assertIn("Ashish Vaswani", text)
        self.assertIn("Transformer", text)

    def test_three_repeated_runs_have_identical_normalized_structure(self) -> None:
        record = {"scope": "Same generated PDF and backend repeated three times"}
        for backend in ("pymupdf", "poppler"):
            normalized = []
            durations = []
            for number in range(3):
                start = time.perf_counter()
                code, _, _, path, _ = self.run_pipeline(FIXTURES / "basic" / "two-column.pdf", f"repeat-{backend}-{number}", backend)
                durations.append(time.perf_counter() - start)
                self.assertEqual(code, 0)
                document = read_json(path)
                document.pop("case_id", None)
                ids = [block["block_id"] for page in document["pages"] for block in page["blocks"]]
                self.assertEqual(len(ids), len(set(ids)))
                normalized.append(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            self.assertEqual(normalized[0], normalized[1])
            self.assertEqual(normalized[1], normalized[2])
            record[backend] = {"runs": 3, "normalized_identical": True, "durations_seconds": durations}
        atomic_write_json(self.root / "repeatability-report.json", record)

    def test_table_recovery_failure_preserves_text_and_surfaces_diagnostics(self) -> None:
        with patch("pymupdf.Page.find_tables", side_effect=RuntimeError("synthetic table detector unavailable")):
            probe_code, _, extract_code, document_path, _ = self.run_pipeline(
                FIXTURES / "basic" / "two-column.pdf", "table-recovery-failure", "pymupdf"
            )
        self.assertEqual((probe_code, extract_code), (0, 0))
        document = read_json(document_path)
        self.assertEqual(document["coverage"]["processed"], 2)
        self.assertEqual(document["errors"], [])
        self.assertEqual(len(document["rendered_pages"]), 2)
        for page in document["pages"]:
            self.assertTrue(any("table_recovery_unavailable: RuntimeError: synthetic table detector unavailable" in warning for warning in page["warnings"]))
        text = " ".join(block["text"] for page in document["pages"] for block in page["blocks"])
        self.assertIn("L1 Left column opening statement.", text)
        self.assertIn("R2 Right column continuing note.", text)

    def test_tesseract_adapter_uses_detected_preferred_renderer(self) -> None:
        for preferred, expected in (("pymupdf", "pymupdf"), (None, "poppler")):
            capabilities = {"preferred_renderer": preferred, "ocr": {"available": True}, "tools": {}}
            with patch("pdf_evidence.extractors._render_page", return_value=(self.root / "fake-render.png", None)) as renderer:
                text, error = _tesseract_page(FIXTURES / "advanced" / "scanned.pdf", 1, capabilities, self.root)
            self.assertIsNone(text)
            self.assertIn("OCR executable not available", error)
            self.assertEqual(renderer.call_args.args[0], expected)

    def test_renderer_failure_is_explicit_and_does_not_invent_ocr(self) -> None:
        with patch("pdf_evidence.extractors._render_page", return_value=(None, "synthetic unavailable renderer")):
            _, _, code, document_path, _ = self.run_pipeline(FIXTURES / "advanced" / "scanned.pdf", "render-failure", "pymupdf")
        self.assertEqual(code, 6)
        document = read_json(document_path)
        self.assertTrue(document["rendered_pages"] == [])
        self.assertTrue(any("render_failed" in warning for warning in document["pages"][0]["warnings"]))
        self.assertNotIn("SCANNED OCR TEST 123", json.dumps(document))


class PopplerXmlAdapterTest(unittest.TestCase):
    """Synthetic XML adapter checks, not claimed text or renderings of a real PDF."""

    def setUp(self) -> None:
        self.root = TEST_WORK / f"poppler-xml-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def synthetic_xml() -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<doc><page width="600" height="800">\n'
            ' <block xMin="50" yMin="30" xMax="300" yMax="100">\n'
            '  <line xMin="50" yMin="30" xMax="90" yMax="50"><word xMin="50" yMin="30" xMax="90" yMax="50">A\x01B</word></line>\n'
            '  <line xMin="60" yMin="55" xMax="80" yMax="70"><word xMin="60" yMin="55" xMax="80" yMax="70">\x1c</word></line>\n'
            '  <line xMin="90" yMin="75" xMax="100" yMax="90"><word xMin="90" yMin="75" xMax="100" yMax="90">x̃</word></line>\n'
            ' </block>\n'
            '</page></doc>'
        ).encode("utf-8")

    def test_control_recovery_is_reversible_and_traced(self) -> None:
        original = self.synthetic_xml()
        path = self.root / "controls.xml"
        path.write_bytes(original)
        with self.assertRaises(ET.ParseError):
            ET.parse(path)
        root, recovery = parse_poppler_bbox(path)
        page = next(element for element in root.iter() if element.tag == "page")
        blocks = _poppler_page(page)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["raw_text"], "A\x01B\n\x1c\nx̃")
        self.assertEqual(blocks[0]["bbox"], [50.0, 30.0, 300.0, 100.0])
        self.assertNotIn("__PDF_READER_XML_RECOVERY_", blocks[0]["raw_text"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(recovery["control_character_count"], 2)
        self.assertEqual(recovery["codepoints"], ["U+0001", "U+001C"])
        self.assertEqual(recovery["source_xml_sha256"], sha256(original).hexdigest())
        self.assertEqual(recovery["pages"][0]["first_occurrences"][0]["original_xml_line"], 4)
        atomic_write_json(self.root / "recovered.json", {"blocks": blocks})
        encoded = (self.root / "recovered.json").read_bytes()
        self.assertIn(b"\\u0001", encoded)
        self.assertIn(b"\\u001c", encoded)
        self.assertNotIn(b"\x01", encoded)
        self.assertNotIn(b"\x1c", encoded)

    @staticmethod
    def floating_text_xml(line_count=5, body_token_count=12, right_texts=None, same_y=False):
        page = ET.Element("page", {"width": "612", "height": "792"})
        block = ET.SubElement(page, "block", {"xMin": "108", "yMin": "100", "xMax": "504.00189", "yMax": "150"})
        body_words = ["many", "ordinary", "body", "words", "remain", "inside", "the", "left", "source", "fragment", "without", "loss"][:body_token_count]

        def add_words(line, texts, start, end, y):
            gap = 3.0
            width = (end - start - gap * (len(texts) - 1)) / len(texts)
            for index, text in enumerate(texts):
                left = start + index * (width + gap)
                ET.SubElement(line, "word", {"xMin": f"{left:.6f}", "yMin": f"{y:.6f}", "xMax": f"{left + width:.6f}", "yMax": f"{y + 8:.6f}"}).text = text
            boxes = [word.attrib for word in line]
            for key, value in (("xMin", min(float(item["xMin"]) for item in boxes)), ("yMin", min(float(item["yMin"]) for item in boxes)), ("xMax", max(float(item["xMax"]) for item in boxes)), ("yMax", max(float(item["yMax"]) for item in boxes))):
                line.set(key, f"{value:.6f}")

        for line_index in range(line_count):
            y = 100.0 if same_y else 100.0 + line_index * 10
            line = ET.SubElement(block, "line")
            add_words(line, body_words, 108.0, 379.201897, y)
            if right_texts is not None and line_index in (1, 2):
                add_words(line, right_texts[line_index - 1], 389.163, 504.00189, y)
        original = "\n".join(" ".join(word.text or "" for word in line) for line in block)
        return page, original

    def test_floating_text_partition_is_geometric_and_conservative(self) -> None:
        aside = [["aside", "text", "has", "five", "words"], ["row", "two", "retains", "its", "words"]]
        page, original = self.floating_text_xml(right_texts=aside)
        blocks = _poppler_page(page)
        self.assertEqual(len(blocks), 2)
        left, right = blocks
        self.assertEqual(right["raw_text"], "aside text has five words\nrow two retains its words")
        self.assertEqual(sorted(" ".join(block["raw_text"] for block in blocks).split()), sorted(original.split()))
        for block, fragment in ((left, "left_original_words"), (right, "right_original_words")):
            self.assertTrue(block["requires_visual_review"])
            self.assertEqual(block["source_text_before_adjustments"], original)
            self.assertEqual(len(block["source_text_adjustments"]), 1)
            adjustment = block["source_text_adjustments"][0]
            self.assertEqual(adjustment["kind"], "poppler_wrapped_text_partition")
            self.assertEqual(adjustment["fragment"], fragment)
            self.assertEqual(adjustment["source_xml_block_index"], 0)
            self.assertEqual(adjustment["partition_line_indices"], [1, 2])
            self.assertEqual(adjustment["source_block_word_count"], 70)
            self.assertAlmostEqual(adjustment["common_gap_points"], 9.961103, places=5)
            self.assertEqual(len(adjustment["line_boundaries"]), 2)
            self.assertEqual(len(adjustment["line_boundaries"][0]["raw_word_bboxes"]), 2)
            self.assertTrue(block["warnings"])
        for candidate_page, original_text in (
            self.floating_text_xml(),
            self.floating_text_xml(right_texts=aside, same_y=True),
            self.floating_text_xml(line_count=2, body_token_count=3, right_texts=[["∆", "labels", "stay", "literal", "source"], ["and", "no", "cell", "ownership", "is", "inferred"]]),
        ):
            candidate_blocks = _poppler_page(candidate_page)
            self.assertEqual(len(candidate_blocks), 1)
            self.assertEqual(candidate_blocks[0]["raw_text"], original_text)
            self.assertNotIn("source_text_adjustments", candidate_blocks[0])
            self.assertFalse(candidate_blocks[0].get("table_candidate", False))
        self.assertIn("∆", candidate_blocks[0]["raw_text"])

    def test_numeric_subscript_join_is_geometric_and_audited(self) -> None:
        root = ET.fromstring(
            '<doc><page width="600" height="800"><block xMin="320" yMin="30" xMax="350" yMax="78.068482">'
            '<line xMin="325.443" yMin="30.106318" xMax="345.929823" yMax="38.484654">'
            '<word xMin="325.443" yMin="30.106318" xMax="338.624505" yMax="38.068482">AP</word>'
            '<word xMin="338.624" yMin="33.176546" xMax="345.929823" yMax="38.484654">50</word></line>'
            '<line xMin="325.443" yMin="50.106318" xMax="348.929823" yMax="58.484654">'
            '<word xMin="325.443" yMin="50.106318" xMax="338.624505" yMax="58.068482">AP</word>'
            '<word xMin="341.624" yMin="53.176546" xMax="348.929823" yMax="58.484654">50</word></line>'
            '<line xMin="325.443" yMin="70.106318" xMax="345.929823" yMax="78.068482">'
            '<word xMin="325.443" yMin="70.106318" xMax="338.624505" yMax="78.068482">AP</word>'
            '<word xMin="338.624" yMin="70.106318" xMax="345.929823" yMax="78.068482">50</word></line>'
            '</block></page></doc>'
        )
        block = _poppler_page(root.find("page"))[0]
        self.assertEqual(block["raw_text"], "AP50\nAP 50\nAP 50")
        self.assertEqual(block["source_text_before_adjustments"], "AP 50\nAP 50\nAP 50")
        self.assertEqual(len(block["source_text_adjustments"]), 1)
        adjustment = block["source_text_adjustments"][0]
        self.assertEqual(adjustment["raw_word_texts"], ["AP", "50"])
        self.assertAlmostEqual(adjustment["gap_points"], -0.000505)
        self.assertEqual(adjustment["scope"], "same_poppler_xml_line")
        control_page = ET.Element("page")
        container = ET.SubElement(control_page, "block", {"xMin": "10", "yMin": "10", "xMax": "30", "yMax": "30"})
        word = ET.SubElement(container, "word", {"xMin": "10", "yMin": "10", "xMax": "30", "yMax": "30"})
        word.text = "\x1c"
        fallback = _poppler_page(control_page)
        self.assertEqual(fallback[0]["raw_text"], "\x1c")
        self.assertNotIn("source_text_adjustments", fallback[0])

    def test_letter_subscripts_and_punctuation_use_tight_geometry(self) -> None:
        # Synthetic XML using measured caption-style coordinates, not a scientific result.
        base = (185.125, 287.137764, 197.517443, 293.372341)
        ordinary = (145.001096, 283.617357, 185.120487, 292.523922)
        rows = [
            [("RoBERTa", ordinary), ("base", base), (",", (198.015, 283.617357, 200.50565, 292.523922)),
             ("DeBERTa", (281.376895, 283.617357, 321.486323, 292.523922)), ("XXL", (321.482, 287.137764, 335.813159, 293.372341))],
            [("RoBERTa", ordinary), ("base", (186.125, base[1], 198.517443, base[3])), (",", (199.015, 283.617357, 201.50565, 292.523922))],
            [("RoBERTa", ordinary), ("base", (185.125, ordinary[1], 197.517443, ordinary[3])), (",", (198.015, 283.617357, 200.50565, 292.523922))],
            [("RoBERTa", ordinary), ("base", base), (",", (200.517, 283.617357, 203.00765, 292.523922))],
        ]
        page = ET.Element("page", {"width": "612", "height": "792"})
        block = ET.SubElement(ET.SubElement(page, "block", {"xMin": "140", "yMin": "280", "xMax": "340", "yMax": "300"}), "line")
        for text, bbox in rows[0]:
            ET.SubElement(block, "word", dict(zip(("xMin", "yMin", "xMax", "yMax"), map(str, bbox)))).text = text
        positive = _poppler_page(page)[0]
        self.assertEqual(positive["raw_text"], "RoBERTabase, DeBERTaXXL")
        expected_before = "RoBERTa base , DeBERTa XXL"
        self.assertEqual(positive["source_text_before_adjustments"], expected_before)
        kinds = [event["kind"] for event in positive["source_text_adjustments"]]
        self.assertEqual(kinds.count("alphabetic_subscript_geometry"), 2)
        self.assertEqual(kinds.count("post_subscript_punctuation_geometry"), 1)
        punctuation = next(event for event in positive["source_text_adjustments"] if event["kind"] == "post_subscript_punctuation_geometry")
        self.assertEqual(punctuation["raw_word_texts"], ["base", ","])
        self.assertAlmostEqual(punctuation["gap_points"], 0.497557)
        self.assertTrue(positive["requires_visual_review"])
        for coordinates, adjustment_count in ((rows[1], 0), (rows[2], 0), (rows[3], 1)):
            page = ET.Element("page", {"width": "612", "height": "792"})
            block = ET.SubElement(ET.SubElement(page, "block", {"xMin": "140", "yMin": "280", "xMax": "340", "yMax": "300"}), "line")
            for text, bbox in coordinates:
                ET.SubElement(block, "word", dict(zip(("xMin", "yMin", "xMax", "yMax"), map(str, bbox)))).text = text
            record = _poppler_page(page)[0]
            expected = "RoBERTabase ," if adjustment_count else "RoBERTa base ,"
            self.assertEqual(record["raw_text"], expected)
            self.assertEqual(record.get("source_text_before_adjustments"), "RoBERTa base ," if adjustment_count else None)
            self.assertEqual(len(record.get("source_text_adjustments", [])), adjustment_count)

    def test_adapter_warnings_and_json_preserve_controls(self) -> None:
        case = read_json(FIXTURES / "basic" / "case.json")
        case["pdf"] = str((FIXTURES / "basic" / "two-column.pdf").resolve())
        case["case_id"] = "synthetic-xml-control-adapter"
        atomic_write_json(self.root / "case.json", case)
        plan = self.root / "case-plan.json"
        self.assertEqual(inspect_case_command(self.root / "case.json", FIXTURES / "basic" / "task.md", None, self.root / "cli", plan, self.root / "early.json"), 0)
        capabilities_path = self.root / "capabilities.json"
        atomic_write_json(capabilities_path, {"tools": {"pdftotext": {"path": "synthetic-pdftotext"}}, "ocr": {"available": False}})
        original = self.synthetic_xml()

        def synthetic_tool(command, log_path, timeout=120):
            Path(command[-1]).write_bytes(original)
            log_path.write_text("synthetic external-tool adapter stub", encoding="utf-8")
            return 0, ""

        with patch("pdf_evidence.probe.run_to_log", side_effect=synthetic_tool), patch("pdf_evidence.extractors.run_to_log", side_effect=synthetic_tool), patch("pdf_evidence.extractors._render_page", return_value=(None, "synthetic renderer stub")) as renderer:
            probe_path = self.root / "probe.json"
            probe_code = probe_pdf_command(plan, capabilities_path, probe_path, backend="poppler")
            document_path = self.root / "document.json"
            extract_code = extract_command(plan, probe_path, capabilities_path, self.root / "cli", document_path)
        self.assertEqual((probe_code, extract_code), (0, 0))
        probe_record = read_json(probe_path)["documents"][0]
        self.assertEqual(probe_record["properties"]["xml_recovery"]["control_character_count"], 2)
        self.assertTrue(probe_record["pages"][0]["warnings"])
        document = read_json(document_path)
        self.assertTrue(any(warning.startswith("poppler_xml_controls") for warning in document["pages"][0]["warnings"]))
        self.assertEqual(renderer.call_count, 1)
        self.assertEqual(document["pages"][0]["blocks"][0]["source_text"], "A\x01B\n\x1c\nx̃")
        self.assertEqual(document["extraction_recovery"]["control_character_count"], 2)
        self.assertEqual((self.root / "cli" / "extract-bbox.html").read_bytes(), original)
        self.assertEqual(read_json(probe_path)["documents"][0]["properties"]["xml_recovery"]["source_xml_sha256"], document["extraction_recovery"]["source_xml_sha256"])
        encoded = document_path.read_bytes()
        self.assertIn(b"\\u0001", encoded)
        self.assertIn(b"\\u001c", encoded)
        self.assertNotIn(b"__PDF_READER_XML_RECOVERY_", encoded)
        selection_path = self.root / "context-selection.json"
        index_path = self.root / "context-index.json"
        budget_path = self.root / "context-budget.json"
        context_code = select_context_command(plan, document_path, selection_path, index_path, budget_path, block_ids=[document["pages"][0]["blocks"][0]["block_id"]], max_tokens=4000)
        self.assertEqual(context_code, 0)
        index = read_json(index_path)
        self.assertEqual(index["extraction_recovery"], document["extraction_recovery"])
        self.assertIn("unverified", index["recovery_warning"])
        self.assertEqual(read_json(selection_path)["selected_blocks"][0]["text"], "A\x01B\n\x1c\nx̃")

    def test_repair_rejects_collision_and_does_not_fix_other_bad_xml(self) -> None:
        collision = self.root / "collision.xml"
        collision.write_bytes(b'<doc><page><word>__PDF_READER_XML_RECOVERY_99__\x01</word></page></doc>')
        with self.assertRaisesRegex(ValueError, "reserved recovery marker"):
            parse_poppler_bbox(collision)
        malformed = self.root / "malformed.xml"
        malformed.write_bytes(b"<doc><page><word>one & two</word></page></doc>")
        with self.assertRaises(ET.ParseError):
            parse_poppler_bbox(malformed)


class MissingExtractorReportingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TEST_WORK / "m3-missing-extractor"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.capabilities = cls.root / "capabilities.json"
        assert capabilities_command(None, cls.capabilities) == 0

    def test_missing_pymupdf_is_reported_instead_of_crashing(self) -> None:
        pdf = (FIXTURES / "basic" / "two-column.pdf").resolve()
        plan = self.root / "plan.json"
        probe = self.root / "probe.json"
        atomic_write_json(plan, {
            "schema_version": "1.0",
            "case_id": "synthetic-missing-pymupdf",
            "early_result": str(self.root / "result.json"),
        })
        atomic_write_json(probe, {"schema_version": "1.0", "documents": [{
            "path": str(pdf),
            "backend": "pymupdf",
            "status": "ready",
            "page_count": 1,
            "pages": [{"page_number": 1, "status": "text", "action": "extract", "width": 612, "height": 792}],
            "errors": [],
        }]})
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pymupdf":
                raise ImportError("simulated missing PyMuPDF")
            return real_import(name, *args, **kwargs)

        document_path = self.root / "document.json"
        with patch("builtins.__import__", side_effect=fake_import):
            code = extract_command(plan, probe, self.capabilities, self.root, document_path)
        self.assertEqual(code, 6)
        document = read_json(document_path)
        self.assertEqual(document["status"], "partial")
        self.assertEqual(document["coverage"]["processed"], 0)
        kinds = {error.get("kind") for error in document["errors"]}
        self.assertIn("extractor_unavailable", kinds)


if __name__ == "__main__":
    unittest.main()
