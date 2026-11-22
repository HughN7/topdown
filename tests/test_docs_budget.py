import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MAX_BASE_LINES = 50
MAX_COMBINED_LINES = 150


class TestDocsBudget(unittest.TestCase):
    def test_core_base_stays_within_line_budget(self) -> None:
        base_lines = len((ROOT_DIR / "core" / "base.md").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            base_lines,
            MAX_BASE_LINES,
            msg=f"core/base.md is {base_lines} lines (max {MAX_BASE_LINES})",
        )

    def test_base_plus_any_language_and_zone_stays_within_budget(self) -> None:
        base_lines = len((ROOT_DIR / "core" / "base.md").read_text(encoding="utf-8").splitlines())
        lang_files = sorted((ROOT_DIR / "languages").glob("*.md"))
        zone_files = sorted((ROOT_DIR / "zones").glob("*.md"))
        self.assertTrue(lang_files, "Expected at least one language pack")
        self.assertTrue(zone_files, "Expected at least one zone profile")
        for lang_file in lang_files:
            lang_lines = len(lang_file.read_text(encoding="utf-8").splitlines())
            for zone_file in zone_files:
                zone_lines = len(zone_file.read_text(encoding="utf-8").splitlines())
                total = base_lines + lang_lines + zone_lines
                self.assertLessEqual(
                    total,
                    MAX_COMBINED_LINES,
                    msg=f"base + {lang_file.name} + {zone_file.name} = {total} lines (max {MAX_COMBINED_LINES})",
                )


if __name__ == "__main__":
    unittest.main()
