"""Universal tree-sitter AST analyzer for Topdown structural rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, NamedTuple

from tree_sitter_language_pack import get_parser

from engine.specs import LanguageSpec, spec_for_path

MAX_FUNCTION_LINES = 80
MAX_NESTING_DEPTH = 2
MAX_BUSINESS_DEPTH = 2
GAMING_NAME_PATTERN = re.compile(r"(?:_part\d+|_helper\d*|Part\d+|Helper\d*)$")
IGNORE_PATTERN = re.compile(r"topdown-ignore(?:\((.*)\))?")
REASON_PATTERN = re.compile(r"""reason\s*=\s*(?:"([^"]+)"|'([^']+)')""")
RULE_ID_PATTERN = re.compile(r"\b(?:CANON|LLM)-\d{2}\b")
TIER_TAG_PATTERN = re.compile(r"^\s*(?:#|//)\s*\[L([0-3])\](?:\s|$)")
PY_HEADER_COMMENT_PATTERN = re.compile(r"^#(?:!|\s*-\*-|\s*coding[:=])")
TS_HEADER_COMMENT_PATTERN = re.compile(
    r"^(?://|/\*+)\s*(?:@ts-|eslint-disable|copyright\b|license\b|spdx-license-identifier\b)",
    re.IGNORECASE,
)
CHAR_LITERAL_PATTERN = r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.)|[^'\\])'"
HEADER_NOISE_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|#.*'),
    "rust": re.compile(
        rf'(?<![A-Za-z0-9_])(?:[bc])?r(#*)"[\s\S]*?"\1|"(?:\\.|[^"\\])*"|{CHAR_LITERAL_PATTERN}|//.*'
    ),
}
DEFAULT_HEADER_NOISE_PATTERN = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|/\*[\s\S]*?\*/|//.*'
)
HEADER_PAIR_MAP = {"]": "[", ")": "(", "}": "{"}
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

FnKey = tuple[str, int]
FnPair = tuple[FnKey, FnKey]
CallGraph = dict[FnKey, set[FnKey]]
DeclaredTags = dict[FnKey, int | None]
TierState = tuple[CallGraph, DeclaredTags]


class OverloadSpan(NamedTuple):
    """Line span for an absorbed @overload stub or implementation anchor."""

    outer_start_line: int
    start_line: int
    end_line: int


class TierBounds(NamedTuple):
    """Computed call-graph depth, height, and valid [L0..L3] tier bounds."""

    d_max: int
    height: int
    lb_0: int
    ub_0: int
    is_adapter: bool
    valid_tiers: list[str]


class SpanHeader(NamedTuple):
    """Function span and its suppression header lines."""

    outer_start_line: int
    end_line: int
    header_lines: set[int]


class TreeSignature(NamedTuple):
    """Extracted async flag, parameter shape tokens, (target, default) pairs, and body statements."""

    is_async: bool
    param_shape: tuple[str, ...]
    param_ops: tuple[tuple[Any, Any], ...]
    body_stmts: tuple[Any, ...]


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    message: str


@dataclass(frozen=True)
class IgnoreDirective:
    line: int
    rules: frozenset[str]
    has_reason: bool
    is_wildcard: bool = False


@dataclass(frozen=True)
class FunctionEntry:
    name: str
    node: Any
    start_line: int
    end_line: int
    outer_start_line: int = 0
    overload_anchors: tuple[OverloadSpan, ...] = ()


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


