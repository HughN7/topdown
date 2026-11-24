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

    def test_stacked_topdown_ignore_above_tier_tag_and_attributes(self) -> None:
        import check_rust

        stacked_fn = (
            '// topdown-ignore(LLM-02, reason="legacy [L1] entrypoint")\n'
            "// [L0] Entrypoint\n"
            "/** Rustdoc block */\n"
            "#[inline]\n"
            "pub fn load(opt: Option<i64>) -> i64 {\n"
            "    opt.unwrap()\n"
            "}\n"
        )
        no_adjacent_leak = (
            'pub fn first() -> i64 { 1 } // topdown-ignore(CANON-05, reason="only first")\n'
            "pub fn second(a: bool, b: bool, c: bool) -> i64 {\n"
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
        self.assertEqual(check_rust.check_source(stacked_fn, "stacked.rs"), [])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(no_adjacent_leak, "leak.rs")])

    def test_same_line_close_and_open_fn_no_false_canon03_or_canon05(self) -> None:
        import check_rust

        shared_fns = (
            "pub fn first() -> bool {\n"
            "    let is_loading = true;\n"
            "    is_loading\n"
            "} pub fn second() -> bool {\n"
            "    let is_error = false;\n"
            "    is_error\n"
            "}\n"
        )
        shared_cfg_test = (
            "pub fn prod() -> bool {\n"
            "    true\n"
            "} #[cfg(test)] mod tests {\n"
            "    #[test]\n"
            "    fn works() {\n"
            "        let val = Some(1).unwrap();\n"
            "        assert_eq!(val, 1);\n"
            "    }\n"
            "}\n"
        )
        ignore_bleed_canon05 = (
            '// topdown-ignore(CANON-05, reason="only first")\n'
            "pub fn first() -> bool {\n"
            "    true\n"
            "} pub fn second(a: bool, b: bool, c: bool) -> bool {\n"
            "    if a {\n"
            "        if b {\n"
            "            if c {\n"
            "                return true;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    false\n"
            "}\n"
        )
        ignore_bleed_canon03 = (
            '// topdown-ignore(CANON-03, reason="only first")\n'
            "pub fn first() -> bool {\n"
            "    true\n"
            "} pub fn second() -> bool {\n"
            "    let is_loading = true;\n"
            "    let is_error = false;\n"
            "    is_loading || is_error\n"
            "}\n"
        )
        self.assertEqual(check_rust.check_source(shared_fns, "shared_fns.rs"), [])
        self.assertEqual(check_rust.check_source(shared_cfg_test, "shared_cfg.rs"), [])
        self.assertEqual(
            [v.rule for v in check_rust.check_source(ignore_bleed_canon05, "bleed05.rs")],
            ["CANON-05"],
        )
        self.assertEqual(
            [v.rule for v in check_rust.check_source(ignore_bleed_canon03, "bleed03.rs")],
            ["CANON-03"],
        )

    def test_multiline_typed_let_flushes_into_scope_canon03(self) -> None:
        import check_rust

        multiline_let = (
            "pub fn fetch_state(opt: Option<i64>) -> bool {\n"
            "    let is_loading: bool = compute_flag(\n"
            "        opt,\n"
            "        true,\n"
            "    );\n"
            "    let is_error: bool = false;\n"
            "    is_loading || is_error\n"
            "}\n"
        )
        multiline_impl_params = (
            "pub struct Service;\n"
            "impl Service {\n"
            "    pub fn check_a(\n"
            "        &self,\n"
            "        is_loading: bool,\n"
            "    ) -> bool {\n"
            "        is_loading\n"
            "    }\n"
            "    pub fn check_b(\n"
            "        &self,\n"
            "        is_error: bool,\n"
            "    ) -> bool {\n"
            "        is_error\n"
            "    }\n"
            "}\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(multiline_let, "mlet.rs")])
        self.assertEqual(check_rust.check_source(multiline_impl_params, "impl.rs"), [])

    def test_tail_expression_resets_pending_start(self) -> None:
        import check_rust

        tail_leak = (
            "pub fn first(x: i64) -> i64 {\n"
            '    x + 1 // topdown-ignore(CANON-03, reason="x")\n'
            "}\n"
            "pub fn second() -> bool {\n"
            "    let is_loading = true;\n"
            "    let is_error = false;\n"
            "    is_loading || is_error\n"
            "}\n"
        )
        top_stmt_leak = (
            "const LIMIT: i64 =\n"
            '    1; // topdown-ignore(CANON-03, reason="const")\n'
            "pub struct FlagBag {\n"
            "    pub is_loading: bool,\n"
            "    pub is_error: bool,\n"
            "}\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(tail_leak, "tail.rs")])
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(top_stmt_leak, "topstmt.rs")])

    def test_multiline_control_header_canon03_and_canon05(self) -> None:
        import check_rust

        multiline_if_deep = (
            "pub fn deep_if(cond_a: bool, cond_b: bool, c: bool, d: bool) -> i64 {\n"
            "    if cond_a\n"
            "        && cond_b\n"
            "    {\n"
            "        if c {\n"
            "            if d {\n"
            "                return 1;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        multiline_if_state = (
            "pub fn state_if(cond_a: bool, cond_b: bool) -> bool {\n"
            "    let is_loading = true;\n"
            "    if cond_a\n"
            "        && cond_b\n"
            "    {\n"
            "        let is_error = true;\n"
            "        return is_error;\n"
            "    }\n"
            "    is_loading\n"
            "}\n"
        )
        multiline_if_let_struct_deep = (
            "pub fn deep_let_struct(x: Foo, c: bool, d: bool) -> i64 {\n"
            "    if let Foo {\n"
            "        bar\n"
            "    } = x {\n"
            "        if c {\n"
            "            if d {\n"
            "                return 1;\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        multiline_if_let_struct_state = (
            "pub fn state_let_struct(x: Foo) -> bool {\n"
            "    let is_loading = true;\n"
            "    if let Foo {\n"
            "        bar\n"
            "    } = x {\n"
            "        let is_error = true;\n"
            "        return is_error;\n"
            "    }\n"
            "    is_loading\n"
            "}\n"
        )
        multiline_let_else_flat = (
            "pub fn flat_let_else(opt: Option<i64>, a: bool, b: bool) -> i64 {\n"
            "    if a {\n"
            "        if b {\n"
            "            let Some(v) = opt else\n"
            "            { return 0; };\n"
            "            return v;\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        non_rust_keywords = (
            "pub fn non_rust_ctrl(a: bool, b: bool) -> i64 {\n"
            "    if a {\n"
            "        if b {\n"
            "            switch {\n"
            "                try {\n"
            "                    catch {\n"
            "                        return 1;\n"
            "                    }\n"
            "                }\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        other_multiline_controls = (
            "pub fn loop_while_for_match(items: &[i64], a: bool, b: bool) -> i64 {\n"
            "    for item\n"
            "        in items\n"
            "    {\n"
            "        while a\n"
            "            && b\n"
            "        {\n"
            "            match item\n"
            "            {\n"
            "                0 => return 1,\n"
            "                _ => {}\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        else_if_and_loop_state = (
            "pub fn else_if_loop(cond: bool) -> bool {\n"
            "    let is_loading = true;\n"
            "    if cond {\n"
            "        return false;\n"
            "    } else if !cond\n"
            "        && true\n"
            "    {\n"
            "        loop\n"
            "        {\n"
            "            let is_error = true;\n"
            "            return is_error;\n"
            "        }\n"
            "    }\n"
            "    is_loading\n"
            "}\n"
        )
        nested_if_let_struct_depth2 = (
            "pub fn sequential_depth2(a: bool, b: bool, c: bool, x: Foo) -> i64 {\n"
            "    if a {\n"
            "        if let Foo {\n"
            "            bar\n"
            "        } = x {\n"
            "            return 1;\n"
            "        }\n"
            "    }\n"
            "    if b {\n"
            "        if c {\n"
            "            return 2;\n"
            "        }\n"
            "    }\n"
            "    0\n"
            "}\n"
        )
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(multiline_if_deep, "mif05.rs")])
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(multiline_if_state, "mif03.rs")])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(multiline_if_let_struct_deep, "mlet05.rs")])
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(multiline_if_let_struct_state, "mlet03.rs")])
        self.assertEqual(check_rust.check_source(multiline_let_else_flat, "let_else.rs"), [])
        self.assertEqual(check_rust.check_source(non_rust_keywords, "nonrust.rs"), [])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(other_multiline_controls, "other_ctrl.rs")])
        self.assertIn("CANON-03", [v.rule for v in check_rust.check_source(else_if_and_loop_state, "else_loop.rs")])
        self.assertEqual(check_rust.check_source(nested_if_let_struct_depth2, "seq_depth2.rs"), [])

    def test_multiline_rust_attribute_header_walk(self) -> None:
        import check_rust

        suppressed_fn = (
            '// topdown-ignore(CANON-05, reason="legacy nesting")\n'
            "#[cfg_attr(\n"
            '    all(feature = "a]b", feature = "c(d"), // trailing ] ) }\n'
            "    inline,\n"
            ")]\n"
            "pub fn wrapped(a: bool, b: bool, c: bool) -> i64 {\n"
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
        suppressed_struct = (
            '// topdown-ignore(CANON-03, reason="ffi layout")\n'
            "#[derive(\n"
            "    Debug,\n"
            "    Clone,\n"
            ")]\n"
            "pub struct FfiFlags {\n"
            "    pub is_loading: bool,\n"
            "    pub is_error: bool,\n"
            "}\n"
        )
        inner_attr_blocks = (
            '// topdown-ignore(CANON-05, reason="crate attr only")\n'
            "#![allow(\n"
            "    dead_code\n"
            ")]\n"
            "pub fn unchecked(a: bool, b: bool, c: bool) -> i64 {\n"
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
        line1_inline_attr = (
            '#[inline] // topdown-ignore(CANON-05, reason="inline attr at line 1")\n'
            "pub fn second(a: bool, b: bool, c: bool) -> i64 {\n"
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
        post_prev_end_attr = (
            "pub fn first() -> i64 {\n"
            "    1\n"
            "}\n"
            "#[cfg_attr(\n"
            "    all(),\n"
            '    inline // topdown-ignore(CANON-05, reason="post prev_end attr")\n'
            ")]\n"
            "pub fn second(a: bool, b: bool, c: bool) -> i64 {\n"
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
        self.assertEqual(check_rust.check_source(suppressed_fn, "attr_fn.rs"), [])
        self.assertEqual(check_rust.check_source(suppressed_struct, "attr_struct.rs"), [])
        self.assertIn("CANON-05", [v.rule for v in check_rust.check_source(inner_attr_blocks, "inner_attr.rs")])
        lines = suppressed_fn.splitlines()
        collected = check_rust.collect_contiguous_header_lines(
            lines, 6, prev_end_line=0, comment_lines={1}
        )
        self.assertEqual(collected, {1, 2, 3, 4, 5})
        self.assertEqual(check_rust.check_source(line1_inline_attr, "line1_attr.rs"), [])
        self.assertEqual(check_rust.check_source(post_prev_end_attr, "post_prev_attr.rs"), [])
        self.assertEqual(
            check_rust.collect_contiguous_header_lines(
                post_prev_end_attr.splitlines(), 8, prev_end_line=3, comment_lines=set()
            ),
            {4, 5, 6, 7},
        )

    def test_module_size_ceiling_and_packed_statements(self) -> None:
        import check_rust

        monolith = "\n".join(
            f"pub fn fn_{idx}(value: i32) -> i32 {{\n"
            + "".join(f"    let step_{step} = value + {step};\n" for step in range(28))
            + "    value\n}\n"
            for idx in range(16)
        )
        self.assertEqual(len([v for v in check_rust.check_source(monolith, "src/big.rs") if v.rule == "CANON-01" and v.line == 1]), 1)
        self.assertEqual(len([v for v in check_rust.check_source(monolith, "tests/big.rs") if v.rule == "CANON-01" and v.line == 1]), 0)
        waived = '// topdown-ignore(CANON-01, reason="standalone CLI")\n' + monolith
        self.assertEqual(len([v for v in check_rust.check_source(waived, "src/big.rs") if v.rule == "CANON-01" and v.line == 1]), 0)

        packed = "pub fn cramped(value: i32) -> i32 {\n    let first = value + 1; let second = first + 2;\n    second\n}\n"
        self.assertEqual(len([v for v in check_rust.check_source(packed, "src/packed.rs") if v.rule == "LLM-05"]), 1)


if __name__ == "__main__":
    unittest.main()
