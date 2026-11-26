from __future__ import annotations

import unittest

from tests._support import TEST_WORK
from pdf_evidence.case_io import atomic_write_json, empty_result, error_record, read_json
from pdf_evidence.coverage import ITEM_ORDER, build_coverage_report
from pdf_evidence.result_io import finalize_result_command
from pdf_evidence.validators import validate_result_command, validate_result_object


class ResultValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TEST_WORK / "m2-result"
        self.root.mkdir(parents=True, exist_ok=True)
        self.plan = {
            "case_id": "synthetic-basic-v1",
            "output_contract": {
                "fields": {
                    "answer": {"type": "string", "required": True},
                    "keywords": {"type": "array"},
                },
                "required": ["answer"],
            },
        }
        self.document = {
            "schema_version": "1.0",
            "page_count": 1,
            "coverage": {"processed": 1, "failed": 0, "skipped": 0},
            "pages": [{
                "page_number": 1,
                "status": "complete",
                "blocks": [{
                    "block_id": "p0001-b0001",
                    "page_number": 1,
                    "kind": "body",
                    "text": "The measurement is 42.5 mV with α = 0.3.",
                    "bbox": [10, 10, 200, 30],
                }],
            }],
        }
        self.plan_path = self.root / "plan.json"
        self.document_path = self.root / "document.json"
        atomic_write_json(self.plan_path, self.plan)
        atomic_write_json(self.document_path, self.document)
        self.answer_path = self.root / "answer.json"
        self.result_path = self.root / "result.json"

    def write_answer(self, **overrides):
        answer = {
            "fields": {"answer": "The measured value is 42.5 mV.", "keywords": ["measurement"]},
            "status": "success",
            "sources": [{"block_id": "p0001-b0001", "page_number": 1, "quote": "42.5 mV"}],
            "uncertainty": [],
            "errors": [],
        }
        answer.update(overrides)
        atomic_write_json(self.answer_path, answer)
        return answer

    def test_valid_answer_merges_and_validates(self) -> None:
        self.write_answer()
        initial = empty_result(
            self.plan["case_id"],
            self.plan["output_contract"],
            [error_record("pending_processing", "inspect-case", "pending")],
        )
        atomic_write_json(self.result_path, initial)
        code = finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path)
        self.assertEqual(code, 0)
        result = read_json(self.result_path)
        self.assertEqual(result["answer"], "The measured value is 42.5 mV.")
        self.assertEqual(result["sources"][0]["kind"], "body")
        self.assertEqual(result["errors"], [])
        report_path = self.root / "validation.json"
        code = validate_result_command(self.plan_path, self.document_path, self.result_path, report_path)
        self.assertEqual(code, 0)
        self.assertTrue(read_json(report_path)["valid"])

    def test_invalid_quote_is_rejected_and_old_result_survives(self) -> None:
        self.write_answer()
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 0)
        original_bytes = self.result_path.read_bytes()
        self.write_answer(sources=[{"block_id": "p0001-b0001", "page_number": 1, "quote": "99.9 mV"}])
        code = finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path)
        self.assertEqual(code, 8)
        self.assertEqual(self.result_path.read_bytes(), original_bytes)

    def test_missing_required_field_fails_validation(self) -> None:
        result = empty_result(self.plan["case_id"], self.plan["output_contract"], [])
        result["keywords"] = []
        atomic_write_json(self.result_path, result)
        report = validate_result_object(self.plan, self.document, result)
        self.assertFalse(report["valid"])
        self.assertTrue(any("Required output field" in message for message in report["errors"]))

    def test_non_object_evidence_is_rejected_without_old_result_loss(self) -> None:
        self.write_answer()
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 0)
        original_bytes = self.result_path.read_bytes()
        self.write_answer(sources=["not-a-source-object"])
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 8)
        self.assertEqual(self.result_path.read_bytes(), original_bytes)
        report = read_json(self.root / "result.finalize-validation.json")
        self.assertTrue(any("must be an object" in message for message in report["errors"]))

    def test_existing_result_that_is_not_an_object_is_rejected_without_overwrite(self) -> None:
        self.write_answer()
        atomic_write_json(self.result_path, ["not", "an", "object"])
        original_bytes = self.result_path.read_bytes()
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 8)
        self.assertEqual(self.result_path.read_bytes(), original_bytes)
        report = read_json(self.root / "result.finalize-validation.json")
        self.assertFalse(report["valid"])
        self.assertTrue(any("not a JSON object" in message for message in report["errors"]))

    def test_finalize_keeps_prior_artifact_paths(self) -> None:
        self.write_answer()
        initial = empty_result(self.plan["case_id"], self.plan["output_contract"], [])
        initial["artifacts"] = {"case_plan": "/work/case-plan.json", "context_index": "/work/context-index.json"}
        atomic_write_json(self.result_path, initial)
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 0)
        artifacts = read_json(self.result_path)["artifacts"]
        self.assertEqual(artifacts["context_index"], "/work/context-index.json")
        self.assertEqual(artifacts["case_plan"], str(self.plan_path.resolve()))
        self.assertEqual(artifacts["document"], str(self.document_path.resolve()))
        self.assertEqual(artifacts["answer"], str(self.answer_path.resolve()))

    def test_wrong_case_id_and_field_type_fail(self) -> None:
        self.write_answer()
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 0)
        result = read_json(self.result_path)
        result["case_id"] = "wrong-case"
        result["keywords"] = "not-an-array"
        report = validate_result_object(self.plan, self.document, result)
        self.assertFalse(report["valid"])
        self.assertTrue(any("case_id" in message for message in report["errors"]))
        self.assertTrue(any("wrong type" in message for message in report["errors"]))

    def test_success_without_sources_or_with_pending_error_fails(self) -> None:
        result = empty_result(self.plan["case_id"], self.plan["output_contract"], [])
        result.update({"answer": "A result", "keywords": [], "status": "success"})
        report = validate_result_object(self.plan, self.document, result)
        self.assertFalse(report["valid"])
        self.assertTrue(any("must contain sources" in message for message in report["errors"]))

    def test_finalize_writes_structural_coverage_report(self) -> None:
        self.write_answer()
        initial = empty_result(self.plan["case_id"], self.plan["output_contract"], [])
        atomic_write_json(self.result_path, initial)
        self.assertEqual(finalize_result_command(self.plan_path, self.document_path, self.answer_path, self.result_path), 0)
        result = read_json(self.result_path)
        report = result["coverage_report"]
        self.assertEqual(set(report["items"]), set(ITEM_ORDER))
        self.assertEqual(report["method"], "deterministic_heuristics_from_document_json")
        self.assertEqual(sum(report["totals"].values()), len(ITEM_ORDER))
        self.assertTrue(validate_result_object(self.plan, self.document, result)["valid"])


