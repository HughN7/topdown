"""Call-graph, topological order, and [L0..L3] tier signpost verification."""

from __future__ import annotations

import re
from typing import Any

from engine.models import (
    MAX_BUSINESS_DEPTH,
    CallGraph,
    ChainFrame,
    DeclaredTags,
    FnKey,
    FnPair,
    FunctionEntry,
    OverloadSpan,
    PathFrame,
    TierBounds,
    TierState,
    Violation,
)
from engine.specs import LanguageSpec
from engine.suppress import collect_contiguous_header_lines

TIER_TAG_PATTERN = re.compile(r"^\s*(?:#|//)\s*\[L([0-3])\](?:\s|$)")
IDENT_NODE_TYPES = frozenset({"identifier", "simple_identifier", "property_identifier"})


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
    stack: list[ChainFrame] = [ChainFrame(start_key, 0, frozenset({start_key}))]
    while stack:
        current, depth, visited = stack.pop()
        next_callees = call_graph.get(current, set())
        if depth > max_depth:
            max_depth = depth
            deepest_has_local_call = bool(next_callees)
        elif depth == max_depth and next_callees:
            deepest_has_local_call = True
        stack.extend(
            ChainFrame(callee, depth + 1, visited | {callee})
            for callee in next_callees
            if callee not in visited
        )
    return max_depth, deepest_has_local_call


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
    stack: list[PathFrame] = [
        PathFrame(src, dst, 1, frozenset({src, dst}))
        for src in fn_keys
        for dst in call_graph.get(src, set())
        if dst != src
    ]
    while stack:
        src, current, length, visited = stack.pop()
        if length > dist.get((src, current), 0):
            dist[(src, current)] = length
        stack.extend(
            PathFrame(src, nxt, length + 1, visited | {nxt})
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
