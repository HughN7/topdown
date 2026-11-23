"""Reference zero-dependency Python AST checker for the topdown architecture framework."""

from __future__ import annotations

import ast
import difflib
import heapq
import io
import json
import re
import sys
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FUNCTION_LINES = 80
MAX_POSITIONAL_ARGS = 3
MAX_TOTAL_ARGS = 6
MAX_NESTING_DEPTH = 2
MAX_BUSINESS_DEPTH = 2
VAGUE_NOUNS = frozenset({"data", "info", "item", "temp", "result", "handler"})
GAMING_NAME_PATTERN = re.compile(r"(?:_part\d+|_helper\d*)$", re.IGNORECASE)
TEST_ENV_MARKERS = frozenset({"PYTEST_CURRENT_TEST", "UNITTEST_RUNNING"})
TEST_MODULE_MARKERS = frozenset({"pytest", "unittest"})
TEST_PATH_PATTERN = re.compile(r"(?:^|[\\/])(?:tests?|__tests__)[\\/]|(?:^|[\\/])test_[^\\/]+$|_test\.py$", re.IGNORECASE)
SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "build", "dist", "node_modules"})
IGNORE_PATTERN = re.compile(r"#\s*topdown-ignore(?:\((.*)\))?")
REASON_PATTERN = re.compile(r"""reason\s*=\s*(?:"([^"]+)"|'([^']+)')""")
RULE_ID_PATTERN = re.compile(r"\b(?:CANON|LLM)-\d{2}\b")
HEADER_COMMENT_PATTERN = re.compile(r"^#(?:!|\s*-\*-|\s*coding[:=])")
TIER_TAG_PATTERN = re.compile(r"^\s*#\s*\[L([0-3])\](?:\s|$)")
LIFECYCLE_BOOL_PATTERN = re.compile(
    r"^(?:(?:is|has|was)_(?:loading|pending|fetching|submitting|saving|retrying|error|errored|failed|rejected|success|succeeded|loaded|ready|done|completed|idle)|(?:loading|pending|fetching|submitting|errored|failed|succeeded))$"
)
CONTROL_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.Match,
)


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
class CliOptions:
    paths: tuple[str, ...]
    as_json: bool
    apply_fix: bool = False
    dry_run: bool = False
    outline: bool = False


def main() -> int:
    options = parse_cli_args(sys.argv[1:])
    if options is None:
        print(
            "Usage: python3 check_python.py [--json] [--fix] [--dry-run] [--outline] <path> [...]",
            file=sys.stderr,
        )
        return 2
    target_files, error_msg = collect_python_files(options.paths)
    if error_msg is not None:
        print(error_msg, file=sys.stderr)
        return 2
    if options.outline:
        return run_outline_cli(target_files, as_json=options.as_json)
    all_violations: list[Violation] = []
    for file_path in target_files:
        source_text, read_error = read_source_text(file_path)
        if read_error is not None:
            print(read_error, file=sys.stderr)
            return 2
        try:
            source_text = apply_file_fix(source_text, file_path, dry_run=options.dry_run) if options.apply_fix else source_text
            file_violations = [] if (options.apply_fix and options.dry_run) else check_source(source_text, str(file_path))
        except (SyntaxError, ValueError) as exc:
            print(f"Parse error in {file_path}: {exc}", file=sys.stderr)
            return 2
        all_violations.extend(file_violations)
    if not options.dry_run:
        emit_violations(all_violations, as_json=options.as_json)
    return 1 if all_violations else 0


def run_outline_cli(target_files: list[Path], *, as_json: bool) -> int:
    payload: list[dict[str, object]] = []
    for file_path in target_files:
        source_text, read_error = read_source_text(file_path)
        if read_error is not None:
            print(read_error, file=sys.stderr)
            return 2
        try:
            functions = build_file_outline(source_text, str(file_path))
        except (SyntaxError, ValueError) as exc:
            print(f"Parse error in {file_path}: {exc}", file=sys.stderr)
            return 2
        payload.append({"path": str(file_path), "functions": functions})
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_outline_text(payload))
    return 0