def extract_ignore_directives(
    root_node: Any,
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    file_path: str,
) -> tuple[dict[int, IgnoreDirective], list[Violation]]:
    directives: dict[int, IgnoreDirective] = {}
    violations: list[Violation] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in spec.comment_nodes:
            continue
        text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if not text.lstrip().startswith(("#", "//")):
            continue
        match = IGNORE_PATTERN.search(text)
        if not match:
            continue
        line_no = node.start_point[0] + 1
        inner = match.group(1) or ""
        rules = frozenset(RULE_ID_PATTERN.findall(inner))
        has_reason = REASON_PATTERN.search(inner) is not None
        is_wildcard = not REASON_PATTERN.sub("", inner).strip(" ,")
        directives[line_no] = IgnoreDirective(
            line=line_no, rules=rules, has_reason=has_reason, is_wildcard=is_wildcard
        )
        if not has_reason:
            violations.append(
                Violation(file_path, line_no, "CANON-08", 'topdown-ignore requires a non-empty reason="...".')
            )
    return directives, violations


def collect_tree_comment_lines(
    root_node: Any,
    source_bytes: bytes,
    spec: LanguageSpec,
) -> tuple[set[int], set[int]]:
    blanked = bytearray(source_bytes)
    raw_line_comments: set[int] = set()
    raw_all_comments: set[int] = set()
    stack = [root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in spec.comment_nodes:
            continue
        text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace").lstrip()
        start_ln = node.start_point[0] + 1
        end_ln = node.end_point[0] + 1
        if text.startswith(("#", "//")):
            raw_line_comments.add(start_ln)
        raw_all_comments.update(range(start_ln, end_ln + 1))
        blanked[node.start_byte : node.end_byte] = bytes(
            10 if b_val == 10 else 32 for b_val in source_bytes[node.start_byte : node.end_byte]
        )
    cleaned_lines = blanked.decode("utf-8", errors="replace").split("\n")
    standalone = {
        ln for ln in raw_all_comments if 1 <= ln <= len(cleaned_lines) and not cleaned_lines[ln - 1].strip()
    }
    return raw_line_comments & standalone, standalone


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


def collect_module_tier_context(
    top_functions: list[FunctionEntry],
    source_bytes: bytes,
    file_path: str,
    *,
    spec: LanguageSpec,
    comment_sets: tuple[set[int], set[int]],
    extra_end_lines: list[int] | None = None,
) -> tuple[CallGraph, DeclaredTags, list[Violation]]:
    """Build local call graph, declared tier tags, and multi-tag violations keyed by FnKey."""
    line_comment_lines, comment_lines = comment_sets
    raw_lines = source_bytes.decode("utf-8", errors="replace").split("\n")
    fn_lines: dict[FnKey, int] = {(entry.name, entry.start_line): entry.start_line for entry in top_functions}
    call_graph: CallGraph = {
        (entry.name, entry.start_line): (
            collect_local_calls(entry.node, source_bytes, spec=spec, fn_lines=fn_lines)
            - {(entry.name, entry.start_line)}
        )
        for entry in top_functions
    }
    fn_anchors = [
        (
            entry,
            [
                OverloadSpan(entry.outer_start_line or entry.start_line, entry.start_line, entry.end_line),
                *entry.overload_anchors,
            ],
        )
        for entry in top_functions
    ]
    extra_spans = [OverloadSpan(end_ln, end_ln, end_ln) for end_ln in (extra_end_lines or ())]
    ordered_anchors = sorted(
        [anchor for _, anchors in fn_anchors for anchor in anchors] + extra_spans,
        key=lambda span: (span.outer_start_line, span.start_line, span.end_line),
    )
    anchor_index = {id(span): idx for idx, span in enumerate(ordered_anchors)}
    declared_tags: DeclaredTags = {}
    violations: list[Violation] = []
    for entry, anchors in fn_anchors:
        header_lines: set[int] = set()
        for anchor in anchors:
            cur_idx = anchor_index[id(anchor)]
            prev_end = max(
                (
                    other.end_line
                    for other_idx, other in enumerate(ordered_anchors)
                    if other is not anchor
                    and other.end_line <= anchor.outer_start_line
                    and (other.outer_start_line < anchor.outer_start_line or other_idx < cur_idx)
                ),
                default=0,
            )
            leading = collect_contiguous_header_lines(
                raw_lines, anchor.outer_start_line, prev_end_line=prev_end, comment_lines=comment_lines, lang_name=spec.name
            )
            header_lines.update(leading | set(range(anchor.outer_start_line, anchor.start_line)))
        tag_val, has_multiple = extract_function_tier_tags(raw_lines, header_lines & line_comment_lines)
        declared_tags[(entry.name, entry.start_line)] = tag_val
        if has_multiple:
            violations.append(
                Violation(file_path, entry.start_line, "CANON-01", f"'{entry.name}' has multiple tier tags -> keep a single [L0]..[L3] tag.")
            )
    return call_graph, declared_tags, violations


def check_order_and_depth(
    top_functions: list[FunctionEntry],
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    fn_lines: dict[FnKey, int] = {(entry.name, entry.start_line): entry.start_line for entry in top_functions}
    call_graph: CallGraph = {}
    for entry in top_functions:
        fn_key: FnKey = (entry.name, entry.start_line)
        callees = collect_local_calls(entry.node, source_bytes, spec=spec, fn_lines=fn_lines)
        call_graph[fn_key] = callees
        out_of_order = [
            callee_key for callee_key in sorted(callees) if callee_key != fn_key and fn_lines[callee_key] < entry.start_line
        ]
        violations.extend(
            Violation(
                file_path,
                entry.start_line,
                "CANON-01",
                f"'{entry.name}' calls '{callee_key[0]}' defined above it (line {fn_lines[callee_key]}) -> move '{callee_key[0]}' below '{entry.name}' for top-down order.",
            )
            for callee_key in out_of_order
        )
    for entry in top_functions:
        depth, leaf_calls_local = compute_chain_depth((entry.name, entry.start_line), call_graph)
        if depth > MAX_BUSINESS_DEPTH + 1 or (depth == MAX_BUSINESS_DEPTH + 1 and leaf_calls_local):
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "CANON-01",
                    f"'{entry.name}' reaches call depth {depth} (max L0->L1->L2 + L3 leaf) -> flatten pass-through calls.",
                )
            )
    return violations


