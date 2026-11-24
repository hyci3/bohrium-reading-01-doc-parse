from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "evidence-pdf-reader"
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = ROOT / ".work" / "fixtures"
TEST_WORK = ROOT / ".work" / "tests"
TEST_WORK.mkdir(parents=True, exist_ok=True)
