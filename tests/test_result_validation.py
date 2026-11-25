from __future__ import annotations

import unittest

from tests._support import TEST_WORK
from pdf_evidence.case_io import atomic_write_json, empty_result, error_record, read_json
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


if __name__ == "__main__":
    unittest.main()