def read_source_text(file_path: Path) -> tuple[str, str | None]:
    try:
        return file_path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return "", f"Error reading {file_path}: {exc}"


def apply_file_fix(source_text: str, file_path: Path, *, dry_run: bool) -> str:
    fixed_text, refusal = fix_source(source_text)
    if refusal is not None:
        print(f"{file_path}: {refusal}", file=sys.stderr)
        return source_text
    if fixed_text == source_text:
        return source_text
    if dry_run:
        diff_lines = difflib.unified_diff(
            source_text.splitlines(keepends=True),
            fixed_text.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
        print("".join(diff_lines), end="")
        return source_text
    file_path.write_text(fixed_text, encoding="utf-8")
    return fixed_text


def fix_source(source: str) -> tuple[str, str | None]:
    tree = ast.parse(source)
    fn_positions = [
        (idx, node)
        for idx, node in enumerate(tree.body)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(fn_positions) < 2:
        return source, None
    first_pos, last_pos = fn_positions[0][0], fn_positions[-1][0]
    if last_pos - first_pos + 1 != len(fn_positions):
        return source, "Refused --fix: interleaved statements between top-level functions."
    top_functions = [node for _, node in fn_positions]
    if any(fn_node.decorator_list for fn_node in top_functions):
        return source, "Refused --fix: decorated top-level functions may have order-sensitive registration."
    fn_lines = {fn_node.name: fn_node.lineno for fn_node in top_functions}
    call_graph = {fn_node.name: collect_local_calls(fn_node, fn_lines) for fn_node in top_functions}
    ordered_indices = topological_sort_functions(top_functions, call_graph)
    if ordered_indices is None:
        return source, "Refused --fix: cyclic/mutual recursion detected in local call graph."
    if ordered_indices == list(range(len(top_functions))):
        return source, None
    reordered = rebuild_reordered_source(source, top_functions, ordered_indices)
    if reordered is None:
        return source, "Refused --fix: detached comments between functions would be dropped."
    return reordered, None


def build_file_outline(source: str, file_path: str) -> list[dict[str, object]]:
    tree = ast.parse(source, filename=file_path)
    _, _, line_comment_lines = parse_ignore_directives(source, file_path)
    raw_lines = source.split("\n")
    top_functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    all_end_lines = [
        getattr(node, "end_lineno", None) or getattr(node, "lineno", 0)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    fn_lines = {fn_node.name: fn_node.lineno for fn_node in top_functions}
    call_graph = {
        fn_node.name: collect_local_calls(fn_node, fn_lines) - {fn_node.name}
        for fn_node in top_functions
    }
    declared_tags: dict[str, int | None] = {}
    for fn_node in top_functions:
        outer_start = fn_node.decorator_list[0].lineno if fn_node.decorator_list else fn_node.lineno
        prev_end = max((end for end in all_end_lines if end < outer_start), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, outer_start, prev_end_line=prev_end, comment_lines=line_comment_lines
        )
        tag_val, _ = extract_function_tier_tags(
            raw_lines, (leading | set(range(outer_start, fn_node.lineno))) & line_comment_lines
        )
        declared_tags[fn_node.name] = tag_val
    names = [fn_node.name for fn_node in top_functions]
    dist_map = compute_all_pairs_longest_paths(names, call_graph)
    bounds_map = compute_tier_bounds(names, dist_map, declared_tags=declared_tags)
    entries: list[dict[str, object]] = []
    for fn_node in top_functions:
        end_line = fn_node.end_lineno or fn_node.lineno
        tag_val = declared_tags.get(fn_node.name)
        valid_tiers = bounds_map[fn_node.name][5]
        callers = sorted(caller for caller in names if fn_node.name in call_graph.get(caller, set()))
        callees = sorted(call_graph.get(fn_node.name, set()))
        entries.append(
            {
                "name": fn_node.name,
                "start_line": fn_node.lineno,
                "end_line": end_line,
                "line_count": end_line - fn_node.lineno + 1,
                "declared_tier": f"L{tag_val}" if tag_val is not None else None,
                "valid_tiers": valid_tiers,
                "callers": callers,
                "callees": callees,
            }
        )
    return entries


def format_outline_text(payload: list[dict[str, object]]) -> str:
    blocks: list[str] = []
    for file_entry in payload:
        lines = [str(file_entry["path"])]
        functions = file_entry["functions"]
        if not isinstance(functions, list) or not functions:
            lines.append("  (no top-level functions)")
            blocks.append("\n".join(lines))
            continue
        for fn_info in functions:
            declared = fn_info["declared_tier"]
            valid = fn_info["valid_tiers"]
            range_badge = f"[{valid[0]}..{valid[-1]}]" if len(valid) > 1 else "[invalid]"
            inferred_badge = f"[{valid[0]}]" if len(valid) == 1 else range_badge
            badge = f"[{declared}]" if declared is not None else inferred_badge
            callers = fn_info["callers"]
            callees = fn_info["callees"]
            callers_part = f" <- {', '.join(callers)}" if callers else ""
            callees_part = f" -> {', '.join(callees)}" if callees else ""
            span = f"(L{fn_info['start_line']}-L{fn_info['end_line']}, {fn_info['line_count']}L)"
            lines.append(f"  {badge} {fn_info['name']} {span}{callers_part}{callees_part}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def check_source(source: str, file_path: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    directives, ignore_violations, line_comment_lines = parse_ignore_directives(source, file_path)
    raw_lines = source.split("\n")
    top_functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    all_classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    class_methods = [
        member
        for node in all_classes
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    checked_functions = top_functions + class_methods
    all_decls = checked_functions + all_classes
    all_end_lines = [node.end_lineno or node.lineno for node in all_decls]
    raw_violations: list[Violation] = []
    raw_violations.extend(check_order_and_depth(top_functions, file_path))
    raw_violations.extend(
        check_tier_tags(
            top_functions,
            raw_lines,
            file_path,
            line_comment_lines=line_comment_lines,
            all_end_lines=all_end_lines,
        )
    )
    raw_violations.extend(check_class_state_machines(all_classes, file_path))
    is_test_file = bool(TEST_PATH_PATTERN.search(file_path))
    for fn_node in checked_functions:
        raw_violations.extend(check_function_rules(fn_node, file_path, is_test_file=is_test_file))
    filtered = filter_suppressed(
        raw_violations,
        all_decls,
        directives,
        raw_lines=raw_lines,
        comment_lines=line_comment_lines,
    )
    return sorted(ignore_violations + filtered, key=lambda entry: (entry.line, entry.rule))


def check_order_and_depth(
    top_functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    fn_lines = {fn_node.name: fn_node.lineno for fn_node in top_functions}
    call_graph: dict[str, set[str]] = {}
    for fn_node in top_functions:
        callees = collect_local_calls(fn_node, fn_lines)
        call_graph[fn_node.name] = callees
        out_of_order = [
            name for name in sorted(callees) if name != fn_node.name and fn_lines[name] < fn_node.lineno
        ]
        violations.extend(
            Violation(
                path=file_path,
                line=fn_node.lineno,
                rule="CANON-01",
                message=(
                    f"'{fn_node.name}' calls '{callee}' defined above it (line {fn_lines[callee]}) "
                    f"-> move '{callee}' below '{fn_node.name}' for top-down order."
                ),
            )
            for callee in out_of_order
        )
    for fn_node in top_functions:
        depth, leaf_calls_local = compute_chain_depth(fn_node.name, call_graph)
        exceeds_ceiling = depth > MAX_BUSINESS_DEPTH + 1 or (
            depth == MAX_BUSINESS_DEPTH + 1 and leaf_calls_local
        )
        if exceeds_ceiling:
            violations.append(
                Violation(
                    path=file_path,
                    line=fn_node.lineno,
                    rule="CANON-01",
                    message=(
                        f"'{fn_node.name}' reaches call depth {depth} (max L0->L1->L2 + L3 leaf) "
                        "-> flatten pass-through calls or inline trivial helpers."
                    ),
                )
            )
    return violations


def check_tier_tags(
    top_functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    raw_lines: list[str],
    file_path: str,
    *,
    line_comment_lines: set[int],
    all_end_lines: list[int],
) -> list[Violation]:
    violations: list[Violation] = []
    fn_lines = {fn_node.name: fn_node.lineno for fn_node in top_functions}
    call_graph = {
        fn_node.name: collect_local_calls(fn_node, fn_lines) - {fn_node.name}
        for fn_node in top_functions
    }
    declared_tags: dict[str, int | None] = {}
    for fn_node in top_functions:
        outer_start = fn_node.decorator_list[0].lineno if fn_node.decorator_list else fn_node.lineno
        prev_end = max((end for end in all_end_lines if end < outer_start), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, outer_start, prev_end_line=prev_end, comment_lines=line_comment_lines
        )
        tag_val, has_multiple = extract_function_tier_tags(
            raw_lines, (leading | set(range(outer_start, fn_node.lineno))) & line_comment_lines
        )
        declared_tags[fn_node.name] = tag_val
        if has_multiple:
            violations.append(
                Violation(file_path, fn_node.lineno, "CANON-01", f"'{fn_node.name}' has multiple tier tags -> keep a single [L0]..[L3] tag.")
            )
    names = [fn_node.name for fn_node in top_functions]
    dist_map = compute_all_pairs_longest_paths(names, call_graph)
    bounds_map = compute_tier_bounds(names, dist_map, declared_tags=declared_tags)
    for fn_node in top_functions:
        tag_val = declared_tags.get(fn_node.name)
        if tag_val is None:
            continue
        d_max, height, lb_0, ub_0, is_adapter, _ = bounds_map[fn_node.name]
        valid_0 = [f"[L{tier}]" for tier in range(4) if lb_0 <= tier <= ub_0]
        if tag_val == 3 and height > 0:
            violations.append(
                Violation(file_path, fn_node.lineno, "CANON-01", f"'{fn_node.name}' is tagged [L3] (leaf) but calls local functions -> update tag or move local calls to L1/L2.")
            )
        elif tag_val < lb_0 or tag_val > ub_0:
            msg = (
                f"'{fn_node.name}' is tagged [L{tag_val}] outside valid tier range ({', '.join(valid_0)}) for depth {d_max} and height {height} -> update tier tag."
                if valid_0
                else f"'{fn_node.name}' is tagged [L{tag_val}] in a call chain exceeding L0->L3 ceiling (depth {d_max}, height {height}) -> flatten call depth."
            )
            violations.append(Violation(file_path, fn_node.lineno, "CANON-01", msg))
        bad_ancestors = [
            (anc, dist_map[(anc, fn_node.name)])
            for anc in names
            if anc != fn_node.name
            and declared_tags.get(anc) is not None
            and (anc, fn_node.name) in dist_map
            and not (dist_map[(anc, fn_node.name)] == 1 and declared_tags[anc] == 0 and tag_val == 0 and is_adapter)
            and tag_val - declared_tags[anc] < dist_map[(anc, fn_node.name)]
        ]
        if bad_ancestors:
            anc_name, anc_dist = bad_ancestors[0]
            anc_tag = declared_tags[anc_name] or 0
            violations.append(
                Violation(
                    file_path,
                    fn_node.lineno,
                    "CANON-01",
                    f"'{fn_node.name}' is tagged [L{tag_val}] at call distance {anc_dist} below '{anc_name}' ([L{anc_tag}]) -> callee tier must be at least [L{anc_tag + anc_dist}].",
                )
            )
    return violations


def check_class_state_machines(classes: list[ast.ClassDef], file_path: str) -> list[Violation]:
    violations: list[Violation] = []
    for cls_node in classes:
        unique_flags = collect_node_lifecycle_flags(cls_node)
        if len(unique_flags) >= 2:
            violations.append(
                Violation(
                    file_path,
                    cls_node.lineno,
                    "CANON-03",
                    f"Implicit state machine ({', '.join(unique_flags)}) -> replace parallel lifecycle booleans with an explicit status Enum or Literal union.",
                )
            )
    return violations


def check_function_rules(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    *,
    is_test_file: bool = False,
) -> list[Violation]:
    violations: list[Violation] = []
    if GAMING_NAME_PATTERN.search(fn_node.name):
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "LLM-05",
                f"Function '{fn_node.name}' looks like a rule-gaming split -> rename by domain responsibility or inline.",
            )
        )
    line_count = (fn_node.end_lineno or fn_node.lineno) - fn_node.lineno + 1
    if line_count > MAX_FUNCTION_LINES:
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "CANON-01",
                f"'{fn_node.name}' is {line_count} lines (max {MAX_FUNCTION_LINES}) -> extract a cohesive L1/L2 pure helper.",
            )
        )
    pos_count, total_count = count_parameters(fn_node)
    if pos_count > MAX_POSITIONAL_ARGS:
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "CANON-03",
                f"'{fn_node.name}' has {pos_count} positional args (max {MAX_POSITIONAL_ARGS}) -> make extras keyword-only with '*'.",
            )
        )
    if total_count > MAX_TOTAL_ARGS:
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "CANON-03",
                f"'{fn_node.name}' has {total_count} total args (max {MAX_TOTAL_ARGS}) -> group related inputs into a frozen dataclass.",
            )
        )
    nesting = measure_max_nesting(fn_node)
    if nesting > MAX_NESTING_DEPTH:
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "CANON-05",
                f"'{fn_node.name}' has nesting depth {nesting} (max {MAX_NESTING_DEPTH}) -> invert conditions with early return/continue or extract an L2 predicate.",
            )
        )
    lifecycle_flags = collect_node_lifecycle_flags(fn_node)
    if len(lifecycle_flags) >= 2:
        violations.append(
            Violation(
                file_path,
                fn_node.lineno,
                "CANON-03",
                f"Implicit state machine ({', '.join(lifecycle_flags)}) -> replace parallel lifecycle booleans with an explicit status Enum or Literal union.",
            )
        )
    violations.extend(scan_body_violations(fn_node, file_path))
    if not is_test_file:
        violations.extend(scan_test_runner_branching(fn_node, file_path))
    return violations


