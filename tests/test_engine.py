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

    def test_exclude_dirs_in_topdown_toml_skips_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "topdown.toml").write_text(
                'exclude_dirs = ["generated"]\n',
                encoding="utf-8",
            )
            gen_file = root / "generated" / "schema.py"
            gen_file.parent.mkdir(parents=True)
            gen_file.write_text(
                "def leaf(amount_cents: int) -> int:\n    return amount_cents\n\n"
                "def caller(amount_cents: int) -> int:\n    return leaf(amount_cents)\n",
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

    def test_swift_guard_else_if_and_rust_grouped_use_boundaries(self) -> None:
        from engine.analyzer import analyze_source
        from engine.boundaries import BoundaryConfig, check_file_boundaries

        swift_flat = (
            "func classify(value: Int, active: Bool) -> String {\n"
            "    guard active else { return \"off\" }\n"
            "    if value > 100 {\n"
            "        return \"high\"\n"
            "    } else if value > 50 {\n"
            "        return \"mid\"\n"
            "    } else if value > 10 {\n"
            "        return \"low\"\n"
            "    }\n"
            "    return \"zero\"\n"
            "}\n"
        )
        self.assertEqual(analyze_source(swift_flat, "classify.swift"), [])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            rust_file = root / "src" / "domain" / "calc.rs"
            rust_file.parent.mkdir(parents=True)
            rust_source = (
                "use std::{fs, net::TcpStream};\n"
                "use crate::adapters::db;\n"
                "use capital::metrics;\n"
            )
            rust_file.write_text(rust_source, encoding="utf-8")
            cfg = BoundaryConfig(
                root_dir=root,
                pure_dirs=("src/domain",),
                shell_dirs=("src/adapters", "api"),
                extra_io_imports=(),
                use_default_io_imports=True,
            )
            violations = check_file_boundaries(rust_source, rust_file, cfg)
            self.assertEqual(len(violations), 3)
            self.assertFalse(any("capital" in entry.message for entry in violations))

    def test_tier_signposts_and_outline_across_languages(self) -> None:
        from engine.analyzer import analyze_source, outline_source

        ts_bad_l3 = (
            "// [L3] Wrong leaf tag\n"
            "/**\n"
            " * Multi-line JSDoc comment\n"
            " */\n"
            "export function caller(amount: number): number {\n"
            "  return leaf(amount);\n"
            "}\n"
            "function leaf(amount: number): number {\n"
            "  return amount + 1;\n"
            "}\n"
        )
        rust_stacked_ignore = (
            '// topdown-ignore(CANON-05, reason="legacy [L1] branch")\n'
            "// [L0] Entrypoint\n"
            "#[inline]\n"
            "pub fn check(a: bool, b: bool, c: bool) -> i32 {\n"
            "    if a { if b { if c { return 1; } } }\n"
            "    0\n"
            "}\n"
        )
        self.assertIn("CANON-01", [v.rule for v in analyze_source(ts_bad_l3, "bad.ts")])
        self.assertEqual(analyze_source(rust_stacked_ignore, "ok.rs"), [])
        rust_adjacent_leak = (
            'pub fn first() -> i32 { 1 } // topdown-ignore(CANON-05, reason="only first")\n'
            "pub fn second(a: bool, b: bool, c: bool) -> i32 {\n"
            "    if a { if b { if c { return 1; } } }\n"
            "    0\n"
            "}\n"
        )
        self.assertIn("CANON-05", [v.rule for v in analyze_source(rust_adjacent_leak, "leak.rs")])

        ts_after = (ROOT_DIR / "examples" / "fixtures" / "ts_after.ts").read_text(encoding="utf-8")
        outline_entries = outline_source(ts_after, "ts_after.ts")
        self.assertEqual([e["name"] for e in outline_entries], [
            "processPayoutBatch",
            "settleEligiblePayouts",
            "calculateFeeCents",
            "formatCentsUsd",
        ])
        self.assertEqual(outline_entries[0]["declared_tier"], "L0")
        self.assertEqual(outline_entries[0]["valid_tiers"], ["L0"])

        proc = subprocess.run(
            [sys.executable, "-m", "engine.cli", "--outline", str(ROOT_DIR / "examples" / "fixtures" / "python_after.py")],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("[L0] process_payout_batch (L37-L55, 19L) -> format_cents_usd, settle_eligible_payouts", proc.stdout)


if __name__ == "__main__":
    unittest.main()
