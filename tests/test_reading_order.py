from __future__ import annotations

import json
import unittest

from tests._support import FIXTURES, TEST_WORK
from pdf_evidence.case_io import atomic_write_json, read_json
from pdf_evidence.extractors import extract_command
from pdf_evidence.reading_order import _classify, _mark_repeated_margins, order_pages
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


def page(number: int, blocks: list[dict], width: float = 1000, height: float = 1000) -> dict:
    return {"page_number": number, "width": width, "height": height, "status": "complete", "raw_blocks": blocks}


class RepeatedMarginUnitTest(unittest.TestCase):
    def test_repeated_single_word_and_table_candidate_are_not_page_margins(self) -> None:
        pages = []
        for number, prefix in [(1, "r0"), (2, "r1")]:
            candidate = block(prefix + "3", "he | likes | play", 75, 80, 350, 95)
            candidate["original_kind"] = "table"
            pages.append(page(number, [
                block(prefix + "0", "System", 75, 69, 102, 77),
                block(prefix + "1", "Conference running header", 190, 30, 360, 40),
                block(prefix + "2", "1", 290, 750, 300, 758),
                candidate,
            ], width=612, height=792))
        _mark_repeated_margins(pages)
        result = {}
        for source in pages:
            for item in source["raw_blocks"]:
                if item["text"] in {"System", "Conference running header", "1", "he | likes | play"}:
                    result[item["text"]] = item["raw_kind"]
        self.assertEqual(result["System"], "body")
        self.assertEqual(result["Conference running header"], "header")
        self.assertEqual(result["1"], "footer")
        self.assertEqual(result["he | likes | play"], "body")

    def test_repeated_header_with_physical_page_prefix_preserves_raw_text(self) -> None:
        pages = [
            page(number, [block("r0", f"{number}\nExample et al.", 34, 11, 118, 20)], width=595, height=842)
            for number in (2, 4, 6)
        ]
        ordered, _ = order_pages(pages)
        for source, output in zip(pages, ordered):
            header = output["blocks"][0]
            self.assertEqual(header["kind"], "header")
            self.assertEqual(header["source_text"], source["raw_blocks"][0]["raw_text"])
            self.assertTrue(any("page-number prefix" in warning for warning in header["warnings"]))

    def test_page_prefix_normalization_requires_matching_pages_and_repetition(self) -> None:
        for numbers in ((2, 4), (1, 2, 3)):
            with self.subTest(numbers=numbers):
                pages = [
                    page(number, [block("r0", f"{prefix}\nExample et al.", 34, 11, 118, 20)], width=595, height=842)
                    for number, prefix in zip(numbers, numbers if len(numbers) == 2 else (2, 9, 17))
                ]
                _mark_repeated_margins(pages)
                self.assertTrue(all(item["raw_blocks"][0]["raw_kind"] == "body" for item in pages))

    def test_numeric_bands_and_body_numbers_remain_body(self) -> None:
        pages, _ = order_pages([page(12, [
            block("r1", "62", 203, 41, 210, 48),
            block("r2", "42", 73, 53, 80, 60),
            block("r3", "60", 203, 54, 210, 61),
            block("r4", "42", 73, 400, 80, 408),
            block("r5", "12", 290, 750, 300, 758),
        ], width=595, height=842)])
        blocks = {item["raw_id"]: item for item in pages[0]["blocks"]}
        for raw_id in ("r1", "r2", "r3", "r4"):
            self.assertEqual(blocks[raw_id]["kind"], "body")
            self.assertEqual(blocks[raw_id]["source_text"], blocks[raw_id]["raw_text"])
        for raw_id in ("r1", "r2", "r3"):
            self.assertTrue(any("Multiple isolated numeric" in warning for warning in blocks[raw_id]["warnings"]))
        self.assertEqual(blocks["r5"]["kind"], "footer")


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

    def test_caption_delimiter_distinguishes_labels_from_discussions(self) -> None:
        for text in ("Figure 5 presents an ablation curve.", "Table 2 shows a comparison.", "Fig. 6 (middle) illustrates a result."):
            self.assertEqual(_classify(block("r0", text, 50, 100, 450, 130))["kind"], "body")
        figure = _classify(block("r1", "Figure 2: BERT input representation.", 50, 160, 450, 200))
        table = _classify(block("r2", "Table A1. Model configurations.", 50, 210, 450, 240))
        chinese = _classify(block("r3", "图 2：残差块结构。", 50, 250, 450, 280))
        self.assertEqual((figure["kind"], figure["caption_number"]), ("figure_caption", "2"))
        self.assertEqual((table["kind"], table["caption_number"]), ("table_caption", "A1"))
        self.assertEqual((chinese["kind"], chinese["caption_number"]), ("figure_caption", "2"))

    def test_small_graphics_labels_and_numeric_rows_are_not_sections(self) -> None:
        graph = block("r0", "70 ViT-H/14 BiT-L (R152x4)", 180, 285, 490, 300)
        graph["font_size_max"] = 7.77
        numbers = block("r1", "1 512 512 5.29 24.9 4 128 128", 110, 310, 490, 340)
        numbers["font_size_max"] = 9.96
        section = block("r2", "4.3 PRE-TRAINING DATA REQUIREMENTS", 108, 567, 292, 578)
        section["font_size_max"] = 9.96
        self.assertEqual(_classify(graph, 9.96)["kind"], "body")
        self.assertEqual(_classify(numbers, 9.96)["kind"], "body")
        self.assertEqual(_classify(section, 9.96)["kind"], "heading")
        expression = block("r3", "2 x (Conv 3x3 + GN + ReLU)", 180, 400, 490, 415)
        self.assertEqual(_classify(expression, 9.96)["kind"], "body")

    def test_bottom_numbered_prose_does_not_capture_later_sections(self) -> None:
        footnote_text = "11\nA related system is described in earlier work, but its system\nhas improved substantially after publication."
        pages, _ = order_pages([
            page(1, [
                block("r0", "4 Experiments", 50, 40, 550, 55),
                block("r1", footnote_text, 300, 710, 550, 770),
            ], width=600, height=800),
            page(2, [
                block("r0", "System", 75, 69, 102, 77),
                block("r1", "Main body text", 75, 150, 300, 165),
            ], width=600, height=800),
        ])
        footnote = next(item for item in pages[0]["blocks"] if item["source_text"] == footnote_text)
        self.assertEqual(footnote["kind"], "body")
        self.assertTrue(any("bottom margin" in warning and "footnote" in warning for warning in footnote["warnings"]))
        self.assertTrue(all("A related system" not in section for item in pages[1]["blocks"] for section in item["section_path"]))
        real_heading, _ = order_pages([page(1, [block("r0", "5\nConclusion.", 300, 710, 550, 770)], width=600, height=800)])
        self.assertEqual(real_heading[0]["blocks"][0]["kind"], "heading")

    def test_many_short_plot_labels_do_not_dominate_font_reference(self) -> None:
        graph = []
        for index in range(17):
            raw = block("r{}".format(index), "{} model-label performance".format(index + 70), 100, 190 + index * 8, 260, 197 + index * 8)
            raw["font_size_max"] = 7.77
            graph.append(raw)
        paragraphs = []
        for index in range(6):
            raw = block("r{}".format(index + 30), "This is a full paragraph of measured scientific text with enough actual characters to represent the dominant body font rather than the short labels inside the plotted area.", 100, 360 + index * 45, 900, 385 + index * 45)
            raw["font_size_max"] = 9.96
            paragraphs.append(raw)
        caption = block("r40", "Figure 2: The complete plot caption.", 100, 700, 900, 720)
        caption["font_size_max"] = 9.96
        pages, _ = order_pages([page(1, graph + paragraphs + [caption])])
        caption_output = next(item for item in pages[0]["blocks"] if item["text"].startswith("Figure 2:"))
        self.assertEqual(caption_output["section_path"], [])
        for item in pages[0]["blocks"]:
            if item["text"].endswith("model-label performance"):
                self.assertEqual(item["kind"], "body")

    def test_crossing_table_bbox_does_not_capture_following_heading(self) -> None:
        graph = block("r0", "70 ViT-H/14 BiT-L", 180, 285, 490, 300)
        graph["font_size_max"] = 7.77
        table = block("r1", "synthetic | overlapping | candidate", 136, 290, 494, 650)
        table.update({"original_kind": "table", "rows": [["synthetic", "overlapping", "candidate"]], "table_warning": "Unverified candidate"})
        caption = block("r2", "Figure 2: Breakdown of performance.", 112, 391, 497, 402)
        caption["font_size_max"] = 9.96
        before = block("r3", "A full paragraph that belongs before the real subsection.", 108, 415, 504, 490)
        following = block("r4", "A second full paragraph that also precedes the subsection.", 108, 497, 504, 551)
        heading = block("r5", "4.3 PRE-TRAINING DATA REQUIREMENTS", 108, 567, 292, 578)
        after = block("r6", "Supporting body text that follows the subsection heading.", 108, 626, 504, 665)
        for raw in (before, following, heading, after):
            raw["font_size_max"] = 9.96
        pages, _ = order_pages([page(1, [graph, table, caption, before, following, heading, after], width=612, height=792)])
        ordered = [item["text"] for item in pages[0]["blocks"]]
        self.assertLess(ordered.index(caption["text"]), ordered.index(before["text"]))
        self.assertLess(ordered.index(before["text"]), ordered.index(following["text"]))
        self.assertLess(ordered.index(following["text"]), ordered.index(heading["text"]))
        self.assertLess(ordered.index(heading["text"]), ordered.index(after["text"]))
        caption_output = next(item for item in pages[0]["blocks"] if item["text"] == caption["text"])
        candidate_output = next(item for item in pages[0]["blocks"] if item["original_kind"] == "table")
        self.assertEqual(caption_output["section_path"], [])
        self.assertTrue(candidate_output["layout_barrier_excluded"])
        self.assertTrue(any("table_barrier_excluded" in warning for warning in pages[0]["warnings"]))

        actual_table = block("r1", "Model | Result\nA | 1", 60, 80, 940, 300)
        actual_table.update({"original_kind": "table", "rows": [["Model", "Result"], ["A", "1"]], "table_warning": "Unverified candidate"})
        actual_caption = block("r2", "Figure 3: A separate downstream illustration.", 60, 350, 940, 380)
        valid_pages, _ = order_pages([page(1, [actual_table, actual_caption])])
        self.assertEqual(valid_pages[0]["blocks"][0]["original_kind"], "table")
        self.assertNotIn("layout_barrier_excluded", valid_pages[0]["blocks"][0])


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
        for path in (self.layout_path, self.poppler_path):
            document = read_json(path)
            first, last = document["pages"][0]["blocks"][0], document["pages"][0]["blocks"][-1]
            self.assertEqual(first["kind"], "header")
            self.assertEqual(last["kind"], "footer")
            self.assertEqual(first["source_text"], first["raw_text"])
            self.assertEqual(last["source_text"], last["raw_text"])
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


