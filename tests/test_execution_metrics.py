from __future__ import annotations

import subprocess
import sys
import time
import unittest
import uuid

from tests._support import FIXTURES, ROOT, SCRIPTS, SKILL_ROOT, TEST_WORK
from pdf_evidence.case_io import atomic_write_json, read_json


class ExecutionMetricsTest(unittest.TestCase):
    def test_actual_cli_stage_timings_and_bounded_reports(self) -> None:
        root = TEST_WORK / f"m6-execution-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        plan = TEST_WORK / "m3-integration" / "basic-fit-plan.json"
        caps = root / "capabilities.json"
        probe = root / "probe.json"
        document = root / "document.json"
        index = root / "context-index.json"
        budget = root / "context-budget.json"
        selection = root / "selection.json"
        answer = root / "answer.json"
        result = root / "result.json"
        calls = [
            ("capabilities", ["capabilities", "--output", str(caps)]),
            ("probe", ["probe-pdf", "--plan", str(plan), "--capabilities", str(caps), "--output", str(probe)]),
            ("extract_and_order", ["extract", "--plan", str(plan), "--probe", str(probe), "--capabilities", str(caps), "--work", str(root / "extract"), "--output", str(document)]),
            ("select_context", ["select-context", "--plan", str(plan), "--document", str(document), "--index-output", str(index), "--budget-state", str(budget), "--output", str(selection), "--query", "full width extraction", "--max-tokens", "4000"]),
        ]
        metrics = []
        for name, arguments in calls:
            start = time.perf_counter()
            completed = subprocess.run([sys.executable, str(SCRIPTS / "run.py"), *arguments], cwd=ROOT, text=True, capture_output=True, timeout=120, check=False)
            metrics.append({
                "stage": name,
                "seconds": time.perf_counter() - start,
                "returncode": completed.returncode,
                "stdout_chars": len(completed.stdout),
                "stderr_chars": len(completed.stderr),
            })
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertLessEqual(len(completed.stdout), 2000, completed.stdout)
            self.assertNotIn("A FULL WIDTH SECTION TITLE FOR THE TEST DOCUMENT", completed.stdout)
        doc = read_json(document)
        citation = next(block for block in doc["pages"][0]["blocks"] if block["text"] == "A FULL WIDTH SECTION TITLE FOR THE TEST DOCUMENT")
        atomic_write_json(answer, {
            "fields": {"answer": "Synthetic CLI timing fixture; not a scientific conclusion.", "keywords": []},
            "status": "success",
            "sources": [{"block_id": citation["block_id"], "page_number": 1, "quote": citation["text"]}],
            "uncertainty": [],
            "errors": [],
        })
        final_calls = [
            ("finalize", ["finalize-result", "--plan", str(plan), "--document", str(document), "--answer", str(answer), "--output", str(result)]),
            ("result_validation", ["validate-result", "--plan", str(plan), "--document", str(document), "--result", str(result), "--output", str(root / "result-validation.json")]),
            ("package_validation", ["validate-package", "--skill-dir", str(SKILL_ROOT), "--output", str(root / "package-validation.json")]),
        ]
        for name, arguments in final_calls:
            start = time.perf_counter()
            completed = subprocess.run([sys.executable, str(SCRIPTS / "run.py"), *arguments], cwd=ROOT, text=True, capture_output=True, timeout=120, check=False)
            metrics.append({"stage": name, "seconds": time.perf_counter() - start, "returncode": completed.returncode, "stdout_chars": len(completed.stdout), "stderr_chars": len(completed.stderr)})
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertLessEqual(len(completed.stdout), 2000, completed.stdout)
        self.assertTrue(read_json(root / "package-validation.json")["valid"])
        self.assertTrue(read_json(root / "result-validation.json")["valid"])
        self.assertEqual(len(metrics), 7)
        self.assertTrue(all(item["seconds"] > 0 for item in metrics))
        atomic_write_json(root / "execution-metrics.json", {
            "scope": "Local synthetic CLI metrics; not Harbor performance or model-token measurements",
            "fixtures": str(FIXTURES / "basic" / "two-column.pdf"),
            "stages": metrics,
            "total_script_seconds": sum(item["seconds"] for item in metrics),
            "max_stdout_chars": max(item["stdout_chars"] for item in metrics),
        })


if __name__ == "__main__":
    unittest.main()
