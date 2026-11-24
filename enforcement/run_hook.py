"""PostToolUse hook dispatcher for Topdown (Claude Code, Cursor, Antigravity).

Reads hook JSON from stdin, locates the edited file_path, runs the matching single-file
Topdown checker, and exits 2 with actionable refactor guidance on stderr if violations exist.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENFORCEMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ENFORCEMENT_DIR.parent
TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
ENGINE_EXTENSIONS = frozenset({".swift", ".go"})
OUTLINE_SCRIPTS = frozenset({"check_python.py", "check_ts.mjs"})
MAX_OUTLINE_LINES = 25


def main() -> int:
    raw_input = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    target_path = extract_file_path(raw_input)
    if target_path is None or not target_path.is_file() or target_path.name.endswith(".d.ts"):
        return 0
    command = build_checker_command(target_path)
    if not command:
        return 0
    env = build_subprocess_env()
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if proc.returncode == 0:
        return 0
    output_parts = [part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()]
    output = "\n".join(output_parts)
    if proc.returncode != 1:
        print(f"Topdown checker skipped ({target_path}): {output}", file=sys.stderr)
        return 0
    guidance = f"Topdown violations in {target_path}:\n{output}"
    if ("CANON-01" in output or "LLM-08" in output) and supports_outline(command):
        outline = fetch_module_outline(command, env)
        if outline:
            guidance = f"{guidance}\nModule outline:\n{outline}"
    print(guidance, file=sys.stderr)
    return 2


def extract_file_path(raw_input: str) -> Path | None:
    if not raw_input.strip():
        return None
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    candidates = [
        tool_input.get("file_path") if isinstance(tool_input, dict) else None,
        tool_input.get("path") if isinstance(tool_input, dict) else None,
        payload.get("file_path"),
        payload.get("path"),
    ]
    for raw_path in candidates:
        if isinstance(raw_path, str) and raw_path.strip():
            return Path(raw_path)
    return None


def build_checker_command(target_path: Path) -> tuple[str, ...]:
    suffix = target_path.suffix.lower()
    if suffix == ".py":
        return (sys.executable, str(ENFORCEMENT_DIR / "check_python.py"), str(target_path))
    if suffix == ".rs":
        return (sys.executable, str(ENFORCEMENT_DIR / "check_rust.py"), str(target_path))
    if suffix in TS_EXTENSIONS:
        node_bin = shutil.which("node")
        if node_bin is None:
            return ()
        return (node_bin, str(ENFORCEMENT_DIR / "check_ts.mjs"), str(target_path))
    engine_spec = importlib.util.find_spec("engine")
    has_engine = (REPO_ROOT / "engine" / "cli.py").is_file() or (
        engine_spec is not None
        and engine_spec.submodule_search_locations is not None
        and importlib.util.find_spec("engine.cli") is not None
    )
    if suffix in ENGINE_EXTENSIONS and has_engine and importlib.util.find_spec("tree_sitter_language_pack") is not None:
        return (sys.executable, "-m", "engine.cli", str(target_path))
    return ()


def build_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    repo_str = str(REPO_ROOT)
    env["PYTHONPATH"] = f"{repo_str}{os.pathsep}{existing}" if existing else repo_str
    return env


def supports_outline(command: tuple[str, ...]) -> bool:
    if len(command) < 2:
        return False
    checker_tokens = command[:-1]
    return any(
        Path(token).name in OUTLINE_SCRIPTS or token == "engine.cli"
        for token in checker_tokens
    )


def fetch_module_outline(command: tuple[str, ...], env: dict[str, str]) -> str:
    if len(command) < 2:
        return ""
    outline_cmd = (*command[:-1], "--outline", command[-1])
    try:
        proc = subprocess.run(
            outline_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    stdout_text = proc.stdout or ""
    if proc.returncode != 0 or not stdout_text.strip():
        return ""
    lines = stdout_text.rstrip("\r\n").splitlines()
    if len(lines) > MAX_OUTLINE_LINES:
        lines = [*lines[:MAX_OUTLINE_LINES], "... (truncated)"]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