class CoverageReportTest(unittest.TestCase):
    """The ten-item structural report must be decided from the document alone."""

    def _document(self):
        return {
            "schema_version": "1.0",
            "page_count": 2,
            "pages": [
                {
                    "page_number": 1,
                    "width": 600,
                    "height": 800,
                    "status": "complete",
                    "blocks": [
                        {"block_id": "p0001-b0001", "page_number": 1, "kind": "body", "text": "A Study of Columns",
                         "bbox": [40, 40, 560, 80], "font_size_max": 18.0, "font_bold": True, "page_reading_order": 1},
                        {"block_id": "p0001-b0002", "page_number": 1, "kind": "body",
                         "text": "Ada Lovelace, University of London", "bbox": [40, 90, 560, 106], "page_reading_order": 2},
                        {"block_id": "p0001-b0003", "page_number": 1, "kind": "heading", "text": "Abstract",
                         "bbox": [40, 120, 200, 136], "page_reading_order": 3},
                        {"block_id": "p0001-b0004", "page_number": 1, "kind": "body", "text": "We study columns.",
                         "bbox": [40, 140, 560, 170], "page_reading_order": 4},
                        {"block_id": "p0001-b0005", "page_number": 1, "kind": "heading", "text": "1 Introduction",
                         "bbox": [40, 190, 300, 206], "page_reading_order": 5, "section_path": ["1 Introduction"]},
                        {"block_id": "p0001-b0006", "page_number": 1, "kind": "heading", "text": "1.1 Method",
                         "bbox": [40, 220, 300, 236], "page_reading_order": 6,
                         "section_path": ["1 Introduction", "1.1 Method"]},
                        {"block_id": "p0001-b0007", "page_number": 1, "kind": "header", "text": "Preprint",
                         "bbox": [40, 10, 200, 20], "page_reading_order": 7},
                        {"block_id": "p0001-b0008", "page_number": 1, "kind": "reference",
                         "text": "[1] A. Author. A paper. 2020.", "bbox": [40, 400, 560, 420], "page_reading_order": 8},
                        {"block_id": "p0001-b0009", "page_number": 1, "kind": "reference",
                         "text": "[2] B. Author. Another paper. 2021.", "bbox": [40, 420, 560, 440], "page_reading_order": 9},
                    ],
                },
                {
                    "page_number": 2,
                    "width": 600,
                    "height": 800,
                    "status": "complete",
                    "blocks": [
                        {"block_id": "p0002-b0001", "page_number": 2, "kind": "figure", "text": "",
                         "bbox": [40, 40, 560, 300], "page_reading_order": 1},
                        {"block_id": "p0002-b0002", "page_number": 2, "kind": "figure_caption",
                         "text": "Figure 1: Overview of the method.", "bbox": [40, 300, 560, 316], "page_reading_order": 2},
                        {"block_id": "p0002-b0003", "page_number": 2, "kind": "table",
                         "text": "7B | 99.3 | 41GB", "bbox": [40, 340, 560, 400], "page_reading_order": 3,
                         "table_row_count": 1, "table_column_count": 3},
                        {"block_id": "p0002-b0004", "page_number": 2, "kind": "table_caption",
                         "text": "Table 1: Results.", "bbox": [40, 320, 560, 336], "page_reading_order": 4},
                        {"block_id": "p0002-b0005", "page_number": 2, "kind": "formula",
                         "text": "L = L0 + α x (3)", "bbox": [40, 420, 560, 440], "page_reading_order": 5},
                        {"block_id": "p0002-b0006", "page_number": 2, "kind": "footer", "text": "1",
                         "bbox": [40, 780, 560, 795], "page_reading_order": 6},
                        {"block_id": "p0002-b0007", "page_number": 2, "kind": "body",
                         "text": "1 See the appendix for the derivation.", "bbox": [40, 730, 560, 745],
                         "page_reading_order": 7},
                    ],
                },
            ],
        }

    def test_items_are_decided_from_blocks_and_geometry(self) -> None:
        report = build_coverage_report(self._document())
        items = report["items"]
        self.assertEqual(list(items), list(ITEM_ORDER))
        self.assertEqual(items["title"]["status"], "present")
        self.assertEqual(items["title"]["pages"], [1])
        self.assertEqual(items["authors_and_affiliations"]["status"], "present")
        self.assertEqual(items["abstract"]["status"], "present")
        self.assertEqual(items["section_hierarchy"]["status"], "present")
        self.assertEqual(items["figures_and_captions"]["status"], "present")
        self.assertEqual(items["tables_and_captions"]["status"], "present")
        self.assertEqual(items["formula_numbers"]["status"], "present")
        self.assertEqual(items["references"]["status"], "partial")
        self.assertEqual(items["headers_and_footers"]["status"], "present")
        self.assertEqual(items["footnotes"]["status"], "present")
        self.assertIn(1, items["headers_and_footers"]["pages"])
        self.assertIn(2, items["footnotes"]["pages"])

    def test_missing_structures_are_reported_as_gaps_not_invented(self) -> None:
        document = {
            "schema_version": "1.0",
            "page_count": 1,
            "pages": [{
                "page_number": 1,
                "width": 600,
                "height": 800,
                "status": "complete",
                "blocks": [{"block_id": "p0001-b0001", "page_number": 1, "kind": "body",
                            "text": "Plain paragraph without any structure.", "bbox": [40, 40, 560, 60]}],
            }],
        }
        report = build_coverage_report(document)
        self.assertEqual(report["items"]["figures_and_captions"]["status"], "absent")
        self.assertEqual(report["items"]["tables_and_captions"]["status"], "absent")
        self.assertEqual(report["items"]["references"]["status"], "absent")
        self.assertEqual(report["items"]["footnotes"]["status"], "uncertain")
        gap_names = {gap["item"] for gap in report["gaps"]}
        self.assertIn("figures_and_captions", gap_names)
        self.assertIn("references", gap_names)
        self.assertTrue(all(gap["action"] for gap in report["gaps"]))


if __name__ == "__main__":
    unittest.main()
