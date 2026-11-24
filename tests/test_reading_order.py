from __future__ import annotations

import json
import unittest

from tests._support import FIXTURES, TEST_WORK
from pdf_evidence.case_io import atomic_write_json, read_json
from pdf_evidence.extractors import extract_command
from pdf_evidence.reading_order import order_pages
from pdf_evidence.validators import evaluate_order_command, evaluate_order_object


def block(raw_id: str, text: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {
        "raw_id": raw_id,
        "text": text,
        "raw_text": text,
        "bbox": [x0, y0, x1, y1],
        "source_order": int(raw_id.lstrip("r") or 0),
        "original_kind": "body",
        "raw_kind": "body",
    }


def page(number: int, blocks: list[dict]) -> dict:
    return {"page_number": number, "width": 1000, "height": 1000, "status": "complete", "raw_blocks": blocks}


class ReadingOrderUnitTest(unittest.TestCase):
    def test_columns_are_not_interleaved_and_full_width_title_leads(self) -> None:
        source = page(1, [
            block("r0", "FULL WIDTH TITLE", 50, 50, 950, 90),
            block("r1", "L1", 50, 150, 450, 180),
            block("r2", "R1", 550, 150, 950, 180),
            block("r3", "L2", 50, 220, 450, 250),
            block("r4", "R2", 550, 220, 950, 250),
        ])
        pages, debug = order_pages([source])
        self.assertEqual([item["text"] for item in pages[0]["blocks"]], ["FULL WIDTH TITLE", "L1", "L2", "R1", "R2"])
        self.assertEqual(debug["pages"][0]["zones"][0]["type"], "full_width")
        self.assertEqual(pages[0]["layout"]["zones"][1]["columns"], 2)

    def test_unstable_column_hypothesis_falls_back_with_ambiguity(self) -> None:
        source = page(1, [
            block("r0", "L1", 50, 150, 200, 180),
            block("r1", "M1", 280, 150, 390, 180),
            block("r2", "M2", 500, 150, 640, 180),
            block("r3", "R1", 780, 150, 940, 180),
        ])
        pages, debug = order_pages([source])
        self.assertTrue(debug["pages"][0]["ambiguous"])
        self.assertEqual(debug["pages"][0]["zones"][0]["columns"], 1)
        self.assertEqual([item["text"] for item in pages[0]["blocks"]], ["L1", "M1", "M2", "R1"])
        self.assertIn("layout_ambiguous", pages[0]["warnings"][0])
        self.assertTrue(all(item["warnings"] for item in pages[0]["blocks"]))

    def test_cross_page_is_a_candidate_not_a_silent_merge(self) -> None:
        source = [
            page(1, [block("r0", "The paragraph continues across", 50, 150, 900, 180)]),
            page(2, [block("r0", "the next page provides its result.", 50, 150, 900, 180)]),
        ]
        pages, _ = order_pages(source)
        previous = pages[0]["blocks"][0]
        following = pages[1]["blocks"][0]
        self.assertEqual(previous["text"], "The paragraph continues across")
        self.assertEqual(following["text"], "the next page provides its result.")
        self.assertEqual(previous["links"][0]["type"], "continues_candidate")
        self.assertTrue(previous["links"][0]["review_required"])

    def test_unterminated_uppercase_start_does_not_create_false_link(self) -> None:
        source = [
            page(1, [block("r0", "An unfinished title", 50, 150, 900, 180)]),
            page(2, [block("r0", "New section evidence starts here.", 50, 150, 900, 180)]),
        ]
        pages, _ = order_pages(source)
        self.assertEqual(pages[0]["blocks"][0]["links"], [])

    def test_formula_and_special_characters_are_preserved(self) -> None:
        source = page(1, [block("r0", "α_i^2 ≤ β + γ (3.2)", 50, 150, 450, 180)])
        pages, _ = order_pages([source])
        formula = pages[0]["blocks"][0]
        self.assertEqual(formula["kind"], "formula")
        self.assertEqual(formula["formula_number"], "3.2")
        self.assertIn("α_i^2 ≤ β + γ", formula["text"])


class ReadingOrderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TEST_WORK / "m4-order"
        cls.root.mkdir(parents=True, exist_ok=True)
        source_root = TEST_WORK / "m3-integration"
        cls.plan = source_root / "basic-fit-plan.json"
        cls.probe = source_root / "basic-fit-probe.json"
        cls.poppler_probe = source_root / "basic-poppler-probe.json"
        cls.capabilities = source_root / "capabilities.json"
        cls.layout_path = cls.root / "layout-document.json"
        cls.naive_path = cls.root / "naive-document.json"
        assert extract_command(cls.plan, cls.probe, cls.capabilities, cls.root / "layout", cls.layout_path, "layout") == 0
        assert extract_command(cls.plan, cls.probe, cls.capabilities, cls.root / "naive", cls.naive_path, "naive") == 0
        cls.poppler_path = cls.root / "poppler-document.json"
        assert extract_command(cls.plan, cls.poppler_probe, cls.capabilities, cls.root / "poppler", cls.poppler_path, "layout") == 0
        cls.gold_path = TEST_WORK.parents[1] / "tests" / "fixtures" / "two-column-gold.json"

    def test_layout_matches_manual_gold_for_both_extractors(self) -> None:
        gold = read_json(self.gold_path)
        for path in (self.layout_path, self.poppler_path):
            report = evaluate_order_object(read_json(path), gold)
            self.assertTrue(report["valid_gold_match"], report["errors"])
            self.assertTrue(report["document_exact_match"])
            self.assertEqual(report["order_accuracy"], 1.0)

    def test_naive_baseline_has_real_gold_inversions(self) -> None:
        report = evaluate_order_object(read_json(self.naive_path), read_json(self.gold_path))
        self.assertFalse(report["document_exact_match"])
        self.assertLess(report["order_accuracy"], 1.0)

    def test_headers_and_page_numbers_are_preserved_separately(self) -> None:
        document = read_json(self.layout_path)
        self.assertEqual(document["pages"][0]["blocks"][0]["kind"], "header")
        self.assertEqual(document["pages"][0]["blocks"][-1]["kind"], "footer")
        body_kinds = {block["kind"] for block in document["pages"][0]["blocks"][1:-1]}
        self.assertNotIn("header", body_kinds)
        self.assertNotIn("footer", body_kinds)

    def test_missing_gold_text_returns_nonzero(self) -> None:
        bad_gold = self.root / "bad-gold.json"
        gold = read_json(self.gold_path)
        gold["pages"]["1"][0] = "TEXT NOT IN DOCUMENT"
        atomic_write_json(bad_gold, gold)
        report_path = self.root / "bad-evaluation.json"
        self.assertEqual(evaluate_order_command(self.layout_path, bad_gold, report_path), 7)
        self.assertFalse(read_json(report_path)["valid_gold_match"])


if __name__ == "__main__":
    unittest.main()
