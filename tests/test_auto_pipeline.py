from __future__ import annotations

import unittest

from tests._support import FIXTURES, TEST_WORK
from pdf_evidence.case_io import read_json
from pdf_evidence.pipeline import auto_command


class AutoPipelineTest(unittest.TestCase):
    def test_one_call_builds_document_coverage_and_summary(self) -> None:
        work = TEST_WORK / "m9-auto"
        output = work / "output"
        code = auto_command(
            FIXTURES / "basic" / "case.json",
            FIXTURES / "basic" / "task.md",
            None,
            work,
            output,
        )
        self.assertEqual(code, 0)
        summary = read_json(work / "auto-summary.json")
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["case_id"], "synthetic-basic-v1")
        self.assertEqual(summary["document"]["page_count"], 2)
        self.assertGreater(summary["document"]["block_count"], 0)
        steps = {step["step"]: step for step in summary["steps"]}
        self.assertEqual([name for name in steps], ["capabilities", "inspect-case", "probe-pdf", "extract", "coverage-report"])
        self.assertEqual(steps["extract"]["returncode"], 0)
        self.assertTrue((work / "document.json").is_file())
        self.assertTrue((work / "coverage.json").is_file())
        self.assertTrue((output / "result.json").is_file())
        self.assertIn("next_actions", summary)

    def test_failed_case_still_writes_a_diagnostic_summary(self) -> None:
        work = TEST_WORK / "m9-auto-missing"
        output = work / "output"
        code = auto_command(work / "absent-case.json", work / "absent-task.md", None, work, output)
        self.assertNotEqual(code, 0)
        summary = read_json(work / "auto-summary.json")
        self.assertEqual(summary["status"], "incomplete")
        self.assertFalse(any(step["step"] == "extract" and step["ok"] for step in summary["steps"]))
        self.assertTrue(summary["next_actions"])


if __name__ == "__main__":
    unittest.main()
