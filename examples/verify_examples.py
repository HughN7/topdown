"""Runs Topdown checkers on Before/After examples and prints a violation summary table."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ENFORCEMENT_DIR = ROOT_DIR / "enforcement"
FIXTURES_DIR = ROOT_DIR / "examples" / "fixtures"


@dataclass(frozen=True)
class ExampleSpec:
    language: str
    command_prefix: tuple[str, ...]
    before_path: Path
    after_path: Path


@dataclass(frozen=True)
class ExampleRow:
    language: str
    before_count: int | None
    after_count: int | None
    status: str


def main() -> int:
    specs = build_example_specs()
    rows = [evaluate_example_pair(spec) for spec in specs]
    print(format_summary_table(rows))
    has_failure = any(row.status == "FAIL" for row in rows)
    return 1 if has_failure else 0


def build_example_specs() -> tuple[ExampleSpec, ...]:
    node_bin = shutil.which("node")
    ts_prefix = (node_bin, str(ENFORCEMENT_DIR / "check_ts.mjs"), "--json") if node_bin else ()
    return (
        ExampleSpec(
            language="Python",
            command_prefix=(sys.executable, str(ENFORCEMENT_DIR / "check_python.py"), "--json"),
            before_path=FIXTURES_DIR / "python_before.py",
            after_path=FIXTURES_DIR / "python_after.py",
        ),
        ExampleSpec(
            language="TypeScript",
            command_prefix=ts_prefix,
            before_path=FIXTURES_DIR / "ts_before.ts",
            after_path=FIXTURES_DIR / "ts_after.ts",
        ),
        ExampleSpec(
            language="Rust",
            command_prefix=(sys.executable, str(ENFORCEMENT_DIR / "check_rust.py"), "--json"),
            before_path=FIXTURES_DIR / "rust_before.rs",
            after_path=FIXTURES_DIR / "rust_after.rs",
        ),
    )


def evaluate_example_pair(spec: ExampleSpec) -> ExampleRow:
    if not spec.command_prefix:
        return ExampleRow(spec.language, None, None, "SKIPPED (node missing)")
    before_count = count_checker_violations(spec.command_prefix, spec.before_path)
    after_count = count_checker_violations(spec.command_prefix, spec.after_path)
    passed = before_count is not None and after_count is not None and before_count > 0 and after_count == 0
    return ExampleRow(
        language=spec.language,
        before_count=before_count,
        after_count=after_count,
        status="PASS" if passed else "FAIL",
    )


def count_checker_violations(command_prefix: tuple[str, ...], target_path: Path) -> int | None:
    proc = subprocess.run(
        [*command_prefix, str(target_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        return None
    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return len(entries) if isinstance(entries, list) else None


def format_summary_table(rows: list[ExampleRow]) -> str:
    header = [
        "| Language | Before Violations | After Violations | Delta | Status |",
        "|---|---:|---:|---:|---|",
    ]
    body_lines: list[str] = []
    for row in rows:
        if row.before_count is None or row.after_count is None:
            body_lines.append(f"| {row.language} | - | - | - | {row.status} |")
            continue
        delta = row.after_count - row.before_count
        body_lines.append(
            f"| {row.language} | {row.before_count} | {row.after_count} | {delta} | {row.status} |"
        )
    return "\n".join(header + body_lines)


if __name__ == "__main__":
    raise SystemExit(main())
