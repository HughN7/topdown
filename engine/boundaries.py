"""Strictly opt-in cross-file CANON-02 boundary checker driven by topdown.toml.

Never assumes directory semantics on external repositories: if topdown.toml is absent
or declares no `pure_dirs`, zero boundary violations are emitted.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tree_sitter_language_pack import get_parser

from engine.analyzer import Violation
from engine.specs import LanguageSpec, spec_for_path

IMPORT_TOKEN_PATTERN = re.compile(r"""["']([^"']+)["']|\b(?:import|from|use)\s+([A-Za-z0-9_.:/]+)""")


@dataclass(frozen=True)
class BoundaryConfig:
    root_dir: Path
    pure_dirs: tuple[str, ...]
    shell_dirs: tuple[str, ...]
    extra_io_imports: tuple[str, ...]
    use_default_io_imports: bool


def load_boundary_config(root_dir: Path, explicit_config: Path | None) -> tuple[BoundaryConfig | None, str | None]:
    config_path = explicit_config if explicit_config is not None else root_dir / "topdown.toml"
    if not config_path.is_file():
        if explicit_config is not None:
            return None, f"Config file not found: {explicit_config}"
        return None, None
    try:
        raw_table = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"Invalid config {config_path}: {exc}"
    section = raw_table.get("boundaries")
    if not isinstance(section, dict):
        return None, None
    pure_dirs = tuple(normalize_rel_dir(entry) for entry in section.get("pure_dirs", []) if isinstance(entry, str))
    if not pure_dirs:
        return None, None
    shell_dirs = tuple(normalize_rel_dir(entry) for entry in section.get("shell_dirs", []) if isinstance(entry, str))
    extra_io = tuple(entry for entry in section.get("extra_io_imports", []) if isinstance(entry, str))
    use_defaults = bool(section.get("use_default_io_imports", True))
    return BoundaryConfig(config_path.parent.resolve(), pure_dirs, shell_dirs, extra_io, use_defaults), None


def check_file_boundaries(source: str, file_path: Path, config: BoundaryConfig) -> list[Violation]:
    spec = spec_for_path(file_path)
    if spec is None or not is_inside_dirs(file_path, config.root_dir, config.pure_dirs):
        return []
    imports = extract_imports(source, spec)
    banned_io = (spec.default_io_imports if config.use_default_io_imports else ()) + config.extra_io_imports
    shell_stems = tuple(dir_path.replace("/", ".") for dir_path in config.shell_dirs)
    violations: list[Violation] = []
    for line_no, target in imports:
        if matches_io_import(target, banned_io):
            violations.append(
                Violation(
                    str(file_path),
                    line_no,
                    "CANON-02",
                    f"Pure-core file imports I/O module '{target}' -> move I/O to an L0 orchestrator or adapter and pass values in.",
                )
            )
        elif matches_shell_import(target, config.shell_dirs, shell_stems):
            violations.append(
                Violation(
                    str(file_path),
                    line_no,
                    "CANON-02",
                    f"Pure-core file imports shell module '{target}' -> invert dependency and pass data from L0 into pure core.",
                )
            )
    return violations


def extract_imports(source: str, spec: LanguageSpec) -> list[tuple[int, str]]:
    parser = get_parser(spec.name)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    found: list[tuple[int, str]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type not in spec.import_nodes:
            stack.extend(node.named_children)
            continue
        text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        line_no = node.start_point[0] + 1
        tokens = [
            match.group(1) or match.group(2)
            for match in IMPORT_TOKEN_PATTERN.finditer(text)
            if match.group(1) or match.group(2)
        ]
        found.extend((line_no, token) for token in tokens)
    return sorted(found, key=lambda pair: pair[0])


def normalize_rel_dir(raw_dir: str) -> str:
    return raw_dir.replace("\\", "/").strip("/")


def is_inside_dirs(file_path: Path, root_dir: Path, rel_dirs: tuple[str, ...]) -> bool:
    try:
        rel_posix = file_path.resolve().relative_to(root_dir).as_posix()
    except ValueError:
        return False
    return any(rel_posix == prefix or rel_posix.startswith(f"{prefix}/") for prefix in rel_dirs)


def matches_io_import(import_target: str, banned_io: tuple[str, ...]) -> bool:
    return any(
        import_target == prefix
        or import_target.startswith(f"{prefix}.")
        or import_target.startswith(f"{prefix}/")
        or import_target.startswith(f"{prefix}::")
        for prefix in banned_io
    )


def matches_shell_import(import_target: str, shell_dirs: tuple[str, ...], shell_stems: tuple[str, ...]) -> bool:
    normalized = import_target.replace("\\", "/")
    return any(
        shell_dir in normalized or normalized == stem or normalized.startswith(f"{stem}.")
        for shell_dir, stem in zip(shell_dirs, shell_stems)
    )
