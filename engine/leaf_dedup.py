"""AST-only LLM-08 duplicate leaf primitive detection."""

from __future__ import annotations

from typing import Any

from engine.models import FnKey, FunctionEntry, TierState, TreeSignature, Violation
from engine.specs import LanguageSpec

IDENT_NODE_TYPES = frozenset({"identifier", "simple_identifier", "property_identifier"})
IDENT_BIND_TYPES = frozenset({"identifier", "simple_identifier"})
SKIP_DELIMS = frozenset({"(", ")", "[", "]", "{", "}", ",", ";"})
COMP_NODE_TYPES = frozenset(
    {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}
)
COMP_CLAUSE_TYPES = frozenset({"for_in_clause", "if_clause"})
CLOSURE_NODE_TYPES = frozenset({"lambda", "arrow_function", "function_expression", "closure_expression"})
LOOP_RHS_FIELDS = frozenset({"right", "collection", "value"})
LOOP_BODY_TYPES = frozenset({"block", "statement_block", "function_body", "statements"})
SCOPED_BODY_TYPES = frozenset({
    "statement_block",
    "catch_clause",
    "for_in_statement",
    "for_statement",
    "for_expression",
    "if_expression",
    "while_expression",
    "match_arm",
    "block",
    "function_body",
    "catch_block",
    "guard_statement",
    "if_statement",
})
LOOP_HEADER_TYPES = frozenset({"for_in_clause", "for_in_statement", "for_expression", "range_clause"})
BIND_SHADOW_FIELDS = frozenset({
    ("for_in_clause", "left"),
    ("variable_declarator", "name"),
    ("for_in_statement", "left"),
    ("catch_clause", "parameter"),
    ("let_declaration", "pattern"),
    ("let_condition", "pattern"),
    ("for_expression", "pattern"),
    ("match_arm", "pattern"),
    ("property_declaration", "name"),
    ("for_statement", "item"),
    ("catch_block", "error"),
    ("guard_statement", "bound_identifier"),
    ("if_statement", "bound_identifier"),
    ("short_var_declaration", "left"),
    ("var_spec", "name"),
    ("range_clause", "left"),
})
BIND_ASSIGN_FIELDS = frozenset({
    ("assignment", "left"),
    ("for_statement", "left"),
    ("as_pattern", "alias"),
    ("named_expression", "name"),
})
BIND_DEFAULT_FIELDS = frozenset({
    ("default_parameter", "value"),
    ("typed_default_parameter", "value"),
    ("required_parameter", "value"),
    ("optional_parameter", "value"),
    ("assignment_pattern", "right"),
    ("object_assignment_pattern", "right"),
})
BIND_CONTAINERS = frozenset({
    "pattern_list",
    "tuple_pattern",
    "list_pattern",
    "list_splat_pattern",
    "dictionary_splat_pattern",
    "parenthesized_expression",
    "as_pattern_target",
    "splat_pattern",
    "typed_parameter",
    "default_parameter",
    "typed_default_parameter",
    "object_pattern",
    "array_pattern",
    "rest_pattern",
    "pair_pattern",
    "object_assignment_pattern",
    "assignment_pattern",
    "required_parameter",
    "optional_parameter",
    "struct_pattern",
    "field_pattern",
    "slice_pattern",
    "tuple_struct_pattern",
    "ref_pattern",
    "mut_pattern",
    "captured_pattern",
    "or_pattern",
    "remaining_field_pattern",
    "match_pattern",
    "parameter",
    "pattern",
    "value_binding_pattern",
    "expression_list",
    "parameter_declaration",
    "variadic_parameter_declaration",
    "lambda_parameters",
    "formal_parameters",
    "closure_parameters",
})


def check_duplicate_leaves(
    top_functions: list[FunctionEntry],
    source_bytes: bytes,
    file_path: str,
    *,
    spec: LanguageSpec,
    tier_state: TierState,
    comment_lines: set[int],
) -> list[Violation]:
    """Flag duplicate 6+ line non-L0 leaf helpers (LLM-08)."""
    call_graph, declared_tags = tier_state
    raw_lines = source_bytes.decode("utf-8", errors="replace").split("\n")
    violations: list[Violation] = []
    seen_skeletons: dict[tuple[object, ...], FnKey] = {}
    for entry in top_functions:
        fn_key: FnKey = (entry.name, entry.start_line)
        raw_callees = call_graph[fn_key] if fn_key in call_graph else call_graph.get(entry.name, set())  # type: ignore[arg-type]
        callees = raw_callees - {fn_key, entry.name}
        tag_val = declared_tags[fn_key] if fn_key in declared_tags else declared_tags.get(entry.name)  # type: ignore[arg-type]
        if callees or tag_val == 0:
            continue
        if count_tree_leaf_body_lines(entry.node, raw_lines, comment_lines, spec=spec) < 6:
            continue
        sig_info = extract_tree_signature_ops(entry.node, source_bytes, spec=spec)
        skeleton = normalize_tree_leaf_skeleton(entry.node, source_bytes, spec=spec, sig_info=sig_info)
        if not skeleton:
            continue
        first_seen = seen_skeletons.get(skeleton)
        if first_seen is not None:
            first_name, first_line = first_seen
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "LLM-08",
                    f"Function '{entry.name}' duplicates leaf helper '{first_name}' (line {first_line}); reuse the existing helper instead of adding a duplicate.",
                )
            )
            continue
        seen_skeletons[skeleton] = fn_key
    return violations