def parse_cli_args(raw_args: list[str]) -> CliOptions | None:
    as_json = False
    apply_fix = False
    dry_run = False
    outline = False
    paths: list[str] = []
    for token in raw_args:
        if token == "--json":
            as_json = True
            continue
        if token == "--fix":
            apply_fix = True
            continue
        if token == "--dry-run":
            dry_run = True
            continue
        if token == "--outline":
            outline = True
            continue
        if token.startswith("-"):
            return None
        paths.append(token)
    if not paths or (outline and (apply_fix or dry_run)):
        return None
    return CliOptions(paths=tuple(paths), as_json=as_json, apply_fix=apply_fix, dry_run=dry_run, outline=outline)


def collect_python_files(raw_paths: tuple[str, ...]) -> tuple[list[Path], str | None]:
    collected: list[Path] = []
    for raw_entry in raw_paths:
        target = Path(raw_entry)
        if not target.exists():
            return [], f"Path not found: {raw_entry}"
        if target.is_dir():
            collected.extend(
                path
                for path in sorted(target.rglob("*.py"))
                if not any(part in SKIP_DIRS for part in path.relative_to(target).parts)
            )
        else:
            collected.append(target)
    return collected, None


def emit_violations(violations: list[Violation], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(entry) for entry in violations], indent=2))
        return
    for entry in violations:
        print(f"{entry.path}:{entry.line}: [{entry.rule}] {entry.message}")


