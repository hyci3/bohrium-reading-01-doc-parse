from __future__ import annotations

import unittest
import uuid

from tests._support import TEST_WORK
from pdf_evidence.case_io import atomic_write_json, read_json
from pdf_evidence.context import (
    document_content_sha256,
    estimate_json_tokens,
    merge_segment_results,
    select_context_command,
)
from pdf_evidence.result_io import finalize_result_command


class ContextWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TEST_WORK / f"m5-context-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=False)
        self.plan = {
            "schema_version": "1.0",
            "case_id": "synthetic-context",
            "output_contract": {
                "fields": {
                    "answer": {"type": "string", "required": True},
                    "keywords": {"type": "array"},
                },
                "required": ["answer"],
            },
        }
        self.plan_path = self.root / "plan.json"
        atomic_write_json(self.plan_path, self.plan)
        pages = []
        order = 0
        for page_number in (1, 2):
            blocks = []
            for index in range(4):
                order += 1
                blocks.append({
                    "block_id": f"p{page_number:04d}-b{index + 1:04d}",
                    "page_number": page_number,
                    "bbox": [50, 50 + index * 80, 550, 70 + index * 80],
                    "kind": "heading" if index == 0 else "body",
                    "section_path": ["Methods"] if page_number == 1 else ["Results"],
                    "global_reading_order": order,
                    "text": ("Alpha methods section." if index == 0 else "Evidence block %d contains measured values and alpha details. " % order) * 4,
                })
            pages.append({
                "page_number": page_number,
                "width": 600,
                "height": 800,
                "status": "complete",
                "blocks": blocks,
                "warnings": [],
            })
        self.document = {
            "schema_version": "1.0",
            "case_id": "synthetic-context",
            "page_count": 2,
            "status": "complete",
            "coverage": {"declared_pages": 2, "processed": 2, "failed": 0, "skipped": 0, "unaccounted": 0},
            "pages": pages,
            "rendered_pages": [],
        }
        self.document_path = self.root / "document.json"
        atomic_write_json(self.document_path, self.document)

    def paths(self, name: str):
        directory = self.root / name
        return directory / "selection.json", directory / "index.json", directory / "budget.json"

    def select(self, name: str, **kwargs):
        selection, index, budget = self.paths(name)
        budget = kwargs.pop("budget_state", budget)
        code = select_context_command(self.plan_path, self.document_path, selection, index, budget, **kwargs)
        return code, selection, index, budget

    def test_block_boundary_cursor_and_no_full_document_in_index(self) -> None:
        code, selection_path, index_path, budget_path = self.select("bounded", max_tokens=500)
        self.assertEqual(code, 0)
        selection = read_json(selection_path)
        self.assertTrue(selection["truncated"])
        self.assertGreater(selection["remaining_blocks"], 0)
        self.assertLessEqual(selection["new_evidence_estimated_tokens"], 500)
        self.assertFalse(any("…" in block["text"] for block in selection["selected_blocks"]))
        for block in selection["selected_blocks"]:
            self.assertNotIn("…", block["text"])
            self.assertEqual(block["text"], next(source["text"] for page in self.document["pages"] for source in page["blocks"] if source["block_id"] == block["block_id"]))
        code, second_path, second_index_path, _ = self.select("bounded-next", max_tokens=500, cursor=selection["next_cursor"], budget_state=budget_path)
        self.assertEqual(code, 0)
        self.assertTrue(second_index_path.is_file())
        second = read_json(second_path)
        first_ids = {block["block_id"] for block in selection["selected_blocks"]}
        self.assertFalse(any(block["block_id"] in first_ids for block in second["selected_blocks"] if not block.get("reuse_only")))
        index = read_json(index_path)
        self.assertNotIn(self.document["pages"][0]["blocks"][1]["text"], str(index))
        self.assertEqual(selection["token_count_method"], "estimated")

    def test_duplicate_evidence_is_reference_only_and_not_recharged(self) -> None:
        code, first_path, _, state_path = self.select("duplicate", query="Alpha methods", max_tokens=4000)
        self.assertEqual(code, 0)
        first = read_json(first_path)
        self.assertGreater(first["new_evidence_estimated_tokens"], 0)
        original_usage = first["budget"]["case_source_injection_used_tokens"]
        code, second_path, _, _ = self.select("duplicate-again", query="Alpha methods", max_tokens=4000, budget_state=state_path)
        self.assertEqual(code, 0)
        second = read_json(second_path)
        self.assertEqual(second["new_evidence_estimated_tokens"], 0)
        self.assertEqual(second["budget"]["case_source_injection_used_tokens"], original_usage)
        self.assertTrue(all(block.get("reuse_only") for block in second["selected_blocks"]))
        self.assertTrue(all("text" not in block for block in second["selected_blocks"]))
        self.assertEqual(second["selected_blocks"][0]["reuse_of"], str(first_path.resolve()))

    def test_usage_event_actual_tokens_take_precedence_and_deduplicate(self) -> None:
        event = self.root / "usage-event.json"
        atomic_write_json(event, {"event_id": "model-request-1", "kind": "model_interaction", "actual_tokens": 1500, "estimated_tokens": 4000})
        code, first_path, _, state_path = self.select("usage", query="Alpha methods", max_tokens=4000, usage_event=event)
        self.assertEqual(code, 0)
        first = read_json(first_path)
        self.assertEqual(first["budget"]["non_text_history_tokens"], 1500)
        state = read_json(state_path)
        self.assertEqual(state["non_text_actual_tokens"], 1500)
        self.assertEqual(state["non_text_estimated_tokens"], 0)
        code, second_path, _, _ = self.select("usage-again", query="Alpha methods", max_tokens=4000, budget_state=state_path, usage_event=event)
        self.assertEqual(code, 0)
        state = read_json(state_path)
        self.assertEqual(state["non_text_actual_tokens"], 1500)
        self.assertEqual(len(state["usage_events"]), 1)

    def test_minimum_unit_budget_exhaustion_is_explicit(self) -> None:
        code, selection_path, _, state_path = self.select("exhausted", max_tokens=1)
        self.assertEqual(code, 11)
        selection = read_json(selection_path)
        self.assertEqual(selection["selected_blocks"], [])
        self.assertEqual(selection["status"], "budget_exhausted")
        self.assertEqual(selection["remaining_blocks"], 8)
        self.assertEqual(selection["next_cursor"], None)
        self.assertEqual(selection["token_count_method"], "estimated")
        self.assertTrue(any(error["kind"] == "context_budget_exhausted" for error in selection["errors"]))
        self.assertTrue(state_path.is_file())

    def test_real_document_index_is_compact(self) -> None:
        real_document = TEST_WORK / "m3-integration" / "real-attention-document.json"
        real_plan = TEST_WORK / "m3-integration" / "real-attention-plan.json"
        selection = self.root / "real-index" / "selection.json"
        index = self.root / "real-index" / "index.json"
        budget = self.root / "real-index" / "budget.json"
        code = select_context_command(real_plan, real_document, selection, index, budget, max_tokens=5000, query="abstract encoder attention")
        self.assertEqual(code, 0)
        real_index = read_json(index)
        self.assertLessEqual(estimate_json_tokens(real_index), 12000)
        self.assertEqual(real_index["token_count_method"], "estimated")
        selected = read_json(selection)["selected_blocks"]
        self.assertGreater(len(selected), 0)
        self.assertTrue(all(block["block_id"] for block in selected))

    def test_multiline_header_and_unverified_table_rows_reach_selection(self) -> None:
        table = {
            "block_id": "p0001-b0099",
            "page_number": 1,
            "kind": "table",
            "section_path": ["Experiments"],
            "global_reading_order": 99,
            "text": "Model & Method # Trainable Parameters MNLI SST-2",
            "source_text": "Model & Method\n# Trainable\nParameters\nMNLI SST-2",
            "rows": [["Model & Method", "# Trainable\nParameters", "MNLI"]],
            "table_warning": "Automatic rows do not verify merged cells or column ownership",
        }
        self.document["pages"][0]["blocks"].append(table)
        atomic_write_json(self.document_path, self.document)
        code, selection_path, index_path, _ = self.select("table-evidence", block_ids=["p0001-b0099"], max_tokens=4000)
        self.assertEqual(code, 0)
        selected = read_json(selection_path)["selected_blocks"][0]
        self.assertEqual(selected["text"], table["source_text"])
        self.assertIn("# Trainable\nParameters", selected["text"])
        self.assertEqual(selected["rows"], table["rows"])
        self.assertTrue(selected["table_candidate"])
        self.assertTrue(selected["requires_visual_review"])
        self.assertIn("merged cells", selected["table_warning"])
        entry = next(item for item in read_json(index_path)["special_blocks"] if item["block_id"] == table["block_id"])
        self.assertEqual(entry["row_count"], 1)
        self.assertEqual(entry["max_column_count"], 3)
        self.assertNotIn("rows", entry)
        self.assertTrue(entry["requires_visual_review"])

    def test_layout_warnings_reach_selection_and_compact_index(self) -> None:
        warnings = [
            "Unstable multi-column hypothesis; retained coordinate fallback instead of forcing column ownership",
            "Second diagnostic is retained without truncation",
        ]
        block = self.document["pages"][0]["blocks"][1]
        original_full_text = block["text"]
        block["warnings"] = warnings
        block["text"] = "AP50"
        block["bbox"] = [10, 10, 24, 20]
        block["source_text"] = "AP50"
        block["source_text_before_adjustments"] = "AP 50"
        block["source_text_adjustments"] = [{
            "kind": "numeric_subscript_geometry",
            "raw_word_texts": ["AP", "50"],
            "raw_word_bboxes": [[10, 10, 20, 20], [20, 12, 24, 17]],
            "gap_points": 0.0,
        }]
        atomic_write_json(self.document_path, self.document)
        code, selection_path, index_path, _ = self.select("layout-warnings", block_ids=[block["block_id"]], max_tokens=4000)
        self.assertEqual(code, 0)
        selected = read_json(selection_path)["selected_blocks"][0]
        self.assertEqual(selected["warnings"], warnings)
        index = read_json(index_path)
        self.assertTrue(index["pages"][0]["risk"])
        inventory = read_json(index["inventory_artifact"])
        preview = next(item for item in inventory["blocks"] if item["block_id"] == block["block_id"])
        self.assertEqual(preview["warning_count"], len(warnings))
        self.assertEqual(preview["source_text_adjustment_count"], 1)
        self.assertNotIn("warnings", preview)
        self.assertNotIn(original_full_text, str(index))
        self.assertEqual(selected["source_text_before_adjustments"], "AP 50")
        self.assertEqual(selected["source_text_adjustments"], block["source_text_adjustments"])

    def test_full_document_segments_merge_actual_agent_results(self) -> None:
        segments_dir = self.root / "full" / "segments"
        selection_path, index_path, budget_path = self.paths("full")
        batches = []
        for _ in range(20):
            code = select_context_command(
                self.plan_path, self.document_path, selection_path, index_path, budget_path,
                max_tokens=700, full_document=True, segments_dir=segments_dir,
            )
            self.assertIn(code , (0, 11), code)
            selection = read_json(selection_path)
            if selection["new_evidence_count"]:
                batches.append(selection)
            if selection["remaining_blocks"] == 0:
                break
            self.assertEqual(code, 0)
        else:
            self.fail("Full-document cursor did not make progress")
        self.assertEqual(read_json(budget_path)["full_document_completed"], True)
        manifest = read_json(segments_dir / "manifest.json")
        covered = []
        expected_values = []
        for entry in manifest["segments"]:
            source = read_json(entry["input_path"])
            self.assertEqual(source["document_sha256"], manifest["document_sha256"])
            self.assertEqual(source["input_sha256"], entry["input_sha256"])
            self.assertRegex(source["input_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(source["block_ids"], entry["block_ids"])
            self.assertLessEqual(len(source["glossary"]), 16)
            covered.extend(source["block_ids"])
            segment_answer = "\n".join(f"Synthetic translation for {block_id}" for block_id in source["block_ids"])
            expected_values.append(segment_answer)
            atomic_write_json(entry["expected_result_path"], {
                "segment_id": source["segment_id"],
                "document_sha256": source["document_sha256"],
                "input_sha256": source["input_sha256"],
                "block_ids": source["block_ids"],
                "status": "complete",
                "fields": {"answer": segment_answer, "keywords": []},
                "sources": [{"block_id": block_id, "page_number": block["page_number"]} for block_id, block in zip(source["block_ids"], source["blocks"])],
                "errors": [],
            })
        self.assertEqual(len(covered), len(set(covered)))
        expected_ids = [block["block_id"] for page in self.document["pages"] for block in page["blocks"]]
        self.assertEqual(covered, expected_ids)
        merged_path = self.root / "full" / "merged.json"
        merged = merge_segment_results(self.plan, self.document, segments_dir, merged_path)
        self.assertTrue(merged["complete"])
        self.assertEqual(merged["fields"]["answer"], "\n\n".join(expected_values))
        self.assertEqual(merged["coverage"], {"expected_blocks": 8, "completed_blocks": 8, "missing_block_ids": [], "unexpected_block_ids": []})
        answer_path = self.root / "full" / "answer.json"
        atomic_write_json(answer_path, {"status": "success"})
        result_path = self.root / "full" / "result.json"
        code = finalize_result_command(self.plan_path, self.document_path, answer_path, result_path, segments_dir, merged_path)
        self.assertEqual(code, 0)
        result = read_json(result_path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["answer"], merged["fields"]["answer"])
        self.assertEqual(len(result["sources"]), 8)
        self.assertTrue(result["context"]["segmented_result_complete"])

    def test_targeted_cursor_retains_scope_and_rejects_expansion(self) -> None:
        code, reference_path, _, _ = self.select("scope-reference", pages=[1], max_tokens=4000)
        self.assertEqual(code, 0)
        reference = read_json(reference_path)
        self.assertEqual(reference["new_evidence_count"], 4)
        limit = reference["new_evidence_estimated_tokens"] - 1
        code, first_path, _, state_path = self.select("scoped", pages=[1], max_tokens=limit)
        self.assertEqual(code, 0)
        first = read_json(first_path)
        self.assertTrue(first["truncated"])
        self.assertGreater(first["remaining_blocks"], 0)
        self.assertEqual(first["selected_count"], first["new_evidence_count"] + first["reused_reference_count"])
        code, second_path, _, _ = self.select("scoped-next", cursor=first["next_cursor"], budget_state=state_path, max_tokens=4000)
        self.assertEqual(code, 0)
        second = read_json(second_path)
        self.assertEqual(second["filters"]["pages"], [1])
        self.assertEqual(second["remaining_blocks"], 0)
        self.assertFalse(second["truncated"])
        self.assertTrue(all(block["page_number"] == 1 for block in second["selected_blocks"]))
        self.assertEqual(first["new_evidence_estimated_tokens"] + second["new_evidence_estimated_tokens"], reference["new_evidence_estimated_tokens"])
        code, _, _, _ = self.select("scoped-explicit-repeat", pages=[1], cursor=first["next_cursor"], budget_state=state_path, max_tokens=4000)
        self.assertEqual(code, 0)
        state_before = read_json(state_path)
        with self.assertRaisesRegex(ValueError, "Cursor selection scope changed"):
            self.select("scope-expansion", pages=[2], cursor=first["next_cursor"], budget_state=state_path, max_tokens=4000)
        self.assertEqual(read_json(state_path), state_before)

    def test_missing_segment_result_preserves_unfinished_scope(self) -> None:
        directory = self.root / "missing"
        directory.mkdir(exist_ok=True)
        manifest = {
            "case_id": "synthetic-context",
            "document_sha256": document_content_sha256(self.document),
            "expected_block_ids": ["p0001-b0001", "p0001-b0002"],
            "segments": [{
                "segment_id": "segment-0001",
                "sequence": 1,
                "input_sha256": "0" * 64,
                "block_ids": ["p0001-b0001"],
                "expected_result_path": str(directory / "segment-0001.segment-result.json"),
            }],
        }
        atomic_write_json(directory / "manifest.json", manifest)
        merged = merge_segment_results(self.plan, self.document, directory, directory / "merged.json")
        self.assertFalse(merged["complete"])
        self.assertEqual(merged["status"], "partial")
        self.assertEqual(merged["coverage"]["missing_block_ids"], ["p0001-b0001", "p0001-b0002"])
        errors = read_json(directory / "merged.json")["errors"]
        self.assertTrue(any(error["kind"] == "segment_result_missing" for error in errors))


if __name__ == "__main__":
    unittest.main()
