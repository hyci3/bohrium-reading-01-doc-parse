from __future__ import annotations

import contextlib
import io
import unittest
import uuid

from tests._support import TEST_WORK
from pdf_evidence.case_io import atomic_write_json, read_json
from pdf_evidence.context import estimate_json_tokens, select_context_command


class LargeContextStressTest(unittest.TestCase):
    def test_above_source_limit_preserves_budget_cursor_and_bounded_stdout(self) -> None:
        root = TEST_WORK / f"m6-stress-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        pages = []
        sequence = 0
        for page_number in range(1, 4):
            blocks = []
            for index in range(1, 101):
                sequence += 1
                blocks.append({
                    "block_id": f"p{page_number:04d}-b{index:04d}",
                    "page_number": page_number,
                    "global_reading_order": sequence,
                    "kind": "body",
                    "section_path": ["Stress"],
                    "text": f"Source block {sequence:04d} with synthetic evidence. " + "Measured words " * 140,
                    "bbox": [50, 50 + index, 500, 60 + index],
                })
            pages.append({"page_number": page_number, "width": 600, "height": 800, "status": "complete", "blocks": blocks, "warnings": []})
        document = {
            "schema_version": "1.0",
            "case_id": "synthetic-over-limit",
            "page_count": 3,
            "status": "complete",
            "coverage": {"declared_pages": 3, "processed": 3, "failed": 0, "skipped": 0, "unaccounted": 0},
            "pages": pages,
            "rendered_pages": [],
        }
        plan = {
            "case_id": "synthetic-over-limit",
            "output_contract": {"fields": {"translation": {"type": "string", "required": True}}, "required": ["translation"]},
        }
        document_path = root / "document.json"
        plan_path = root / "plan.json"
        atomic_write_json(document_path, document)
        self.assertGreater(estimate_json_tokens([block["text"] for page in document["pages"] for block in page["blocks"]]), 96000)
        atomic_write_json(plan_path, plan)
        selection_path = root / "selection.json"
        index_path = root / "index.json"
        budget_path = root / "budget.json"
        segments_dir = root / "segments"
        stdout_measurements = []
        calls = []
        for _ in range(20):
            console = io.StringIO()
            with contextlib.redirect_stdout(console):
                code = select_context_command(
                    plan_path, document_path, selection_path, index_path, budget_path,
                    full_document=True, segments_dir=segments_dir,
                )
            output = console.getvalue()
            stdout_measurements.append(len(output))
            self.assertLessEqual(len(output), 2000, output)
            self.assertNotIn("Source block", output)
            selection = read_json(selection_path)
            calls.append((code, selection))
            self.assertLessEqual(selection["new_evidence_estimated_tokens"], 24000)
            self.assertFalse(any(block["text"].endswith(("Mea", "ur", "word")) for block in selection["selected_blocks"] if "text" in block))
            if code == 11:
                break
            self.assertEqual(code, 0)
        else:
            self.fail("Budget exhaustion not reached within a bounded number of requests")
        last = calls[-1][1]
        state = read_json(budget_path)
        self.assertGreater(last["remaining_blocks"], 0)
        self.assertTrue(last["truncated"])
        self.assertFalse(state["full_document_completed"])
        self.assertIsNotNone(last["next_cursor"])
        self.assertEqual(state["usage_events"], [])
        used = state["source_injection_used_tokens"]
        self.assertEqual(last["budget"]["effective_case_source_budget_tokens"], 81600)
        self.assertLessEqual(used, 81600)
        self.assertGreater(used, 76800)
        self.assertLessEqual(estimate_json_tokens(read_json(index_path)), 12000)
        self.assertTrue(any(error["kind"] in {"budget_exhausted", "context_budget_exhausted"} for error in last["errors"]))
        manifest = read_json(segments_dir / "manifest.json")
        covered = [block_id for segment in manifest["segments"] for block_id in segment["block_ids"]]
        self.assertEqual(len(covered), len(set(covered)))
        self.assertLess(len(covered), len(document["pages"]) * 100)
        for segment in manifest["segments"]:
            self.assertRegex(segment["input_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(read_json(segment["input_path"])["blocks"])
        atomic_write_json(root / "stress-report.json", {
            "scope": "Synthetic >96,000 estimated-token source test",
            "total_raw_estimated_tokens": sum(block["estimated_tokens"] if "estimated_tokens" in block else estimate_json_tokens(block["text"]) for page in document["pages"] for block in page["blocks"]),
            "batch_count": len(calls),
            "last_exit_code": calls[-1][0],
            "used_source_tokens": used,
            "remaining_blocks": last["remaining_blocks"],
            "max_stdout_characters": max(stdout_measurements),
            "completed_segments": len(manifest["segments"]),
        })


if __name__ == "__main__":
    unittest.main()