def parse_ignore_directives(
    source: str,
    file_path: str,
) -> tuple[dict[int, IgnoreDirective], list[Violation], set[int]]:
    directives: dict[int, IgnoreDirective] = {}
    violations: list[Violation] = []
    line_comment_lines: set[int] = set()
    raw_lines = source.split("\n")
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type != tokenize.COMMENT:
            continue
        line_no = tok.start[0]
        if 1 <= line_no <= len(raw_lines) and raw_lines[line_no - 1].lstrip().startswith("#"):
            line_comment_lines.add(line_no)
        match = IGNORE_PATTERN.search(tok.string)
        if not match:
            continue
        inner = match.group(1) or ""
        rules = frozenset(RULE_ID_PATTERN.findall(inner))
        has_reason = REASON_PATTERN.search(inner) is not None
        stripped_without_reason = REASON_PATTERN.sub("", inner).strip(" ,")
        is_wildcard = not stripped_without_reason
        directives[line_no] = IgnoreDirective(
            line=line_no, rules=rules, has_reason=has_reason, is_wildcard=is_wildcard
        )
        if not has_reason:
            violations.append(
                Violation(file_path, line_no, "CANON-08", 'topdown-ignore requires a non-empty reason="...".')
            )
    return directives, violations, line_comment_lines