def check_tier_tags(
    top_functions: list[FunctionEntry],
    file_path: str,
    *,
    tier_state: TierState,
) -> list[Violation]:
    call_graph, declared_tags = tier_state
    violations: list[Violation] = []
    fn_keys: list[FnKey] = [(entry.name, entry.start_line) for entry in top_functions]
    dist_map = compute_all_pairs_longest_paths(fn_keys, call_graph)
    bounds_map = compute_tier_bounds(fn_keys, dist_map, declared_tags=declared_tags)
    for entry in top_functions:
        fn_key: FnKey = (entry.name, entry.start_line)
        tag_val = declared_tags.get(fn_key)
        if tag_val is None:
            continue
        d_max, height, lb_0, ub_0, is_adapter, _ = bounds_map[fn_key]
        valid_0 = [f"[L{tier}]" for tier in range(4) if lb_0 <= tier <= ub_0]
        bad_ancestors = [
            (anc_key, dist_map[(anc_key, fn_key)])
            for anc_key in fn_keys
            if anc_key != fn_key
            and declared_tags.get(anc_key) is not None
            and (anc_key, fn_key) in dist_map
            and not (dist_map[(anc_key, fn_key)] == 1 and declared_tags[anc_key] == 0 and tag_val == 0 and is_adapter)
            and tag_val - (declared_tags[anc_key] or 0) < dist_map[(anc_key, fn_key)]
        ]
        if tag_val == 3 and height > 0:
            violations.append(
                Violation(file_path, entry.start_line, "CANON-01", f"'{entry.name}' is tagged [L3] (leaf) but calls local functions -> update tag or move local calls to L1/L2.")
            )
        elif tag_val < lb_0 or tag_val > ub_0:
            msg = (
                f"'{entry.name}' is tagged [L{tag_val}] outside valid tier range ({', '.join(valid_0)}) for depth {d_max} and height {height} -> update tier tag."
                if valid_0
                else f"'{entry.name}' is tagged [L{tag_val}] in a call chain exceeding L0->L3 ceiling (depth {d_max}, height {height}) -> flatten call depth."
            )
            violations.append(Violation(file_path, entry.start_line, "CANON-01", msg))
        elif bad_ancestors:
            anc_key, anc_dist = bad_ancestors[0]
            anc_name = anc_key[0]
            anc_tag = declared_tags[anc_key] or 0
            required_tier = anc_tag + anc_dist
            bad_msg = (
                f"'{entry.name}' is tagged [L{tag_val}] at call distance {anc_dist} below '{anc_name}' ([L{anc_tag}]) -> callee tier must be at least [L{required_tier}]."
                if required_tier <= 3
                else f"'{entry.name}' is tagged [L{tag_val}] at call distance {anc_dist} below '{anc_name}' ([L{anc_tag}]), which exceeds L0->L3 depth -> update '{anc_name}' tier tag or flatten call depth."
            )
            violations.append(Violation(file_path, entry.start_line, "CANON-01", bad_msg))
    return violations


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


