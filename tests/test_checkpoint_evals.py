import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType

ROOT_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = ROOT_DIR / "tests" / "checkpoint_evals"
CHECK_PYTHON_PATH = ROOT_DIR / "enforcement" / "check_python.py"
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "enforcement"))

import check_python

HAS_TREE_SITTER = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_language_pack") is not None
)

CHECKPOINT_EXPECTATIONS: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    ("cp1_base.py", frozenset(), frozenset()),
    ("cp2_naive.py", frozenset({"CANON-03", "LLM-08", "CANON-05"}), frozenset({"LLM-08", "CANON-05"})),
    ("cp2_clean.py", frozenset(), frozenset()),
    ("cp3_naive.py", frozenset({"CANON-01", "CANON-03"}), frozenset({"CANON-01"})),
    ("cp3_clean.py", frozenset(), frozenset()),
)

CLEAN_CHECKPOINTS: tuple[str, ...] = ("cp1_base.py", "cp2_clean.py", "cp3_clean.py")
EXPECTED_OUTLINE_NAMES: list[str] = [
    "process_payout_batch",
    "settle_eligible_payouts",
    "build_settled_payout",
    "format_usd_cents",
]


class TestCheckpointEvals(unittest.TestCase):
    def test_checkpoint_fixtures_do_not_match_test_glob(self) -> None:
        self.assertEqual(list(EVALS_DIR.glob("test_*.py")), [])
        expected_files = {name for name, _, _ in CHECKPOINT_EXPECTATIONS}
        actual_files = {path.name for path in EVALS_DIR.glob("*.py")}
        self.assertEqual(actual_files, expected_files)

    def test_check_python_detects_erosion_and_clean_refactors(self) -> None:
        for filename, expected_py_rules, _ in CHECKPOINT_EXPECTATIONS:
            target = EVALS_DIR / filename
            source = target.read_text(encoding="utf-8")
            violations = check_python.check_source(source, str(target))
            actual_rules = frozenset(entry.rule for entry in violations)
            self.assertEqual(actual_rules, expected_py_rules, msg=f"Unexpected rules for {filename}: {violations}")

        cp2_viols = check_python.check_source((EVALS_DIR / "cp2_naive.py").read_text(encoding="utf-8"), "cp2_naive.py")
        llm08_msgs = [entry.message for entry in cp2_viols if entry.rule == "LLM-08"]
        self.assertEqual(len(llm08_msgs), 1)
        self.assertIn("format_usd_cents", llm08_msgs[0])

        cp3_viols = check_python.check_source((EVALS_DIR / "cp3_naive.py").read_text(encoding="utf-8"), "cp3_naive.py")
        canon03_msgs = [entry.message for entry in cp3_viols if entry.rule == "CANON-03"]
        self.assertTrue(any("is_retrying" in msg and "is_failed" in msg for msg in canon03_msgs), msg=canon03_msgs)

    def test_clean_checkpoints_emit_valid_outline_dag(self) -> None:
        for filename in CLEAN_CHECKPOINTS:
            target = EVALS_DIR / filename
            source = target.read_text(encoding="utf-8")
            outline_rows = check_python.build_file_outline(source, str(target))
            assert_valid_outline_dag(self, outline_rows, label=filename)

            proc = subprocess.run(
                [sys.executable, str(CHECK_PYTHON_PATH), "--outline", "--json", str(target)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(len(payload), 1)
            assert_valid_outline_dag(self, payload[0]["functions"], label=f"{filename} --outline --json")

    def test_clean_checkpoints_execute_behaviorally(self) -> None:
        cp1 = load_checkpoint_module(EVALS_DIR / "cp1_base.py", "checkpoint_eval_cp1_base")
        cp1_requests = (
            cp1.PayoutRequest("acct_a", 10_000, True, True),
            cp1.PayoutRequest("acct_b", 5_000, False, True),
        )
        cp1_saved: list[object] = []
        cp1_summary = cp1.process_payout_batch(
            cp1_requests,
            tier="standard",
            min_fee_cents=150,
            is_dry_run=False,
            save_summary=cp1_saved.append,
        )
        self.assertEqual(cp1_summary.total_net_cents, 9_750)
        self.assertEqual(cp1_summary.formatted_total, "$97.50")
        self.assertEqual(len(cp1_saved), 1)

        cp2 = load_checkpoint_module(EVALS_DIR / "cp2_clean.py", "checkpoint_eval_cp2_clean")
        cp2_requests = (
            cp2.PayoutRequest("acct_fx", 10_000, True, True, tier_override="vip", fx_rate_bps=12_000),
        )
        cp2_summary = cp2.process_payout_batch(
            cp2_requests,
            tier="standard",
            min_fee_cents=150,
            is_dry_run=True,
        )
        self.assertEqual(cp2_summary.total_net_cents, 11_880)
        self.assertEqual(cp2_summary.formatted_total, "$118.80")

        cp3 = load_checkpoint_module(EVALS_DIR / "cp3_clean.py", "checkpoint_eval_cp3_clean")
        cp3_requests = (
            cp3.PayoutRequest("acct_ok", "idem_1", 10_000, True, True),
            cp3.PayoutRequest("acct_dup", "idem_1", 10_000, True, True),
            cp3.PayoutRequest("acct_retry", "idem_2", 10_000, True, True, retry_attempt=1),
            cp3.PayoutRequest("acct_fail", "idem_3", 10_000, True, True, retry_attempt=3),
        )
        cp3_summary = cp3.process_payout_batch(
            cp3_requests,
            tier="standard",
            min_fee_cents=150,
            is_dry_run=True,
        )
        self.assertEqual(cp3_summary.total_net_cents, 9_750)
        self.assertEqual(
            [payout.status for payout in cp3_summary.settled_payouts],
            [
                cp3.PayoutStatus.SETTLED,
                cp3.PayoutStatus.SKIPPED_DUPLICATE,
                cp3.PayoutStatus.RETRYING,
                cp3.PayoutStatus.FAILED,
            ],
        )

    @unittest.skipUnless(HAS_TREE_SITTER, "Optional tree-sitter engine dependencies not installed")
    def test_engine_analyzer_matches_checkpoint_expectations(self) -> None:
        from engine.analyzer import analyze_source, outline_source

        try:
            for filename, _, expected_engine_rules in CHECKPOINT_EXPECTATIONS:
                target = EVALS_DIR / filename
                source = target.read_text(encoding="utf-8")
                violations = analyze_source(source, str(target))
                actual_rules = frozenset(entry.rule for entry in violations)
                self.assertEqual(
                    actual_rules,
                    expected_engine_rules,
                    msg=f"Unexpected engine rules for {filename}: {violations}",
                )

            for filename in CLEAN_CHECKPOINTS:
                target = EVALS_DIR / filename
                outline_rows = outline_source(target.read_text(encoding="utf-8"), str(target))
                assert_valid_outline_dag(self, outline_rows, label=f"engine:{filename}")
        except Exception as exc:
            if "DownloadError" in type(exc).__name__ or "DownloadError" in str(exc):
                self.skipTest(f"Tree-sitter Python grammar unavailable offline: {exc}")
            raise


def load_checkpoint_module(module_path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def assert_valid_outline_dag(
    test_case: unittest.TestCase,
    outline_rows: list[dict[str, object]],
    *,
    label: str,
) -> None:
    test_case.assertEqual([row["name"] for row in outline_rows], EXPECTED_OUTLINE_NAMES, msg=label)
    start_lines = [int(row["start_line"]) for row in outline_rows]
    test_case.assertEqual(start_lines, sorted(start_lines), msg=label)
    test_case.assertEqual(len(set(start_lines)), 4, msg=label)
    for tier_idx, row in enumerate(outline_rows):
        expected_tier = f"L{tier_idx}"
        test_case.assertEqual(row["declared_tier"], expected_tier, msg=label)
        test_case.assertEqual(row["valid_tiers"], [expected_tier], msg=label)
    edges_by_name = {
        str(row["name"]): (row["callers"], row["callees"])
        for row in outline_rows
    }
    test_case.assertEqual(edges_by_name["process_payout_batch"], ([], ["format_usd_cents", "settle_eligible_payouts"]), msg=label)
    test_case.assertEqual(edges_by_name["settle_eligible_payouts"], (["process_payout_batch"], ["build_settled_payout"]), msg=label)
    test_case.assertEqual(edges_by_name["build_settled_payout"], (["settle_eligible_payouts"], ["format_usd_cents"]), msg=label)
    test_case.assertEqual(edges_by_name["format_usd_cents"], (["build_settled_payout", "process_payout_batch"], []), msg=label)


if __name__ == "__main__":
    unittest.main()
