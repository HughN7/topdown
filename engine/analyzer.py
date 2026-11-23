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
TIER_TAG_PATTERN = re.compile(r"^\s*(?:#|//)\s*\[L([0-3])\](?:\s|$)")
PY_HEADER_COMMENT_PATTERN = re.compile(r"^#(?:!|\s*-\*-|\s*coding[:=])")
TS_HEADER_COMMENT_PATTERN = re.compile(
    r"^(?://|/\*+)\s*(?:@ts-|eslint-disable|copyright\b|license\b|spdx-license-identifier\b)",
    re.IGNORECASE,
)
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
    line_comment_lines, comment_lines = collect_tree_comment_lines(tree.root_node, source_bytes, spec)
    raw_lines = source.split("\n")
    top_functions = collect_top_functions(tree.root_node, source_bytes, spec)
    member_functions = collect_member_functions(tree.root_node, source_bytes, spec)
    all_functions = top_functions + member_functions
    raw_violations = (
        check_order_and_depth(top_functions, source_bytes, spec=spec, file_path=file_path)
        + check_tier_tags(
            top_functions,
            source_bytes,
            file_path,
            spec=spec,
            raw_lines=raw_lines,
            comment_sets=(line_comment_lines, comment_lines),
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
    raw_lines = source.split("\n")
    top_functions = collect_top_functions(tree.root_node, source_bytes, spec)
    all_end_lines = [entry.end_line for entry in top_functions]
    fn_lines = {entry.name: entry.start_line for entry in top_functions}
    call_graph = {
        entry.name: collect_local_calls(entry.node, source_bytes, spec=spec, fn_lines=fn_lines) - {entry.name}
        for entry in top_functions
    }
    declared_tags: dict[str, int | None] = {}
    for entry in top_functions:
        outer = entry.outer_start_line or entry.start_line
        prev_end = max((end for end in all_end_lines if end < outer), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, outer, prev_end_line=prev_end, comment_lines=comment_lines, lang_name=spec.name
        )
        tag_val, _ = extract_function_tier_tags(
            raw_lines, (leading | set(range(outer, entry.start_line))) & line_comment_lines
        )
        declared_tags[entry.name] = tag_val
    names = [entry.name for entry in top_functions]
    dist_map = compute_all_pairs_longest_paths(names, call_graph)
    bounds_map = compute_tier_bounds(names, dist_map, declared_tags=declared_tags)
    entries: list[dict[str, Any]] = []
    for entry in top_functions:
        tag_val = declared_tags.get(entry.name)
        callers = sorted(caller for caller in names if entry.name in call_graph.get(caller, set()))
        callees = sorted(call_graph.get(entry.name, set()))
        entries.append(
            {
                "name": entry.name,
                "start_line": entry.start_line,
                "end_line": entry.end_line,
                "line_count": entry.end_line - entry.start_line + 1,
                "declared_tier": f"L{tag_val}" if tag_val is not None else None,
                "valid_tiers": bounds_map[entry.name][5],
                "callers": callers,
                "callees": callees,
            }
        )
    return entries


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


def check_tier_tags(
    top_functions: list[FunctionEntry],
    source_bytes: bytes,
    file_path: str,
    *,
    spec: LanguageSpec,
    raw_lines: list[str],
    comment_sets: tuple[set[int], set[int]],
) -> list[Violation]:
    line_comment_lines, comment_lines = comment_sets
    violations: list[Violation] = []
    all_end_lines = [entry.end_line for entry in top_functions]
    fn_lines = {entry.name: entry.start_line for entry in top_functions}
    call_graph = {
        entry.name: collect_local_calls(entry.node, source_bytes, spec=spec, fn_lines=fn_lines) - {entry.name}
        for entry in top_functions
    }
    declared_tags: dict[str, int | None] = {}
    for entry in top_functions:
        outer = entry.outer_start_line or entry.start_line
        prev_end = max((end for end in all_end_lines if end < outer), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, outer, prev_end_line=prev_end, comment_lines=comment_lines, lang_name=spec.name
        )
        tag_val, has_multiple = extract_function_tier_tags(
            raw_lines, (leading | set(range(outer, entry.start_line))) & line_comment_lines
        )
        declared_tags[entry.name] = tag_val
        if has_multiple:
            violations.append(
                Violation(file_path, entry.start_line, "CANON-01", f"'{entry.name}' has multiple tier tags -> keep a single [L0]..[L3] tag.")
            )
    names = [entry.name for entry in top_functions]
    dist_map = compute_all_pairs_longest_paths(names, call_graph)
    bounds_map = compute_tier_bounds(names, dist_map, declared_tags=declared_tags)
    for entry in top_functions:
        tag_val = declared_tags.get(entry.name)
        if tag_val is None:
            continue
        d_max, height, lb_0, ub_0, is_adapter, _ = bounds_map[entry.name]
        valid_0 = [f"[L{tier}]" for tier in range(4) if lb_0 <= tier <= ub_0]
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
        bad_ancestors = [
            (anc, dist_map[(anc, entry.name)])
            for anc in names
            if anc != entry.name
            and declared_tags.get(anc) is not None
            and (anc, entry.name) in dist_map
            and not (dist_map[(anc, entry.name)] == 1 and declared_tags[anc] == 0 and tag_val == 0 and is_adapter)
            and tag_val - declared_tags[anc] < dist_map[(anc, entry.name)]
        ]
        if bad_ancestors:
            anc_name, anc_dist = bad_ancestors[0]
            anc_tag = declared_tags[anc_name] or 0
            violations.append(
                Violation(
                    file_path,
                    entry.start_line,
                    "CANON-01",
                    f"'{entry.name}' is tagged [L{tag_val}] at call distance {anc_dist} below '{anc_name}' ([L{anc_tag}]) -> callee tier must be at least [L{anc_tag + anc_dist}].",
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
    *,
    raw_lines: list[str],
    comment_lines: set[int],
    lang_name: str,
) -> list[Violation]:
    all_end_lines = [entry.end_line for entry in top_functions]
    span_headers: list[tuple[int, int, set[int]]] = []
    for fn_entry in top_functions:
        outer = fn_entry.outer_start_line or fn_entry.start_line
        prev_end = max((end for end in all_end_lines if end < outer), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, outer, prev_end_line=prev_end, comment_lines=comment_lines, lang_name=lang_name
        )
        span_headers.append((outer, fn_entry.end_line, leading | set(range(outer, fn_entry.start_line + 1))))
    decl_start_lines = {outer for outer, _, _ in span_headers} | {fn_entry.start_line for fn_entry in top_functions}
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for outer_line, end_line, header_set in span_headers
            if outer_line <= entry.line <= end_line
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


def collect_contiguous_header_lines(
    raw_lines: list[str],
    outer_start: int,
    *,
    prev_end_line: int,
    comment_lines: set[int],
    lang_name: str,
) -> set[int]:
    collected: set[int] = set()
    line_no = outer_start - 1
    while line_no > prev_end_line and line_no >= 1:
        trimmed = raw_lines[line_no - 1].strip()
        is_attr = (lang_name == "rust" and trimmed.startswith("#[") and not trimmed.startswith("#![")) or (
            lang_name in {"python", "typescript", "tsx", "javascript", "swift"} and trimmed.startswith("@")
        )
        is_file_header = (lang_name == "python" and bool(PY_HEADER_COMMENT_PATTERN.match(trimmed))) or (
            line_no == 1 and lang_name in {"typescript", "tsx", "javascript"} and bool(TS_HEADER_COMMENT_PATTERN.search(trimmed))
        )
        if (line_no not in comment_lines and not is_attr) or is_file_header:
            break
        collected.add(line_no)
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
    names: list[str],
    call_graph: dict[str, set[str]],
) -> dict[tuple[str, str], int]:
    dist: dict[tuple[str, str], int] = {}
    stack: list[tuple[str, str, int, frozenset[str]]] = [
        (src, dst, 1, frozenset({src, dst}))
        for src in names
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
    names: list[str],
    dist: dict[tuple[str, str], int],
    *,
    declared_tags: dict[str, int | None],
) -> dict[str, tuple[int, int, int, int, bool, list[str]]]:
    d_max_map = {
        name: max((length for (src, dst), length in dist.items() if dst == name), default=0)
        for name in names
    }
    height_map = {
        name: max((length for (src, dst), length in dist.items() if src == name), default=0)
        for name in names
    }
    l0_adapters = {
        name
        for name in names
        if height_map[name] == 0
        and d_max_map[name] == 1
        and all(
            d_max_map[caller] == 0 and declared_tags.get(caller) in {0, None}
            for (caller, dst), length in dist.items()
            if dst == name and length == 1
        )
    }
    bounds: dict[str, tuple[int, int, int, int, bool, list[str]]] = {}
    for name in names:
        is_adapter = name in l0_adapters
        lb_0 = 0 if is_adapter else d_max_map[name]
        ub_0 = 3 - height_map[name]
        anc_lbs = [
            (declared_tags[anc] or 0) + length
            for (anc, dst), length in dist.items()
            if dst == name
            and declared_tags.get(anc) is not None
            and not (length == 1 and declared_tags[anc] == 0 and is_adapter)
        ]
        desc_ubs = [
            0 if (length == 1 and declared_tags[desc] == 0 and desc in l0_adapters) else ((declared_tags[desc] or 0) - length)
            for (src, desc), length in dist.items()
            if src == name and declared_tags.get(desc) is not None
        ]
        lb = max([lb_0, *anc_lbs])
        ub = min([ub_0, *desc_ubs])
        valid_tiers = [f"L{tier}" for tier in range(4) if lb <= tier <= ub]
        bounds[name] = (d_max_map[name], height_map[name], lb_0, ub_0, is_adapter, valid_tiers)
    return bounds
