from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from tests._support import FIXTURES, ROOT, TEST_WORK
from pdf_evidence.case_io import atomic_write_json, inspect_case_command, read_json
from pdf_evidence.capabilities import capabilities_command
from pdf_evidence.extractors import extract_command, _tesseract_page
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


if __name__ == "__main__":
    unittest.main()