def count_tree_leaf_body_lines(
    fn_node: Any,
    raw_lines: list[str],
    comment_lines: set[int],
    *,
    spec: LanguageSpec,
) -> int:
    """Count non-blank, non-comment, non-brace, non-docstring body lines starting at the first inner statement."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return 0
    stmts = [child for child in body.named_children if child.type not in spec.comment_nodes]
    if len(stmts) == 1 and stmts[0].type in {"statements", "statement_list"}:
        stmts = [child for child in stmts[0].named_children if child.type not in spec.comment_nodes]
    if spec.name == "python" and stmts:
        first = stmts[0]
        is_expr_str = (
            first.type == "expression_statement"
            and len(first.named_children) == 1
            and first.named_children[0].type == "string"
        )
        if first.type == "string" or is_expr_str:
            stmts = stmts[1:]
    if not stmts:
        return 0
    start_line = stmts[0].start_point[0] + 1
    end_line = stmts[-1].end_point[0] + 1
    return sum(
        1
        for line_no in range(start_line, end_line + 1)
        if 1 <= line_no <= len(raw_lines)
        and (trimmed := raw_lines[line_no - 1].strip())
        and line_no not in comment_lines
        and trimmed not in {"{", "}", "};"}
    )


def extract_tree_signature_ops(fn_node: Any, source_bytes: bytes, *, spec: LanguageSpec) -> TreeSignature:
    """Extract async flag, parameter shape tokens, (target, default) pairs, and body statements for LLM-08."""
    is_async = any(
        ch.type == "async" or (ch.type == "function_modifiers" and any(mc.type == "async" for mc in ch.children))
        for ch in fn_node.children
    )
    params_box, single_param = fn_node.child_by_field_name("parameters"), fn_node.child_by_field_name("parameter")
    if spec.name == "swift":
        swift_items = [
            (fn_node.field_name_for_child(idx), child)
            for idx, child in enumerate(fn_node.children)
            if child.type == "parameter" or fn_node.field_name_for_child(idx) == "default_value"
        ]
        raw_params = [
            (child, swift_items[pos + 1][1] if pos + 1 < len(swift_items) and swift_items[pos + 1][0] == "default_value" else None)
            for pos, (_, child) in enumerate(swift_items)
            if child.type == "parameter"
        ]
    elif single_param is not None:
        raw_params = [(single_param, None)]
    elif params_box is not None:
        raw_params = [(child, None) for child in params_box.children if child.type in spec.param_nodes]
    else:
        raw_params = []
    param_shape: list[str] = []
    param_ops: list[tuple[Any, Any]] = []
    for pnode, ext_default in raw_params:
        if pnode.type in {"positional_separator", "keyword_separator"}:
            param_shape.append("/" if pnode.type == "positional_separator" else "*")
            continue
        child_types = {child.type for child in pnode.children} | {pnode.type}
        shape_tag = ""
        if "dictionary_splat_pattern" in child_types:
            shape_tag = "**"
        elif "list_splat_pattern" in child_types:
            shape_tag = "*"
        elif child_types & {"rest_pattern", "variadic_parameter_declaration", "..."}:
            shape_tag = "..."
        elif pnode.type == "self_parameter":
            shape_tag = "self"
        elif pnode.type == "optional_parameter":
            shape_tag = "?"
        ext_node = pnode.child_by_field_name("external_name")
        if ext_node is not None:
            ext_text = source_bytes[ext_node.start_byte : ext_node.end_byte].decode("utf-8", errors="replace")
            shape_tag = f"{ext_text}:{shape_tag}"
        go_names = [ch for idx, ch in enumerate(pnode.children) if pnode.field_name_for_child(idx) == "name"] if spec.name == "go" else []
        fallback_tgt = pnode.named_children[0] if pnode.type == "typed_parameter" and pnode.named_children else pnode
        default_node = ext_default or pnode.child_by_field_name("value") or pnode.child_by_field_name("right")
        single_tgt = pnode.child_by_field_name("pattern") or pnode.child_by_field_name("left") or pnode.child_by_field_name("name") or fallback_tgt
        targets = go_names if len(go_names) > 1 else [single_tgt]
        param_shape.extend([shape_tag] * len(targets))
        param_ops.extend((tgt, default_node) for tgt in targets)
    body = fn_node.child_by_field_name("body")
    stmts = [child for child in body.named_children if child.type not in spec.comment_nodes] if body is not None else []
    first = stmts[0] if stmts else None
    is_expr_doc = first is not None and first.type == "expression_statement" and len(first.named_children) == 1 and first.named_children[0].type == "string"
    if spec.name == "python" and first is not None and (first.type == "string" or is_expr_doc):
        stmts = stmts[1:]
    return TreeSignature(is_async, tuple(param_shape), tuple(param_ops), tuple(stmts))


def normalize_tree_leaf_skeleton(
    fn_node: Any, source_bytes: bytes, *, spec: LanguageSpec, sig_info: TreeSignature
) -> tuple[object, ...]:
    """Normalize a leaf function's signature and body AST into an alpha-renamed skeleton tuple."""
    if not sig_info.body_stmts and fn_node.child_by_field_name("body") is None:
        return ()
    tokens: list[object] = [("SIG", sig_info.is_async, len(sig_info.param_ops), sig_info.param_shape)]
    env: dict[str, str] = {}
    scope_stack: list[dict[str, str | None]] = [{}]
    counters = [0, 0]
    param_ops = [
        entry
        for tgt, dfn in reversed(sig_info.param_ops)
        for entry in (("EMIT", ("NO_DEFAULT",), None) if dfn is None else ("AST", dfn, "default"), ("BIND_P", tgt, "param"))
    ]
    stack: list[tuple[str, Any, str | None]] = [*[("AST", stmt, "stmt") for stmt in reversed(sig_info.body_stmts)], ("EMIT", ("END_SIG",), None), *param_ops]
    while stack:
        op, node, fld = stack.pop()
        if op == "EMIT":
            tokens.append(node)
        elif op == "POP":
            saved = scope_stack.pop()
            env = {vn: bv for vn, bv in env.items() if vn not in saved} | {vn: bv for vn, bv in saved.items() if bv is not None}
        elif node.type in spec.comment_nodes or (not node.is_named and node.type in SKIP_DELIMS):
            continue
        elif node.type in spec.shorthand_nodes and op == "AST":
            raw_text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            tokens.append((fld, node.type, raw_text, env.get(raw_text, raw_text)))
        elif node.type in (IDENT_BIND_TYPES | spec.shorthand_nodes) and op not in {"AST", "LIT"}:
            raw_text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            need_bind = raw_text not in (env if op == "BIND_A" else scope_stack[-1])
            prefix, cidx = ("$p", 0) if op == "BIND_P" else ("$v", 1)
            scope_stack[-1].update({raw_text: env.get(raw_text)} if need_bind else {})
            env.update({raw_text: f"{prefix}{counters[cidx]}"} if need_bind else {})
            counters[cidx] += 1 if need_bind else 0
            bound_tok = (fld, node.type, raw_text, env[raw_text]) if node.type in spec.shorthand_nodes else (fld, "ID", env[raw_text])
            tokens.append(bound_tok)
        elif node.type in IDENT_BIND_TYPES and op == "AST":
            raw_text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            tokens.append((fld, "ID", env.get(raw_text, raw_text)))
        elif not node.children:
            tokens.append((fld, node.type, source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")))
        elif op not in {"AST", "LIT"} and node.type not in BIND_CONTAINERS:
            stack.append(("AST", node, fld))
        else:
            has_scope = op == "AST" and (node.type in (COMP_NODE_TYPES | CLOSURE_NODE_TYPES) or (spec.name != "python" and node.type in SCOPED_BODY_TYPES))
            scope_stack.extend([{}] if has_scope else ())
            tokens.append((fld, "OPEN", node.type))
            stack.extend([("EMIT", ("CLOSE", node.type), None), *([("POP", None, None)] if has_scope else [])])
            numbered = [(idx, node.field_name_for_child(idx), child) for idx, child in enumerate(node.children)]
            is_loop = node.type in LOOP_HEADER_TYPES or (node.type == "for_statement" and spec.name in {"python", "swift"})
            mode = next((idx for cond, idx in ((node.type in COMP_NODE_TYPES, 0), (node.type in CLOSURE_NODE_TYPES, 1), (is_loop, 2)) if cond), -1)
            key_fns = (
                lambda row: 0 if row[2].type in COMP_CLAUSE_TYPES else 1,
                lambda row: 0 if row[1] in {"parameters", "parameter"} else 1,
                lambda row: int(row[1] not in LOOP_RHS_FIELDS) + 2 * int(row[1] == "body" or row[2].type in LOOP_BODY_TYPES),
            )
            numbered = sorted(numbered, key=key_fns[mode]) if mode >= 0 else numbered
            is_closure = node.type in CLOSURE_NODE_TYPES
            child_ops = [
                (
                    next((tag for cond, tag in (
                        (op == "LIT" or cf == "type" or f"{node.type}.{cf}" in spec.member_key_fields or (cf in spec.member_key_fields and ch.type in IDENT_BIND_TYPES), "LIT"),
                        (op != "AST" and (node.type, cf) in BIND_DEFAULT_FIELDS, "AST"),
                        (op != "AST", op),
                        ((node.type, cf) in BIND_SHADOW_FIELDS or (is_closure and cf in {"parameters", "parameter"}), "BIND_S"),
                        ((node.type, cf) in BIND_ASSIGN_FIELDS, "BIND_A"),
                    ) if cond), "AST"),
                    ch,
                    cf,
                )
                for _, cf, ch in reversed(numbered)
            ]
            stack.extend(child_ops)
    return tuple(tokens)