def filter_suppressed(
    violations: list[Violation],
    checked_functions: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef],
    directives: dict[int, IgnoreDirective],
    *,
    raw_lines: list[str],
    comment_lines: set[int],
) -> list[Violation]:
    all_end_lines = [node.end_lineno or node.lineno for node in checked_functions]
    fn_spans: list[tuple[int, int, set[int]]] = []
    for fn_node in checked_functions:
        dec_line = fn_node.decorator_list[0].lineno if fn_node.decorator_list else fn_node.lineno
        def_line = fn_node.lineno
        end_line = fn_node.lineno if isinstance(fn_node, ast.ClassDef) else (fn_node.end_lineno or fn_node.lineno)
        prev_end = max((end for end in all_end_lines if end < dec_line), default=0)
        leading = collect_contiguous_header_lines(
            raw_lines, dec_line, prev_end_line=prev_end, comment_lines=comment_lines
        )
        fn_spans.append((dec_line, end_line, leading | set(range(dec_line, def_line + 1))))
    decl_start_lines = {dec_line for dec_line, _, _ in fn_spans} | {fn_node.lineno for fn_node in checked_functions}
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for dec_line, end_line, header_set in fn_spans
            if dec_line <= entry.line <= end_line
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


def collect_local_calls(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    fn_lines: dict[str, int],
) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(fn_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in fn_lines
    }


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


