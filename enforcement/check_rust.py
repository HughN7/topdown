"""Lightweight zero-dependency heuristic Rust structural checker for topdown.

Intended as a complement to Clippy (see languages/rust.md), not a full Rust parser.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FUNCTION_LINES = 80
MAX_NESTING_DEPTH = 2
IGNORE_PATTERN = re.compile(r"//\s*topdown-ignore(?:\((.*)\))?")
REASON_PATTERN = re.compile(r"""reason\s*=\s*(?:"([^"]+)"|'([^']+)')""")
RULE_ID_PATTERN = re.compile(r"\b(?:CANON|LLM)-\d{2}\b")
FN_DECL_PATTERN = re.compile(
    r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?(?:extern\s+(?:"[^"]*"\s+)?)?fn\s+[A-Za-z_]\w*'
)
NESTED_FN_PATTERN = re.compile(r"\bfn\s+[A-Za-z_]\w*")
CONTROL_OPEN_PATTERN = re.compile(r"\b(?:if|for|while|loop|match)\b[^{]*\{|\belse\s*\{")
UNWRAP_PATTERN = re.compile(r"\.(?:unwrap|unwrap_or_default)\s*\(")
CFG_TEST_PATTERN = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
TEST_PATH_PATTERN = re.compile(r"(?:^|[\\/])tests[\\/]|_test\.rs$", re.IGNORECASE)
SKIP_DIRS = frozenset({".git", "target", ".venv", "node_modules"})
CHAR_LITERAL_PATTERN = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.)|[^'\\])'")
RAW_STRING_START_PATTERN = re.compile(r'(?:[bc])?r(#*)"')


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
class FnSpan:
    start_line: int
    body_start_line: int
    end_line: int


@dataclass(frozen=True)
class CliOptions:
    paths: tuple[str, ...]
    as_json: bool


def main() -> int:
    options = parse_cli_args(sys.argv[1:])
    if options is None:
        print("Usage: python3 check_rust.py [--json] <path> [...]", file=sys.stderr)
        return 2
    target_files, error_msg = collect_rust_files(options.paths)
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
    emit_violations(all_violations, as_json=options.as_json)
    return 1 if all_violations else 0


def check_source(source: str, file_path: str) -> list[Violation]:
    cleaned, comments = lex_rust_source(source)
    directives, ignore_violations = parse_ignore_directives(comments, file_path)
    cleaned_lines = cleaned.splitlines()
    masked_lines = mask_cfg_test_lines(cleaned_lines)
    fn_spans = extract_fn_spans(masked_lines)
    raw_violations = (
        check_fn_spans(fn_spans, masked_lines, file_path)
        + check_unwrap_calls(masked_lines, file_path)
        + check_inline_cfg_test(cleaned_lines, fn_spans, file_path)
    )
    kept = filter_suppressed(raw_violations, fn_spans, directives)
    return sorted(ignore_violations + kept, key=lambda entry: (entry.line, entry.rule))


def check_fn_spans(fn_spans: list[FnSpan], lines: list[str], file_path: str) -> list[Violation]:
    violations: list[Violation] = []
    for span in fn_spans:
        line_count = span.end_line - span.start_line + 1
        if line_count > MAX_FUNCTION_LINES:
            violations.append(
                Violation(
                    file_path,
                    span.start_line,
                    "CANON-01",
                    f"Function starting at line {span.start_line} is {line_count} lines (max {MAX_FUNCTION_LINES}) -> extract a cohesive L1/L2 pure helper.",
                )
            )
        max_nesting = measure_control_nesting(lines, span.body_start_line, span.end_line)
        if max_nesting > MAX_NESTING_DEPTH:
            violations.append(
                Violation(
                    file_path,
                    span.start_line,
                    "CANON-05",
                    f"Control-flow nesting depth {max_nesting} (max {MAX_NESTING_DEPTH}) -> use early return / '?' propagation or extract an L2 helper.",
                )
            )
        nested_lines = [
            line_no
            for line_no in range(span.body_start_line + 1, span.end_line + 1)
            if NESTED_FN_PATTERN.search(lines[line_no - 1])
        ]
        violations.extend(
            Violation(
                file_path,
                line_no,
                "CANON-05",
                "Nested fn declaration inside a function is banned -> lift to module top level.",
            )
            for line_no in nested_lines
        )
    return violations


def check_unwrap_calls(lines: list[str], file_path: str) -> list[Violation]:
    if TEST_PATH_PATTERN.search(file_path):
        return []
    return [
        Violation(
            file_path,
            idx + 1,
            "LLM-02",
            "Avoid .unwrap() / .unwrap_or_default() in non-test code -> propagate with '?' or match explicitly.",
        )
        for idx, line_text in enumerate(lines)
        if UNWRAP_PATTERN.search(line_text)
    ]


def check_inline_cfg_test(
    cleaned_lines: list[str],
    fn_spans: list[FnSpan],
    file_path: str,
) -> list[Violation]:
    if TEST_PATH_PATTERN.search(file_path):
        return []
    return [
        Violation(
            file_path,
            line_no,
            "LLM-07",
            "Inline #[cfg(test)] inside production function -> inject test behavior via explicit L0 parameters.",
        )
        for span in fn_spans
        for line_no in range(span.body_start_line + 1, span.end_line + 1)
        if CFG_TEST_PATTERN.search(cleaned_lines[line_no - 1])
    ]


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


def collect_rust_files(raw_paths: tuple[str, ...]) -> tuple[list[Path], str | None]:
    collected: list[Path] = []
    for raw_entry in raw_paths:
        target = Path(raw_entry)
        if not target.exists():
            return [], f"Path not found: {raw_entry}"
        if target.is_dir():
            collected.extend(
                path
                for path in sorted(target.rglob("*.rs"))
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
    comments: list[tuple[int, str]],
    file_path: str,
) -> tuple[dict[int, IgnoreDirective], list[Violation]]:
    directives: dict[int, IgnoreDirective] = {}
    violations: list[Violation] = []
    for line_no, comment_text in comments:
        match = IGNORE_PATTERN.search(comment_text)
        if not match:
            continue
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


def lex_rust_source(source: str) -> tuple[str, list[tuple[int, str]]]:
    chars: list[str] = []
    comments: list[tuple[int, str]] = []
    idx = 0
    line_no = 1
    length = len(source)
    while idx < length:
        ch = source[idx]
        nxt = source[idx + 1] if idx + 1 < length else ""
        if ch == "/" and nxt == "/":
            end_idx = source.find("\n", idx)
            end_idx = length if end_idx == -1 else end_idx
            comments.append((line_no, source[idx:end_idx]))
            idx = end_idx
        elif ch == "/" and nxt == "*":
            end_idx = find_nested_block_comment_end(source, idx + 2)
            newlines = source[idx:end_idx].count("\n")
            chars.append("\n" * newlines)
            line_no += newlines
            idx = end_idx
        elif (idx == 0 or not source[idx - 1].isalnum()) and (raw_match := RAW_STRING_START_PATTERN.match(source, idx)):
            end_idx = source.find('"' + raw_match.group(1), raw_match.end())
            step_end = length if end_idx == -1 else end_idx + 1 + len(raw_match.group(1))
            newlines = source[idx:step_end].count("\n")
            chars.append(" " + ("\n" * newlines))
            line_no += newlines
            idx = step_end
        elif ch == "'" and (char_match := CHAR_LITERAL_PATTERN.match(source, idx)):
            chars.append(" ")
            idx = char_match.end()
        elif ch == '"':
            str_match = re.match(r'"(?:\\.|[^"\\])*"', source[idx:], re.DOTALL)
            step_end = idx + str_match.end() if str_match else length
            newlines = source[idx:step_end].count("\n")
            chars.append(" " + ("\n" * newlines))
            line_no += newlines
            idx = step_end
        else:
            line_no += 1 if ch == "\n" else 0
            chars.append(ch)
            idx += 1
    return "".join(chars), comments


def find_nested_block_comment_end(source: str, start_idx: int) -> int:
    depth = 1
    probe = start_idx
    length = len(source)
    while probe < length and depth > 0:
        pair = source[probe : probe + 2]
        if pair == "/*":
            depth += 1
            probe += 2
        elif pair == "*/":
            depth -= 1
            probe += 2
        else:
            probe += 1
    return probe


def mask_cfg_test_lines(lines: list[str]) -> list[str]:
    masked: list[str] = []
    pending_test_attr = False
    in_test_block = False
    test_brace_depth = 0
    file_brace_depth = 0
    for line_text in lines:
        opens = line_text.count("{")
        closes = line_text.count("}")
        has_cfg_test = file_brace_depth == 0 and bool(CFG_TEST_PATTERN.search(line_text))
        if has_cfg_test and opens > 0:
            test_brace_depth = opens - closes
            in_test_block = test_brace_depth > 0
            masked.append("")
            continue
        if has_cfg_test and line_text.strip().endswith(";"):
            masked.append("")
            continue
        if has_cfg_test:
            pending_test_attr = True
            masked.append("")
            continue
        if pending_test_attr and opens > 0:
            pending_test_attr = False
            test_brace_depth = opens - closes
            in_test_block = test_brace_depth > 0
            masked.append("")
            continue
        if in_test_block:
            test_brace_depth += opens - closes
            in_test_block = test_brace_depth > 0
            masked.append("")
            continue
        if pending_test_attr and line_text.strip() and not line_text.strip().startswith("#["):
            pending_test_attr = not line_text.strip().endswith(";")
            masked.append("")
            continue
        file_brace_depth += opens - closes
        masked.append(line_text)
    return masked


def extract_fn_spans(lines: list[str]) -> list[FnSpan]:
    spans: list[FnSpan] = []
    brace_depth = 0
    pending_start = -1
    active_start = -1
    active_body_start = -1
    active_base = 0
    for idx, line_text in enumerate(lines):
        trimmed = line_text.strip()
        opens = line_text.count("{")
        closes = line_text.count("}")
        if active_start == -1 and pending_start == -1 and FN_DECL_PATTERN.search(line_text):
            pending_start = idx + 1
        if pending_start != -1 and opens > 0:
            active_start = pending_start
            active_body_start = idx + 1
            active_base = brace_depth
            pending_start = -1
        elif pending_start != -1 and trimmed.endswith(";"):
            pending_start = -1
        brace_depth += opens - closes
        if active_start != -1 and brace_depth <= active_base:
            spans.append(FnSpan(active_start, active_body_start, idx + 1))
            active_start = -1
    return spans


def measure_control_nesting(lines: list[str], body_start_line: int, end_line: int) -> int:
    control_stack: list[int] = []
    brace_depth = 0
    max_nesting = 0
    in_let_stmt = False
    for line_no in range(body_start_line, end_line + 1):
        line_text = lines[line_no - 1]
        trimmed = line_text.strip()
        leading_closes = len(trimmed) - len(trimmed.lstrip("}"))
        if re.search(r"^\s*let\b", line_text):
            in_let_stmt = True
        is_let_else = in_let_stmt and bool(re.search(r"\belse\s*\{", line_text))
        is_match_guard = "=>" in line_text
        opens = line_text.count("{")
        closes = line_text.count("}")
        brace_depth -= leading_closes
        while control_stack and brace_depth <= control_stack[-1]:
            control_stack.pop()
        if line_no > body_start_line and not is_let_else and not is_match_guard and CONTROL_OPEN_PATTERN.search(line_text):
            control_stack.append(brace_depth)
            max_nesting = max(max_nesting, len(control_stack))
        brace_depth += opens - (closes - leading_closes)
        while control_stack and brace_depth <= control_stack[-1]:
            control_stack.pop()
        if ";" in line_text:
            in_let_stmt = False
    return max_nesting


def filter_suppressed(
    violations: list[Violation],
    fn_spans: list[FnSpan],
    directives: dict[int, IgnoreDirective],
) -> list[Violation]:
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for span in fn_spans
            if span.start_line <= entry.line <= span.end_line
            for line_num in (span.start_line, span.start_line - 1)
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


if __name__ == "__main__":
    raise SystemExit(main())
