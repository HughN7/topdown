import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = ROOT_DIR / "enforcement" / "run_hook.py"
sys.path.insert(0, str(ROOT_DIR / "enforcement"))

import run_hook


class TestRunHook(unittest.TestCase):
    def test_clean_python_file_exits_0(self) -> None:
        clean_fixture = ROOT_DIR / "examples" / "fixtures" / "python_after.py"
        payload = json.dumps({"tool_input": {"file_path": str(clean_fixture)}})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_violating_python_file_exits_2_with_stderr_guidance(self) -> None:
        bad_fixture = ROOT_DIR / "examples" / "fixtures" / "python_before.py"
        payload = json.dumps({"tool_input": {"file_path": str(bad_fixture)}})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("CANON-", proc.stderr)

    def test_unsupported_extension_exits_0(self) -> None:
        readme_path = ROOT_DIR / "README.md"
        payload = json.dumps({"file_path": str(readme_path)})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_violating_rust_file_exits_2(self) -> None:
        bad_rust = ROOT_DIR / "examples" / "fixtures" / "rust_before.rs"
        payload = json.dumps({"tool_input": {"path": str(bad_rust)}})
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("LLM-02", proc.stderr)

    def test_malformed_json_exits_0_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input="{not-json",
                cwd=tmp_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)

    def test_swift_file_dispatches_to_engine_when_installed(self) -> None:
        import importlib.util

        if importlib.util.find_spec("tree_sitter_language_pack") is None:
            self.skipTest("Optional tree-sitter engine not installed")
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_swift = Path(tmp_dir) / "sample.swift"
            bad_swift.write_text(
                "func applyFee(amountCents: Int) -> Int { return amountCents - 50 }\n\n"
                "func orchestrate(amountCents: Int) -> Int { return applyFee(amountCents: amountCents) }\n",
                encoding="utf-8",
            )
            payload = json.dumps({"tool_input": {"file_path": str(bad_swift)}})
            proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=payload,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and ("DownloadError" in proc.stderr or "skipped" in proc.stderr):
                self.skipTest("Swift tree-sitter grammar unavailable offline")
            self.assertEqual(proc.returncode, 2)
            self.assertIn("CANON-01", proc.stderr)

    def test_hook_appends_outline_on_canon01_and_llm08_failure(self) -> None:
        canon01_source = (
            "def leaf_step(amount_cents: int) -> int:\n"
            "    return amount_cents - 50\n\n"
            "def orchestrate(amount_cents: int) -> int:\n"
            "    return leaf_step(amount_cents)\n"
        )
        llm08_source = (
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            canon_file = Path(tmp_dir) / "canon01_sample.py"
            canon_file.write_text(canon01_source, encoding="utf-8")
            canon_proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(canon_file)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(canon_proc.returncode, 2)
            self.assertIn("CANON-01", canon_proc.stderr)
            self.assertIn("\nModule outline:\n", canon_proc.stderr)
            canon_outline = canon_proc.stderr.split("\nModule outline:\n", 1)[1]
            self.assertIn("leaf_step", canon_outline)
            self.assertIn("orchestrate", canon_outline)

            llm_file = Path(tmp_dir) / "llm08_sample.py"
            llm_file.write_text(llm08_source, encoding="utf-8")
            llm_proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(llm_file)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(llm_proc.returncode, 2)
            self.assertIn("LLM-08", llm_proc.stderr)
            self.assertIn("\nModule outline:\n", llm_proc.stderr)
            llm_outline = llm_proc.stderr.split("\nModule outline:\n", 1)[1]
            self.assertIn("clean_alpha", llm_outline)
            self.assertIn("clean_beta", llm_outline)

    def test_hook_skips_outline_on_non_structural_violation_and_rust(self) -> None:
        canon03_source = (
            "def many_args(first: int, second: int, third: int, fourth: int) -> int:\n"
            "    return first + second + third + fourth\n"
        )
        long_rust_body = "\n".join(f"    total += {idx};" for idx in range(82))
        long_rust_source = f"pub fn long_worker(mut total: i64) -> i64 {{\n{long_rust_body}\n    total\n}}\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            py_file = Path(tmp_dir) / "canon03_only.py"
            py_file.write_text(canon03_source, encoding="utf-8")
            py_proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(py_file)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(py_proc.returncode, 2)
            self.assertIn("CANON-03", py_proc.stderr)
            self.assertNotIn("\nModule outline:\n", py_proc.stderr)

            rs_fixture = ROOT_DIR / "examples" / "fixtures" / "rust_before.rs"
            rs_fixture_proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(rs_fixture)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(rs_fixture_proc.returncode, 2)
            self.assertNotIn("\nModule outline:\n", rs_fixture_proc.stderr)

            rs_canon01_file = Path(tmp_dir) / "long_rust.rs"
            rs_canon01_file.write_text(long_rust_source, encoding="utf-8")
            rs_canon01_proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(rs_canon01_file)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(rs_canon01_proc.returncode, 2)
            self.assertIn("CANON-01", rs_canon01_proc.stderr)
            self.assertNotIn("\nModule outline:\n", rs_canon01_proc.stderr)

    def test_hook_truncates_outline_over_25_lines(self) -> None:
        header_funcs = [
            "def helper_leaf(value: int) -> int:\n    return value + 1\n",
            "def orchestrate(value: int) -> int:\n    return helper_leaf(value)\n",
        ]
        extra_funcs = [
            f"def worker_fn_{idx:02d}(value: int) -> int:\n    return value + {idx + 2}\n"
            for idx in range(28)
        ]
        many_source = "\n".join(header_funcs + extra_funcs)
        with tempfile.TemporaryDirectory() as tmp_dir:
            many_file = Path(tmp_dir) / "many_functions.py"
            many_file.write_text(many_source, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(HOOK_SCRIPT)],
                input=json.dumps({"tool_input": {"file_path": str(many_file)}}),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("\nModule outline:\n", proc.stderr)
            outline_section = proc.stderr.split("\nModule outline:\n", 1)[1].rstrip("\r\n")
            outline_lines = outline_section.splitlines()
            self.assertEqual(len(outline_lines), 26)
            self.assertEqual(outline_lines[-1], "... (truncated)")
            self.assertNotIn("worker_fn_27", outline_section)

        lines_25 = "\n".join(f"  [L3] fn_{idx} (L{idx}-L{idx}, 1L)" for idx in range(25)) + "\n"
        lines_30 = "\n".join(f"  [L3] fn_{idx} (L{idx}-L{idx}, 1L)" for idx in range(30)) + "\n"
        with mock.patch.object(
            run_hook.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess(["cmd"], 0, stdout=lines_25, stderr=""),
                subprocess.CompletedProcess(["cmd"], 0, stdout=lines_30, stderr=""),
            ],
        ):
            exact_25 = run_hook.fetch_module_outline(("python", "check_python.py", "sample.py"), {})
            capped_30 = run_hook.fetch_module_outline(("python", "check_python.py", "sample.py"), {})
        self.assertEqual(len(exact_25.splitlines()), 25)
        self.assertNotIn("... (truncated)", exact_25)
        self.assertEqual(len(capped_30.splitlines()), 26)
        self.assertEqual(capped_30.splitlines()[-1], "... (truncated)")

    def test_supports_outline_targets(self) -> None:
        self.assertTrue(run_hook.supports_outline((sys.executable, "enforcement/check_python.py", "app.py")))
        self.assertTrue(run_hook.supports_outline(("node", "enforcement/check_ts.mjs", "app.ts")))
        self.assertTrue(run_hook.supports_outline((sys.executable, "-m", "engine.cli", "app.swift")))
        self.assertFalse(run_hook.supports_outline((sys.executable, "enforcement/check_rust.py", "lib.rs")))
        self.assertFalse(run_hook.supports_outline((sys.executable, "enforcement/check_rust.py", "check_python.py")))
        self.assertFalse(run_hook.supports_outline(("ruff", "app.py")))
        self.assertFalse(run_hook.supports_outline(()))

    def test_fetch_outline_error_handling_and_clean_output_joining(self) -> None:
        with mock.patch.object(
            run_hook.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd=["cmd"], timeout=5),
        ) as mock_run:
            self.assertEqual(run_hook.fetch_module_outline(("python", "check_python.py", "target.py"), {}), "")
        self.assertEqual(list(mock_run.call_args[0][0]), ["python", "check_python.py", "--outline", "target.py"])

        with mock.patch.object(run_hook.subprocess, "run", side_effect=OSError("missing binary")):
            self.assertEqual(run_hook.fetch_module_outline(("python", "-m", "engine.cli", "target.go"), {}), "")

        with mock.patch.object(
            run_hook.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess(["cmd"], 2, stdout="partial", stderr="err"),
                subprocess.CompletedProcess(["cmd"], 0, stdout="   \n  ", stderr=""),
            ],
        ):
            self.assertEqual(run_hook.fetch_module_outline(("python", "check_python.py", "target.py"), {}), "")
            self.assertEqual(run_hook.fetch_module_outline(("python", "check_python.py", "target.py"), {}), "")

        with tempfile.TemporaryDirectory() as tmp_dir:
            target_file = Path(tmp_dir) / "sample.py"
            target_file.write_text("def a_fn():\n    pass\n", encoding="utf-8")
            payload = json.dumps({"tool_input": {"file_path": str(target_file)}}).encode("utf-8")
            fake_stdin = mock.MagicMock()
            fake_stdin.buffer = io.BytesIO(payload)
            fake_stderr = io.StringIO()
            with (
                mock.patch.object(run_hook.sys, "stdin", fake_stdin),
                mock.patch.object(run_hook.sys, "stderr", fake_stderr),
                mock.patch.object(
                    run_hook.subprocess,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess(
                            ["cmd"],
                            1,
                            stdout="sample.py:1: [CANON-01] order violation",
                            stderr="checker note",
                        ),
                        subprocess.TimeoutExpired(cmd=["cmd"], timeout=5),
                    ],
                ),
            ):
                exit_code = run_hook.main()
            self.assertEqual(exit_code, 2)
            rendered = fake_stderr.getvalue()
            self.assertIn("sample.py:1: [CANON-01] order violation\nchecker note", rendered)
            self.assertNotIn("Module outline:", rendered)

    def test_config_lock_disabled_by_default(self) -> None:
        clean_env = dict(os.environ)
        clean_env.pop("TOPDOWN_LOCK_CONFIGS", None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            toml_file = root / "topdown.toml"
            toml_file.write_text("[boundaries]\npure_dirs = []\n", encoding="utf-8")
            zone_file = root / "zones" / "prototype.md"
            zone_file.parent.mkdir(parents=True)
            zone_file.write_text("# Prototype\n", encoding="utf-8")
            for target_file in (toml_file, zone_file, ROOT_DIR / "zones" / "prototype.md"):
                proc = subprocess.run(
                    [sys.executable, str(HOOK_SCRIPT)],
                    input=json.dumps({"tool_input": {"file_path": str(target_file)}}),
                    env=clean_env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_config_lock_blocks_rule_files_when_enabled(self) -> None:
        self.assertTrue(run_hook.is_locked_config_path(Path("topdown.toml")))
        self.assertTrue(run_hook.is_locked_config_path(Path("AGENTS.md")))
        self.assertTrue(run_hook.is_locked_config_path(Path("languages/python.md")))
        self.assertTrue(run_hook.is_locked_config_path(Path("zones/prototype.md")))
        self.assertFalse(run_hook.is_locked_config_path(Path("README.md")))
        self.assertFalse(run_hook.is_locked_config_path(Path("core/helper.py")))

        locked_env = {**os.environ, "TOPDOWN_LOCK_CONFIGS": "1"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            toml_file = root / "topdown.toml"
            agents_file = root / "AGENTS.md"
            core_file = root / "core" / "base.md"
            toml_file.write_text("", encoding="utf-8")
            agents_file.write_text("", encoding="utf-8")
            core_file.parent.mkdir(parents=True)
            core_file.write_text("", encoding="utf-8")
            for target_file in (toml_file, agents_file, core_file, ROOT_DIR / "core" / "base.md"):
                proc = subprocess.run(
                    [sys.executable, str(HOOK_SCRIPT)],
                    input=json.dumps({"tool_input": {"file_path": str(target_file)}}),
                    env=locked_env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 2, msg=f"Expected exit 2 for {target_file}")
                self.assertIn("Topdown config lock (TOPDOWN_LOCK_CONFIGS=1):", proc.stderr)
                self.assertIn("ask a human to approve rule changes.", proc.stderr)

        clean_py = ROOT_DIR / "examples" / "fixtures" / "python_after.py"
        clean_proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps({"tool_input": {"file_path": str(clean_py)}}),
            env=locked_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(clean_proc.returncode, 0, msg=clean_proc.stderr)

        bad_py = ROOT_DIR / "examples" / "fixtures" / "python_before.py"
        bad_proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps({"tool_input": {"file_path": str(bad_py)}}),
            env=locked_env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad_proc.returncode, 2)
        self.assertIn("CANON-", bad_proc.stderr)
        self.assertNotIn("Topdown config lock", bad_proc.stderr)


if __name__ == "__main__":
    unittest.main()
