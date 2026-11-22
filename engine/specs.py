"""Declarative language grammar specifications for the universal tree-sitter engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    function_nodes: frozenset[str]
    wrapper_nodes: frozenset[str]
    call_nodes: frozenset[str]
    control_nodes: frozenset[str]
    import_nodes: frozenset[str]
    comment_nodes: frozenset[str]
    default_io_imports: tuple[str, ...]


LANGUAGE_SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="python",
        extensions=(".py",),
        function_nodes=frozenset({"function_definition"}),
        wrapper_nodes=frozenset({"decorated_definition"}),
        call_nodes=frozenset({"call"}),
        control_nodes=frozenset({"if_statement", "for_statement", "while_statement", "with_statement", "try_statement", "match_statement"}),
        import_nodes=frozenset({"import_statement", "import_from_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("httpx", "requests", "urllib", "sqlite3", "psycopg", "pathlib", "subprocess", "socket"),
    ),
    LanguageSpec(
        name="typescript",
        extensions=(".ts",),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset({"export_statement"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
    ),
    LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset({"export_statement"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
    ),
    LanguageSpec(
        name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset({"export_statement"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
    ),
    LanguageSpec(
        name="rust",
        extensions=(".rs",),
        function_nodes=frozenset({"function_item"}),
        wrapper_nodes=frozenset(),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_expression", "for_expression", "while_expression", "loop_expression", "match_expression"}),
        import_nodes=frozenset({"use_declaration"}),
        comment_nodes=frozenset({"line_comment", "block_comment"}),
        default_io_imports=("std::fs", "std::net", "reqwest", "tokio::fs", "tokio::net", "sqlx"),
    ),
    LanguageSpec(
        name="swift",
        extensions=(".swift",),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset(),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "while_statement", "switch_statement", "do_statement", "guard_statement"}),
        import_nodes=frozenset({"import_declaration"}),
        comment_nodes=frozenset({"comment", "multiline_comment"}),
        default_io_imports=("SwiftData", "CoreData", "Network"),
    ),
    LanguageSpec(
        name="go",
        extensions=(".go",),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset(),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "expression_switch_statement", "type_switch_statement", "select_statement"}),
        import_nodes=frozenset({"import_spec"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("net/http", "os", "database/sql", "net"),
    ),
)

EXTENSION_TO_SPEC: dict[str, LanguageSpec] = {
    ext: spec for spec in LANGUAGE_SPECS for ext in spec.extensions
}


def spec_for_path(file_path: str | Path) -> LanguageSpec | None:
    return EXTENSION_TO_SPEC.get(Path(file_path).suffix.lower())
