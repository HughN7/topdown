"""PostToolUse hook dispatcher for Topdown (Claude Code, Cursor, Gemini CLI).

Reads hook JSON from stdin, locates the edited file_path, runs the matching single-file
Topdown checker, and exits 2 with actionable refactor guidance on stderr if violations exist.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ENFORCEMENT_DIR = Path(__file__).resolve().parent
TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})


def main() -> int:
    raw_input = sys.stdin.read()
    target_path = extract_file_path(raw_input)
    if target_path is None or not target_path.is_file():
        return 0
    command = build_checker_command(target_path)
    if not command:
        return 0
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return 0
    output = (proc.stdout + proc.stderr).strip()
    print(f"Topdown violations in {target_path}:\n{output}", file=sys.stderr)
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
    return ()


if __name__ == "__main__":
    raise SystemExit(main())
