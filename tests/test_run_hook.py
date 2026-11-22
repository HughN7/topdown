import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = ROOT_DIR / "enforcement" / "run_hook.py"


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


if __name__ == "__main__":
    unittest.main()
