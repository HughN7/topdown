"""CLI entrypoint for the universal Topdown tree-sitter engine."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.specs import spec_for_path

WAIVER_SCAN_PATTERN = re.compile(
    r"""(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\\n])*`)|((?:#|//)[^\n]*)"""
)
WAIVER_MARKER_PATTERN = re.compile(r"\btopdown-ignore\b")

SKIP_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "__pycache__",
    "dist",
    "build",
    ".build",
    ".svelte-kit",
    ".next",
})


@dataclass(frozen=True)
class EngineOptions:
    paths: tuple[str, ...]
    as_json: bool
    config_path: Path | None
    outline: bool = False
    receipt: bool = False


@dataclass(frozen=True)
class ReceiptStats:
    files_checked: int
    waivers: int
    boundaries_enabled: bool
    pure_dirs_count: int


def main() -> int:
    options = parse_cli_args(sys.argv[1:])
    if options is None:
        print("Usage: topdown [--json] [--outline] [--receipt] [--config topdown.toml] <path> [...]", file=sys.stderr)
        return 2
    try:
        from engine.analyzer import Violation, analyze_source, outline_source
        from engine.boundaries import check_file_boundaries, load_boundary_config
    except ImportError as exc:
        print(f"Missing optional engine dependency: {exc}.", file=sys.stderr)
        return 2
    first_raw = Path(options.paths[0])
    if not first_raw.exists():
        print(f"Path not found: {options.paths[0]}", file=sys.stderr)
        return 2
    first_target = first_raw.resolve()
    config_root = first_target if first_target.is_dir() else first_target.parent
    boundary_cfg, cfg_error = load_boundary_config(config_root, options.config_path)
    if cfg_error is not None:
        print(cfg_error, file=sys.stderr)
        return 2
    exclude_dirs = boundary_cfg.exclude_dirs if boundary_cfg is not None else ()
    exclude_root = boundary_cfg.root_dir if boundary_cfg is not None else None
    target_files, file_error = collect_supported_files(
        options.paths, exclude_dirs=exclude_dirs, exclude_root=exclude_root
    )
    if file_error is not None:
        print(file_error, file=sys.stderr)
        return 2
    if options.outline:
        return run_engine_outline(target_files, outline_source, as_json=options.as_json)
    all_violations: list[Violation] = []
    waiver_count = 0
    for file_path in target_files:
        try:
            source_text = file_path.read_text(encoding="utf-8")
            file_violations = analyze_source(source_text, str(file_path))
            boundary_violations = check_file_boundaries(source_text, file_path, boundary_cfg) if boundary_cfg is not None else []
            all_violations.extend(file_violations + boundary_violations)
            waiver_count += count_waiver_comments(source_text) if options.receipt else 0
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error reading {file_path}: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Engine error on {file_path}: {exc}", file=sys.stderr)
            return 2
    if options.receipt:
        receipt_stats = ReceiptStats(
            files_checked=len(target_files),
            waivers=waiver_count,
            boundaries_enabled=boundary_cfg is not None and bool(boundary_cfg.pure_dirs),
            pure_dirs_count=len(boundary_cfg.pure_dirs) if boundary_cfg is not None else 0,
        )
        print(format_verification_receipt(receipt_stats, all_violations))
    else:
        emit_violations(all_violations, as_json=options.as_json)
    return 1 if all_violations else 0


def run_engine_outline(
    target_files: list[Path],
    outline_fn: object,
    *,
    as_json: bool,
) -> int:
    payload: list[dict[str, object]] = []
    for file_path in target_files:
        try:
            source_text = file_path.read_text(encoding="utf-8")
            functions = outline_fn(source_text, str(file_path))  # type: ignore[operator]
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error reading {file_path}: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Engine error on {file_path}: {exc}", file=sys.stderr)
            return 2
        payload.append({"path": str(file_path), "functions": functions})
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_engine_outline_text(payload))
    return 0


def format_engine_outline_text(payload: list[dict[str, object]]) -> str:
    blocks: list[str] = []
    for file_entry in payload:
        lines = [str(file_entry["path"])]
        functions = file_entry["functions"]
        if not isinstance(functions, list) or not functions:
            lines.append("  (no top-level functions)")
            blocks.append("\n".join(lines))
            continue
        for fn_info in functions:
            declared = fn_info["declared_tier"]
            valid = fn_info["valid_tiers"]
            range_badge = f"[{valid[0]}..{valid[-1]}]" if len(valid) > 1 else "[invalid]"
            inferred_badge = f"[{valid[0]}]" if len(valid) == 1 else range_badge
            badge = f"[{declared}]" if declared is not None else inferred_badge
            callers = fn_info["callers"]
            callees = fn_info["callees"]
            callers_part = f" <- {', '.join(callers)}" if callers else ""
            callees_part = f" -> {', '.join(callees)}" if callees else ""
            span = f"(L{fn_info['start_line']}-L{fn_info['end_line']}, {fn_info['line_count']}L)"
            lines.append(f"  {badge} {fn_info['name']} {span}{callers_part}{callees_part}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def parse_cli_args(raw_args: list[str]) -> EngineOptions | None:
    as_json = False
    outline = False
    receipt = False
    config_path: Path | None = None
    paths: list[str] = []
    idx = 0
    while idx < len(raw_args):
        token = raw_args[idx]
        if token == "--json":
            as_json = True
        elif token == "--outline":
            outline = True
        elif token == "--receipt":
            receipt = True
        elif token == "--config" and idx + 1 < len(raw_args):
            config_path = Path(raw_args[idx + 1])
            idx += 1
        elif token.startswith("--config="):
            config_path = Path(token[len("--config=") :])
        elif token.startswith("-"):
            return None
        else:
            paths.append(token)
        idx += 1
    if not paths or (receipt and (outline or as_json)):
        return None
    return EngineOptions(
        paths=tuple(paths),
        as_json=as_json,
        config_path=config_path,
        outline=outline,
        receipt=receipt,
    )


def collect_supported_files(
    raw_paths: tuple[str, ...],
    *,
    exclude_dirs: tuple[str, ...] = (),
    exclude_root: Path | None = None,
) -> tuple[list[Path], str | None]:
    collected: list[Path] = []
    for raw_entry in raw_paths:
        target = Path(raw_entry)
        if not target.exists():
            return [], f"Path not found: {raw_entry}"
        if target.is_file():
            is_supported = spec_for_path(target) is not None and not target.name.endswith(".d.ts")
            collected.extend([target] if is_supported else [])
            continue
        found = [
            path
            for path in sorted(target.rglob("*"))
            if path.is_file()
            and spec_for_path(path) is not None
            and not path.name.endswith(".d.ts")
            and not is_excluded_path(path, target, exclude_dirs=exclude_dirs, exclude_root=exclude_root)
        ]
        collected.extend(found)
    return collected, None


def is_excluded_path(
    file_path: Path,
    target_root: Path,
    *,
    exclude_dirs: tuple[str, ...],
    exclude_root: Path | None,
) -> bool:
    rel_to_target = file_path.relative_to(target_root)
    if any(part in SKIP_DIRS for part in rel_to_target.parts):
        return True
    if not exclude_dirs:
        return False
    base = exclude_root if exclude_root is not None else target_root.resolve()
    try:
        rel_posix = file_path.resolve().relative_to(base).as_posix()
    except ValueError:
        rel_posix = rel_to_target.as_posix()
    return any(rel_posix == prefix or rel_posix.startswith(f"{prefix}/") for prefix in exclude_dirs)


def emit_violations(violations: list[object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(entry) for entry in violations], indent=2))
        return
    for entry in violations:
        print(f"{entry.path}:{entry.line}: [{entry.rule}] {entry.message}")


def count_waiver_comments(source_text: str) -> int:
    waiver_count = 0
    for line in source_text.splitlines():
        comments = (match.group(1) for match in WAIVER_SCAN_PATTERN.finditer(line) if match.group(1))
        if any(WAIVER_MARKER_PATTERN.search(comment) for comment in comments):
            waiver_count += 1
    return waiver_count


def format_verification_receipt(stats: ReceiptStats, violations: list[object]) -> str:
    boundary_violations = [entry for entry in violations if getattr(entry, "rule", "") == "CANON-02"]
    if boundary_violations:
        boundary_status = "FAIL"
        scope = f" across {stats.pure_dirs_count} pure_dirs" if stats.pure_dirs_count > 0 else ""
        boundary_detail = f"{len(boundary_violations)} CANON-02 violations{scope}"
    elif stats.boundaries_enabled:
        boundary_status = "PASS"
        boundary_detail = f"0 CANON-02 violations across {stats.pure_dirs_count} pure_dirs"
    else:
        boundary_status = "Opt-in"
        boundary_detail = "No pure_dirs configured in topdown.toml"
    structural_status = "FAIL" if violations else "PASS"
    lines = [
        "## Topdown Verification Receipt",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
        f"| **Structural Compliance** | {structural_status} | {stats.files_checked} files checked, {len(violations)} violations |",
        f"| **Override Waivers (`CANON-08`)** | {stats.waivers} waivers | Explicit `topdown-ignore` annotations across checked files |",
        f"| **Boundary Integrity (`CANON-02`)** | {boundary_status} | {boundary_detail} |",
    ]
    if violations:
        lines.extend(["", "### Violations", ""])
        for entry in violations:
            lines.append(f"- {entry.path}:{entry.line}: [{entry.rule}] {entry.message}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