def count_parameters(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
    args = fn_node.args
    positional = [arg.arg for arg in args.posonlyargs + args.args]
    if positional and positional[0] in {"self", "cls"}:
        positional = positional[1:]
    pos_count = len(positional)
    total_count = pos_count + len(args.kwonlyargs)
    return pos_count, total_count


def measure_max_nesting(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    max_depth = 0
    stack: list[tuple[ast.AST, int]] = [(stmt, 0) for stmt in fn_node.body]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        next_depth = depth + 1 if isinstance(node, CONTROL_NODES) else depth
        max_depth = max(max_depth, next_depth)
        is_if_node = isinstance(node, ast.If)
        stack.extend(
            (child, depth if is_if_node and isinstance(child, ast.If) and child in node.orelse else next_depth)
            for child in ast.iter_child_nodes(node)
        )
    return max_depth


def collect_node_lifecycle_flags(
    scope_node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    is_class = isinstance(scope_node, ast.ClassDef)
    boundary_types = (ast.ClassDef,) if is_class else (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    owned_nodes: list[ast.AST] = []
    queue: list[ast.AST] = list(scope_node.body)
    while queue:
        current = queue.pop()
        if isinstance(current, boundary_types):
            continue
        owned_nodes.append(current)
        queue.extend(ast.iter_child_nodes(current))
    candidates: list[tuple[str, ast.AST | None, ast.AST | None]] = []
    if not is_class:
        pos_params = scope_node.args.posonlyargs + scope_node.args.args
        pad_count = len(pos_params) - len(scope_node.args.defaults)
        pos_defaults = [None] * pad_count + list(scope_node.args.defaults)
        param_pairs = list(zip(pos_params, pos_defaults)) + list(
            zip(scope_node.args.kwonlyargs, scope_node.args.kw_defaults)
        )
        candidates.extend((param.arg, param.annotation, default) for param, default in param_pairs)
    target_stmts = scope_node.body if is_class else owned_nodes
    candidates.extend(
        (stmt.target.id, stmt.annotation, stmt.value)
        for stmt in target_stmts
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )
    candidates.extend(
        (target.id, None, stmt.value)
        for stmt in target_stmts
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Name)
    )
    if is_class:
        candidates.extend(
            (node.target.attr, node.annotation, node.value)
            for node in owned_nodes
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and isinstance(node.target.value, ast.Name)
            and node.target.value.id == "self"
        )
        candidates.extend(
            (target.attr, None, node.value)
            for node in owned_nodes
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        )
    matched: list[str] = []
    for name, ann, val in candidates:
        if not LIFECYCLE_BOOL_PATTERN.match(name):
            continue
        is_bool_ann = ann is not None and (
            (isinstance(ann, ast.Name) and ann.id == "bool")
            or (isinstance(ann, ast.Constant) and ann.value == "bool")
        )
        is_bool_val = ann is None and val is not None and isinstance(val, ast.Constant) and isinstance(val.value, bool)
        is_bool_prefix = ann is None and val is None and name.startswith(("is_", "has_", "was_"))
        if is_bool_ann or is_bool_val or is_bool_prefix:
            matched.append(name)
    return list(dict.fromkeys(matched))


def scan_body_violations(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    all_params = fn_node.args.posonlyargs + fn_node.args.args + fn_node.args.kwonlyargs
    violations.extend(
        Violation(file_path, fn_node.lineno, "LLM-03", f"Vague or single-char parameter name '{param.arg}'.")
        for param in all_params
        if param.arg in VAGUE_NOUNS or (len(param.arg) == 1 and param.arg != "_")
    )
    comp_target_ids = {
        id(target_node)
        for node in ast.walk(fn_node)
        if isinstance(node, ast.comprehension)
        for target_node in ast.walk(node.target)
        if isinstance(target_node, ast.Name)
    }
    for node in ast.walk(fn_node):
        if node is fn_node:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.append(
                Violation(file_path, node.lineno, "CANON-05", f"Nested function '{node.name}' is banned -> lift to module top level as an L2/L3 helper.")
            )
            continue
        if isinstance(node, ast.IfExp) and (isinstance(node.body, ast.IfExp) or isinstance(node.orelse, ast.IfExp)):
            violations.append(
                Violation(file_path, node.lineno, "CANON-05", "Nested inline conditional expression is banned -> replace with flat if/return guard clauses.")
            )
            continue
        is_single_char = isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and len(node.id) == 1 and node.id != "_"
        is_vague_var = isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in VAGUE_NOUNS and id(node) not in comp_target_ids
        if is_single_char or is_vague_var:
            violations.append(
                Violation(file_path, node.lineno, "LLM-03", f"Vague or single-char variable name '{node.id}' -> use a descriptive domain noun.")
            )
            continue
        if not isinstance(node, ast.ExceptHandler):
            continue
        is_broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"})
        only_stmt = node.body[0] if len(node.body) == 1 else None
        is_empty_return = isinstance(only_stmt, ast.Return) and (
            only_stmt.value is None
            or (isinstance(only_stmt.value, ast.Constant) and only_stmt.value.value in {None, ""})
            or (isinstance(only_stmt.value, (ast.List, ast.Dict, ast.Tuple)) and not getattr(only_stmt.value, "elts", getattr(only_stmt.value, "keys", [])))
        )
        if is_broad and (isinstance(only_stmt, ast.Pass) or is_empty_return):
            violations.append(
                Violation(file_path, node.lineno, "LLM-02", "Broad exception handler silently swallows errors -> catch a specific exception or propagate to L0.")
            )
    return violations


def scan_test_runner_branching(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Constant) and node.value in TEST_ENV_MARKERS:
            violations.append(
                Violation(file_path, node.lineno, "LLM-07", f"Test-runner env check '{node.value}' in production code -> inject test behavior via explicit L0 parameters.")
            )
        elif (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and node.left.value in TEST_MODULE_MARKERS
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            and any(
                isinstance(comp, ast.Attribute) and comp.attr == "modules"
                for comp in node.comparators
            )
        ):
            violations.append(
                Violation(file_path, node.lineno, "LLM-07", f"Test-runner module check '{node.left.value}' in production code -> inject test behavior via explicit L0 parameters.")
            )
    return violations


def topological_sort_functions(
    top_functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    call_graph: dict[str, set[str]],
) -> list[int] | None:
    name_to_idx = {fn_node.name: idx for idx, fn_node in enumerate(top_functions)}
    in_degree = [0] * len(top_functions)
    edges: list[list[int]] = [[] for _ in top_functions]
    pairs = [
        (caller_idx, name_to_idx[callee_name])
        for caller_idx, fn_node in enumerate(top_functions)
        for callee_name in sorted(call_graph.get(fn_node.name, set()))
        if name_to_idx[callee_name] != caller_idx
    ]
    for caller_idx, callee_idx in pairs:
        edges[caller_idx].append(callee_idx)
        in_degree[callee_idx] += 1
    ready = [idx for idx, deg in enumerate(in_degree) if deg == 0]
    heapq.heapify(ready)
    ordered: list[int] = []
    while ready:
        current = heapq.heappop(ready)
        ordered.append(current)
        for neighbor in edges[current]:
            in_degree[neighbor] -= 1
        newly_ready = [neighbor for neighbor in edges[current] if in_degree[neighbor] == 0]
        for neighbor in newly_ready:
            heapq.heappush(ready, neighbor)
    return ordered if len(ordered) == len(top_functions) else None


def rebuild_reordered_source(
    source: str,
    top_functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    ordered_indices: list[int],
) -> str | None:
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    prev_end = 0
    for fn_node in top_functions:
        start_idx = fn_node.lineno - 1
        while (
            start_idx > prev_end
            and lines[start_idx - 1].lstrip().startswith("#")
            and not HEADER_COMMENT_PATTERN.match(lines[start_idx - 1].lstrip())
        ):
            start_idx -= 1
        if prev_end > 0 and any(line_text.strip() for line_text in lines[prev_end:start_idx]):
            return None
        end_idx = fn_node.end_lineno or fn_node.lineno
        spans.append((start_idx, end_idx))
        prev_end = end_idx
    prefix = "".join(lines[: spans[0][0]])
    suffix = "".join(lines[spans[-1][1] :])
    blocks = ["".join(lines[spans[idx][0] : spans[idx][1]]).rstrip("\r\n") for idx in ordered_indices]
    trailing_newline = "\n" if source.endswith("\n") else ""
    if suffix:
        sep = "\n" if suffix.startswith(("\n", "\r\n")) else "\n\n"
        return prefix + "\n\n".join(blocks) + sep + suffix
    return prefix + "\n\n".join(blocks) + trailing_newline


def collect_contiguous_header_lines(
    raw_lines: list[str],
    outer_start: int,
    *,
    prev_end_line: int,
    comment_lines: set[int],
) -> set[int]:
    collected: set[int] = set()
    line_no = outer_start - 1
    while line_no > prev_end_line and line_no >= 1:
        trimmed = raw_lines[line_no - 1].lstrip()
        if line_no not in comment_lines or HEADER_COMMENT_PATTERN.match(trimmed):
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


if __name__ == "__main__":
    raise SystemExit(main())
