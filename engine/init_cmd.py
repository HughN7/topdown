"""Non-destructive onboarding command (`topdown init`) for Topdown repositories."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

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

EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".rs": "rust",
    ".swift": "swift",
    ".go": "go",
}

LANG_PACK_PATHS: dict[str, str] = {
    "python": "languages/python.md",
    "typescript": "languages/typescript.md",
    "rust": "languages/rust.md",
    "swift": "languages/swift.md",
}

AGENT_MARKER_PATHS = ("AGENTS.md", "CLAUDE.md", ".cursor", ".claude", ".github/workflows")
OPTIONAL_EXCLUDE_CANDIDATES = ("generated", "migrations", "dist", "build")


@dataclass(frozen=True)
class InitOptions:
    target_dir: Path
    dry_run: bool = False
    force: bool = False
    as_json: bool = False


@dataclass(frozen=True)
class RepoScan:
    root_dir: Path
    languages: tuple[str, ...]
    existing_agent_files: tuple[str, ...]
    suggested_excludes: tuple[str, ...]


def run_init_cli(raw_args: list[str]) -> int:
    options = parse_init_args(raw_args)
    if options is None:
        print("Usage: topdown init [dir] [--dry-run] [--force] [--json]", file=sys.stderr)
        return 2
    if not options.target_dir.exists() or not options.target_dir.is_dir():
        print(f"Directory not found: {options.target_dir}", file=sys.stderr)
        return 2
    root_dir = options.target_dir.resolve()
    scan = scan_repo_layout(root_dir)
    toml_path = root_dir / "topdown.toml"
    toml_content = build_starter_toml(scan.suggested_excludes)
    toml_status = apply_starter_toml(toml_path, toml_content, dry_run=options.dry_run, force=options.force)
    manifest = build_init_manifest(scan, toml_status)
    if options.as_json:
        print(json.dumps(manifest, indent=2))
    else:
        print(format_init_text(manifest))
    return 0


def parse_init_args(raw_args: list[str]) -> InitOptions | None:
    dry_run = False
    force = False
    as_json = False
    positional: list[str] = []
    for token in raw_args:
        if token == "--dry-run":
            dry_run = True
        elif token == "--force":
            force = True
        elif token == "--json":
            as_json = True
        elif token.startswith("-"):
            return None
        else:
            positional.append(token)
    if len(positional) > 1:
        return None
    target_dir = Path(positional[0]) if positional else Path(".")
    return InitOptions(target_dir=target_dir, dry_run=dry_run, force=force, as_json=as_json)


def scan_repo_layout(root_dir: Path) -> RepoScan:
    found_langs: set[str] = set()
    for path in root_dir.rglob("*"):
        if not path.is_file() or path.name.endswith(".d.ts"):
            continue
        rel_parts = path.relative_to(root_dir).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        lang = EXT_TO_LANG.get(path.suffix.lower())
        if lang is not None:
            found_langs.add(lang)
    agent_files = tuple(marker for marker in AGENT_MARKER_PATHS if (root_dir / marker).exists())
    excludes = tuple(name for name in OPTIONAL_EXCLUDE_CANDIDATES if (root_dir / name).is_dir())
    return RepoScan(
        root_dir=root_dir,
        languages=tuple(sorted(found_langs)),
        existing_agent_files=agent_files,
        suggested_excludes=excludes,
    )


def build_starter_toml(exclude_dirs: tuple[str, ...]) -> str:
    exclude_literal = ", ".join(f'"{entry}"' for entry in exclude_dirs)
    return (
        "# Topdown repository configuration (all boundary checks are strictly opt-in).\n"
        "# Use bare directory prefixes relative to this file (do not append /** globs).\n\n"
        f"exclude_dirs = [{exclude_literal}]\n\n"
        "[boundaries]\n"
        '# pure_dirs = ["src/domain"]\n'
        '# shell_dirs = ["src/adapters", "prototypes"]\n'
    )


def apply_starter_toml(toml_path: Path, content: str, *, dry_run: bool, force: bool) -> str:
    existed = toml_path.exists()
    if existed and not force:
        return "exists (kept; pass --force to overwrite)"
    if dry_run:
        return "would_overwrite" if existed else "would_create"
    toml_path.write_text(content, encoding="utf-8")
    return "overwritten" if existed else "created"


def build_init_manifest(scan: RepoScan, toml_status: str) -> dict[str, object]:
    packs = ["core/base.md"] + [LANG_PACK_PATHS[lang] for lang in scan.languages if lang in LANG_PACK_PATHS]
    hook_snippet = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write|MultiEdit",
                    "hooks": [{"type": "command", "command": "python3 enforcement/run_hook.py"}],
                }
            ]
        }
    }
    next_steps = [
        f"Add {', '.join(packs)} to AGENTS.md (or CLAUDE.md / .cursor/rules).",
        "Wire enforcement/run_hook.py into your agent PostToolUse hook config.",
        "Optionally uncomment pure_dirs/shell_dirs in topdown.toml to enforce CANON-02 boundaries.",
        "Run `topdown --receipt <src>` on PRs for a deterministic verification summary.",
    ]
    return {
        "root": str(scan.root_dir),
        "detected_languages": list(scan.languages),
        "recommended_packs": packs,
        "topdown_toml_status": toml_status,
        "existing_agent_files": list(scan.existing_agent_files),
        "hook_snippet": hook_snippet,
        "next_steps": next_steps,
    }


def format_init_text(manifest: dict[str, object]) -> str:
    langs = manifest["detected_languages"]
    packs = manifest["recommended_packs"]
    existing = manifest["existing_agent_files"]
    next_steps = manifest["next_steps"]
    lang_str = ", ".join(langs) if isinstance(langs, list) and langs else "(none detected)"
    pack_str = ", ".join(packs) if isinstance(packs, list) else ""
    existing_str = ", ".join(existing) if isinstance(existing, list) and existing else "(none)"
    lines = [
        f"Topdown init ({manifest['root']})",
        f"  Detected languages:   {lang_str}",
        f"  Recommended packs:    {pack_str}",
        f"  topdown.toml:         {manifest['topdown_toml_status']}",
        f"  Existing agent files: {existing_str}",
        "",
        "Next steps:",
    ]
    if isinstance(next_steps, list):
        lines.extend(f"  {idx}. {step}" for idx, step in enumerate(next_steps, start=1))
    return "\n".join(lines)
