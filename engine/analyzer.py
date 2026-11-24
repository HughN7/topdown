"""Universal tree-sitter AST analyzer for Topdown structural rules."""

from __future__ import annotations

from typing import Any

from tree_sitter_language_pack import get_parser

from engine.leaf_dedup import check_duplicate_leaves
from engine.models import (
    GAMING_NAME_PATTERN,
    MAX_FUNCTION_LINES,
    MAX_MODULE_CODE_LINES,
    MAX_MODULE_FUNCTIONS,
    MAX_NESTING_DEPTH,
    TEST_PATH_PATTERN,
    FnKey,
    FunctionEntry,
    IgnoreDirective,
    OverloadSpan,
    TierState,
    Violation,
)
from engine.specs import LanguageSpec, spec_for_path
from engine.suppress import (
    collect_contiguous_header_lines,
    collect_tree_comment_lines,
    extract_ignore_directives,
    filter_suppressed,
)
from engine.tier_graph import (
    check_order_and_depth,
    check_tier_tags,
    collect_module_tier_context,
    compute_all_pairs_longest_paths,
    compute_tier_bounds,
)
from engine.type_complexity import check_type_complexity

IDENT_NODE_TYPES = frozenset({"identifier", "simple_identifier", "property_identifier"})


def analyze_source(source: str, file_path: str) -> list[Violation]:
    spec = spec_for_path(file_path)
    if spec is None:
        return []
    parser = get_parser(spec.name)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    directives, ignore_violations = extract_ignore_directives(
        tree.root_node, source_bytes, spec=spec, file_path=file_path
    )
    line_comment_lines, comment_lines = collect_tree_comment_lines(tree.root_node, source_bytes, spec)
    raw_lines = source.split("\n")
    top_functions = collect_top_functions(tree.root_node, source_bytes, spec)
    member_functions = collect_member_functions(tree.root_node, source_bytes, spec)
    all_functions = top_functions + member_functions
    extra_ends = [entry.end_line for entry in member_functions] + [
        anchor.end_line for entry in member_functions for anchor in entry.overload_anchors
    ]
    call_graph, declared_tags, multi_tag_violations = collect_module_tier_context(
        top_functions,
        source_bytes,
        file_path,
        spec=spec,
        comment_sets=(line_comment_lines, comment_lines),
        extra_end_lines=extra_ends,
    )
    tier_state: TierState = (call_graph, declared_tags)
    raw_violations = (
        multi_tag_violations
        + check_order_and_depth(top_functions, source_bytes, spec=spec, file_path=file_path)
        + check_tier_tags(top_functions, file_path, tier_state=tier_state)
        + check_duplicate_leaves(
            top_functions,
            source_bytes,
            file_path,
            spec=spec,
            tier_state=tier_state,
            comment_lines=comment_lines,
        )
        + check_function_rules(all_functions, spec, file_path)
        + check_type_complexity(all_functions, source_bytes, file_path, spec=spec)
        + check_module_size_and_packing(
            raw_lines,
            tree.root_node,
            file_path,
            fn_count=len(all_functions),
            comment_lines=comment_lines,
            directives=directives,
        )
    )
    kept = filter_suppressed(
        raw_violations,
        all_functions,
        directives,
        raw_lines=raw_lines,
        comment_lines=comment_lines,
        lang_name=spec.name,
    )
    return sorted(ignore_violations + kept, key=lambda entry: (entry.line, entry.rule))


def outline_source(source: str, file_path: str) -> list[dict[str, Any]]:
    spec = spec_for_path(file_path)
    if spec is None:
        return []
    parser = get_parser(spec.name)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    line_comment_lines, comment_lines = collect_tree_comment_lines(tree.root_node, source_bytes, spec)
    top_functions = collect_top_functions(tree.root_node, source_bytes, spec)
    member_functions = collect_member_functions(tree.root_node, source_bytes, spec)
    extra_ends = [entry.end_line for entry in member_functions] + [
        anchor.end_line for entry in member_functions for anchor in entry.overload_anchors
    ]
    call_graph, declared_tags, _ = collect_module_tier_context(
        top_functions,
        source_bytes,
        file_path,
        spec=spec,
        comment_sets=(line_comment_lines, comment_lines),
        extra_end_lines=extra_ends,
    )
    fn_keys: list[FnKey] = [(entry.name, entry.start_line) for entry in top_functions]
    dist_map = compute_all_pairs_longest_paths(fn_keys, call_graph)
    bounds_map = compute_tier_bounds(fn_keys, dist_map, declared_tags=declared_tags)
    entries: list[dict[str, Any]] = []
    for entry in top_functions:
        fn_key: FnKey = (entry.name, entry.start_line)
        tag_val = declared_tags.get(fn_key)
        callers = sorted({caller_key[0] for caller_key in fn_keys if fn_key in call_graph.get(caller_key, set())})
        callees = sorted({callee_key[0] for callee_key in call_graph.get(fn_key, set())})
        entries.append(
            {
                "name": entry.name,
                "start_line": entry.start_line,
                "end_line": entry.end_line,
                "line_count": entry.end_line - entry.start_line + 1,
                "declared_tier": f"L{tag_val}" if tag_val is not None else None,
                "valid_tiers": bounds_map[fn_key].valid_tiers,
                "callers": callers,
                "callees": callees,
            }
        )
    return entries


