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

from engine.analyzer import Violation, extract_ignore_directives
from engine.specs import LanguageSpec, spec_for_path

QUOTED_IMPORT_PATTERN = re.compile(r"""["']([^"']+)["']""")
PY_FROM_PATTERN = re.compile(r"\bfrom\s+([A-Za-z0-9_.]+)\s+import\b")
PY_IMPORT_PATTERN = re.compile(r"\bimport\s+(.+)")
RUST_GROUP_PATTERN = re.compile(r"\buse\s+([A-Za-z0-9_:]+)::\{([^}]+)\}")
RUST_SINGLE_PATTERN = re.compile(r"\buse\s+([A-Za-z0-9_:]+)")
SWIFT_IMPORT_PATTERN = re.compile(r"\bimport\s+(?:(?:typealias|struct|class|enum|protocol|let|var|func)\s+)?([A-Za-z0-9_.]+)")


@dataclass(frozen=True)
class BoundaryConfig:
    root_dir: Path
    pure_dirs: tuple[str, ...]
    shell_dirs: tuple[str, ...]
    extra_io_imports: tuple[str, ...]
    use_default_io_imports: bool
    exclude_dirs: tuple[str, ...] = ()


def load_boundary_config(root_dir: Path, explicit_config: Path | None) -> tuple[BoundaryConfig | None, str | None]:
    config_path = explicit_config if explicit_config is not None else find_ancestor_config(root_dir)
    if config_path is None or not config_path.is_file():
        if explicit_config is not None:
            return None, f"Config file not found: {explicit_config}"
        return None, None
    try:
        raw_table = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"Invalid config {config_path}: {exc}"
    section = raw_table.get("boundaries") if isinstance(raw_table.get("boundaries"), dict) else {}
    raw_excludes = raw_table.get("exclude_dirs", section.get("exclude_dirs", []))
    raw_pure = section.get("pure_dirs", [])
    raw_shell = section.get("shell_dirs", [])
    raw_extra = section.get("extra_io_imports", [])
    exclude_dirs = tuple(normalize_rel_dir(entry) for entry in raw_excludes if isinstance(entry, str)) if isinstance(raw_excludes, list) else ()
    pure_dirs = tuple(normalize_rel_dir(entry) for entry in raw_pure if isinstance(entry, str)) if isinstance(raw_pure, list) else ()
    if not pure_dirs and not exclude_dirs:
        return None, None
    shell_dirs = tuple(normalize_rel_dir(entry) for entry in raw_shell if isinstance(entry, str)) if isinstance(raw_shell, list) else ()
    extra_io = tuple(entry for entry in raw_extra if isinstance(entry, str)) if isinstance(raw_extra, list) else ()
    use_defaults = bool(section.get("use_default_io_imports", True))
    return BoundaryConfig(config_path.parent.resolve(), pure_dirs, shell_dirs, extra_io, use_defaults, exclude_dirs), None


def check_file_boundaries(source: str, file_path: Path, config: BoundaryConfig) -> list[Violation]:
    spec = spec_for_path(file_path)
    if spec is None or not config.pure_dirs or not is_inside_dirs(file_path, config.root_dir, config.pure_dirs):
        return []
    parser = get_parser(spec.name)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    directives, _ = extract_ignore_directives(tree.root_node, source_bytes, spec=spec, file_path=str(file_path))
    imports = extract_imports(tree.root_node, source_bytes, spec)
    banned_io = (spec.default_io_imports if config.use_default_io_imports else ()) + config.extra_io_imports
    shell_segments = tuple(tuple(dir_path.split("/")) for dir_path in config.shell_dirs)
    violations: list[Violation] = []
    for line_no, target in imports:
        is_ignored = any(
            (directive := directives.get(check_line)) is not None
            and directive.has_reason
            and (directive.is_wildcard or "CANON-02" in directive.rules)
            for check_line in (line_no, line_no - 1)
        )
        if is_ignored:
            continue
        if matches_io_import(target, banned_io):
            violations.append(
                Violation(
                    str(file_path),
                    line_no,
                    "CANON-02",
                    f"Pure-core file imports I/O module '{target}' -> move I/O to an L0 orchestrator or adapter and pass values in.",
                )
            )
        elif matches_shell_import(target, shell_segments):
            violations.append(
                Violation(
                    str(file_path),
                    line_no,
                    "CANON-02",
                    f"Pure-core file imports shell module '{target}' -> invert dependency and pass data from L0 into pure core.",
                )
            )
    return violations


def find_ancestor_config(start_dir: Path) -> Path | None:
    current = start_dir.resolve()
    for folder in (current, *current.parents):
        candidate = folder / "topdown.toml"
        if candidate.is_file():
            return candidate
    return None


def extract_imports(root_node: object, source_bytes: bytes, spec: LanguageSpec) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type not in spec.import_nodes:
            stack.extend(node.named_children)
            continue
        target_node = node.child_by_field_name("source") if node.type == "export_statement" else node
        if target_node is None:
            continue
        text = source_bytes[target_node.start_byte : target_node.end_byte].decode("utf-8", errors="replace")
        line_no = target_node.start_point[0] + 1
        tokens = QUOTED_IMPORT_PATTERN.findall(text) if node.type == "export_statement" else parse_import_targets(text, spec.name)
        found.extend((line_no, token) for token in tokens)
    return sorted(found, key=lambda pair: pair[0])


def parse_import_targets(text: str, lang_name: str) -> list[str]:
    if lang_name == "python":
        from_match = PY_FROM_PATTERN.search(text)
        if from_match:
            return [from_match.group(1)]
        imp_match = PY_IMPORT_PATTERN.search(text)
        if not imp_match:
            return []
        return [part.strip().split()[0] for part in imp_match.group(1).split(",") if part.strip()]
    if lang_name == "rust":
        group_match = RUST_GROUP_PATTERN.search(text)
        if group_match:
            prefix = group_match.group(1).lstrip(":")
            return [f"{prefix}::{entry.strip().split()[0]}" for entry in group_match.group(2).split(",") if entry.strip()]
        single = RUST_SINGLE_PATTERN.search(text)
        return [single.group(1).lstrip(":")] if single else []
    if lang_name == "swift":
        match = SWIFT_IMPORT_PATTERN.search(text)
        return [match.group(1)] if match else []
    return QUOTED_IMPORT_PATTERN.findall(text)


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


def matches_shell_import(import_target: str, shell_segments: tuple[tuple[str, ...], ...]) -> bool:
    target_segments = tuple(
        seg for seg in re.split(r"[/.:\\]+", import_target) if seg and seg != "crate" and seg != "self" and seg != "super"
    )
    if not target_segments:
        return False
    for shell_segs in shell_segments:
        if contains_segment_run(target_segments, shell_segs):
            return True
        if len(shell_segs) > 1 and shell_segs[0] in {"src", "lib", "app"} and contains_segment_run(target_segments, shell_segs[1:]):
            return True
    return False


def contains_segment_run(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    span = len(needle)
    if span == 0 or span > len(haystack):
        return False
    return any(haystack[idx : idx + span] == needle for idx in range(len(haystack) - span + 1))
