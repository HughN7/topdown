import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

HAS_TREE_SITTER = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_language_pack") is not None
)


@unittest.skipUnless(HAS_TREE_SITTER, "Optional tree-sitter engine dependencies not installed")
class TestUniversalEngine(unittest.TestCase):
    def test_python_typescript_rust_swift_go_clean_modules_pass(self) -> None:
        from engine.analyzer import analyze_source

        samples = {
            "clean.py": (
                "def orchestrate(amount_cents: int) -> str:\n"
                "    net_cents = apply_fee(amount_cents)\n"
                "    return format_usd(net_cents)\n\n"
                "def apply_fee(amount_cents: int) -> int:\n"
                "    return amount_cents - 50\n\n"
                "def format_usd(amount_cents: int) -> str:\n"
                '    return f"${amount_cents / 100:.2f}"\n'
            ),
            "clean.ts": (
                "export function orchestrate(amountCents: number): string {\n"
                "  return formatUsd(amountCents - 50);\n"
                "}\n\n"
                "function formatUsd(amountCents: number): string {\n"
                "  return `$${(amountCents / 100).toFixed(2)}`;\n"
                "}\n"
            ),
            "clean.rs": (
                "pub fn orchestrate(amount_cents: i64) -> i64 {\n"
                "    apply_fee(amount_cents)\n"
                "}\n\n"
                "fn apply_fee(amount_cents: i64) -> i64 {\n"
                "    amount_cents - 50\n"
                "}\n"
            ),
            "clean.swift": (
                "func orchestrate(amountCents: Int) -> Int {\n"
                "    return applyFee(amountCents: amountCents)\n"
                "}\n\n"
                "func applyFee(amountCents: Int) -> Int {\n"
                "    return amountCents - 50\n"
                "}\n"
            ),
            "clean.go": (
                "package payout\n\n"
                "func Orchestrate(amountCents int) int {\n"
                "    return applyFee(amountCents)\n"
                "}\n\n"
                "func applyFee(amountCents int) int {\n"
                "    return amountCents - 50\n"
                "}\n"
            ),
        }
        for filename, source in samples.items():
            self.assertEqual(analyze_source(source, filename), [], msg=f"Failed on {filename}")

    def test_topdown_order_and_nesting_and_anti_gaming_across_languages(self) -> None:
        from engine.analyzer import analyze_source

        swift_out_of_order = (
            "func applyFee(amountCents: Int) -> Int {\n"
            "    return amountCents - 50\n"
            "}\n\n"
            "func orchestrate(amountCents: Int) -> Int {\n"
            "    return applyFee(amountCents: amountCents)\n"
            "}\n"
        )
        rust_deep_and_gaming = (
            "pub fn process_part2(flag_a: bool, flag_b: bool, flag_c: bool) -> i64 {\n"
            "    if flag_a {\n"
            "        if flag_b {\n"
            "            if flag_c {\n"
            "                return 1;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        go_out_of_order = (
            "package payout\n\n"
            "func applyFee(amountCents int) int {\n"
            "    return amountCents - 50\n"
            "}\n\n"
            "func Orchestrate(amountCents int) int {\n"
            "    return applyFee(amountCents)\n"
            "}\n"
        )
        self.assertIn("CANON-01", [v.rule for v in analyze_source(swift_out_of_order, "bad.swift")])
        rust_rules = [v.rule for v in analyze_source(rust_deep_and_gaming, "bad.rs")]
        self.assertIn("CANON-05", rust_rules)
        self.assertIn("LLM-05", rust_rules)
        self.assertIn("CANON-01", [v.rule for v in analyze_source(go_out_of_order, "bad.go")])

    def test_topdown_ignore_suppresses_with_reason_and_flags_without(self) -> None:
        from engine.analyzer import analyze_source

        suppressed = (
            '// topdown-ignore(CANON-01, reason="C ABI layout order")\n'
            "func applyFee(amountCents: Int) -> Int {\n"
            "    return amountCents - 50\n"
            "}\n\n"
            '// topdown-ignore(CANON-01, reason="C ABI layout order")\n'
            "func orchestrate(amountCents: Int) -> Int {\n"
            "    return applyFee(amountCents: amountCents)\n"
            "}\n"
        )
        missing_reason = (
            "// topdown-ignore(CANON-01)\n"
            "func applyFee(amountCents: Int) -> Int { return amountCents }\n"
        )
        self.assertEqual(analyze_source(suppressed, "ok.swift"), [])
        self.assertIn("CANON-08", [v.rule for v in analyze_source(missing_reason, "bad.swift")])

    def test_no_boundary_assumptions_without_topdown_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            core_file = root / "src" / "core" / "db.py"
            core_file.parent.mkdir(parents=True)
            core_file.write_text(
                "import httpx\n\n"
                "def fetch_rate(currency: str) -> str:\n"
                "    return currency\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--json", str(root)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertEqual(json.loads(proc.stdout), [])

    def test_opt_in_topdown_toml_enforces_canon02_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "topdown.toml").write_text(
                "[boundaries]\n"
                'pure_dirs = ["src/domain"]\n'
                'shell_dirs = ["src/adapters"]\n'
                'extra_io_imports = ["stripe"]\n',
                encoding="utf-8",
            )
            domain_file = root / "src" / "domain" / "pricing.py"
            domain_file.parent.mkdir(parents=True)
            domain_file.write_text(
                "import httpx\n"
                "import stripe\n"
                "from src.adapters import ledger_db\n\n"
                "def compute_price(amount_cents: int) -> int:\n"
                "    return amount_cents\n",
                encoding="utf-8",
            )
            adapter_file = root / "src" / "adapters" / "ledger_db.py"
            adapter_file.parent.mkdir(parents=True)
            adapter_file.write_text(
                "import httpx\n\n"
                "def save_entry(amount_cents: int) -> int:\n"
                "    return amount_cents\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--json", str(root)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stderr + proc.stdout)
            violations = json.loads(proc.stdout)
            canon02 = [entry for entry in violations if entry["rule"] == "CANON-02"]
            self.assertEqual(len(canon02), 3)
            self.assertTrue(all("pricing.py" in entry["path"] for entry in canon02))


if __name__ == "__main__":
    unittest.main()