class OrderEvaluationAccountingTest(unittest.TestCase):
    def test_page_after_a_missing_block_is_still_scored(self) -> None:
        document = {"pages": [
            {"page_number": 1, "blocks": [{"kind": "body", "text": "alpha"}, {"kind": "body", "text": "gamma"}]},
            {"page_number": 2, "blocks": [{"kind": "body", "text": "one"}, {"kind": "body", "text": "two"}]},
        ]}
        gold = {"case_id": "synthetic", "pages": {"1": ["alpha", "beta", "gamma"], "2": ["one", "two"]}}
        report = evaluate_order_object(document, gold)
        self.assertFalse(report["valid_gold_match"])
        self.assertFalse(report["document_exact_match"])
        self.assertEqual([item["page_number"] for item in report["missing"]], [1])
        self.assertEqual([item["text"] for item in report["missing"]], ["beta"])
        self.assertEqual([page["page_number"] for page in report["pages"]], [2])
        self.assertEqual(report["pages"][0]["order_accuracy"], 1.0)
        self.assertEqual(report["comparable_pairs"], 1)
        self.assertEqual(report["exact_page_count"], 1)


class BlockClassificationTest(unittest.TestCase):
    def test_numbered_sentences_and_fragments_are_not_headings(self) -> None:
        samples = [
            "3. Is the software that was used to preprocess/clean/label the data available?",
            "2. Llama 2-Chat, a fine-tuned version of Llama 2 that is optimized for dialogue use cases.",
            "2 of the first quantization as inputs to a second quantization.",
        ]
        for text in samples:
            item = _classify({"text": text, "original_kind": "body"})
            self.assertEqual(item["kind"], "body", text)
            self.assertIsNone(item.get("heading_level"))

    def test_math_fragments_are_formula_not_headings(self) -> None:
        for text in ["2 , WNF4) + XBF16LBF16", "1 , ck-bit", "\x12 i + 1", "1 LBF16"]:
            item = _classify({"text": text, "original_kind": "body"})
            self.assertEqual(item["kind"], "formula", text)
            self.assertTrue(
                any(warning.startswith("formula_fragment_unverified") for warning in item["warnings"]),
                item["warnings"],
            )

    def test_numeric_table_row_is_not_a_formula(self) -> None:
        item = _classify({"text": "7B 2k ✗ 1.0T 3.0 × 10−4", "original_kind": "body"})
        self.assertEqual(item["kind"], "body")

    def test_appendix_headings_get_levels_and_section_path(self) -> None:
        source = page(47, [
            block("r0", "A.2 Additional Details for Pretraining", 50, 50, 500, 66),
            block("r1", "A.2.1 Architecture Changes Compared to Llama 1", 50, 80, 500, 96),
            block("r2", "Grouped-Query Attention. A standard practice is to cache keys only.", 50, 110, 500, 126),
        ])
        pages, _ = order_pages([source])
        sections = [item["kind"] for item in pages[0]["blocks"]]
        self.assertEqual(sections[:2], ["heading", "heading"])
        self.assertEqual(pages[0]["blocks"][0]["heading_level"], 2)
        self.assertEqual(pages[0]["blocks"][1]["heading_level"], 3)
        self.assertEqual(
            pages[0]["blocks"][2]["section_path"],
            ["A.2 Additional Details for Pretraining", "A.2.1 Architecture Changes Compared to Llama 1"],
        )

    def test_table_blocks_keep_one_line_per_row(self) -> None:
        item = _classify({
            "text": "65B 99.3 4.4 41GB 33B 97.8 4.4 21GB",
            "original_kind": "table",
            "rows": [["", "MMLU", "Squad", "Memory"], ["65B", "99.3", "4.4", "41GB"], ["33B", "97.8", "4.4", "21GB"]],
        })
        self.assertEqual(item["kind"], "table")
        self.assertEqual(item["table_row_count"], 3)
        self.assertEqual(item["table_column_count"], 4)
        lines = item["text"].split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1], "65B | 99.3 | 4.4 | 41GB")
        self.assertIn("table_warning", item)


if __name__ == "__main__":
    unittest.main()