def collect_top_functions(root_node: Any, source_bytes: bytes, spec: LanguageSpec) -> list[FunctionEntry]:
    return collect_block_functions(
        root_node.named_children,
        source_bytes,
        spec=spec,
        include_const_arrows=spec.supports_top_level_const_arrows,
    )


def collect_member_functions(root_node: Any, source_bytes: bytes, spec: LanguageSpec) -> list[FunctionEntry]:
    if not spec.container_nodes:
        return []
    entries: list[FunctionEntry] = []
    stack = [
        child
        for child in reversed(root_node.named_children)
        if unwrap_function_node(child, spec) is None
        and (child.type in spec.container_nodes or child.type in spec.wrapper_nodes)
    ]
    while stack:
        node = stack.pop()
        children = node.named_children
        stack.extend(
            child
            for child in reversed(children)
            if unwrap_function_node(child, spec) is None
            and (child.type in spec.container_nodes or child.type in spec.wrapper_nodes)
        )
        if node.type in spec.container_nodes:
            entries.extend(collect_block_functions(children, source_bytes, spec=spec))
    return entries


def check_function_rules(
    top_functions: list[FunctionEntry],
    spec: LanguageSpec,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    for entry in top_functions:
        line_count = entry.end_line - entry.start_line + 1
        if line_count > MAX_FUNCTION_LINES:
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "CANON-01",
                    f"'{entry.name}' is {line_count} lines (max {MAX_FUNCTION_LINES}) -> extract a cohesive L1/L2 pure helper.",
                )
            )
        if GAMING_NAME_PATTERN.search(entry.name):
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "LLM-05",
                    f"Function '{entry.name}' looks like a rule-gaming split -> rename by domain responsibility or inline.",
                )
            )
        max_nesting, nested_lines = inspect_function_body(entry.node, spec)
        if max_nesting > MAX_NESTING_DEPTH:
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "CANON-05",
                    f"'{entry.name}' has nesting depth {max_nesting} (max {MAX_NESTING_DEPTH}) -> invert with early return/continue or extract an L2 predicate.",
                )
            )
        violations.extend(
            Violation(
                file_path,
                nested_line,
                "CANON-05",
                "Nested function declaration inside a function is banned -> lift to module top level.",
            )
            for nested_line in nested_lines
        )
    return violations


def check_module_size_and_packing(
    raw_lines: list[str],
    root_node: Any,
    file_path: str,
    *,
    fn_count: int,
    comment_lines: set[int],
    directives: dict[int, IgnoreDirective],
) -> list[Violation]:
    violations: list[Violation] = []
    if not TEST_PATH_PATTERN.search(file_path):
        code_lines = sum(1 for idx, line in enumerate(raw_lines, start=1) if line.strip() and idx not in comment_lines)
        first_code_line = next(
            (idx for idx, line in enumerate(raw_lines, start=1) if line.strip() and idx not in comment_lines),
            len(raw_lines) + 1,
        )
        has_waiver = any(
            line_no < first_code_line and directive.has_reason and (directive.is_wildcard or "CANON-01" in directive.rules)
            for line_no, directive in directives.items()
        )
        if code_lines > MAX_MODULE_CODE_LINES and fn_count > MAX_MODULE_FUNCTIONS and not has_waiver:
            violations.append(
                Violation(
                    file_path,
                    1,
                    "CANON-01",
                    f"Module has {code_lines} code lines and {fn_count} functions (max {MAX_MODULE_CODE_LINES} lines / {MAX_MODULE_FUNCTIONS} functions) -> split by domain responsibility into cohesive modules.",
                )
            )
    block_types = frozenset({"module", "program", "source_file", "block", "statement_block", "function_body", "statements"})
    packed_lines: set[int] = set()
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type in block_types:
            stmt_lines = [ch.start_point[0] + 1 for ch in node.named_children if "comment" not in ch.type]
            packed_lines.update(ln for idx, ln in enumerate(stmt_lines) if ln in stmt_lines[:idx])
        stack.extend(node.named_children)
    violations.extend(
        Violation(file_path, line_no, "LLM-05", "Multiple statements packed onto one line -> format with one statement per line.")
        for line_no in sorted(packed_lines)
    )
    return violations


