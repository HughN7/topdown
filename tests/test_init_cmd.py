import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


class TestInitCmd(unittest.TestCase):
    def test_init_detects_languages_dry_run_and_json_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "src").mkdir()
            (root / "dist").mkdir()
            (root / "src" / "app.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
            (root / "src" / "view.tsx").write_text("export function view() { return null; }\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("# Existing\n", encoding="utf-8")

            dry_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "init", str(root), "--dry-run", "--json"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(dry_proc.returncode, 0, msg=dry_proc.stderr + dry_proc.stdout)
            manifest = json.loads(dry_proc.stdout)
            self.assertEqual(manifest["detected_languages"], ["python", "typescript"])
            self.assertEqual(
                manifest["recommended_packs"],
                ["core/base.md", "languages/python.md", "languages/typescript.md"],
            )
            self.assertEqual(manifest["topdown_toml_status"], "would_create")
            self.assertEqual(manifest["existing_agent_files"], ["AGENTS.md"])
            self.assertIn("hooks", manifest["hook_snippet"])
            self.assertFalse((root / "topdown.toml").exists())

            create_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "init", str(root)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(create_proc.returncode, 0, msg=create_proc.stderr + create_proc.stdout)
            self.assertIn("topdown.toml:         created", create_proc.stdout)
            self.assertTrue((root / "topdown.toml").is_file())
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "# Existing\n")

            from engine.boundaries import load_boundary_config

            boundary_cfg, cfg_error = load_boundary_config(root, None)
            self.assertIsNone(cfg_error)
            self.assertIsNotNone(boundary_cfg)
            self.assertEqual(boundary_cfg.exclude_dirs, ("dist",))

            keep_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "init", str(root), "--json"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(keep_proc.returncode, 0, msg=keep_proc.stderr)
            self.assertIn("exists", json.loads(keep_proc.stdout)["topdown_toml_status"])

            force_proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "init", str(root), "--force", "--json"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(force_proc.returncode, 0, msg=force_proc.stderr)
            self.assertEqual(json.loads(force_proc.stdout)["topdown_toml_status"], "overwritten")

    def test_init_parses_empty_excludes_and_rejects_bad_flags_or_missing_dir(self) -> None:
        from engine.boundaries import load_boundary_config

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
            (root / "global.d.ts").write_text("declare const x: number;\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "engine.cli", "init", str(root), "--json"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertEqual(json.loads(proc.stdout)["detected_languages"], ["go"])
            boundary_cfg, cfg_error = load_boundary_config(root, None)
            self.assertIsNone(cfg_error)
            self.assertIsNone(boundary_cfg)

        bad_flag = subprocess.run(
            [sys.executable, "-m", "engine.cli", "init", "--bogus"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(bad_flag.returncode, 2)
        self.assertIn("Usage:", bad_flag.stderr)

        missing_dir = subprocess.run(
            [sys.executable, "-m", "engine.cli", "init", "does-not-exist-dir-xyz"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(missing_dir.returncode, 2)
        self.assertIn("Directory not found", missing_dir.stderr)

    def test_init_and_load_boundary_config_work_without_tree_sitter(self) -> None:
        from unittest import mock

        from engine import cli
        from engine.boundaries import load_boundary_config

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "app.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": None}):
                with mock.patch.object(sys, "argv", ["topdown", "init", str(root)]):
                    self.assertEqual(cli.main(), 0)
                boundary_cfg, cfg_error = load_boundary_config(root, None)
                self.assertIsNone(cfg_error)
                self.assertIsNone(boundary_cfg)


if __name__ == "__main__":
    unittest.main()
