import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "enforcement"))


class TestCheckRust(unittest.TestCase):
    def test_clean_topdown_rust_passes(self) -> None:
        import check_rust

        source = (
            "pub fn process_payout(amount_cents: i64) -> Result<String, &'static str> {\n"
            "    if amount_cents <= 0 {\n"
            '        return Err("non-positive amount");\n'
            "    }\n"
            "    let fee_cents = compute_fee(amount_cents);\n"
            "    Ok(format_usd(amount_cents - fee_cents))\n"
            "}\n\n"
            "fn compute_fee(amount_cents: i64) -> i64 {\n"
            "    amount_cents / 50\n"
            "}\n\n"
            "fn format_usd(amount_cents: i64) -> String {\n"
            '    format!("${:.2}", amount_cents as f64 / 100.0)\n'
            "}\n"
        )
        self.assertEqual(check_rust.check_source(source, "clean.rs"), [])

    def test_fn_over_80_lines_flags_canon01(self) -> None:
        import check_rust

        body = "\n".join(f"    total += {idx};" for idx in range(82))
        source = f"pub fn long_fn(mut total: i64) -> i64 {{\n{body}\n    total\n}}\n"
        violations = check_rust.check_source(source, "long.rs")
        self.assertIn("CANON-01", [v.rule for v in violations])

    def test_deep_nesting_and_nested_fn_flag_canon05(self) -> None:
        import check_rust

        deep = (
            "fn deep(a: bool, b: bool, c: bool) -> i64 {\n"
            "    if a {\n"
            "        if b {\n"
            "            if c {\n"
            "                return 1;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        nested = (
            "fn outer(count: i64) -> i64 {\n"
            "    fn inner(step: i64) -> i64 {\n"
            "        step + 1\n"
            "    }\n"
            "    inner(count)\n"
            "}\n"
        )
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(deep, "d.rs")])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(nested, "n.rs")])

    def test_unwrap_and_unwrap_or_default_flag_llm02(self) -> None:
        import check_rust

        source = (
            "fn load_val(opt: Option<i64>) -> i64 {\n"
            "    let first = opt.unwrap();\n"
            "    let second = opt.unwrap_or_default();\n"
            "    first + second\n"
            "}\n"
        )
        violations = check_rust.check_source(source, "u.rs")
        self.assertEqual([v.rule for v in violations], ["LLM-02", "LLM-02"])

    def test_cfg_test_module_is_exempt_from_unwrap(self) -> None:
        import check_rust

        source = (
            "pub fn add(a: i64, b: i64) -> i64 {\n"
            "    a + b\n"
            "}\n\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    #[test]\n"
            "    fn works() {\n"
            "        let val = Some(1).unwrap();\n"
            "        assert_eq!(val, 1);\n"
            "    }\n"
            "}\n"
        )
        self.assertEqual(check_rust.check_source(source, "t.rs"), [])

    def test_comments_and_strings_do_not_trigger_false_positives(self) -> None:
        import check_rust

        source = (
            "pub fn describe() -> &'static str {\n"
            "    // opt.unwrap() and opt.unwrap_or_default()\n"
            '    let note = "fn fake() { x.unwrap(); }";\n'
            "    note\n"
            "}\n"
        )
        self.assertEqual(check_rust.check_source(source, "s.rs"), [])

    def test_topdown_ignore_with_and_without_reason(self) -> None:
        import check_rust

        suppressed = (
            '// topdown-ignore(LLM-02, reason="static regex verified by test")\n'
            "fn load(opt: Option<i64>) -> i64 {\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        missing_reason = (
            "// topdown-ignore(LLM-02)\n"
            "fn load(opt: Option<i64>) -> i64 {\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        self.assertEqual(check_rust.check_source(suppressed, "ig1.rs"), [])
        self.assertIn("CANON-08", [v.rule for v in check_rust.check_source(missing_reason, "ig2.rs")])

    def test_cli_missing_file_exits_2(self) -> None:
        checker = ROOT_DIR / "enforcement" / "check_rust.py"
        proc = subprocess.run(
            [sys.executable, str(checker), str(ROOT_DIR / "missing.rs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.strip())

    def test_multiline_fn_signature_and_depth2_else_handled(self) -> None:
        import check_rust

        multiline_deep = (
            "pub fn wrapped(\n"
            "    a: bool,\n"
            "    b: bool,\n"
            "    c: bool,\n"
            ") -> i64 {\n"
            "    if a {\n"
            "        if b {\n"
            "            if c {\n"
            "                return 1;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        depth2_else = (
            "pub fn flat(a: bool, b: bool) -> i64 {\n"
            "    if a {\n"
            "        if b {\n"
            "            return 1;\n"
            "        } else {\n"
            "            return 2;\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(multiline_deep, "ml.rs")])
        self.assertEqual(check_rust.check_source(depth2_else, "ok.rs"), [])

    def test_raw_strings_char_literals_and_single_line_cfg_test(self) -> None:
        import check_rust

        raw_and_char = (
            "pub fn parse(opt: Option<i64>) -> i64 {\n"
            "    let q = '\"';\n"
            '    let path = r#"say "hi" ok"#;\n'
            "    let _ = (q, path);\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        single_line_cfg = (
            "#[cfg(test)] mod tests;\n"
            "pub fn prod(opt: Option<i64>) -> i64 {\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        self.assertIn("LLM-02", [v.rule for v in check_rust.check_source(raw_and_char, "rc.rs")])
        self.assertIn("LLM-02", [v.rule for v in check_rust.check_source(single_line_cfg, "cfg.rs")])

    def test_bare_topdown_ignore_parens_in_reason_and_string_literal(self) -> None:
        import check_rust

        bare = "// topdown-ignore\npub fn ok() -> i64 { 1 }\n"
        parens = (
            '// topdown-ignore(LLM-02, reason="verified by f()")\n'
            "pub fn load(opt: Option<i64>) -> i64 {\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        in_str = 'pub fn msg() -> &\'static str {\n    "// topdown-ignore(LLM-02)"\n}\n'
        self.assertIn("CANON-08", [v.rule for v in check_rust.check_source(bare, "b.rs")])
        self.assertEqual(check_rust.check_source(parens, "p.rs"), [])
        self.assertEqual(check_rust.check_source(in_str, "s.rs"), [])

    def test_inline_cfg_test_inside_fn_flags_llm07_but_mod_tests_passes(self) -> None:
        import check_rust

        inline_cfg = (
            "pub fn send_email(address: &str) -> String {\n"
            "    #[cfg(test)]\n"
            '    return "stub".to_string();\n'
            "    address.to_string()\n"
            "}\n"
        )
        self.assertIn("LLM-07", [v.rule for v in check_rust.check_source(inline_cfg, "src/mailer.rs")])
        self.assertEqual(check_rust.check_source(inline_cfg, "tests/mailer_test.rs"), [])

    def test_byte_raw_strings_nested_comments_and_let_else_handled(self) -> None:
        import check_rust

        clean = (
            "pub fn parse(opt: Option<i64>, flag_a: bool, flag_b: bool) -> i64 {\n"
            '    let bytes = br#"hello "world" {"#;\n'
            "    /* outer /* inner */ fn fake() { opt.unwrap(); } */\n"
            "    let brace = '\\x7b';\n"
            "    let _ = (bytes, brace);\n"
            "    if flag_a {\n"
            "        if flag_b {\n"
            "            let Some(val) = opt else { return 0; };\n"
            "            return val;\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        let_if_deep = (
            "pub fn pick(a: bool, b: bool, c: bool) -> i32 {\n"
            "    let chosen = if a {\n"
            "        if b {\n"
            "            1\n"
            "        } else {\n"
            "            if c {\n"
            "                2\n"
            "            } else {\n"
            "                3\n"
            "            }\n"
            "        }\n"
            "    } else {\n"
            "        4\n"
            "    };\n"
            "    chosen\n"
            "}\n"
        )
        self.assertEqual(check_rust.check_source(clean, "ok.rs"), [])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(let_if_deep, "deep.rs")])

    def test_implicit_state_machine_flags_canon03_and_separate_structs_pass(self) -> None:
        import check_rust

        bad_struct = (
            "pub struct SyncView {\n"
            "    pub is_loading: bool,\n"
            "    pub is_error: bool,\n"
            "}\n"
        )
        suppressed = (
            '// topdown-ignore(CANON-03, reason="FFI struct layout")\n'
            "#[derive(Debug, Clone)]\n"
            "pub struct FfiState {\n"
            "    pub is_pending: bool,\n"
            "    pub is_failed: bool,\n"
            "}\n"
        )
        separate_clean = (
            "pub struct Account {\n"
            "    pub is_active: bool,\n"
            "    pub is_cleared: bool,\n"
            "    pub is_loading: bool,\n"
            "}\n"
            "pub struct ErrorBanner {\n"
            "    pub is_error: bool,\n"
            "}\n"
            "impl ErrorBanner {\n"
            "    pub fn check_loading(&self, is_loading: bool) -> bool {\n"
            "        is_loading\n"
            "    }\n"
            "    pub fn check_error(&self, is_error: bool) -> bool {\n"
            "        is_error\n"
            "    }\n"
            "}\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(bad_struct, "view.rs")])
        self.assertEqual(check_rust.check_source(suppressed, "ffi.rs"), [])
        self.assertEqual(check_rust.check_source(separate_clean, "clean.rs"), [])
        branch_fn = (
            "pub fn run_sync(cond: bool) -> bool {\n"
            "    let is_loading = true;\n"
            "    if cond {\n"
            "        let is_error = true;\n"
            "        return is_error;\n"
            "    }\n"
            "    is_loading\n"
            "}\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(branch_fn, "branch.rs")])


if __name__ == "__main__":
    unittest.main()
