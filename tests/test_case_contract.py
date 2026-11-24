from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests._support import FIXTURES, TEST_WORK
from pdf_evidence.case_io import decode_contract, inspect_case_command, read_json


class CaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TEST_WORK / "m2-contract"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.case_file = FIXTURES / "basic" / "case.json"
        cls.task_file = FIXTURES / "basic" / "task.md"
        cls.pdf_file = FIXTURES / "basic" / "two-column.pdf"
        cls.base_case = read_json(cls.case_file)

    def call_inspect(self, case_data: dict, label: str, pdf_path: Path | None = None):
        case_file = self.root / f"{label}-case.json"
        case_file.write_text(json.dumps(case_data), encoding="utf-8")
        plan = self.root / f"{label}-plan.json"
        early = self.root / f"{label}-result.json"
        selected_pdf = pdf_path or self.pdf_file
        code = inspect_case_command(
            str(case_file), str(self.task_file), [str(selected_pdf)], str(self.root / "work"), str(plan), str(early)
        )
        return code, plan, early

    def test_complete_contract_creates_early_result(self) -> None:
        code, plan_path, early_path = self.call_inspect(self.base_case, "valid")
        self.assertEqual(code, 0)
        plan = read_json(plan_path)
        early = read_json(early_path)
        self.assertEqual(plan["case_id"], "synthetic-basic-v1")
        self.assertEqual(len(plan["task_sha256"]), 64)
        self.assertEqual(len(plan["pdfs"][0]["sha256"]), 64)
        self.assertIn("answer", early)
        self.assertIn("keywords", early)
        self.assertIsNone(early["answer"])
        self.assertEqual(early["status"], "partial")
        self.assertEqual(early["errors"][0]["kind"], "pending_processing")

    def test_missing_pdf_is_distinct_failure(self) -> None:
        case = dict(self.base_case)
        case["pdf"] = str(self.root / "missing.pdf")
        code, _, early_path = self.call_inspect(case, "missing-pdf", self.root / "missing.pdf")
        self.assertEqual(code, 4)
        errors = read_json(early_path)["errors"]
        self.assertTrue(any(error["kind"] == "material_missing" for error in errors))

    def test_missing_contract_is_rejected(self) -> None:
        case = dict(self.base_case)
        case.pop("output_contract")
        code, _, early_path = self.call_inspect(case, "missing-contract")
        self.assertEqual(code, 4)
        self.assertTrue(any(error["kind"] == "invalid_contract" for error in read_json(early_path)["errors"]))

    def test_metadata_collision_is_rejected(self) -> None:
        case = json.loads(json.dumps(self.base_case))
        case["output_contract"]["fields"]["status"] = {"type": "string"}
        code, _, early_path = self.call_inspect(case, "collision")
        self.assertEqual(code, 4)
        self.assertTrue(any("reserved result metadata" in error["message"] for error in read_json(early_path)["errors"]))

    def test_json_schema_contract_form_is_supported(self) -> None:
        fields, required, errors = decode_contract({
            "type": "object",
            "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["answer"],
        })
        self.assertEqual(errors, [])
        self.assertEqual(set(fields), {"answer", "count"})
        self.assertEqual(required, ["answer"])


if __name__ == "__main__":
    unittest.main()
