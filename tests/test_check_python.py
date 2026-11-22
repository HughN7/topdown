import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "enforcement"))


class TestCheckPython(unittest.TestCase):
    def test_clean_topdown_module_passes(self) -> None:
        import check_python

        source = (
            "def run_workflow(amount_cents: int) -> str:\n"
            "    net_cents = apply_fee(amount_cents)\n"
            "    return format_usd(net_cents)\n\n"
            "def apply_fee(amount_cents: int) -> int:\n"
            "    fee_cents = compute_fee(amount_cents)\n"
            "    return amount_cents - fee_cents\n\n"
            "def compute_fee(amount_cents: int) -> int:\n"
            "    return clamp_min(amount_cents // 100, 50)\n\n"
            "def clamp_min(value_cents: int, floor_cents: int) -> int:\n"
            "    return value_cents if value_cents >= floor_cents else floor_cents\n\n"
            "def format_usd(amount_cents: int) -> str:\n"
            '    return f"${amount_cents / 100:.2f}"\n'
        )
        violations = check_python.check_source(source, "clean.py")
        self.assertEqual(violations, [])

    def test_multi_l0_shared_helper_passes(self) -> None:
        import check_python

        source = (
            "def export_csv(amount_cents: int) -> str:\n"
            "    return format_usd(amount_cents)\n\n"
            "def export_json(amount_cents: int) -> str:\n"
            "    return format_usd(amount_cents)\n\n"
            "def format_usd(amount_cents: int) -> str:\n"
            '    return f"${amount_cents / 100:.2f}"\n'
        )
        violations = check_python.check_source(source, "multi.py")
        self.assertEqual(violations, [])

    def test_out_of_order_callee_above_caller_flags_canon01(self) -> None:
        import check_python

        source = (
            "def helper(amount_cents: int) -> int:\n"
            "    return amount_cents + 1\n\n"
            "def orchestrator(amount_cents: int) -> int:\n"
            "    return helper(amount_cents)\n"
        )
        violations = check_python.check_source(source, "order.py")
        rules = [v.rule for v in violations]
        self.assertIn("CANON-01", rules)

    def test_call_depth_exceeding_l3_flags_canon01(self) -> None:
        import check_python

        source = (
            "def level_zero(count: int) -> int:\n"
            "    return level_one(count)\n\n"
            "def level_one(count: int) -> int:\n"
            "    return level_two(count)\n\n"
            "def level_two(count: int) -> int:\n"
            "    return level_three(count)\n\n"
            "def level_three(count: int) -> int:\n"
            "    return level_four(count)\n\n"
            "def level_four(count: int) -> int:\n"
            "    return count + 1\n"
        )
        violations = check_python.check_source(source, "depth.py")
        rules = [v.rule for v in violations]
        self.assertIn("CANON-01", rules)

    def test_mutual_recursion_does_not_crash(self) -> None:
        import check_python

        source = (
            "def ping(remaining: int) -> int:\n"
            "    if remaining <= 0:\n"
            "        return 0\n"
            "    return pong(remaining - 1)\n\n"
            "def pong(remaining: int) -> int:\n"
            "    if remaining <= 0:\n"
            "        return 0\n"
            "    return ping(remaining - 1)\n"
        )
        violations = check_python.check_source(source, "cycle.py")
        self.assertIsInstance(violations, list)

    def test_function_over_80_lines_flags_canon01(self) -> None:
        import check_python

        body = "\n".join(f"    total += {idx}" for idx in range(82))
        source = f"def long_fn(total: int) -> int:\n{body}\n    return total\n"
        violations = check_python.check_source(source, "long.py")
        self.assertIn("CANON-01", [v.rule for v in violations])

    def test_positional_and_total_params_canon03(self) -> None:
        import check_python

        bad_pos = "def fn_bad(a: int, b: int, c: int, d: int) -> int:\n    return a + b + c + d\n"
        good_kw = (
            "def fn_good(first: int, second: int, third: int, *, fourth: int, fifth: int) -> int:\n"
            "    return first + second + third + fourth + fifth\n"
        )
        bad_total = (
            "def fn_many(a: int, b: int, *, c: int, d: int, e: int, f: int, g: int) -> int:\n"
            "    return a + b + c + d + e + f + g\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_pos, "p1.py")])
        self.assertEqual(check_python.check_source(good_kw, "p2.py"), [])
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_total, "p3.py")])

    def test_deep_nesting_inner_def_nested_ternary_flag_canon05(self) -> None:
        import check_python

        deep = (
            "def deep_fn(flag_a: bool, flag_b: bool, flag_c: bool) -> int:\n"
            "    if flag_a:\n"
            "        if flag_b:\n"
            "            if flag_c:\n"
            "                return 1\n"
            "    return 0\n"
        )
        inner = (
            "def outer(count: int) -> int:\n"
            "    def inner_fn(step: int) -> int:\n"
            "        return step + 1\n"
            "    return inner_fn(count)\n"
        )
        ternary = (
            "def pick(flag_a: bool, flag_b: bool) -> str:\n"
            '    return "a" if flag_a else ("b" if flag_b else "c")\n'
        )
        self.assertIn("CANON-05", [v.rule for v in check_python.check_source(deep, "d.py")])
        self.assertIn("CANON-05", [v.rule for v in check_python.check_source(inner, "i.py")])
        self.assertIn("CANON-05", [v.rule for v in check_python.check_source(ternary, "t.py")])

    def test_error_swallowing_flags_llm02(self) -> None:
        import check_python

        swallow_pass = (
            "def parse_num(raw_text: str) -> int:\n"
            "    try:\n"
            "        return int(raw_text)\n"
            "    except Exception:\n"
            "        pass\n"
            "    return 0\n"
        )
        swallow_none = (
            "def parse_opt(raw_text: str) -> int | None:\n"
            "    try:\n"
            "        return int(raw_text)\n"
            "    except:\n"
            "        return None\n"
        )
        clean_reraise = (
            "def parse_strict(raw_text: str) -> int:\n"
            "    try:\n"
            "        return int(raw_text)\n"
            "    except ValueError:\n"
            "        raise\n"
        )
        self.assertIn("LLM-02", [v.rule for v in check_python.check_source(swallow_pass, "s1.py")])
        self.assertIn("LLM-02", [v.rule for v in check_python.check_source(swallow_none, "s2.py")])
        self.assertEqual(check_python.check_source(clean_reraise, "s3.py"), [])

    def test_vague_nouns_flag_llm03(self) -> None:
        import check_python

        bad_param = "def process(data: int) -> int:\n    temp = data + 1\n    return temp\n"
        ok_comp = (
            "def double_values(amounts: list[int]) -> list[int]:\n"
            "    return [item * 2 for item in amounts]\n"
        )
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(bad_param, "v1.py")])
        self.assertEqual(check_python.check_source(ok_comp, "v2.py"), [])

    def test_single_char_names_flag_llm03_except_underscore(self) -> None:
        import check_python

        bad_loop = (
            "def list_names(names: list[str]) -> list[str]:\n"
            "    lines: list[str] = []\n"
            "    for i, n in enumerate(names):\n"
            '        lines.append(f"{i}: {n}")\n'
            "    return lines\n"
        )
        bad_param = "def square(x: int) -> int:\n    return x * x\n"
        good_loop = (
            "def list_names(names: list[str]) -> list[str]:\n"
            "    lines: list[str] = []\n"
            "    for index, name in enumerate(names):\n"
            '        lines.append(f"{index}: {name}")\n'
            "    return lines\n"
        )
        good_discard = (
            "def repeat_count(times: int) -> int:\n"
            "    total = 0\n"
            "    for _ in range(times):\n"
            "        total += 1\n"
            "    return total\n"
        )
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(bad_loop, "sc1.py")])
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(bad_param, "sc2.py")])
        self.assertEqual(check_python.check_source(good_loop, "sc3.py"), [])
        self.assertEqual(check_python.check_source(good_discard, "sc4.py"), [])

    def test_anti_gaming_function_names_flag_llm05_and_messages_are_actionable(self) -> None:
        import check_python

        bad_part = "def process_part2(amount_cents: int) -> int:\n    return amount_cents + 1\n"
        bad_helper = "def fee_helper(amount_cents: int) -> int:\n    return amount_cents + 1\n"
        good_name = "def calculate_fee_cents(amount_cents: int) -> int:\n    return amount_cents + 1\n"
        part_violations = check_python.check_source(bad_part, "ag1.py")
        helper_violations = check_python.check_source(bad_helper, "ag2.py")
        self.assertIn("LLM-05", [v.rule for v in part_violations])
        self.assertIn("LLM-05", [v.rule for v in helper_violations])
        self.assertIn("->", part_violations[0].message)
        self.assertEqual(check_python.check_source(good_name, "ag3.py"), [])

    def test_test_runner_branching_flags_llm07_in_prod_but_not_tests(self) -> None:
        import check_python

        prod_pytest = (
            "import os\n\n"
            "def send_email(address: str) -> str:\n"
            '    if os.getenv("PYTEST_CURRENT_TEST"):\n'
            '        return "stub"\n'
            "    return address\n"
        )
        prod_unittest = (
            "import sys\n\n"
            "def send_email(address: str) -> str:\n"
            '    if "unittest" in sys.modules:\n'
            '        return "stub"\n'
            "    return address\n"
        )
        self.assertIn("LLM-07", [v.rule for v in check_python.check_source(prod_pytest, "src/mailer.py")])
        self.assertIn("LLM-07", [v.rule for v in check_python.check_source(prod_unittest, "src/mailer.py")])
        self.assertEqual(check_python.check_source(prod_pytest, "tests/test_mailer.py"), [])

    def test_fix_reorders_undecorated_functions_and_preserves_comments(self) -> None:
        import check_python

        out_of_order = (
            '"""Module doc."""\n\n'
            "# L3 Leaf\n"
            "def format_usd(amount_cents: int) -> str:\n"
            '    return f"${amount_cents / 100:.2f}"\n\n'
            "# L0 Orchestrator\n"
            "def render_total(amount_cents: int) -> str:\n"
            "    return format_usd(amount_cents)\n"
        )
        fixed_source, refusal = check_python.fix_source(out_of_order)
        self.assertIsNone(refusal)
        self.assertLess(fixed_source.index("def render_total"), fixed_source.index("def format_usd"))
        self.assertIn("# L3 Leaf\ndef format_usd", fixed_source)
        self.assertEqual(check_python.check_source(fixed_source, "fixed.py"), [])

        checker = ROOT_DIR / "enforcement" / "check_python.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "sample.py"
            target.write_text(out_of_order, encoding="utf-8")
            dry_proc = subprocess.run(
                [sys.executable, str(checker), "--fix", "--dry-run", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(dry_proc.returncode, 0)
            self.assertIn("@@", dry_proc.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), out_of_order)

            fix_proc = subprocess.run(
                [sys.executable, str(checker), "--fix", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(fix_proc.returncode, 0)
            self.assertEqual(check_python.check_source(target.read_text(encoding="utf-8"), str(target)), [])

    def test_fix_refuses_decorated_interleaved_and_cyclic_files(self) -> None:
        import check_python

        decorated = (
            "@register\n"
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n\n"
            "def caller(count: int) -> int:\n"
            "    return leaf(count)\n"
        )
        interleaved = (
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n\n"
            "DISPATCH = leaf\n\n"
            "def caller(count: int) -> int:\n"
            "    return leaf(count)\n"
        )
        cyclic = (
            "def ping(count: int) -> int:\n"
            "    return pong(count)\n\n"
            "def pong(count: int) -> int:\n"
            "    return ping(count)\n"
        )
        for sample in (decorated, interleaved, cyclic):
            fixed_source, refusal = check_python.fix_source(sample)
            self.assertIsNotNone(refusal)
            self.assertEqual(fixed_source, sample)

    def test_fix_preserves_shebang_and_separates_trailing_main_guard(self) -> None:
        import check_python

        sample = (
            "#!/usr/bin/env python3\n"
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n\n"
            "def orchestrate(count: int) -> int:\n"
            "    return leaf(count)\n"
            'if __name__ == "__main__":\n'
            "    orchestrate(1)\n"
        )
        fixed_source, refusal = check_python.fix_source(sample)
        self.assertIsNone(refusal)
        self.assertTrue(fixed_source.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("1if", fixed_source)
        self.assertEqual(check_python.check_source(fixed_source, "ok.py"), [])

    def test_unknown_ignore_rule_id_does_not_act_as_wildcard(self) -> None:
        import check_python

        typo_ignore = (
            '# topdown-ignore(CANON-9, reason="typo rule id")\n'
            "def process(data: int) -> int:\n"
            "    return data\n"
        )
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(typo_ignore, "t.py")])

    def test_topdown_ignore_suppresses_with_reason_and_flags_without(self) -> None:
        import check_python

        suppressed = (
            '# topdown-ignore(CANON-05, reason="legacy state machine")\n'
            "def pick(flag_a: bool, flag_b: bool) -> str:\n"
            '    return "a" if flag_a else ("b" if flag_b else "c")\n'
        )
        missing_reason = (
            "# topdown-ignore(CANON-05)\n"
            "def pick(flag_a: bool, flag_b: bool) -> str:\n"
            '    return "a" if flag_a else ("b" if flag_b else "c")\n'
        )
        self.assertEqual(check_python.check_source(suppressed, "ig1.py"), [])
        self.assertIn("CANON-08", [v.rule for v in check_python.check_source(missing_reason, "ig2.py")])

    def test_syntax_error_and_missing_file_exit_2_and_json_output(self) -> None:
        checker = ROOT_DIR / "enforcement" / "check_python.py"
        missing_proc = subprocess.run(
            [sys.executable, str(checker), str(ROOT_DIR / "nonexistent_file.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing_proc.returncode, 2)
        self.assertTrue(missing_proc.stderr.strip())

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_syntax = Path(tmp_dir) / "bad.py"
            bad_syntax.write_text("def broken(:\n", encoding="utf-8")
            syntax_proc = subprocess.run(
                [sys.executable, str(checker), str(bad_syntax)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(syntax_proc.returncode, 2)
            self.assertTrue(syntax_proc.stderr.strip())

            viol_file = Path(tmp_dir) / "viol.py"
            viol_file.write_text("def fn(data: int) -> int:\n    return data\n", encoding="utf-8")
            json_proc = subprocess.run(
                [sys.executable, str(checker), "--json", str(viol_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(json_proc.returncode, 1)
            payload = json.loads(json_proc.stdout)
            self.assertEqual(payload[0]["rule"], "LLM-03")

    def test_bare_topdown_ignore_and_parens_in_reason_and_strings(self) -> None:
        import check_python

        bare = "# topdown-ignore\ndef clean(count: int) -> int:\n    return count\n"
        parens_reason = (
            '# topdown-ignore(CANON-05, reason="mirrors f() behavior")\n'
            "def pick(flag_a: bool, flag_b: bool) -> str:\n"
            '    return "a" if flag_a else ("b" if flag_b else "c")\n'
        )
        in_string = (
            'def describe() -> str:\n    return "# topdown-ignore(CANON-05)"\n'
        )
        self.assertIn("CANON-08", [v.rule for v in check_python.check_source(bare, "b.py")])
        self.assertEqual(check_python.check_source(parens_reason, "p.py"), [])
        self.assertEqual(check_python.check_source(in_string, "s.py"), [])

    def test_class_methods_are_checked_and_multiline_comprehension_handled(self) -> None:
        import check_python

        cls_bad = (
            "class Worker:\n"
            "    def run(self, data: int) -> int:\n"
            "        return data\n"
        )
        multiline_comp = (
            "def double_all(amounts: list[int]) -> list[int]:\n"
            "    return [\n"
            "        item * 2\n"
            "        for item in amounts\n"
            "    ]\n"
        )
        same_line_vague = (
            "def double_all(amounts: list[int]) -> list[int]:\n"
            "    data = [item * 2 for item in amounts]\n"
            "    return data\n"
        )
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(cls_bad, "c.py")])
        self.assertEqual(check_python.check_source(multiline_comp, "mc.py"), [])
        self.assertIn("LLM-03", [v.rule for v in check_python.check_source(same_line_vague, "sl.py")])

    def test_invalid_utf8_and_nul_bytes_exit_2(self) -> None:
        checker = ROOT_DIR / "enforcement" / "check_python.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_utf8 = Path(tmp_dir) / "bad_utf8.py"
            bad_utf8.write_bytes(b"\xff\xfe\x00\x00")
            proc = subprocess.run(
                [sys.executable, str(checker), str(bad_utf8)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.strip())

            nul_file = Path(tmp_dir) / "nul.py"
            nul_file.write_bytes(b"x = 1\x00\n")
            proc_nul = subprocess.run(
                [sys.executable, str(checker), str(nul_file)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_nul.returncode, 2)
            self.assertTrue(proc_nul.stderr.strip())

    def test_implicit_state_machine_flags_canon03_and_orthogonal_booleans_pass(self) -> None:
        import check_python

        bad_dataclass = (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class FetchState:\n"
            "    is_loading: bool = False\n"
            "    is_error: bool = False\n"
        )
        bad_params = (
            "def render_badge(*, is_pending: bool, is_completed: bool) -> str:\n"
            '    return "done" if is_completed else "wait"\n'
        )
        suppressed = (
            '# topdown-ignore(CANON-03, reason="external wire format")\n'
            "class WireState:\n"
            "    is_loading: bool = False\n"
            "    is_error: bool = False\n"
        )
        orthogonal = (
            "class Account:\n"
            "    is_active: bool = True\n"
            "    is_verified: bool = False\n"
            "    is_loading: bool = False\n"
        )
        non_bool_counts = (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class QueueCounts:\n"
            "    pending: int = 0\n"
            "    failed: int = 0\n"
            "    succeeded: int = 0\n\n"
            "def format_counts(pending: int, failed: int) -> str:\n"
            '    return f"{pending}/{failed}"\n'
        )
        init_and_locals = (
            "class FetchController:\n"
            "    def __init__(self) -> None:\n"
            "        self.is_loading = False\n"
            "        self.is_error = False\n\n"
            "def run_request() -> bool:\n"
            "    is_loading = True\n"
            "    is_error = False\n"
            "    return is_loading or is_error\n"
        )
        stacked_decorator_ignore = (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            '# topdown-ignore(CANON-03, reason="legacy schema")\n'
            "class LegacyState:\n"
            "    is_loading: bool = False\n"
            "    is_error: bool = False\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_dataclass, "state.py")])
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_params, "view.py")])
        self.assertEqual(check_python.check_source(suppressed, "wire.py"), [])
        self.assertEqual(check_python.check_source(orthogonal, "acct.py"), [])
        self.assertEqual(check_python.check_source(non_bool_counts, "counts.py"), [])
        self.assertEqual(len([v for v in check_python.check_source(init_and_locals, "init.py") if v.rule == "CANON-03"]), 2)
        self.assertEqual(check_python.check_source(stacked_decorator_ignore, "dec.py"), [])


if __name__ == "__main__":
    unittest.main()
