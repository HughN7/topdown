"""Reference zero-dependency Python AST checker for the topdown architecture framework."""

from __future__ import annotations

import ast
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
IGNORE_PATTERN = re.compile(r"#\s*topdown-ignore(?:\((.*)\))?")
REASON_PATTERN = re.compile(r"""reason\s*=\s*(?:"([^"]+)"|'([^']+)')""")
RULE_ID_PATTERN = re.compile(r"\b(?:CANON|LLM)-\d{2}\b")
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


@dataclass(frozen=True)
class CliOptions:
    paths: tuple[str, ...]
    as_json: bool


def main() -> int:
    options = parse_cli_args(sys.argv[1:])
    if options is None:
        print("Usage: python3 check_python.py [--json] <path> [...]", file=sys.stderr)
        return 2
    target_files, error_msg = collect_python_files(options.paths)
    if error_msg is not None:
        print(error_msg, file=sys.stderr)
        return 2
    all_violations: list[Violation] = []
    for file_path in target_files:
        try:
            source_text = file_path.read_text(encoding="utf-8")
            all_violations.extend(check_source(source_text, str(file_path)))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(f"Error reading {file_path}: {exc}", file=sys.stderr)
            return 2
        except SyntaxError as exc:
            print(f"SyntaxError in {file_path}:{exc.lineno}: {exc.msg}", file=sys.stderr)
            return 2
    emit_violations(all_violations, as_json=options.as_json)
    return 1 if all_violations else 0


def check_source(source: str, file_path: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    directives, ignore_violations = parse_ignore_directives(source, file_path)
    top_functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    class_methods = [
        member
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    checked_functions = top_functions + class_methods
    raw_violations: list[Violation] = []
    raw_violations.extend(check_order_and_depth(top_functions, file_path))
    for fn_node in checked_functions:
        raw_violations.extend(check_function_rules(fn_node, file_path))
    filtered = filter_suppressed(raw_violations, checked_functions, directives)
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
                message=f"'{fn_node.name}' calls '{callee}' defined above it (line {fn_lines[callee]}).",
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
                    message=f"'{fn_node.name}' exceeds L0->L1->L2 + L3 leaf call-depth ceiling (depth {depth}).",
                )
            )
    return violations


def check_function_rules(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    line_count = (fn_node.end_lineno or fn_node.lineno) - fn_node.lineno + 1
    if line_count > MAX_FUNCTION_LINES:
        violations.append(
            Violation(file_path, fn_node.lineno, "CANON-01", f"'{fn_node.name}' is {line_count} lines (max {MAX_FUNCTION_LINES}).")
        )
    pos_count, total_count = count_parameters(fn_node)
    if pos_count > MAX_POSITIONAL_ARGS:
        violations.append(
            Violation(file_path, fn_node.lineno, "CANON-03", f"'{fn_node.name}' has {pos_count} positional args (max {MAX_POSITIONAL_ARGS}).")
        )
    if total_count > MAX_TOTAL_ARGS:
        violations.append(
            Violation(file_path, fn_node.lineno, "CANON-03", f"'{fn_node.name}' has {total_count} total args (max {MAX_TOTAL_ARGS}).")
        )
    nesting = measure_max_nesting(fn_node)
    if nesting > MAX_NESTING_DEPTH:
        violations.append(
            Violation(file_path, fn_node.lineno, "CANON-05", f"'{fn_node.name}' has nesting depth {nesting} (max {MAX_NESTING_DEPTH}).")
        )
    violations.extend(scan_body_violations(fn_node, file_path))
    return violations


def parse_cli_args(raw_args: list[str]) -> CliOptions | None:
    as_json = False
    paths: list[str] = []
    for token in raw_args:
        if token == "--json":
            as_json = True
            continue
        if token.startswith("-"):
            return None
        paths.append(token)
    if not paths:
        return None
    return CliOptions(paths=tuple(paths), as_json=as_json)


def collect_python_files(raw_paths: tuple[str, ...]) -> tuple[list[Path], str | None]:
    collected: list[Path] = []
    for raw_entry in raw_paths:
        target = Path(raw_entry)
        if not target.exists():
            return [], f"Path not found: {raw_entry}"
        if target.is_dir():
            collected.extend(sorted(target.rglob("*.py")))
        else:
            collected.append(target)
    return collected, None


def emit_violations(violations: list[Violation], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(entry) for entry in violations], indent=2))
        return
    for entry in violations:
        print(f"{entry.path}:{entry.line}: [{entry.rule}] {entry.message}")


def parse_ignore_directives(source: str, file_path: str) -> tuple[dict[int, IgnoreDirective], list[Violation]]:
    directives: dict[int, IgnoreDirective] = {}
    violations: list[Violation] = []
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type != tokenize.COMMENT:
            continue
        match = IGNORE_PATTERN.search(tok.string)
        if not match:
            continue
        line_no = tok.start[0]
        inner = match.group(1) or ""
        rules = frozenset(RULE_ID_PATTERN.findall(inner))
        has_reason = REASON_PATTERN.search(inner) is not None
        directives[line_no] = IgnoreDirective(line=line_no, rules=rules, has_reason=has_reason)
        if not has_reason:
            violations.append(
                Violation(file_path, line_no, "CANON-08", 'topdown-ignore requires a non-empty reason="...".')
            )
    return directives, violations


def filter_suppressed(
    violations: list[Violation],
    checked_functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    directives: dict[int, IgnoreDirective],
) -> list[Violation]:
    fn_spans = [(fn_node.lineno, fn_node.end_lineno or fn_node.lineno) for fn_node in checked_functions]
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for start_line, end_line in fn_spans
            if start_line <= entry.line <= end_line
            for line_num in (start_line, start_line - 1)
        }
        candidate_lines = {entry.line, entry.line - 1} | span_lines
        is_suppressed = any(
            (directive := directives.get(check_line)) is not None
            and directive.has_reason
            and (not directive.rules or entry.rule in directive.rules)
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


def scan_body_violations(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> list[Violation]:
    violations: list[Violation] = []
    all_params = fn_node.args.posonlyargs + fn_node.args.args + fn_node.args.kwonlyargs
    violations.extend(
        Violation(file_path, fn_node.lineno, "LLM-03", f"Vague parameter name '{param.arg}'.")
        for param in all_params
        if param.arg in VAGUE_NOUNS
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
            violations.append(Violation(file_path, node.lineno, "CANON-05", f"Nested function '{node.name}' is banned."))
            continue
        if isinstance(node, ast.IfExp) and (isinstance(node.body, ast.IfExp) or isinstance(node.orelse, ast.IfExp)):
            violations.append(Violation(file_path, node.lineno, "CANON-05", "Nested inline conditional expression is banned."))
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in VAGUE_NOUNS and id(node) not in comp_target_ids:
            violations.append(Violation(file_path, node.lineno, "LLM-03", f"Vague variable name '{node.id}'."))
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
            violations.append(Violation(file_path, node.lineno, "LLM-02", "Broad exception handler silently swallows errors."))
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
