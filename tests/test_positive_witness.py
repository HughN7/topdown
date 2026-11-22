import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
WITNESS_DIR = ROOT_DIR / "tests" / "positive_witness"
sys.path.insert(0, str(ROOT_DIR / "enforcement"))


class TestPositiveWitness(unittest.TestCase):
    def test_python_idioms_pass_with_zero_violations_and_zero_waivers(self) -> None:
        import check_python

        target = WITNESS_DIR / "python_idioms.py"
        source = target.read_text(encoding="utf-8")
        self.assertNotIn("topdown-ignore", source)
        self.assertEqual(check_python.check_source(source, str(target)), [])

    def test_rust_idioms_pass_with_zero_violations_and_zero_waivers(self) -> None:
        import check_rust

        target = WITNESS_DIR / "rust_idioms.rs"
        source = target.read_text(encoding="utf-8")
        self.assertNotIn("topdown-ignore", source)
        self.assertEqual(check_rust.check_source(source, str(target)), [])


if __name__ == "__main__":
    unittest.main()