def filter_suppressed(
    violations: list[Violation],
    top_functions: list[FunctionEntry],
    directives: dict[int, IgnoreDirective],
    *,
    raw_lines: list[str],
    comment_lines: set[int],
    lang_name: str,
) -> list[Violation]:
    fn_anchors = [
        (
            outer,
            fn_entry,
            [OverloadSpan(outer, fn_entry.start_line, fn_entry.end_line), *fn_entry.overload_anchors],
        )
        for fn_entry in top_functions
        for outer in (fn_entry.outer_start_line or fn_entry.start_line,)
    ]
    ordered_anchors = sorted(
        (anchor for _, _, anchors in fn_anchors for anchor in anchors),
        key=lambda span: (span.outer_start_line, span.start_line, span.end_line),
    )
    anchor_index = {id(span): idx for idx, span in enumerate(ordered_anchors)}
    span_headers: list[SpanHeader] = []
    for outer, fn_entry, anchors in fn_anchors:
        header_lines = set(range(outer, fn_entry.start_line + 1))
        for anchor in anchors:
            cur_idx = anchor_index[id(anchor)]
            prev_end = max(
                (
                    other.end_line
                    for other_idx, other in enumerate(ordered_anchors)
                    if other is not anchor
                    and other.end_line <= anchor.outer_start_line
                    and (other.outer_start_line < anchor.outer_start_line or other_idx < cur_idx)
                ),
                default=0,
            )
            leading = collect_contiguous_header_lines(
                raw_lines, anchor.outer_start_line, prev_end_line=prev_end, comment_lines=comment_lines, lang_name=lang_name
            )
            header_lines.update(leading | set(range(anchor.outer_start_line, anchor.start_line + 1)))
        span_headers.append(SpanHeader(outer, fn_entry.end_line, header_lines))
    decl_start_lines = {span.outer_start_line for span in span_headers} | {fn_entry.start_line for fn_entry in top_functions}
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for outer_line, end_line, header_set in span_headers
            if outer_line <= entry.line <= end_line
            and not (outer_line < end_line and entry.line == end_line and entry.line in decl_start_lines)
            for line_num in header_set
        }
        prev_comment = (
            {entry.line - 1}
            if (entry.line - 1) in comment_lines and entry.line not in decl_start_lines
            else set()
        )
        candidate_lines = {entry.line} | prev_comment | span_lines
        is_suppressed = any(
            (directive := directives.get(check_line)) is not None
            and directive.has_reason
            and (directive.is_wildcard or entry.rule in directive.rules)
            for check_line in candidate_lines
        )
        if not is_suppressed:
            kept.append(entry)
    return kept


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


