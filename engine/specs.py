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
    container_nodes: frozenset[str]
    call_nodes: frozenset[str]
    control_nodes: frozenset[str]
    import_nodes: frozenset[str]
    comment_nodes: frozenset[str]
    default_io_imports: tuple[str, ...]
    supports_top_level_const_arrows: bool = False
    param_nodes: frozenset[str] = frozenset()
    shorthand_nodes: frozenset[str] = frozenset()
    member_key_fields: frozenset[str] = frozenset()


LANGUAGE_SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="python",
        extensions=(".py",),
        function_nodes=frozenset({"function_definition"}),
        wrapper_nodes=frozenset({"decorated_definition"}),
        container_nodes=frozenset({"class_definition", "block"}),
        call_nodes=frozenset({"call"}),
        control_nodes=frozenset({"if_statement", "for_statement", "while_statement", "with_statement", "try_statement", "match_statement"}),
        import_nodes=frozenset({"import_statement", "import_from_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("httpx", "requests", "urllib", "sqlite3", "psycopg", "pathlib", "subprocess", "socket"),
        supports_top_level_const_arrows=False,
        param_nodes=frozenset({
            "identifier",
            "typed_parameter",
            "default_parameter",
            "typed_default_parameter",
            "list_splat_pattern",
            "dictionary_splat_pattern",
            "positional_separator",
            "keyword_separator",
        }),
        shorthand_nodes=frozenset(),
        member_key_fields=frozenset({"attribute", "keyword_argument.name"}),
    ),
    LanguageSpec(
        name="typescript",
        extensions=(".ts",),
        function_nodes=frozenset({"function_declaration", "method_definition"}),
        wrapper_nodes=frozenset({"export_statement"}),
        container_nodes=frozenset({"class_declaration", "class_body"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement", "export_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
        supports_top_level_const_arrows=True,
        param_nodes=frozenset({
            "required_parameter",
            "optional_parameter",
            "identifier",
            "assignment_pattern",
            "rest_pattern",
            "object_pattern",
            "array_pattern",
        }),
        shorthand_nodes=frozenset({"shorthand_property_identifier", "shorthand_property_identifier_pattern"}),
        member_key_fields=frozenset({"key", "property"}),
    ),
    LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        function_nodes=frozenset({"function_declaration", "method_definition"}),
        wrapper_nodes=frozenset({"export_statement"}),
        container_nodes=frozenset({"class_declaration", "class_body"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement", "export_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
        supports_top_level_const_arrows=True,
        param_nodes=frozenset({
            "required_parameter",
            "optional_parameter",
            "identifier",
            "assignment_pattern",
            "rest_pattern",
            "object_pattern",
            "array_pattern",
        }),
        shorthand_nodes=frozenset({"shorthand_property_identifier", "shorthand_property_identifier_pattern"}),
        member_key_fields=frozenset({"key", "property"}),
    ),
    LanguageSpec(
        name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        function_nodes=frozenset({"function_declaration", "method_definition"}),
        wrapper_nodes=frozenset({"export_statement"}),
        container_nodes=frozenset({"class_declaration", "class_body"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "try_statement"}),
        import_nodes=frozenset({"import_statement", "export_statement"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("node:fs", "fs", "node:http", "node:https", "axios", "pg", "mysql2"),
        supports_top_level_const_arrows=True,
        param_nodes=frozenset({
            "identifier",
            "assignment_pattern",
            "rest_pattern",
            "object_pattern",
            "array_pattern",
        }),
        shorthand_nodes=frozenset({"shorthand_property_identifier", "shorthand_property_identifier_pattern"}),
        member_key_fields=frozenset({"key", "property"}),
    ),
    LanguageSpec(
        name="rust",
        extensions=(".rs",),
        function_nodes=frozenset({"function_item"}),
        wrapper_nodes=frozenset(),
        container_nodes=frozenset({"impl_item", "declaration_list", "mod_item"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_expression", "for_expression", "while_expression", "loop_expression", "match_expression"}),
        import_nodes=frozenset({"use_declaration"}),
        comment_nodes=frozenset({"line_comment", "block_comment"}),
        default_io_imports=("std::fs", "std::net", "reqwest", "tokio::fs", "tokio::net", "sqlx"),
        supports_top_level_const_arrows=False,
        param_nodes=frozenset({"parameter", "self_parameter"}),
        shorthand_nodes=frozenset({"shorthand_field_initializer", "shorthand_field_identifier"}),
        member_key_fields=frozenset({"type", "field"}),
    ),
    LanguageSpec(
        name="swift",
        extensions=(".swift",),
        function_nodes=frozenset({"function_declaration"}),
        wrapper_nodes=frozenset(),
        container_nodes=frozenset({"class_declaration", "class_body", "protocol_declaration", "protocol_body"}),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "while_statement", "switch_statement", "do_statement"}),
        import_nodes=frozenset({"import_declaration"}),
        comment_nodes=frozenset({"comment", "multiline_comment"}),
        default_io_imports=("SwiftData", "CoreData", "Network", "Foundation.FileManager"),
        supports_top_level_const_arrows=False,
        param_nodes=frozenset({"parameter"}),
        shorthand_nodes=frozenset(),
        member_key_fields=frozenset({"suffix"}),
    ),
    LanguageSpec(
        name="go",
        extensions=(".go",),
        function_nodes=frozenset({"function_declaration", "method_declaration"}),
        wrapper_nodes=frozenset(),
        container_nodes=frozenset(),
        call_nodes=frozenset({"call_expression"}),
        control_nodes=frozenset({"if_statement", "for_statement", "expression_switch_statement", "type_switch_statement", "select_statement"}),
        import_nodes=frozenset({"import_spec"}),
        comment_nodes=frozenset({"comment"}),
        default_io_imports=("net/http", "os", "database/sql", "net"),
        supports_top_level_const_arrows=False,
        param_nodes=frozenset({"parameter_declaration", "variadic_parameter_declaration"}),
        shorthand_nodes=frozenset(),
        member_key_fields=frozenset({"field"}),
    ),
)

EXTENSION_TO_SPEC: dict[str, LanguageSpec] = {
    ext: spec for spec in LANGUAGE_SPECS for ext in spec.extensions
}


def spec_for_path(file_path: str | Path) -> LanguageSpec | None:
    return EXTENSION_TO_SPEC.get(Path(file_path).suffix.lower())
