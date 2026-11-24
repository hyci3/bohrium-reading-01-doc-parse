from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from tests._support import SKILL_ROOT, TEST_WORK
from pdf_evidence.capabilities import capabilities_command, detect_capabilities
from pdf_evidence.validators import validate_package


class CapabilityDetectionTest(unittest.TestCase):
    def test_actual_development_capabilities(self) -> None:
        output = TEST_WORK / "m1" / "capabilities.json"
        code = capabilities_command(None, output)
        self.assertEqual(code, 0)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["preferred_extractor"], "pymupdf")
        self.assertTrue(report["pymupdf"]["available"])
        self.assertTrue(report["tools"]["pdftotext"]["available"])
        self.assertTrue(report["tools"]["pdftoppm"]["available"])
        self.assertIn("version", report["python"])

    def test_no_extractor_is_reported_with_nonzero_exit(self) -> None:
        output = TEST_WORK / "m1" / "capabilities-none.json"
        with mock.patch("pdf_evidence.capabilities.shutil.which", return_value=None), mock.patch(
            "pdf_evidence.capabilities.importlib.util.find_spec", return_value=None
        ):
            code = capabilities_command(None, output)
        self.assertEqual(code, 3)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertIsNone(report["preferred_extractor"])
        self.assertEqual(report["status"], "extractor_unavailable")

    def test_resource_file_is_hashed_but_not_treated_as_executable_proof(self) -> None:
        resources = TEST_WORK / "m1" / "resources.yaml"
        resources.parent.mkdir(parents=True, exist_ok=True)
        resources.write_text("tools:\n  - name: pdftotext\n", encoding="utf-8")
        report = detect_capabilities(resources, TEST_WORK / "m1")
        self.assertTrue(report["resources"]["found"])
        self.assertIn("pdftotext", report["resources"]["tool_hints"])
        self.assertEqual(len(report["resources"]["sha256"]), 64)


class PackageValidationTest(unittest.TestCase):
    def test_current_minimal_package_is_valid(self) -> None:
        report = validate_package(SKILL_ROOT)
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["valid"])
        self.assertGreater(report["file_count"], 0)

    def test_invalid_metadata_and_utf8_are_rejected(self) -> None:
        bad = TEST_WORK / "m1" / "bad-skill"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "SKILL.md").write_text("---\nname: Bad_Name\n---\nBody\n", encoding="utf-8")
        (bad / "binary.bin").write_bytes(b"\xff\xfe")
        report = validate_package(bad)
        self.assertFalse(report["valid"])
        self.assertTrue(any("frontmatter" in error.lower() for error in report["errors"]))
        self.assertTrue(any("UTF-8" in error for error in report["errors"]))

    def test_machine_specific_path_is_rejected(self) -> None:
        bad = TEST_WORK / "m1" / "another-skill"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "SKILL.md").write_text(
            "---\nname: another-skill\ndescription: Test skill\n---\nUse D:\\secret\\paper.pdf\n",
            encoding="utf-8",
        )
        report = validate_package(bad)
        self.assertFalse(report["valid"])
        self.assertTrue(any("Machine-specific" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