def collect_local_calls(
    fn_node: Any,
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    fn_lines: dict[FnKey, int],
) -> set[FnKey]:
    keys_by_name: dict[str, list[FnKey]] = {}
    for fn_key in fn_lines:
        keys_by_name.setdefault(fn_key[0], []).append(fn_key)
    callees: set[FnKey] = set()
    body = fn_node.child_by_field_name("body")
    params_node = fn_node.child_by_field_name("parameters")
    default_roots = [
        dfn
        for pnode in (params_node.children if params_node is not None else ())
        if pnode.type in spec.param_nodes
        and (dfn := pnode.child_by_field_name("value") or pnode.child_by_field_name("right")) is not None
    ]
    swift_defaults = (
        [ch for idx, ch in enumerate(fn_node.children) if fn_node.field_name_for_child(idx) == "default_value"]
        if spec.name == "swift"
        else []
    )
    stack = ([body] if body is not None else []) + default_roots + swift_defaults
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type not in spec.call_nodes:
            continue
        callee_node = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
        if callee_node is None:
            continue
        callee_text = source_bytes[callee_node.start_byte : callee_node.end_byte].decode("utf-8", errors="replace")
        callees.update(keys_by_name.get(callee_text, ()))
    return callees


def compute_chain_depth(start_key: FnKey, call_graph: CallGraph) -> tuple[int, bool]:
    max_depth = 0
    deepest_has_local_call = False
    stack: list[tuple[FnKey, int, frozenset[FnKey]]] = [(start_key, 0, frozenset({start_key}))]
    while stack:
        current, depth, visited = stack.pop()
        next_callees = call_graph.get(current, set())
        if depth > max_depth:
            max_depth = depth
            deepest_has_local_call = bool(next_callees)
        elif depth == max_depth and next_callees:
            deepest_has_local_call = True
        stack.extend(
            (callee, depth + 1, visited | {callee})
            for callee in next_callees
            if callee not in visited
        )
    return max_depth, deepest_has_local_call


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


def collect_contiguous_header_lines(
    raw_lines: list[str],
    outer_start: int,
    *,
    prev_end_line: int,
    comment_lines: set[int],
    lang_name: str,
) -> set[int]:
    collected: set[int] = set()
    attr_stack: list[str] = []
    attr_lines: list[int] = []
    pending_delims: list[str] = []
    cur_code = ""
    noise_pattern = HEADER_NOISE_PATTERNS.get(lang_name, DEFAULT_HEADER_NOISE_PATTERN)
    supports_at = lang_name in {"python", "typescript", "tsx", "javascript", "swift"}
    line_no = outer_start - 1
    while (line_no > prev_end_line and line_no >= 1) or pending_delims or (attr_lines and not attr_stack):
        if pending_delims and pending_delims[-1] in "])}":
            attr_stack.append(pending_delims.pop())
            continue
        if pending_delims and (not attr_stack or HEADER_PAIR_MAP[attr_stack[-1]] != pending_delims[-1]):
            return collected
        if pending_delims:
            pending_delims.pop()
            attr_stack.pop()
            continue
        is_attr_open = (lang_name == "rust" and cur_code.startswith("#[") and not cur_code.startswith("#![")) or (
            supports_at and cur_code.startswith("@")
        )
        if attr_lines and not attr_stack and is_attr_open:
            collected.update(attr_lines)
            attr_lines.clear()
            continue
        if attr_lines and not attr_stack:
            break
        if line_no <= prev_end_line or line_no < 1:
            break
        trimmed = raw_lines[line_no - 1].strip()
        is_file_header = (lang_name == "python" and bool(PY_HEADER_COMMENT_PATTERN.match(trimmed))) or (
            line_no == 1 and lang_name in {"typescript", "tsx", "javascript"} and bool(TS_HEADER_COMMENT_PATTERN.search(trimmed))
        )
        if not attr_stack and is_file_header:
            break
        if not attr_stack and line_no in comment_lines:
            collected.add(line_no)
            line_no -= 1
            continue
        if attr_stack and line_no in comment_lines:
            attr_lines.append(line_no)
            line_no -= 1
            continue
        cur_code = noise_pattern.sub(" ", raw_lines[line_no - 1]).strip()
        if not attr_stack and supports_at and cur_code.startswith("@") and not cur_code.endswith((")", "]")):
            collected.add(line_no)
            line_no -= 1
            continue
        if not attr_stack and not cur_code.endswith((")", "]")):
            break
        attr_lines.append(line_no)
        pending_delims = [ch for ch in cur_code if ch in "[](){}"]
        line_no -= 1
    return collected


