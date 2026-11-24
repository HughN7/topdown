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

    def test_block_comment_topdown_ignore_does_not_trigger_canon08(self) -> None:
        from engine.analyzer import analyze_source

        ts_jsdoc = (
            "/**\n"
            " * Documents topdown-ignore behavior without being a directive.\n"
            " */\n"
            "export function renderNote(text: string): string {\n"
            "  return text.trim();\n"
            "}\n"
        )
        rust_block = (
            "/* mentions topdown-ignore in a block comment */\n"
            "pub fn clean_note(raw_text: &str) -> &str {\n"
            "    raw_text.trim()\n"
            "}\n"
        )
        swift_multiline = (
            "/* mentions topdown-ignore in multiline comment */\n"
            "func cleanNote(rawText: String) -> String {\n"
            "    return rawText\n"
            "}\n"
        )
        self.assertEqual(analyze_source(ts_jsdoc, "jsdoc.ts"), [])
        self.assertEqual(analyze_source(rust_block, "block.rs"), [])
        self.assertEqual(analyze_source(swift_multiline, "multi.swift"), [])

    def test_ts_top_level_const_arrow_included_and_inline_callbacks_allowed(self) -> None:
        from engine.analyzer import analyze_source, outline_source

        arrows_out_of_order = (
            "// [L3] Leaf defined above caller\n"
            "const formatScore = (score: number): string => {\n"
            "  return `${score}pts`;\n"
            "};\n\n"
            "// [L0] Orchestrator\n"
            "export const\n"
            "  renderScore = (score: number): string => {\n"
            "    return formatScore(score);\n"
            "  };\n"
        )
        viols = analyze_source(arrows_out_of_order, "arrows.ts")
        order_viols = [v for v in viols if v.rule == "CANON-01" and "defined above it" in v.message]
        self.assertEqual(len(order_viols), 1)
        self.assertEqual(order_viols[0].line, 8)

        outline = outline_source(arrows_out_of_order, "arrows.ts")
        self.assertEqual([row["name"] for row in outline], ["formatScore", "renderScore"])
        self.assertEqual(outline[1]["start_line"], 8)
        self.assertEqual(outline[1]["declared_tier"], "L0")
        self.assertEqual(outline[1]["callees"], ["formatScore"])
        self.assertEqual(outline[0]["callers"], ["renderScore"])

        inline_callback_clean = (
            "export const normalizeNames = (names: string[]): string[] => {\n"
            "  return names.map((entry) => {\n"
            "    return entry.trim().toLowerCase();\n"
            "  });\n"
            "};\n"
        )
        self.assertEqual(analyze_source(inline_callback_clean, "inline.ts"), [])

        js_arrow_clean = (
            "// [L0] Entry\n"
            "export const runTask = (items) => {\n"
            "  return items.map((val) => val + 1);\n"
            "};\n"
        )
        self.assertEqual(analyze_source(js_arrow_clean, "clean.mjs"), [])
        self.assertEqual([row["name"] for row in outline_source(js_arrow_clean, "clean.mjs")], ["runTask"])

    def test_python_overload_excluded_and_headers_absorbed(self) -> None:
        from engine.analyzer import analyze_source, outline_source

        overloaded = (
            "import typing\n"
            "from typing import overload\n\n"
            "# [L0] Orchestrator\n"
            "def run_pipeline(raw_value: str) -> str:\n"
            "    return parse_value(raw_value)\n\n"
            "# [L3] Leaf parser\n"
            '# topdown-ignore(CANON-05, reason="legacy overloaded branch")\n'
            "@overload\n"
            "def parse_value(val: int, flag_a: bool, flag_b: bool, flag_c: bool) -> int: ...\n"
            "@typing.overload\n"
            "def parse_value(val: str, flag_a: bool, flag_b: bool, flag_c: bool) -> str: ...\n"
            "def parse_value(val: int | str, flag_a: bool = False, flag_b: bool = False, flag_c: bool = False) -> int | str:\n"
            "    if flag_a:\n"
            "        if flag_b:\n"
            "            if flag_c:\n"
            "                return val\n"
            "    return val\n\n"
            "class Formatter:\n"
            '    # topdown-ignore(CANON-05, reason="legacy method overload")\n'
            "    @overload\n"
            "    def format_cell(self, raw: int, f1: bool, f2: bool, f3: bool) -> str: ...\n"
            "    @typing.overload\n"
            "    def format_cell(self, raw: str, f1: bool, f2: bool, f3: bool) -> str: ...\n"
            "    def format_cell(self, raw: int | str, f1: bool = False, f2: bool = False, f3: bool = False) -> str:\n"
            "        if f1:\n"
            "            if f2:\n"
            "                if f3:\n"
            "                    return str(raw)\n"
            "        return str(raw)\n"
        )
        self.assertEqual(analyze_source(overloaded, "ov.py"), [])
        outline = outline_source(overloaded, "ov.py")
        self.assertEqual([entry["name"] for entry in outline], ["run_pipeline", "parse_value"])
        self.assertEqual(outline[1]["declared_tier"], "L3")

        conflicting_tags = (
            "from typing import overload\n\n"
            "# [L3] Stub header tag\n"
            "@overload\n"
            "def parse_value(val: int) -> int: ...\n"
            "# [L2] Conflicting impl tag\n"
            "def parse_value(val: int | str) -> int | str:\n"
            "    return val\n"
        )
        self.assertIn("CANON-01", [v.rule for v in analyze_source(conflicting_tags, "ov_dup.py")])

        non_contiguous = (
            "from typing import overload\n\n"
            "# [L3] Interrupted header\n"
            "@overload\n"
            "def parse_value(val: int) -> int: ...\n\n"
            "SENTINEL = 1\n\n"
            "def parse_value(val: int | str) -> int | str:\n"
            "    return val\n"
        )
        nc_outline = outline_source(non_contiguous, "ov_nc.py")
        self.assertIsNone(nc_outline[0]["declared_tier"])

        decorator_calls_excluded = (
            "def make_decorator(config: dict[str, str]):\n"
            "    return lambda fn: fn\n\n"
            "def build_config() -> dict[str, str]:\n"
            '    return {"mode": "strict"}\n\n'
            "def make_type() -> type[str]:\n"
            "    return str\n\n"
            "# [L0] Orchestrator\n"
            "@make_decorator(build_config())\n"
            "def run_handler(\n"
            "    raw_text: make_type(),\n"
            "    suffix: str = default_suffix(),\n"
            "    *,\n"
            "    prefix: str = default_prefix(),\n"
            ") -> make_type():\n"
            "    return prefix + finish_task(raw_text) + suffix\n\n"
            "# [L3] Leaf\n"
            "@make_decorator(build_config())\n"
            "def finish_task(raw_text: make_type()) -> make_type():\n"
            "    return raw_text.strip()\n\n"
            "# [L3] Default suffix\n"
            "def default_suffix() -> str:\n"
            '    return "!"\n\n'
            "# [L3] Default prefix\n"
            "def default_prefix() -> str:\n"
            '    return ">"\n'
        )
        self.assertEqual(analyze_source(decorator_calls_excluded, "dec_calls.py"), [])
        dec_outline = outline_source(decorator_calls_excluded, "dec_calls.py")
        handler_row = next(row for row in dec_outline if row["name"] == "run_handler")
        finish_row = next(row for row in dec_outline if row["name"] == "finish_task")
        self.assertEqual(handler_row["callees"], ["default_prefix", "default_suffix", "finish_task"])
        self.assertEqual(finish_row["callees"], [])

    def test_multiline_attribute_and_same_line_close_guard(self) -> None:
        from engine.analyzer import analyze_source, collect_contiguous_header_lines

        ts_between_decorators = (
            "export class Handler {\n"
            "  @autobind\n"
            '  // topdown-ignore(CANON-05, reason="legacy nesting")\n'
            "  @Decorator({\n"
            '    path: "a)b}c]", // trailing ] ) }\n'
            "    items: [1, 2],\n"
            "  })\n"
            "  public async handle(flagA: boolean, flagB: boolean, flagC: boolean): Promise<number> {\n"
            "    if (flagA) {\n"
            "      if (flagB) {\n"
            "        if (flagC) {\n"
            "          return 1;\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "    return 0;\n"
            "  }\n"
            "}\n"
        )
        rust_line1_attr = (
            '#[inline] // topdown-ignore(CANON-05, reason="inline attr at line 1")\n'
            "pub fn check(flag_a: bool, flag_b: bool, flag_c: bool) -> i64 {\n"
            "    if flag_a { if flag_b { if flag_c { return 1; } } }\n"
            "    0\n"
            "}\n"
        )
        rust_post_prev_end = (
            "pub fn first() -> i64 {\n"
            "    1\n"
            "}\n"
            "#[cfg_attr(\n"
            "    all(),\n"
            '    inline // topdown-ignore(CANON-05, reason="post prev_end attr")\n'
            ")]\n"
            "pub fn second(flag_a: bool, flag_b: bool, flag_c: bool) -> i64 {\n"
            "    if flag_a { if flag_b { if flag_c { return 1; } } }\n"
            "    0\n"
            "}\n"
        )
        same_line_close_no_leak = (
            '// topdown-ignore(CANON-05, reason="only first")\n'
            "export function first(): number {\n"
            "  return 1;\n"
            "} export function second(flagA: boolean, flagB: boolean, flagC: boolean): number {\n"
            "  if (flagA) { if (flagB) { if (flagC) { return 1; } } }\n"
            "  return 0;\n"
            "}\n"
        )
        single_line_suppressed = (
            '# topdown-ignore(LLM-05, reason="ok")\n'
            "def foo_part1() -> int: return 1\n"
        )
        single_line_preceded = (
            "def alpha() -> int: return 0\n"
            '# topdown-ignore(LLM-05, reason="ok")\n'
            "def beta_part2() -> int: return 2\n"
        )
        self.assertEqual(analyze_source(ts_between_decorators, "dec.ts"), [])
        self.assertEqual(analyze_source(rust_line1_attr, "line1.rs"), [])
        self.assertEqual(analyze_source(rust_post_prev_end, "post_prev.rs"), [])
        self.assertEqual(
            collect_contiguous_header_lines(
                rust_post_prev_end.splitlines(), 8, prev_end_line=3, comment_lines=set(), lang_name="rust"
            ),
            {4, 5, 6, 7},
        )
        leak_viols = analyze_source(same_line_close_no_leak, "same_line_leak.ts")
        self.assertEqual([(v.line, v.rule) for v in leak_viols], [(4, "CANON-05")])
        self.assertEqual(analyze_source(single_line_suppressed, "single_line.py"), [])
        self.assertEqual(analyze_source(single_line_preceded, "single_line_prev.py"), [])

    def test_tier_tags_no_double_diagnostic_no_l4_and_l0_adapter_outline_json(self) -> None:
        from engine.analyzer import analyze_source, outline_source

        l0_adapter_and_gap = {
            "ok.py": (
                "# [L0] Adapter Entrypoint\n"
                "def run_adapter(raw_text: str) -> str:\n"
                "    return write_log(raw_text)\n\n"
                "# [L0] Adapter Leaf\n"
                "def write_log(raw_text: str) -> str:\n"
                "    return raw_text.strip()\n\n"
                "# [L0] Gap Entrypoint\n"
                "def run_gap(raw_text: str) -> str:\n"
                "    return normalize_text(raw_text)\n\n"
                "# [L3] Leaf\n"
                "def normalize_text(raw_text: str) -> str:\n"
                "    return raw_text.lower()\n"
            ),
            "ok.ts": (
                "// [L0] Adapter Entrypoint\n"
                "export function runAdapter(rawText: string): string {\n"
                "  return writeLog(rawText);\n"
                "}\n"
                "// [L0] Adapter Leaf\n"
                "function writeLog(rawText: string): string {\n"
                "  return rawText.trim();\n"
                "}\n"
                "// [L0] Gap Entrypoint\n"
                "export function runGap(rawText: string): string {\n"
                "  return normalizeText(rawText);\n"
                "}\n"
                "// [L3] Leaf\n"
                "function normalizeText(rawText: string): string {\n"
                "  return rawText.toLowerCase();\n"
                "}\n"
            ),
            "ok.rs": (
                "// [L0] Adapter Entrypoint\n"
                "pub fn run_adapter(raw_text: &str) -> &str {\n"
                "    write_log(raw_text)\n"
                "}\n"
                "// [L0] Adapter Leaf\n"
                "fn write_log(raw_text: &str) -> &str {\n"
                "    raw_text.trim()\n"
                "}\n"
            ),
            "ok.swift": (
                "// [L0] Adapter Entrypoint\n"
                "func runAdapter(rawText: String) -> String {\n"
                "    return writeLog(rawText: rawText)\n"
                "}\n"
                "// [L0] Adapter Leaf\n"
                "func writeLog(rawText: String) -> String {\n"
                "    return rawText\n"
                "}\n"
            ),
            "ok.go": (
                "package adapter\n\n"
                "// [L0] Adapter Entrypoint\n"
                "func RunAdapter(rawText string) string {\n"
                "    return writeLog(rawText)\n"
                "}\n"
                "// [L0] Adapter Leaf\n"
                "func writeLog(rawText string) string {\n"
                "    return rawText\n"
                "}\n"
            ),
        }
        for filename, src in l0_adapter_and_gap.items():
            self.assertEqual(analyze_source(src, filename), [], msg=filename)

        gap_compressed = (
            "# [L1] Workflow\n"
            "def step_one(count: int) -> int:\n"
            "    return step_two(count)\n\n"
            "def step_two(count: int) -> int:\n"
            "    return step_three(count)\n\n"
            "# [L2] Impossible tier after untagged intermediate\n"
            "def step_three(count: int) -> int:\n"
            "    return count + 1\n"
        )
        self.assertIn("CANON-01", [v.rule for v in analyze_source(gap_compressed, "gap.py")])

        duplicate_tags = (
            "// [L0] First\n"
            "// [L1] Second\n"
            "export function solo(count: number): number {\n"
            "  return count;\n"
            "}\n"
        )
        self.assertIn("CANON-01", [v.rule for v in analyze_source(duplicate_tags, "dup_tag.ts")])

        l1_calling_l0 = (
            "// [L1] Pure workflow\n"
            "export function workflow(count: number): number {\n"
            "  return badLeaf(count);\n"
            "}\n"
            "// [L0] Invalid L0 below L1\n"
            "function badLeaf(count: number): number {\n"
            "  return count + 1;\n"
            "}\n"
        )
        single_diag = [v for v in analyze_source(l1_calling_l0, "inv.ts") if v.rule == "CANON-01"]
        self.assertEqual(len(single_diag), 1)
        self.assertEqual(single_diag[0].line, 6)

        no_l4_chain = (
            "// [L2] Start\n"
            "pub fn mid_step(count: i64) -> i64 {\n"
            "    sub_step(count)\n"
            "}\n"
            "// [L2] Mid\n"
            "fn sub_step(count: i64) -> i64 {\n"
            "    inner_leaf(count)\n"
            "}\n"
            "// [L2] End\n"
            "fn inner_leaf(count: i64) -> i64 {\n"
            "    count + 1\n"
            "}\n"
        )
        no_l4_viols = [v for v in analyze_source(no_l4_chain, "nol4.rs") if v.rule == "CANON-01"]
        self.assertTrue(no_l4_viols)
        for viol in no_l4_viols:
            self.assertNotIn("L4", viol.message)
        leaf_msgs = [v.message for v in no_l4_viols if v.line == 10]
        self.assertEqual(len(leaf_msgs), 1)
        self.assertIn("mid_step", leaf_msgs[0])
        self.assertIn("[L2]", leaf_msgs[0])

        dup_names_py = (
            "# [L0] Entrypoint\n"
            "def run_job(amount_cents: int) -> int:\n"
            "    return render_value(amount_cents)\n\n"
            "# [L1] First variant\n"
            "def render_value(amount_cents: int) -> int:\n"
            "    return double_amount(amount_cents)\n\n"
            "# [L3] Second variant\n"
            "def render_value(amount_cents: int) -> int:\n"
            "    return amount_cents + 1\n\n"
            "# [L3] Leaf\n"
            "def double_amount(amount_cents: int) -> int:\n"
            "    return amount_cents * 2\n"
        )
        self.assertEqual(analyze_source(dup_names_py, "dup_names.py"), [])
        dup_outline = outline_source(dup_names_py, "dup_names.py")
        self.assertEqual(len(dup_outline), 4)
        self.assertEqual(dup_outline[0]["callees"], ["render_value"])
        self.assertEqual(dup_outline[1]["declared_tier"], "L1")
        self.assertEqual(dup_outline[1]["callers"], ["run_job"])
        self.assertEqual(dup_outline[1]["callees"], ["double_amount"])
        self.assertEqual(dup_outline[2]["declared_tier"], "L3")
        self.assertEqual(dup_outline[2]["callers"], ["run_job"])
        self.assertEqual(dup_outline[2]["callees"], [])
        self.assertEqual(dup_outline[3]["callers"], ["render_value"])

        dup_bad_anc = (
            "// [L2] First dup\n"
            "func dupStep(count int) int {\n"
            "    return badLeaf(count)\n"
            "}\n"
            "// [L0] Second dup\n"
            "func dupStep(count int) int {\n"
            "    return count\n"
            "}\n"
            "// [L2] Callee below [L2]\n"
            "func badLeaf(count int) int {\n"
            "    return count + 1\n"
            "}\n"
        )
        bad_msgs = [v.message for v in analyze_source(dup_bad_anc, "dup_anc.go") if v.rule == "CANON-01"]
        self.assertTrue(any("below 'dupStep' ([L2])" in msg for msg in bad_msgs), msg=bad_msgs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_swift = Path(tmp_dir) / "sample.swift"
            sample_swift.write_text(l0_adapter_and_gap["ok.swift"], encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--outline", "--json", str(sample_swift)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload[0]["functions"][0]["declared_tier"], "L0")
            self.assertEqual(payload[0]["functions"][0]["valid_tiers"], ["L0"])
            self.assertEqual(payload[0]["functions"][1]["callers"], ["runAdapter"])
            self.assertEqual(payload[0]["functions"][1]["valid_tiers"], ["L0", "L1", "L2", "L3"])

    def test_engine_llm08_duplicate_leaves_and_operator_shorthand_preservation(self) -> None:
        from engine.analyzer import analyze_source, check_duplicate_leaves, collect_top_functions
        from engine.specs import spec_for_path
        from tree_sitter_language_pack import get_parser

        py_dups = (
            "def normalize_alpha(raw_text: str, *, scale: int = 2) -> list[str]:\n"
            '    """First doc."""\n'
            "    cleaned = raw_text.strip()\n"
            "    tokens = [part.lower() for part in cleaned.split(',') if part]\n"
            "    try:\n"
            "        ordered = sorted(tokens, key=lambda entry: (len(entry), entry))\n"
            "    except ValueError as err:\n"
            "        return [str(err)]\n"
            "    return [f'{token}:{scale}' for token in ordered]\n\n"
            "def normalize_beta(input_str: str, *, factor: int = 2) -> list[str]:\n"
            '    """Different doc."""\n'
            "    trimmed = input_str.strip()\n"
            "    chunks = [chunk.lower() for chunk in trimmed.split(',') if chunk]\n"
            "    try:\n"
            "        ranked = sorted(chunks, key=lambda row: (len(row), row))\n"
            "    except ValueError as exc:\n"
            "        return [str(exc)]\n"
            "    return [f'{word}:{factor}' for word in ranked]\n"
        )
        py_viols = [v for v in analyze_source(py_dups, "dups.py") if v.rule == "LLM-08"]
        self.assertEqual(len(py_viols), 1)
        self.assertEqual(py_viols[0].line, 11)
        self.assertEqual(
            py_viols[0].message,
            "Function 'normalize_beta' duplicates leaf helper 'normalize_alpha' (line 1); reuse the existing helper instead of adding a duplicate.",
        )

        py_suppressed = py_dups.replace(
            "def normalize_beta",
            '# topdown-ignore(LLM-08, reason="distinct domain concept")\ndef normalize_beta',
        )
        self.assertEqual([v for v in analyze_source(py_suppressed, "supp.py") if v.rule == "LLM-08"], [])

        ts_dups = (
            "export function normalizeAlpha(rawText: string, rows: string[]): string[] {\n"
            "  const cleaned = rawText.trim();\n"
            "  const parts = cleaned.split(',');\n"
            "  const mapped = parts.map((entry) => entry.toLowerCase());\n"
            "  const [firstItem] = rows;\n"
            "  const combined = [...mapped, firstItem];\n"
            "  return combined.filter((token) => token.length > 0);\n"
            "}\n\n"
            "export const normalizeBeta = (inputStr: string, items: string[]): string[] => {\n"
            "  const trimmed = inputStr.trim();\n"
            "  const chunks = trimmed.split(',');\n"
            "  const lowered = chunks.map((row) => row.toLowerCase());\n"
            "  const [headEntry] = items;\n"
            "  const merged = [...lowered, headEntry];\n"
            "  return merged.filter((word) => word.length > 0);\n"
            "};\n"
        )
        ts_viols = [v for v in analyze_source(ts_dups, "dups.ts") if v.rule == "LLM-08"]
        self.assertEqual(len(ts_viols), 1)
        self.assertEqual(ts_viols[0].line, 10)
        self.assertEqual(
            ts_viols[0].message,
            "Function 'normalizeBeta' duplicates leaf helper 'normalizeAlpha' (line 1); reuse the existing helper instead of adding a duplicate.",
        )
        ts_suppressed = ts_dups.replace(
            "export const normalizeBeta",
            '// topdown-ignore(LLM-08, reason="distinct domain concept")\nexport const normalizeBeta',
        )
        self.assertEqual([v for v in analyze_source(ts_suppressed, "supp.ts") if v.rule == "LLM-08"], [])

        rust_dups = (
            "pub fn format_alpha(amount: i64) -> i64 {\n"
            "    let abs_val = amount.abs();\n"
            "    let step_one = abs_val + 10;\n"
            "    let step_two = step_one * 2;\n"
            "    let step_three = step_two - 5;\n"
            "    let step_four = step_three / 3;\n"
            "    step_four\n"
            "}\n\n"
            "pub fn format_beta(total: i64) -> i64 {\n"
            "    let mag_val = total.abs();\n"
            "    let part_one = mag_val + 10;\n"
            "    let part_two = part_one * 2;\n"
            "    let part_three = part_two - 5;\n"
            "    let part_four = part_three / 3;\n"
            "    part_four\n"
            "}\n"
        )
        rust_viols = [v for v in analyze_source(rust_dups, "dups.rs") if v.rule == "LLM-08"]
        self.assertEqual(len(rust_viols), 1)
        self.assertEqual(rust_viols[0].line, 10)
        rust_suppressed = rust_dups.replace(
            "pub fn format_beta",
            '// topdown-ignore(LLM-08, reason="separate domain")\npub fn format_beta',
        )
        self.assertEqual([v for v in analyze_source(rust_suppressed, "supp.rs") if v.rule == "LLM-08"], [])

        js_single_param_arrow_dups = (
            "const singleAlpha = rawText => {\n"
            "  const stepOne = rawText.trim();\n"
            "  const stepTwo = stepOne.toLowerCase();\n"
            "  const stepThree = stepTwo.replace('-', '_');\n"
            "  const stepFour = stepThree.slice(1);\n"
            "  const stepFive = stepFour.toUpperCase();\n"
            "  return stepFive;\n"
            "};\n\n"
            "const singleBeta = inputVal => {\n"
            "  const partOne = inputVal.trim();\n"
            "  const partTwo = partOne.toLowerCase();\n"
            "  const partThree = partTwo.replace('-', '_');\n"
            "  const partFour = partThree.slice(1);\n"
            "  const partFive = partFour.toUpperCase();\n"
            "  return partFive;\n"
            "};\n"
        )
        self.assertEqual(len([v for v in analyze_source(js_single_param_arrow_dups, "arrow.js") if v.rule == "LLM-08"]), 1)

        untagged_two_tier = (
            "# [L0] Entry\n"
            "def run_two_tier(first_text: str, second_text: str) -> tuple[str, str]:\n"
            "    return clean_alpha(first_text), clean_beta(second_text)\n\n"
            "def clean_alpha(raw_text: str) -> str:\n"
            "    step_one = raw_text.strip()\n"
            "    step_two = step_one.lower()\n"
            "    step_three = step_two.replace(' ', '_')\n"
            "    step_four = step_three.removeprefix('pre_')\n"
            "    step_five = step_four.removesuffix('_post')\n"
            "    return step_five\n\n"
            "def clean_beta(other_text: str) -> str:\n"
            "    step_one = other_text.strip()\n"
            "    step_two = step_one.lower()\n"
            "    step_three = step_two.replace(' ', '_')\n"
            "    step_four = step_three.removeprefix('pre_')\n"
            "    step_five = step_four.removesuffix('_post')\n"
            "    return step_five\n"
        )
        self.assertEqual(len([v for v in analyze_source(untagged_two_tier, "two_tier.py") if v.rule == "LLM-08"]), 1)

        comp_shadow_dups = (
            "def map_alpha(part: str, raw_items: list[str]) -> list[str]:\n"
            "    prefix = part.strip()\n"
            "    cleaned = [part.upper() for part in raw_items if part]\n"
            "    tagged = [f'{prefix}:{val}' for val in cleaned]\n"
            "    ordered = sorted(tagged)\n"
            "    tail = ordered[-1:]\n"
            "    return ordered + tail\n\n"
            "def map_beta(label: str, rows: list[str]) -> list[str]:\n"
            "    head = label.strip()\n"
            "    trimmed = [entry.upper() for entry in rows if entry]\n"
            "    labeled = [f'{head}:{item_val}' for item_val in trimmed]\n"
            "    ranked = sorted(labeled)\n"
            "    last_part = ranked[-1:]\n"
            "    return ranked + last_part\n"
        )
        self.assertEqual(len([v for v in analyze_source(comp_shadow_dups, "comp_shadow.py") if v.rule == "LLM-08"]), 1)

        swift_loop_dups = (
            "func loopAlpha(entry: Int, entries: [Int]) -> Int {\n"
            "    var total = entry\n"
            "    for entry in entries {\n"
            "        total += entry\n"
            "    }\n"
            "    let stepOne = total * 2\n"
            "    let stepTwo = stepOne + entry\n"
            "    return stepTwo\n"
            "}\n\n"
            "func loopBeta(baseVal: Int, items: [Int]) -> Int {\n"
            "    var acc = baseVal\n"
            "    for rowVal in items {\n"
            "        acc += rowVal\n"
            "    }\n"
            "    let partOne = acc * 2\n"
            "    let partTwo = partOne + baseVal\n"
            "    return partTwo\n"
            "}\n"
        )
        self.assertEqual(len([v for v in analyze_source(swift_loop_dups, "loop.swift") if v.rule == "LLM-08"]), 1)

        negative_cases = [
            # a + b vs a - b
            (
                "ops.py",
                "def add_fn(first: int, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def sub_fn(first: int, second: int) -> int:\n"
                "    step_one = first - second\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n",
            ),
            # arr[1:] vs arr[:1]
            (
                "slices.py",
                "def slice_tail(arr: list[int]) -> list[int]:\n"
                "    part = arr[1:]\n    step_two = len(part)\n    step_three = step_two + 1\n"
                "    step_four = step_three * 2\n    step_five = step_four - 1\n    return part + [step_five]\n\n"
                "def slice_head(arr: list[int]) -> list[int]:\n"
                "    part = arr[:1]\n    step_two = len(part)\n    step_three = step_two + 1\n"
                "    step_four = step_three * 2\n    step_five = step_four - 1\n    return part + [step_five]\n",
            ),
            # CACHE[idx] = val vs OTHER[idx] = val
            (
                "subscripts.py",
                "def write_cache(idx: int, val: int) -> int:\n"
                "    step_one = idx + 1\n    step_two = val * 2\n    CACHE[step_one] = step_two\n"
                "    step_three = step_one + step_two\n    step_four = step_three * 3\n    return step_four\n\n"
                "def write_other(idx: int, val: int) -> int:\n"
                "    step_one = idx + 1\n    step_two = val * 2\n    OTHER[step_one] = step_two\n"
                "    step_three = step_one + step_two\n    step_four = step_three * 3\n    return step_four\n",
            ),
            # Rust Some(val) vs Err(val)
            (
                "enum_pat.rs",
                "pub fn match_some(box_a: Result<i64, i64>) -> i64 {\n"
                "    if let Some(val) = box_a {\n"
                "        let step_one = val + 1;\n"
                "        let step_two = step_one * 2;\n"
                "        let step_three = step_two - 3;\n"
                "        let step_four = step_three + 4;\n"
                "        return step_four;\n"
                "    }\n"
                "    0\n"
                "}\n\n"
                "pub fn match_err(box_b: Result<i64, i64>) -> i64 {\n"
                "    if let Err(val) = box_b {\n"
                "        let step_one = val + 1;\n"
                "        let step_two = step_one * 2;\n"
                "        let step_three = step_two - 3;\n"
                "        let step_four = step_three + 4;\n"
                "        return step_four;\n"
                "    }\n"
                "    0\n"
                "}\n",
            ),
            # TS { name } vs { code } (both destructuring and object literal)
            (
                "shorthand.ts",
                "function retName(rawText: string): { name: string } {\n"
                "  const stepOne = rawText.trim();\n"
                "  const stepTwo = stepOne.toLowerCase();\n"
                "  const stepThree = stepTwo.replace(' ', '_');\n"
                "  const stepFour = stepThree.slice(1);\n"
                "  const name = stepFour.toUpperCase();\n"
                "  return { name };\n"
                "}\n"
                "function retCode(rawText: string): { code: string } {\n"
                "  const stepOne = rawText.trim();\n"
                "  const stepTwo = stepOne.toLowerCase();\n"
                "  const stepThree = stepTwo.replace(' ', '_');\n"
                "  const stepFour = stepThree.slice(1);\n"
                "  const code = stepFour.toUpperCase();\n"
                "  return { code };\n"
                "}\n",
            ),
            (
                "destructure.ts",
                "function bindName(payload: { name: string; code: string }): string {\n"
                "  const { name } = payload;\n"
                "  const stepTwo = name.trim();\n"
                "  const stepThree = stepTwo.toLowerCase();\n"
                "  const stepFour = stepThree.slice(0, 4);\n"
                "  const stepFive = stepFour.toUpperCase();\n"
                "  return stepFive;\n"
                "}\n"
                "function bindCode(payload: { name: string; code: string }): string {\n"
                "  const { code } = payload;\n"
                "  const stepTwo = code.trim();\n"
                "  const stepThree = stepTwo.toLowerCase();\n"
                "  const stepFour = stepThree.slice(0, 4);\n"
                "  const stepFive = stepFour.toUpperCase();\n"
                "  return stepFive;\n"
                "}\n",
            ),
            # Rust Foo { bar } vs Foo { baz } and let Foo { bar } = x
            (
                "rust_shorthand.rs",
                "pub fn init_bar(val: i64) -> Foo {\n"
                "    let step_one = val + 1;\n"
                "    let step_two = step_one * 2;\n"
                "    let step_three = step_two - 3;\n"
                "    let step_four = step_three + 4;\n"
                "    let bar = step_four;\n"
                "    Foo { bar }\n"
                "}\n"
                "pub fn init_baz(val: i64) -> Foo {\n"
                "    let step_one = val + 1;\n"
                "    let step_two = step_one * 2;\n"
                "    let step_three = step_two - 3;\n"
                "    let step_four = step_three + 4;\n"
                "    let baz = step_four;\n"
                "    Foo { baz }\n"
                "}\n",
            ),
            (
                "rust_pat_shorthand.rs",
                "pub fn pat_bar(src: Foo) -> i64 {\n"
                "    let Foo { bar } = src;\n"
                "    let step_one = bar + 1;\n"
                "    let step_two = step_one * 2;\n"
                "    let step_three = step_two - 3;\n"
                "    let step_four = step_three + 4;\n"
                "    step_four\n"
                "}\n"
                "pub fn pat_baz(src: Foo) -> i64 {\n"
                "    let Foo { baz } = src;\n"
                "    let step_one = baz + 1;\n"
                "    let step_two = step_one * 2;\n"
                "    let step_three = step_two - 3;\n"
                "    let step_four = step_three + 4;\n"
                "    step_four\n"
                "}\n",
            ),
            # K&R 5-line body
            (
                "kr_short.ts",
                "function shortOne(\n"
                "  rawText: string,\n"
                "): string {\n"
                "  // comment inside\n"
                "  const stepOne = rawText.trim();\n"
                "  const stepTwo = stepOne.toLowerCase();\n"
                "  const stepThree = stepTwo.replace('-', '_');\n"
                "  const stepFour = stepThree.toUpperCase();\n"
                "  return stepFour;\n"
                "}\n"
                "function shortTwo(\n"
                "  inputStr: string,\n"
                "): string {\n"
                "  // comment inside\n"
                "  const partOne = inputStr.trim();\n"
                "  const partTwo = partOne.toLowerCase();\n"
                "  const partThree = partTwo.replace('-', '_');\n"
                "  const partFour = partThree.toUpperCase();\n"
                "  return partFour;\n"
                "}\n",
            ),
            # leading docstring 5-line body
            (
                "doc_short.py",
                "def short_one(raw_text: str) -> str:\n"
                '    """Long\n    multi-line\n    docstring."""\n'
                "    # comment\n\n"
                "    step_one = raw_text.strip()\n"
                "    step_two = step_one.lower()\n"
                "    step_three = step_two.replace('-', '_')\n"
                "    step_four = step_three.title()\n"
                "    return step_four\n\n"
                "def short_two(input_str: str) -> str:\n"
                '    """Another\n    multi-line\n    docstring."""\n'
                "    # comment\n\n"
                "    part_one = input_str.strip()\n"
                "    part_two = part_one.lower()\n"
                "    part_three = part_two.replace('-', '_')\n"
                "    part_four = part_three.title()\n"
                "    return part_four\n",
            ),
            # [L0] leaf exempt
            (
                "l0_exempt.py",
                "# [L0] Adapter A\n"
                "def fetch_alpha(endpoint: str) -> str:\n"
                "    step_one = endpoint.strip()\n"
                "    step_two = step_one.lower()\n"
                "    step_three = step_two.replace(' ', '')\n"
                "    step_four = step_three.removeprefix('http://')\n"
                "    step_five = step_four.removesuffix('/')\n"
                "    return step_five\n\n"
                "# [L0] Adapter B\n"
                "def fetch_beta(target_url: str) -> str:\n"
                "    step_one = target_url.strip()\n"
                "    step_two = step_one.lower()\n"
                "    step_three = step_two.replace(' ', '')\n"
                "    step_four = step_three.removeprefix('http://')\n"
                "    step_five = step_four.removesuffix('/')\n"
                "    return step_five\n",
            ),
            # swapped params
            (
                "swapped.py",
                "def sub_ab(first: int, second: int) -> int:\n"
                "    val_a = first + 1\n    val_b = second + 2\n    val_c = val_a * 3\n"
                "    val_d = val_b * 4\n    diff = val_c - val_d\n    return diff\n\n"
                "def sub_ba(first: int, second: int) -> int:\n"
                "    val_a = second + 1\n    val_b = first + 2\n    val_c = val_a * 3\n"
                "    val_d = val_b * 4\n    diff = val_c - val_d\n    return diff\n",
            ),
            # different defaults (and = None / = undefined vs NO_DEFAULT, posonly/kwonly)
            (
                "defaults.py",
                "def def_one(amount: int = 1) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def def_two(amount: int = 2) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def def_none(amount: int | None = None) -> int:\n"
                "    step_one = amount or 0\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n\n"
                "def no_def(amount: int | None) -> int:\n"
                "    step_one = amount or 0\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n",
            ),
            (
                "defaults.ts",
                "function defUndef(amount = undefined): number {\n"
                "  const stepOne = amount ?? 0;\n  const stepTwo = stepOne + 1;\n  const stepThree = stepTwo * 2;\n"
                "  const stepFour = stepThree - 3;\n  const stepFive = stepFour + 4;\n  return stepFive;\n"
                "}\n"
                "function noDef(amount?: number): number {\n"
                "  const stepOne = amount ?? 0;\n  const stepTwo = stepOne + 1;\n  const stepThree = stepTwo * 2;\n"
                "  const stepFour = stepThree - 3;\n  const stepFive = stepFour + 4;\n  return stepFive;\n"
                "}\n",
            ),
            (
                "boundaries.py",
                "def pos_only(first: int, /, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n\n"
                "def normal_args(first: int, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n\n"
                "def kw_only(first: int, *, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n",
            ),
            # async vs sync
            (
                "async_vs_sync.py",
                "def sync_fn(amount: int) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "async def async_fn(amount: int) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n",
            ),
        ]
        for filename, src in negative_cases:
            self.assertEqual(
                [v for v in analyze_source(src, filename) if v.rule == "LLM-08"],
                [],
                msg=f"Unexpected LLM-08 on {filename}",
            )

        spec = spec_for_path("direct.py")
        assert spec is not None
        source_bytes = py_dups.encode("utf-8")
        tree = get_parser(spec.name).parse(source_bytes)
        top_fns = collect_top_functions(tree.root_node, source_bytes, spec)
        direct_viols = check_duplicate_leaves(
            top_fns,
            source_bytes,
            "direct.py",
            spec=spec,
            tier_state=({(fn.name, fn.start_line): set() for fn in top_fns}, {(fn.name, fn.start_line): None for fn in top_fns}),
            comment_lines=set(),
        )
        self.assertEqual(len(direct_viols), 1)
        self.assertEqual(direct_viols[0].rule, "LLM-08")

    def test_cli_receipt_clean_and_failing_output(self) -> None:
        from engine.cli import count_waiver_comments

        waiver_sample = (
            '# topdown-ignore(CANON-01, reason="C ABI")\n'
            'const note = "# topdown-ignore inside string";\n'
            'pub fn alpha() -> i32 { 1 } // topdown-ignore(CANON-05, reason="inline")\n'
            'const url = "https://example.com/topdown-ignore";\n'
        )
        self.assertEqual(count_waiver_comments(waiver_sample), 2)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            clean_file = root / "clean.py"
            clean_file.write_text(
                "def orchestrate(amount_cents: int) -> int:\n"
                "    return apply_fee(amount_cents)\n\n"
                "def apply_fee(amount_cents: int) -> int:\n"
                "    return amount_cents - 50\n",
                encoding="utf-8",
            )
            clean_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--receipt", str(clean_file)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(clean_proc.returncode, 0, msg=clean_proc.stderr + clean_proc.stdout)
            self.assertIn("## Topdown Verification Receipt", clean_proc.stdout)
            self.assertIn("| **Structural Compliance** | PASS | 1 files checked, 0 violations |", clean_proc.stdout)
            self.assertIn("| **Override Waivers (`CANON-08`)** | 0 waivers |", clean_proc.stdout)
            self.assertIn("| **Boundary Integrity (`CANON-02`)** | Opt-in |", clean_proc.stdout)
            self.assertNotIn("### Violations", clean_proc.stdout)

            failing_file = root / "failing.py"
            failing_file.write_text(
                '# topdown-ignore(CANON-05, reason="legacy guard")\n'
                "def legacy_check(flag_a: bool, flag_b: bool, flag_c: bool) -> int:\n"
                "    if flag_a:\n"
                "        if flag_b:\n"
                "            if flag_c:\n"
                "                return 1\n"
                "    return 0\n\n"
                "def bad_leaf(amount_cents: int) -> int:\n"
                "    return amount_cents - 50\n\n"
                "def caller_below(amount_cents: int) -> int:\n"
                "    return bad_leaf(amount_cents)\n",
                encoding="utf-8",
            )
            fail_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--receipt", str(failing_file)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(fail_proc.returncode, 1, msg=fail_proc.stderr + fail_proc.stdout)
            self.assertIn("## Topdown Verification Receipt", fail_proc.stdout)
            self.assertIn("| **Structural Compliance** | FAIL | 1 files checked, 1 violations |", fail_proc.stdout)
            self.assertIn("1 waivers", fail_proc.stdout)
            self.assertIn("### Violations", fail_proc.stdout)
            self.assertIn("[CANON-01]", fail_proc.stdout)

        with tempfile.TemporaryDirectory() as bound_dir:
            bound_root = Path(bound_dir)
            (bound_root / "topdown.toml").write_text(
                "[boundaries]\n"
                'pure_dirs = ["src/domain"]\n'
                'shell_dirs = ["src/adapters"]\n',
                encoding="utf-8",
            )
            domain_file = bound_root / "src" / "domain" / "pricing.py"
            domain_file.parent.mkdir(parents=True)
            domain_file.write_text(
                "def compute_price(amount_cents: int) -> int:\n"
                "    return amount_cents - 50\n",
                encoding="utf-8",
            )
            bound_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--receipt", str(bound_root)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(bound_proc.returncode, 0, msg=bound_proc.stderr + bound_proc.stdout)
            self.assertIn("| **Structural Compliance** | PASS | 1 files checked, 0 violations |", bound_proc.stdout)
            self.assertIn("| **Boundary Integrity (`CANON-02`)** | PASS |", bound_proc.stdout)

            domain_file.write_text(
                "import requests\n\n"
                "def compute_price(amount_cents: int) -> int:\n"
                "    return amount_cents - 50\n",
                encoding="utf-8",
            )
            bound_fail = subprocess.run(
                [sys.executable, "-m", "engine.cli", "--receipt", str(bound_root)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(bound_fail.returncode, 1, msg=bound_fail.stderr + bound_fail.stdout)
            self.assertIn("| **Boundary Integrity (`CANON-02`)** | FAIL | 1 CANON-02 violations across 1 pure_dirs |", bound_fail.stdout)

            for bad_flags in (["--outline", "--receipt"], ["--json", "--receipt"]):
                conflict_proc = subprocess.run(
                    [sys.executable, "-m", "engine.cli", *bad_flags, str(bound_root)],
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(conflict_proc.returncode, 2, msg=f"Expected exit 2 for {bad_flags}")
                self.assertIn("Usage:", conflict_proc.stderr)

    def test_canon03_nested_generic_types_and_wide_tuples_across_languages(self) -> None:
        from engine.analyzer import analyze_source

        samples = [
            (
                "types.py",
                "from typing import Optional\n\n"
                "DeepAlias = dict[tuple[tuple[str, int], tuple[str, int]], int]\n\n"
                "def bad_fn(\n"
                "    matrix: dict[str, dict[str, list[int]]],\n"
                ") -> tuple[int, int, int, int]:\n"
                "    local_wide: tuple[str, int, bool, float] = ('a', 1, True, 2.0)\n"
                "    return (1, 2, 3, 4)\n\n"
                '# topdown-ignore(CANON-03, reason="wire format")\n'
                "def waived_fn() -> tuple[int, int, int, int]:\n"
                "    return (1, 2, 3, 4)\n\n"
                "def clean_fn(items: Optional[dict[str, list[int]]]) -> list[tuple[str, int]]:\n"
                "    return []\n",
                3,
            ),
            (
                "types.ts",
                "type DeepAlias = Record<string, Map<string, Array<number>>>;\n\n"
                "export function badFn(matrix: Record<string, Map<string, Array<number>>>): [number, number, number, number] {\n"
                "  return [1, 2, 3, 4];\n"
                "}\n\n"
                "export async function cleanFn(items: Record<string, number[]>): Promise<Readonly<Record<string, number[]>>> {\n"
                "  type LocalRow = [number, number, number, number];\n"
                "  const castVal = items as unknown as [number, number, number, number];\n"
                "  return items;\n"
                "}\n",
                2,
            ),
            (
                "types.rs",
                "type WideAlias = (i32, i32, i32, i32);\n\n"
                "pub fn bad_fn(matrix: HashMap<String, Vec<Vec<i32>>>) -> (i32, i32, i32, i32) {\n"
                "    (1, 2, 3, 4)\n"
                "}\n\n"
                "pub fn clean_fn(items: Result<Option<Vec<i32>>, String>) -> Option<Vec<i32>> {\n"
                "    None\n"
                "}\n",
                2,
            ),
            (
                "types.swift",
                "typealias WideAlias = (Int, Int, Int, Int)\n\n"
                "func badFn(matrix: Dictionary<String, Array<Array<Int>>>) -> (Int, Int, Int, Int) {\n"
                "    return (1, 2, 3, 4)\n"
                "}\n\n"
                "func cleanFn(items: [String: [Int]]?) -> Dictionary<String, Array<()>> {\n"
                "    typealias LocalRow = (Int, Int, Int, Int)\n"
                "    let built = Dictionary<String, Array<Array<Int>>>()\n"
                "    return [:]\n"
                "}\n",
                2,
            ),
            (
                "types.go",
                "package sample\n\n"
                "type DeepAlias = map[string]map[string][]int\n\n"
                "func badFn(matrix map[string]map[string][]int) [][][]int {\n"
                "    return nil\n"
                "}\n\n"
                "func cleanFn(items map[string][]int) []int {\n"
                "    _ = [2][2][2]int{}\n"
                "    return nil\n"
                "}\n",
                2,
            ),
        ]
        for filename, code, expected_count in samples:
            canon03 = [v for v in analyze_source(code, filename) if v.rule == "CANON-03"]
            self.assertEqual(len(canon03), expected_count, msg=f"{filename}: {canon03}")

    def test_engine_module_size_ceiling_and_packed_statements(self) -> None:
        from engine.analyzer import analyze_source

        monolith = "\n".join(
            f"def fn_{idx}(value: int) -> int:\n" + "".join(f"    step_{step} = value + {step}\n" for step in range(28)) + "    return value\n"
            for idx in range(16)
        )
        self.assertEqual(len([v for v in analyze_source(monolith, "src/big.py") if v.rule == "CANON-01" and v.line == 1]), 1)
        self.assertEqual(len([v for v in analyze_source(monolith, "tests/test_big.py") if v.rule == "CANON-01" and v.line == 1]), 0)
        waived = '# topdown-ignore(CANON-01, reason="standalone CLI")\n' + monolith
        self.assertEqual(len([v for v in analyze_source(waived, "src/big.py") if v.rule == "CANON-01" and v.line == 1]), 0)

        packed = "def cramped(value: int) -> int:\n    first = value + 1; second = first + 2\n    return second\n"
        llm05 = [v for v in analyze_source(packed, "src/packed.py") if v.rule == "LLM-05"]
        self.assertEqual(len(llm05), 1, msg=str(llm05))


if __name__ == "__main__":
    unittest.main()
