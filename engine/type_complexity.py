"""AST-only CANON-03 nested generic depth and wide anonymous tuple checks."""

from __future__ import annotations

from typing import Any

from engine.models import FunctionEntry, TypeComplexity, Violation
from engine.specs import LanguageSpec

TRANSPARENT_TYPE_WRAPPERS = frozenset({
    "Optional",
    "Union",
    "Annotated",
    "Final",
    "ClassVar",
    "Required",
    "NotRequired",
    "ReadOnly",
    "Awaitable",
    "Coroutine",
    "Promise",
    "Readonly",
    "Partial",
    "NonNullable",
    "Awaited",
    "Option",
    "Result",
    "Box",
    "Arc",
    "Rc",
    "Pin",
    "Future",
})

SWIFT_TYPE_NODES = frozenset({"user_type", "tuple_type", "array_type", "dictionary_type", "optional_type"})
TYPE_SKIP_SCOPES = frozenset({
    "class_definition",
    "class_declaration",
    "class_expression",
    "struct_item",
    "impl_item",
    "enum_item",
    "type_alias_declaration",
    "typealias_declaration",
    "type_item",
    "type_spec",
})
TYPE_DECL_PARENTS = frozenset({
    "typed_parameter",
    "typed_default_parameter",
    "function_definition",
    "assignment",
    "required_parameter",
    "optional_parameter",
    "function_declaration",
    "method_definition",
    "arrow_function",
    "function_expression",
    "variable_declarator",
    "parameter",
    "function_item",
    "let_declaration",
    "type_annotation",
    "parameter_declaration",
    "variadic_parameter_declaration",
    "method_declaration",
    "var_spec",
})


def check_type_complexity(
    functions: list[FunctionEntry],
    source_bytes: bytes,
    file_path: str,
    *,
    spec: LanguageSpec,
) -> list[Violation]:
    violations: list[Violation] = []
    type_nodes = [
        type_node
        for entry in functions
        for type_node in collect_tree_type_nodes(entry.node, spec)
    ]
    for type_node in type_nodes:
        complexity = measure_tree_type_complexity(type_node, source_bytes, spec)
        line_no = type_node.start_point[0] + 1
        if complexity.max_tuple_width >= 4:
            violations.append(
                Violation(
                    file_path,
                    line_no,
                    "CANON-03",
                    f"Anonymous tuple type has {complexity.max_tuple_width} elements (max 3) -> extract a NamedTuple/dataclass/interface/struct.",
                )
            )
        elif complexity.max_depth >= 3:
            violations.append(
                Violation(
                    file_path,
                    line_no,
                    "CANON-03",
                    f"Inline generic type nesting depth {complexity.max_depth} (max 2) -> extract a named type alias or domain type.",
                )
            )
    return violations


def collect_tree_type_nodes(fn_node: Any, spec: LanguageSpec) -> list[Any]:
    type_nodes: list[Any] = []
    stack: list[tuple[Any, str, str | None]] = [(fn_node, "", None)]
    while stack:
        node, parent_type, field_name = stack.pop()
        if node is not fn_node and (node.type in spec.function_nodes or node.type in TYPE_SKIP_SCOPES):
            continue
        is_decl_parent = parent_type in TYPE_DECL_PARENTS
        is_type_root = is_decl_parent and (
            (spec.name == "python" and node.type == "type")
            or (spec.name in {"typescript", "tsx"} and node.type == "type_annotation")
            or (spec.name == "rust" and field_name in {"type", "return_type"})
            or (spec.name == "swift" and node.type in SWIFT_TYPE_NODES)
            or (spec.name == "go" and field_name in {"type", "result"})
        )
        if node is not fn_node and is_type_root:
            type_nodes.append(node)
            continue
        stack.extend(
            (child, node.type, node.field_name_for_child(idx))
            for idx, child in reversed(list(enumerate(node.children)))
            if child.is_named
        )
    return type_nodes


def measure_tree_type_complexity(type_node: Any, source_bytes: bytes, spec: LanguageSpec) -> TypeComplexity:
    max_depth = 0
    max_tuple_width = 0
    stack: list[tuple[Any, int]] = [(type_node, 0)]
    while stack:
        node, depth = stack.pop()
        node_type = node.type
        if node_type in {"generic_type", "user_type"}:
            has_type_args = any(ch.type in {"type_parameter", "type_arguments"} for ch in node.named_children)
            base_node = node.named_children[0] if node.named_children else None
            base_raw = source_bytes[base_node.start_byte : base_node.end_byte].decode("utf-8", errors="replace") if base_node is not None else ""
            base_name = base_raw.rsplit("::", 1)[-1].rsplit(".", 1)[-1].strip()
            increments = (node_type == "generic_type" or has_type_args) and base_name not in TRANSPARENT_TYPE_WRAPPERS
            next_depth = depth + 1 if increments else depth
            max_depth = max(max_depth, next_depth)
            param_container = next((ch for ch in node.named_children if ch.type == "type_parameter"), None)
            is_py_tuple = spec.name == "python" and base_name in {"tuple", "Tuple"} and param_container is not None
            py_args = [ch for ch in param_container.named_children if ch.type != "comment"] if is_py_tuple else []
            is_variadic = len(py_args) == 2 and source_bytes[py_args[1].start_byte : py_args[1].end_byte].strip() == b"..."
            max_tuple_width = max(max_tuple_width, 0 if is_variadic else len(py_args))
            stack.extend((ch, next_depth) for ch in node.named_children[1:])
            continue
        if node_type == "tuple_type":
            elems = [ch for ch in node.named_children if ch.type != "comment"]
            next_depth = depth + 1 if elems else depth
            max_depth = max(max_depth, next_depth)
            max_tuple_width = max(max_tuple_width, len(elems))
            stack.extend((ch, next_depth) for ch in elems)
            continue
        if node_type in {"array_type", "dictionary_type", "map_type", "slice_type"}:
            next_depth = depth + 1
            max_depth = max(max_depth, next_depth)
            stack.extend((ch, next_depth) for ch in node.named_children)
            continue
        stack.extend((ch, depth) for ch in node.named_children)
    return TypeComplexity(max_depth=max_depth, max_tuple_width=max_tuple_width)