def extract_function_tier_tags(
    raw_lines: list[str],
    candidate_comment_lines: set[int],
) -> tuple[int | None, bool]:
    matched_tiers = [
        int(match.group(1))
        for line_no in sorted(candidate_comment_lines)
        if 1 <= line_no <= len(raw_lines)
        and (match := TIER_TAG_PATTERN.match(raw_lines[line_no - 1])) is not None
    ]
    if len(matched_tiers) > 1:
        return None, True
    return (matched_tiers[0], False) if matched_tiers else (None, False)


def compute_all_pairs_longest_paths(
    fn_keys: list[FnKey],
    call_graph: CallGraph,
) -> dict[FnPair, int]:
    dist: dict[FnPair, int] = {}
    stack: list[tuple[FnKey, FnKey, int, frozenset[FnKey]]] = [
        (src, dst, 1, frozenset({src, dst}))
        for src in fn_keys
        for dst in call_graph.get(src, set())
        if dst != src
    ]
    while stack:
        src, current, length, visited = stack.pop()
        if length > dist.get((src, current), 0):
            dist[(src, current)] = length
        stack.extend(
            (src, nxt, length + 1, visited | {nxt})
            for nxt in call_graph.get(current, set())
            if nxt not in visited and length + 1 > dist.get((src, nxt), 0)
        )
    return dist


def compute_tier_bounds(
    fn_keys: list[FnKey],
    dist: dict[FnPair, int],
    *,
    declared_tags: DeclaredTags,
) -> dict[FnKey, TierBounds]:
    d_max_map = {
        fn_key: max((length for (src, dst), length in dist.items() if dst == fn_key), default=0)
        for fn_key in fn_keys
    }
    height_map = {
        fn_key: max((length for (src, dst), length in dist.items() if src == fn_key), default=0)
        for fn_key in fn_keys
    }
    l0_adapters = {
        fn_key
        for fn_key in fn_keys
        if height_map[fn_key] == 0
        and d_max_map[fn_key] == 1
        and all(
            d_max_map[caller] == 0 and declared_tags.get(caller) in {0, None}
            for (caller, dst), length in dist.items()
            if dst == fn_key and length == 1
        )
    }
    bounds: dict[FnKey, TierBounds] = {}
    for fn_key in fn_keys:
        is_adapter = fn_key in l0_adapters
        lb_0 = 0 if is_adapter else d_max_map[fn_key]
        ub_0 = 3 - height_map[fn_key]
        anc_lbs = [
            (declared_tags[anc] or 0) + length
            for (anc, dst), length in dist.items()
            if dst == fn_key
            and declared_tags.get(anc) is not None
            and not (length == 1 and declared_tags[anc] == 0 and is_adapter)
        ]
        desc_ubs = [
            0 if (length == 1 and declared_tags[desc] == 0 and desc in l0_adapters) else ((declared_tags[desc] or 0) - length)
            for (src, desc), length in dist.items()
            if src == fn_key and declared_tags.get(desc) is not None
        ]
        lb = max([lb_0, *anc_lbs])
        ub = min([ub_0, *desc_ubs])
        valid_tiers = [f"L{tier}" for tier in range(4) if lb <= tier <= ub]
        bounds[fn_key] = TierBounds(d_max_map[fn_key], height_map[fn_key], lb_0, ub_0, is_adapter, valid_tiers)
    return bounds


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
                lambda row: (0 if row[1] in LOOP_RHS_FIELDS else 1) + (2 if row[1] == "body" or row[2].type in LOOP_BODY_TYPES else 0),
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
