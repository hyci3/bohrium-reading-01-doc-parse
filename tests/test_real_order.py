from __future__ import annotations

import unittest

from tests._support import ROOT, TEST_WORK
from pdf_evidence.case_io import read_json
from pdf_evidence.extractors import extract_command


class RealReadingOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TEST_WORK / "m4-real-order"
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.capabilities = TEST_WORK / "m3-integration" / "capabilities.json"
        cls.attention_layout = cls.root / "attention-layout.json"
        assert extract_command(
            TEST_WORK / "m3-integration" / "real-attention-plan.json",
            TEST_WORK / "m3-integration" / "real-attention-probe.json",
            cls.capabilities,
            cls.root / "attention",
            cls.attention_layout,
            "layout",
        ) == 0
        cls.resnet_layout = cls.root / "resnet-layout.json"
        cls.resnet_naive = cls.root / "resnet-naive.json"
        for mode, path in (("layout", cls.resnet_layout), ("naive", cls.resnet_naive)):
            assert extract_command(
                ROOT / ".work" / "m4-resnet" / "plan.json",
                ROOT / ".work" / "m4-resnet" / "probe.json",
                cls.capabilities,
                cls.root / mode,
                path,
                mode,
            ) == 0

    def test_true_two_column_real_page_matches_visual_gold(self) -> None:
        # Verified against the rendered page: left column (3, 3.1, 3.2), then right column (3.3).
        expected_raw_ids = [f"r{index:04d}" for index in range(22)]
        page = read_json(self.resnet_layout)["pages"][2]
        actual = [block["raw_id"] for block in page["blocks"] if block["kind"] not in {"footer", "header"}]
        self.assertEqual(actual, expected_raw_ids)
        self.assertEqual(len(actual), 22)
        self.assertEqual(
            [block["text"] for block in page["blocks"] if block["raw_id"] == "r0006"][0],
            "3.2. Identity Mapping by Shortcuts",
        )
        right_section = next(block for block in page["blocks"] if block["raw_id"] == "r0018")
        self.assertIn("Network Architectures", right_section["text"])

    def test_true_two_column_naive_baseline_is_actually_wrong(self) -> None:
        naive = read_json(self.resnet_naive)["pages"][2]["blocks"]
        by_id = {block["raw_id"]: index for index, block in enumerate(naive)}
        self.assertLess(by_id["r0011"], by_id["r0001"])
        self.assertLess(by_id["r0018"], by_id["r0006"])
        self.assertEqual(naive[0]["text"], "way networks have not demonstrated accuracy gains with extremely increased depth (e.g., over 100 layers).")

    def test_single_column_overlapping_formula_stays_in_its_section(self) -> None:
        page = read_json(self.attention_layout)["pages"][4]
        by_id = {block["raw_id"]: index for index, block in enumerate(page["blocks"])}
        self.assertLess(by_id["r0007"], by_id["r0011"])
        self.assertLess(by_id["r0008"], by_id["r0011"])
        self.assertLess(by_id["r0009"], by_id["r0011"])
        self.assertLess(by_id["r0018"], by_id["r0019"])
        self.assertLess(by_id["r0019"], by_id["r0020"])


if __name__ == "__main__":
    unittest.main()
