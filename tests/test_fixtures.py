from __future__ import annotations

import hashlib
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.make_fixtures import create_advanced_fixtures, create_basic_fixtures


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".work" / "tests" / "m0-fixtures"


class FixtureGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(parents=True)
        create_basic_fixtures(WORK)
        cls.advanced_warnings = create_advanced_fixtures(WORK)

    def test_poppler_reads_real_generated_pdf(self) -> None:
        executable = shutil.which("pdftotext")
        self.assertIsNotNone(executable, "M0 requires the detected local Poppler")
        pdf = WORK / "basic" / "two-column.pdf"
        text_output = WORK / "basic" / "poppler.txt"
        result = subprocess.run(
            [str(executable), str(pdf), str(text_output)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        extracted = text_output.read_text(encoding="utf-8")
        for expected in (
            "A FULL WIDTH SECTION TITLE FOR THE TEST DOCUMENT",
            "L1 Left column opening statement.",
            "R2 Right column continuing note.",
            "S2 The section ends with an acknowledged limitation.",
        ):
            self.assertIn(expected, extracted)

    def test_generation_is_deterministic(self) -> None:
        first = WORK / "basic" / "two-column.pdf"
        second_root = WORK / "repeat"
        create_basic_fixtures(second_root)
        second = second_root / "basic" / "two-column.pdf"
        self.assertEqual(
            hashlib.sha256(first.read_bytes()).hexdigest(),
            hashlib.sha256(second.read_bytes()).hexdigest(),
        )

    def test_current_environment_generates_advanced_failures(self) -> None:
        self.assertEqual(self.advanced_warnings, [])
        for name in ("scanned.pdf", "encrypted.pdf", "corrupt.pdf"):
            path = WORK / "advanced" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 0, name)


if __name__ == "__main__":
    unittest.main()
