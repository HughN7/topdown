"""CLI entrypoint for the universal Topdown tree-sitter engine."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.specs import spec_for_path

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


def main() -> int:
    options = parse_cli_args(sys.argv[1:])
    if options is None:
        print("Usage: topdown [--json] [--config topdown.toml] <path> [...]", file=sys.stderr)
        return 2
    try:
        from engine.analyzer import Violation, analyze_source
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
    all_violations: list[Violation] = []
    for file_path in target_files:
        try:
            source_text = file_path.read_text(encoding="utf-8")
            file_violations = analyze_source(source_text, str(file_path))
            boundary_violations = check_file_boundaries(source_text, file_path, boundary_cfg) if boundary_cfg is not None else []
            all_violations.extend(file_violations + boundary_violations)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error reading {file_path}: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Engine error on {file_path}: {exc}", file=sys.stderr)
            return 2
    emit_violations(all_violations, as_json=options.as_json)
    return 1 if all_violations else 0


def parse_cli_args(raw_args: list[str]) -> EngineOptions | None:
    as_json = False
    config_path: Path | None = None
    paths: list[str] = []
    idx = 0
    while idx < len(raw_args):
        token = raw_args[idx]
        if token == "--json":
            as_json = True
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
    if not paths:
        return None
    return EngineOptions(paths=tuple(paths), as_json=as_json, config_path=config_path)


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


if __name__ == "__main__":
    raise SystemExit(main())
