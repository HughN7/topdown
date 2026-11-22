"""Universal tree-sitter AST analyzer for Topdown structural rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tree_sitter_language_pack import get_parser

from engine.specs import LanguageSpec, spec_for_path

MAX_FUNCTION_LINES = 80
MAX_NESTING_DEPTH = 2
MAX_BUSINESS_DEPTH = 2
GAMING_NAME_PATTERN = re.compile(r"(?:_part\d+|_helper\d*|Part\d+|Helper\d*)$")
IGNORE_PATTERN = re.compile(r"topdown-ignore(?:\((.*)\))?")
REASON_PATTERN = re.compile(r"""reason\s*=\s*(?:"([^"]+)"|'([^']+)')""")
RULE_ID_PATTERN = re.compile(r"\b(?:CANON|LLM)-\d{2}\b")
IDENT_NODE_TYPES = frozenset({"identifier", "simple_identifier", "property_identifier"})


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
    top_functions = collect_top_functions(tree.root_node, source_bytes, spec)
    member_functions = collect_member_functions(tree.root_node, source_bytes, spec)
    all_functions = top_functions + member_functions
    raw_violations = (
        check_order_and_depth(top_functions, source_bytes, spec=spec, file_path=file_path)
        + check_function_rules(all_functions, spec, file_path)
    )
    kept = filter_suppressed(raw_violations, all_functions, directives)
    return sorted(ignore_violations + kept, key=lambda entry: (entry.line, entry.rule))


def check_order_and_depth(
    top_functions: list[FunctionEntry],
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    fn_lines = {entry.name: entry.start_line for entry in top_functions}
    call_graph: dict[str, set[str]] = {}
    for entry in top_functions:
        callees = collect_local_calls(entry.node, source_bytes, spec=spec, fn_lines=fn_lines)
        call_graph[entry.name] = callees
        out_of_order = [
            callee for callee in sorted(callees) if callee != entry.name and fn_lines[callee] < entry.start_line
        ]
        violations.extend(
            Violation(
                file_path,
                entry.start_line,
                "CANON-01",
                f"'{entry.name}' calls '{callee}' defined above it (line {fn_lines[callee]}) -> move '{callee}' below '{entry.name}' for top-down order.",
            )
            for callee in out_of_order
        )
    for entry in top_functions:
        depth, leaf_calls_local = compute_chain_depth(entry.name, call_graph)
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


def collect_top_functions(root_node: Any, source_bytes: bytes, spec: LanguageSpec) -> list[FunctionEntry]:
    entries: list[FunctionEntry] = []
    for child in root_node.named_children:
        target = unwrap_function_node(child, spec)
        if target is None:
            continue
        name = extract_node_name(target, source_bytes)
        if name is None:
            continue
        entries.append(
            FunctionEntry(
                name=name,
                node=target,
                start_line=target.start_point[0] + 1,
                end_line=target.end_point[0] + 1,
                outer_start_line=child.start_point[0] + 1,
            )
        )
    return entries


def collect_member_functions(root_node: Any, source_bytes: bytes, spec: LanguageSpec) -> list[FunctionEntry]:
    if not spec.container_nodes:
        return []
    entries: list[FunctionEntry] = []
    stack = [
        child
        for child in root_node.named_children
        if unwrap_function_node(child, spec) is None
        and (child.type in spec.container_nodes or child.type in spec.wrapper_nodes)
    ]
    while stack:
        node = stack.pop()
        children = node.named_children
        stack.extend(
            child
            for child in children
            if unwrap_function_node(child, spec) is None
            and (child.type in spec.container_nodes or child.type in spec.wrapper_nodes)
        )
        entries.extend(
            FunctionEntry(
                name,
                target,
                target.start_point[0] + 1,
                target.end_point[0] + 1,
                child.start_point[0] + 1,
            )
            for child in children
            if (target := unwrap_function_node(child, spec)) is not None
            and (name := extract_node_name(target, source_bytes)) is not None
        )
    return entries


def unwrap_function_node(node: Any, spec: LanguageSpec) -> Any | None:
    if node.type in spec.function_nodes:
        return node
    if node.type not in spec.wrapper_nodes:
        return None
    for child in node.named_children:
        if child.type in spec.function_nodes:
            return child
    return None


def extract_node_name(fn_node: Any, source_bytes: bytes) -> str | None:
    name_node = fn_node.child_by_field_name("name")
    if name_node is not None:
        return source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
    for child in fn_node.named_children:
        if child.type in IDENT_NODE_TYPES:
            return source_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


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


def collect_local_calls(
    fn_node: Any,
    source_bytes: bytes,
    *,
    spec: LanguageSpec,
    fn_lines: dict[str, int],
) -> set[str]:
    callees: set[str] = set()
    stack = list(fn_node.named_children)
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type not in spec.call_nodes:
            continue
        callee_node = node.child_by_field_name("function") or (node.named_children[0] if node.named_children else None)
        if callee_node is None:
            continue
        callee_text = source_bytes[callee_node.start_byte : callee_node.end_byte].decode("utf-8", errors="replace")
        if callee_text in fn_lines:
            callees.add(callee_text)
    return callees


def compute_chain_depth(start_name: str, call_graph: dict[str, set[str]]) -> tuple[int, bool]:
    max_depth = 0
    deepest_has_local_call = False
    stack: list[tuple[str, int, frozenset[str]]] = [(start_name, 0, frozenset({start_name}))]
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


def filter_suppressed(
    violations: list[Violation],
    top_functions: list[FunctionEntry],
    directives: dict[int, IgnoreDirective],
) -> list[Violation]:
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for fn_entry in top_functions
            if (fn_entry.outer_start_line or fn_entry.start_line) <= entry.line <= fn_entry.end_line
            for line_num in (
                fn_entry.start_line,
                fn_entry.start_line - 1,
                fn_entry.outer_start_line or fn_entry.start_line,
                (fn_entry.outer_start_line or fn_entry.start_line) - 1,
            )
        }
        candidate_lines = {entry.line, entry.line - 1} | span_lines
        is_suppressed = any(
            (directive := directives.get(check_line)) is not None
            and directive.has_reason
            and (directive.is_wildcard or entry.rule in directive.rules)
            for check_line in candidate_lines
        )
        if not is_suppressed:
            kept.append(entry)
    return kept
