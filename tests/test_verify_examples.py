import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "examples"))


class TestVerifyExamples(unittest.TestCase):
    def test_format_summary_table_renders_markdown(self) -> None:
        import verify_examples

        rows = [
            verify_examples.ExampleRow(language="Python", before_count=6, after_count=0, status="PASS"),
            verify_examples.ExampleRow(language="TypeScript", before_count=4, after_count=0, status="PASS"),
        ]
        table = verify_examples.format_summary_table(rows)
        self.assertIn("| Language | Before Violations | After Violations | Delta | Status |", table)
        self.assertIn("| Python | 6 | 0 | -6 | PASS |", table)

    def test_verify_examples_cli_passes_on_repo_fixtures(self) -> None:
        script = ROOT_DIR / "examples" / "verify_examples.py"
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
        self.assertIn("| Python |", proc.stdout)
        self.assertIn("| Rust |", proc.stdout)
        self.assertIn("| TypeScript |", proc.stdout)


if __name__ == "__main__":
    unittest.main()
