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
        branch_locals_and_defaults = (
            "def run_sync(should_run: bool, loading=False) -> bool:\n"
            "    if should_run:\n"
            "        is_error = True\n"
            "        return is_error\n"
            "    return loading\n"
        )
        nested_classes_separate = (
            "class Outer:\n"
            "    def __init__(self) -> None:\n"
            "        self.is_loading = False\n"
            "    class Inner:\n"
            "        def __init__(self) -> None:\n"
            "            self.is_error = False\n"
        )
        class_ignore_does_not_hide_method = (
            '# topdown-ignore(CANON-03, reason="legacy state class")\n'
            "class LegacyController:\n"
            "    is_loading: bool = False\n"
            "    is_error: bool = False\n"
            "    def compute(self, a1: int, a2: int, a3: int, a4: int) -> int:\n"
            "        return a1 + a2 + a3 + a4\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_dataclass, "state.py")])
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(bad_params, "view.py")])
        self.assertEqual(check_python.check_source(suppressed, "wire.py"), [])
        self.assertEqual(check_python.check_source(orthogonal, "acct.py"), [])
        self.assertEqual(check_python.check_source(non_bool_counts, "counts.py"), [])
        self.assertEqual(len([v for v in check_python.check_source(init_and_locals, "init.py") if v.rule == "CANON-03"]), 2)
        self.assertEqual(check_python.check_source(stacked_decorator_ignore, "dec.py"), [])
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(branch_locals_and_defaults, "br.py")])
        self.assertEqual(check_python.check_source(nested_classes_separate, "nested.py"), [])
        self.assertEqual([v.line for v in check_python.check_source(class_ignore_does_not_hide_method, "cls.py")], [5])

    def test_tier_signposts_verified_and_stacked_ignore_supported(self) -> None:
        import check_python

        valid_with_l0_adapter = (
            "# [L0] Orchestrator\n"
            "def run_job(job_id: str) -> str:\n"
            "    raw = fetch_payload(job_id)\n"
            "    return transform_payload(raw)\n\n"
            "# [L0] I/O Adapter Leaf\n"
            "def fetch_payload(job_id: str) -> str:\n"
            "    return job_id\n\n"
            "# [L1] Pure Workflow\n"
            "def transform_payload(raw: str) -> str:\n"
            "    return normalize_text(raw)\n\n"
            "# [L3] Leaf Primitive\n"
            "def normalize_text(raw: str) -> str:\n"
            "    return raw.strip()\n"
        )
        non_leaf_l3 = (
            "# [L3] Wrong tag on caller\n"
            "def caller(count: int) -> int:\n"
            "    return leaf(count)\n\n"
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n"
        )
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
        duplicate_tags = (
            "# [L0] First\n"
            "# [L1] Second\n"
            "def solo(count: int) -> int:\n"
            "    return count\n"
        )
        stacked_ignore = (
            '# topdown-ignore(CANON-05, reason="migrating from [L1] state machine")\n'
            "# [L0] Entrypoint\n"
            "def pick(flag_a: bool, flag_b: bool) -> str:\n"
            '    return "a" if flag_a else ("b" if flag_b else "c")\n'
        )
        self.assertEqual(check_python.check_source(valid_with_l0_adapter, "ok.py"), [])
        self.assertIn("CANON-01", [v.rule for v in check_python.check_source(non_leaf_l3, "l3.py")])
        self.assertIn("CANON-01", [v.rule for v in check_python.check_source(gap_compressed, "gap.py")])
        self.assertIn("CANON-01", [v.rule for v in check_python.check_source(duplicate_tags, "dup.py")])
        self.assertEqual(check_python.check_source(stacked_ignore, "stack.py"), [])
        l1_calling_l0 = (
            "# [L1] Pure Workflow\n"
            "def workflow(count: int) -> int:\n"
            "    return leaf(count)\n\n"
            "# [L0] Invalid L0 below L1\n"
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n"
        )
        self.assertIn("CANON-01", [v.rule for v in check_python.check_source(l1_calling_l0, "l1_l0.py")])
        no_adjacent_start_leak = (
            'def first() -> int: return 1  # topdown-ignore(CANON-05, reason="only first")\n'
            "def second(flag_a: bool, flag_b: bool, flag_c: bool) -> int:\n"
            "    if flag_a:\n"
            "        if flag_b:\n"
            "            if flag_c:\n"
            "                return 1\n"
            "    return 0\n"
        )
        self.assertIn("CANON-05", [v.rule for v in check_python.check_source(no_adjacent_start_leak, "leak.py")])
        deep_outline = (
            "def f0(v: int) -> int:\n    return f1(v)\n"
            "def f1(v: int) -> int:\n    return f2(v)\n"
            "def f2(v: int) -> int:\n    return f3(v)\n"
            "def f3(v: int) -> int:\n    return f4(v)\n"
            "def f4(v: int) -> int:\n    return v\n"
        )
        outline_rows = check_python.build_file_outline(deep_outline, "deep.py")
        self.assertEqual(outline_rows[2]["valid_tiers"], [])
        self.assertIn("[invalid] f2", check_python.format_outline_text([{"path": "deep.py", "functions": outline_rows}]))

    def test_outline_cli_text_and_json_and_flag_conflicts(self) -> None:
        checker = ROOT_DIR / "enforcement" / "check_python.py"
        after_fixture = ROOT_DIR / "examples" / "fixtures" / "python_after.py"
        text_proc = subprocess.run(
            [sys.executable, str(checker), "--outline", str(after_fixture)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(text_proc.returncode, 0, msg=text_proc.stderr)
        self.assertIn("[L0] process_payout_batch (L37-L55, 19L) -> format_cents_usd, settle_eligible_payouts", text_proc.stdout)
        self.assertIn("[L1] settle_eligible_payouts (L59-L79, 21L) <- process_payout_batch -> calculate_fee_cents, format_cents_usd", text_proc.stdout)

        json_proc = subprocess.run(
            [sys.executable, str(checker), "--outline", "--json", str(after_fixture)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(json_proc.returncode, 0, msg=json_proc.stderr)
        payload = json.loads(json_proc.stdout)
        self.assertEqual(len(payload), 1)
        funcs = payload[0]["functions"]
        self.assertEqual(funcs[0]["name"], "process_payout_batch")
        self.assertEqual(funcs[0]["declared_tier"], "L0")
        self.assertEqual(funcs[0]["valid_tiers"], ["L0"])

        conflict_proc = subprocess.run(
            [sys.executable, str(checker), "--outline", "--fix", str(after_fixture)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(conflict_proc.returncode, 2)

    def test_overload_stubs_excluded_and_headers_absorbed(self) -> None:
        import check_python

        overloaded = (
            "import typing\n"
            "from typing import overload\n\n"
            "# [L0] Orchestrator\n"
            "def run_pipeline(raw_value: str) -> str:\n"
            "    return parse_value(raw_value)\n\n"
            "# [L3] Leaf parser\n"
            '# topdown-ignore(CANON-03, reason="legacy overloaded positional signature")\n'
            "@overload\n"
            "def parse_value(val: int, p2: int, p3: int, x: int) -> int: ...\n"
            "@typing.overload\n"
            "def parse_value(val: str, p2: int, p3: int, x: int) -> str: ...\n"
            "def parse_value(val: int | str, p2: int = 0, p3: int = 0, extra: int = 0) -> int | str:\n"
            "    return val\n\n"
            "class Formatter:\n"
            '    # topdown-ignore(CANON-03, reason="legacy method overload")\n'
            "    @overload\n"
            "    def format_cell(self, raw: int, p2: int, p3: int, p4: int) -> str: ...\n"
            "    @typing.overload\n"
            "    def format_cell(self, raw: str, p2: int, p3: int, p4: int) -> str: ...\n"
            "    def format_cell(self, raw: int | str, p2: int = 0, p3: int = 0, p4: int = 0) -> str:\n"
            "        return str(raw)\n"
        )
        self.assertEqual(check_python.check_source(overloaded, "ov.py"), [])
        outline = check_python.build_file_outline(overloaded, "ov.py")
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
        self.assertIn("CANON-01", [v.rule for v in check_python.check_source(conflicting_tags, "ov_dup.py")])

        non_contiguous = (
            "from typing import overload\n\n"
            "# [L3] Interrupted header\n"
            "@overload\n"
            "def parse_value(val: int) -> int: ...\n\n"
            "SENTINEL = 1\n\n"
            "def parse_value(val: int | str) -> int | str:\n"
            "    return val\n"
        )
        nc_outline = check_python.build_file_outline(non_contiguous, "ov_nc.py")
        self.assertIsNone(nc_outline[0]["declared_tier"])

        fix_with_overload = (
            "from typing import overload\n\n"
            "@overload\n"
            "def parse_value(val: int) -> int: ...\n\n"
            "def parse_value(val: int | str) -> int | str:\n"
            "    return val\n\n"
            "def entry(raw: str) -> int | str:\n"
            "    return parse_value(raw)\n"
        )
        fixed, refusal = check_python.fix_source(fix_with_overload)
        self.assertIsNotNone(refusal)
        self.assertEqual(fixed, fix_with_overload)

    def test_duplicate_top_level_names_isolated_and_fix_bails(self) -> None:
        import check_python

        dup_source = (
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
        self.assertEqual(check_python.check_source(dup_source, "dup_names.py"), [])
        outline = check_python.build_file_outline(dup_source, "dup_names.py")
        self.assertEqual(len(outline), 4)
        self.assertEqual(outline[0]["callees"], ["render_value"])
        self.assertEqual(outline[1]["declared_tier"], "L1")
        self.assertEqual(outline[1]["callers"], ["run_job"])
        self.assertEqual(outline[1]["callees"], ["double_amount"])
        self.assertEqual(outline[2]["declared_tier"], "L3")
        self.assertEqual(outline[2]["callers"], ["run_job"])
        self.assertEqual(outline[2]["callees"], [])
        self.assertEqual(outline[3]["callers"], ["render_value"])

        dup_bad_ancestor = (
            "# [L2] First dup\n"
            "def dup_step(count: int) -> int:\n"
            "    return bad_leaf(count)\n\n"
            "# [L0] Second dup\n"
            "def dup_step(count: int) -> int:\n"
            "    return count\n\n"
            "# [L2] Too shallow under [L2] dup_step\n"
            "def bad_leaf(count: int) -> int:\n"
            "    return count + 1\n"
        )
        bad_msgs = [v.message for v in check_python.check_source(dup_bad_ancestor, "dup_anc.py") if v.rule == "CANON-01"]
        self.assertTrue(any("below 'dup_step' ([L2])" in msg for msg in bad_msgs), msg=bad_msgs)

        dup_fix_source = (
            "def dup_step(count: int) -> int:\n"
            "    return count + 1\n\n"
            "def dup_step(count: int) -> int:\n"
            "    return count + 2\n"
        )
        fixed, refusal = check_python.fix_source(dup_fix_source)
        self.assertIsNotNone(refusal)
        self.assertEqual(fixed, dup_fix_source)

    def test_decorator_calls_excluded_from_local_calls(self) -> None:
        import check_python

        source = (
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
        self.assertEqual(check_python.check_source(source, "dec_calls.py"), [])
        outline = check_python.build_file_outline(source, "dec_calls.py")
        handler_row = next(row for row in outline if row["name"] == "run_handler")
        finish_row = next(row for row in outline if row["name"] == "finish_task")
        self.assertEqual(handler_row["callees"], ["default_prefix", "default_suffix", "finish_task"])
        self.assertEqual(finish_row["callees"], [])

    def test_tier_tag_no_double_diagnostic_and_no_l4_suggestion(self) -> None:
        import check_python

        l1_calling_l0 = (
            "# [L1] Pure Workflow\n"
            "def workflow(count: int) -> int:\n"
            "    return leaf(count)\n\n"
            "# [L0] Invalid L0 below L1\n"
            "def leaf(count: int) -> int:\n"
            "    return count + 1\n"
        )
        l1_l0_viols = [v for v in check_python.check_source(l1_calling_l0, "l1_l0.py") if v.rule == "CANON-01"]
        self.assertEqual(len(l1_l0_viols), 1)
        self.assertEqual(l1_l0_viols[0].line, 6)

        l2_to_l2_chain = (
            "# [L2] Too deep start\n"
            "def mid_step(count: int) -> int:\n"
            "    return sub_step(count)\n\n"
            "# [L2] Intermediate\n"
            "def sub_step(count: int) -> int:\n"
            "    return inner_leaf(count)\n\n"
            "# [L2] Callee below [L2] at dist 2\n"
            "def inner_leaf(count: int) -> int:\n"
            "    return count + 1\n"
        )
        chain_viols = [v for v in check_python.check_source(l2_to_l2_chain, "no_l4.py") if v.rule == "CANON-01"]
        self.assertTrue(chain_viols)
        for viol in chain_viols:
            self.assertNotIn("L4", viol.message)
        leaf_msgs = [v.message for v in chain_viols if v.line == 10]
        self.assertEqual(len(leaf_msgs), 1)
        self.assertIn("mid_step", leaf_msgs[0])
        self.assertIn("[L2]", leaf_msgs[0])

    def test_canon03_non_literal_self_lifecycle_assignments(self) -> None:
        import check_python

        non_literal_bad = (
            "class SyncWorker:\n"
            "    def __init__(self, loading: bool, err: str) -> None:\n"
            "        self.is_loading = loading\n"
            "        self.has_error = bool(err)\n"
        )
        was_prefix_bad = (
            "class RetryWorker:\n"
            "    def __init__(self, prev_retry: bool, done_flag: bool) -> None:\n"
            "        self.was_retrying = prev_retry\n"
            "        self.is_loaded = bool(done_flag)\n"
        )
        int_literal_ok = (
            "class NumericFlags:\n"
            "    def __init__(self) -> None:\n"
            "        self.is_loading = 1\n"
            "        self.is_error = 0\n"
        )
        bare_non_literal_ok = (
            "class QueueMetrics:\n"
            "    def __init__(self, count: int, items: list[str]) -> None:\n"
            "        self.loading = count\n"
            "        self.pending = len(items)\n"
        )
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(non_literal_bad, "nl1.py")])
        self.assertIn("CANON-03", [v.rule for v in check_python.check_source(was_prefix_bad, "nl2.py")])
        self.assertEqual(check_python.check_source(int_literal_ok, "nl3.py"), [])
        self.assertEqual(check_python.check_source(bare_non_literal_ok, "nl4.py"), [])

    def test_llm08_duplicate_leaf_detection_and_exemptions(self) -> None:
        import check_python

        renamed_dups = (
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
        viols = [v for v in check_python.check_source(renamed_dups, "dups.py") if v.rule == "LLM-08"]
        self.assertEqual(len(viols), 1)
        self.assertEqual(viols[0].line, 11)
        self.assertEqual(
            viols[0].message,
            "Function 'normalize_beta' duplicates leaf helper 'normalize_alpha' (line 1); reuse the existing helper instead of adding a duplicate.",
        )

        suppressed = renamed_dups.replace(
            "def normalize_beta",
            '# topdown-ignore(LLM-08, reason="distinct domain concept")\ndef normalize_beta',
        )
        self.assertEqual([v for v in check_python.check_source(suppressed, "supp.py") if v.rule == "LLM-08"], [])

        short_with_doc = (
            "def short_one(raw_text: str) -> str:\n"
            '    """Long\n    multi-line\n    docstring."""\n'
            "    # comment line\n\n"
            "    step_one = raw_text.strip()\n"
            "    step_two = step_one.lower()\n"
            "    step_three = step_two.replace('-', '_')\n"
            "    step_four = step_three.title()\n"
            "    return step_four\n\n"
            "def short_two(input_str: str) -> str:\n"
            '    """Another\n    multi-line\n    docstring."""\n'
            "    # comment line\n\n"
            "    part_one = input_str.strip()\n"
            "    part_two = part_one.lower()\n"
            "    part_three = part_two.replace('-', '_')\n"
            "    part_four = part_three.title()\n"
            "    return part_four\n"
        )
        self.assertEqual(check_python.check_source(short_with_doc, "short.py"), [])

        l0_exempt = (
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
            "    return step_five\n"
        )
        self.assertEqual(check_python.check_source(l0_exempt, "l0_exempt.py"), [])

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
        two_tier_viols = [v for v in check_python.check_source(untagged_two_tier, "two_tier.py") if v.rule == "LLM-08"]
        self.assertEqual(len(two_tier_viols), 1)

        non_matching_cases = [
            # swapped params
            (
                "def sub_ab(first: int, second: int) -> int:\n"
                "    val_a = first + 1\n    val_b = second + 2\n    val_c = val_a * 3\n"
                "    val_d = val_b * 4\n    diff = val_c - val_d\n    return diff\n\n"
                "def sub_ba(first: int, second: int) -> int:\n"
                "    val_a = second + 1\n    val_b = first + 2\n    val_c = val_a * 3\n"
                "    val_d = val_b * 4\n    diff = val_c - val_d\n    return diff\n"
            ),
            # = None vs missing default
            (
                "def def_none(amount: int | None = None) -> int:\n"
                "    step_one = amount or 0\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n\n"
                "def no_def(amount: int | None) -> int:\n"
                "    step_one = amount or 0\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n"
            ),
            # pos vs kw-only
            (
                "def pos_args(first: int, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n\n"
                "def kw_args(first: int, *, second: int) -> int:\n"
                "    step_one = first + second\n    step_two = step_one + 1\n    step_three = step_two * 2\n"
                "    step_four = step_three - 3\n    step_five = step_four + 4\n    return step_five\n"
            ),
            # def vs async def
            (
                "def sync_fn(amount: int) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "async def async_fn(amount: int) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n"
            ),
            # True vs 1 vs 1.0
            (
                "def use_bool(amount: int) -> float:\n"
                "    step_one = amount + True\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def use_int(amount: int) -> float:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def use_float(amount: int) -> float:\n"
                "    step_one = amount + 1.0\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n"
            ),
            # + vs -
            (
                "def add_fn(amount: int) -> int:\n"
                "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
                "def sub_fn(amount: int) -> int:\n"
                "    step_one = amount - 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
                "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n"
            ),
        ]
        for idx, sample in enumerate(non_matching_cases):
            self.assertEqual(
                [v for v in check_python.check_source(sample, f"nm_{idx}.py") if v.rule == "LLM-08"],
                [],
                msg=f"Unexpected LLM-08 on case {idx}",
            )

        diff_defaults_and_non_leaf = (
            "def default_one(amount: int = 1) -> int:\n"
            "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
            "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
            "def default_two(amount: int = 2) -> int:\n"
            "    step_one = amount + 1\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
            "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
            "def non_leaf_a(amount: int) -> int:\n"
            "    step_one = leaf_op(amount)\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
            "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
            "def non_leaf_b(amount: int) -> int:\n"
            "    step_one = leaf_op(amount)\n    step_two = step_one + 2\n    step_three = step_two * 3\n"
            "    step_four = step_three - 4\n    step_five = step_four + 5\n    return step_five\n\n"
            "def leaf_op(amount: int) -> int:\n"
            "    return amount + 1\n"
        )
        self.assertEqual(
            [v for v in check_python.check_source(diff_defaults_and_non_leaf, "dd.py") if v.rule == "LLM-08"],
            [],
        )

        import ast
        tree = ast.parse(renamed_dups)
        top_fns = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        direct_viols = check_python.check_duplicate_leaves(
            top_fns,
            renamed_dups.split("\n"),
            "direct.py",
            call_graph={(fn.name, fn.lineno): set() for fn in top_fns},
            declared_tags={(fn.name, fn.lineno): None for fn in top_fns},
            comment_lines=set(),
        )
        self.assertEqual(len(direct_viols), 1)
        self.assertEqual(direct_viols[0].rule, "LLM-08")


if __name__ == "__main__":
    unittest.main()