def collect_block_functions(
    children: list[Any],
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    include_const_arrows: bool = False,
) -> list[FunctionEntry]:
    """Collect function entries in a block while excluding Python @overload stubs and absorbing their headers."""
    entries: list[FunctionEntry] = []
    pending_overloads: list[tuple[str, OverloadSpan]] = []
    for child in children:
        if child.type in spec.comment_nodes:
            continue
        target = unwrap_function_node(child, spec)
        if target is None:
            pending_overloads = []
            entries.extend(unwrap_const_arrows(child, source_bytes) if include_const_arrows else ())
            continue
        name = extract_node_name(target, source_bytes)
        if name is None:
            pending_overloads = []
            continue
        span = OverloadSpan(child.start_point[0] + 1, target.start_point[0] + 1, target.end_point[0] + 1)
        if spec.name == "python" and is_python_overload(child):
            pending_overloads = [pair for pair in pending_overloads if pair[0] == name] + [(name, span)]
            continue
        absorbed = tuple(stub_span for stub_name, stub_span in pending_overloads if stub_name == name)
        pending_overloads = []
        entries.append(
            FunctionEntry(
                name=name,
                node=target,
                start_line=span.start_line,
                end_line=span.end_line,
                outer_start_line=span.outer_start_line,
                overload_anchors=absorbed,
            )
        )
    return entries


def inspect_function_body(fn_node: Any, spec: LanguageSpec) -> tuple[int, list[int]]:
    max_depth = 0
    nested_lines: list[int] = []
    stack: list[tuple[Any, int]] = [(child, 0) for child in fn_node.named_children]
    while stack:
        node, depth = stack.pop()
        if node.type in spec.function_nodes:
            nested_lines.append(node.start_point[0] + 1)
            continue
        is_else_if = (
            node.type in spec.control_nodes
            and node.parent is not None
            and node.parent.type in {"else_clause", "elif_clause", "if_statement"}
            and node != node.parent.child_by_field_name("consequence")
        )
        adds_depth = node.type in spec.control_nodes and not is_else_if
        next_depth = depth + 1 if adds_depth else depth
        max_depth = max(max_depth, next_depth)
        stack.extend((child, next_depth) for child in node.named_children)
    return max_depth, sorted(nested_lines)


def unwrap_function_node(node: Any, spec: LanguageSpec) -> Any | None:
    if node.type in spec.function_nodes:
        return node
    if node.type not in spec.wrapper_nodes:
        return None
    for child in node.named_children:
        if child.type in spec.function_nodes:
            return child
    return None


def is_python_overload(wrapper_node: Any) -> bool:
    """Return True if a Python decorated_definition carries @overload or @typing.overload."""
    if wrapper_node.type != "decorated_definition":
        return False
    for child in wrapper_node.named_children:
        if child.type != "decorator" or not child.named_children:
            continue
        expr = child.named_children[0]
        obj = expr.child_by_field_name("object") if expr.type == "attribute" else None
        attr = expr.child_by_field_name("attribute") if expr.type == "attribute" else None
        is_direct = expr.type == "identifier" and expr.text == b"overload"
        is_qualified = obj is not None and attr is not None and obj.text == b"typing" and attr.text == b"overload"
        if is_direct or is_qualified:
            return True
    return False


def extract_node_name(fn_node: Any, source_bytes: bytes) -> str | None:
    name_node = fn_node.child_by_field_name("name")
    if name_node is not None:
        return source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
    for child in fn_node.named_children:
        if child.type in IDENT_NODE_TYPES:
            return source_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


def unwrap_const_arrows(node: Any, source_bytes: bytes) -> list[FunctionEntry]:
    """Unwrap export_statement? -> lexical_declaration (const) -> variable_declarator -> arrow_function (statement_block)."""
    decl_node = node
    if node.type == "export_statement":
        decl_node = next((ch for ch in node.named_children if ch.type == "lexical_declaration"), None)
    if decl_node is None or decl_node.type != "lexical_declaration":
        return []
    if not any(ch.type == "const" for ch in decl_node.children):
        return []
    outer_line = node.start_point[0] + 1
    entries: list[FunctionEntry] = []
    for var_decl in decl_node.named_children:
        if var_decl.type != "variable_declarator":
            continue
        name_node = var_decl.child_by_field_name("name")
        val_node = var_decl.child_by_field_name("value")
        body_node = val_node.child_by_field_name("body") if val_node is not None and val_node.type == "arrow_function" else None
        if name_node is None or name_node.type not in IDENT_NODE_TYPES or body_node is None or body_node.type != "statement_block":
            continue
        name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        entries.append(
            FunctionEntry(
                name=name,
                node=val_node,
                start_line=var_decl.start_point[0] + 1,
                end_line=val_node.end_point[0] + 1,
                outer_start_line=outer_line,
            )
        )
    return entries
