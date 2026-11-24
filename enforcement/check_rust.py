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
UNWRAP_PATTERN = re.compile(r"\.(?:unwrap|unwrap_or_default)\s*\(")
CFG_TEST_PATTERN = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]")
TEST_PATH_PATTERN = re.compile(r"(?:^|[\\/])tests[\\/]|_test\.rs$", re.IGNORECASE)
SKIP_DIRS = frozenset({".git", "target", ".venv", "node_modules"})
CHAR_LITERAL_PATTERN = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.)|[^'\\])'")
RAW_STRING_START_PATTERN = re.compile(r'(?:[bc])?r(#*)"')
ATTR_NOISE_PATTERN = re.compile(
    rf'(?<![A-Za-z0-9_])(?:[bc])?r(#*)"[\s\S]*?"\1|"(?:\\.|[^"\\])*"|{CHAR_LITERAL_PATTERN.pattern}|//.*'
)
ATTR_PAIR_MAP = {"]": "[", ")": "(", "}": "{"}
FN_EVENT_PATTERN = re.compile(r"[{};\[\]()]")
RUST_CONTROL_WORDS = frozenset({"if", "for", "while", "loop", "match"})
RUST_NESTING_TOKENS = (
    r"=>|//.*"
    r"|(?<![=!<>+\-*/%&|^])=(?![=>])"
    r"|(?<!r#)(?<!::)\b(?:let|if|else|for(?!\s*<)|while|loop|match|in|impl)\b"
    r"|(?:(?<![A-Za-z0-9_])(?:[A-Za-z_]\w*::)*(?:[A-Z][A-Za-z0-9_]*[a-z]\w*|Self)(?:::<[^>{};]*>)?|(?<![=!])!)\s*\{"
    r"|[{}\(\)\[\];]"
)
RUST_NESTING_TOKEN_PATTERN = re.compile(RUST_NESTING_TOKENS)
LIFECYCLE_BOOL_DECL_PATTERN = re.compile(
    r"\b((?:is|has|was)_(?:loading|pending|fetching|submitting|saving|retrying|error|errored|failed|rejected|success|succeeded|loaded|ready|done|completed|idle)|(?:loading|pending|fetching|submitting|errored|failed|succeeded))\s*(?::\s*bool\b|=\s*(?:true|false)\b)"
)
STATE_MACHINE_TOKEN_PATTERN = re.compile(f"{RUST_NESTING_TOKENS}|{LIFECYCLE_BOOL_DECL_PATTERN.pattern}")


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
    body_start_col: int = 0
    end_col: int | None = None


@dataclass
class FnScanState:
    spans: list[FnSpan]
    brace_depth: int = 0
    bracket_depth: int = 0
    paren_depth: int = 0
    pending_start: int = -1
    active_start: int = -1
    active_body_start: int = -1
    active_body_col: int = 0
    active_base: int = 0


@dataclass
class StateMachineScan:
    scope_stack: list[list[object]]
    closed_scopes: list[tuple[int, int, int, list[str]]]
    pending_flags: list[str]
    pending_controls: list[list[int | bool]]
    paren_depth: int = 0
    bracket_depth: int = 0
    condition_brace_depth: int = 0
    pending_start: int = -1
    in_impl_header: bool = False
    last_closed_end: int = 0


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
    cleaned, comments, comment_lines = lex_rust_source(source)
    directives, ignore_violations = parse_ignore_directives(comments, file_path)
    cleaned_lines = cleaned.split("\n")
    masked_lines = mask_cfg_test_lines(cleaned_lines)
    fn_spans = extract_fn_spans(masked_lines)
    raw_violations = (
        check_fn_spans(fn_spans, masked_lines, file_path)
        + check_implicit_state_machines(masked_lines, file_path, directives, comment_lines=comment_lines)
        + check_unwrap_calls(masked_lines, file_path)
        + check_inline_cfg_test(cleaned_lines, fn_spans, file_path)
    )
    kept = filter_suppressed(
        raw_violations,
        fn_spans,
        directives,
        cleaned_lines=cleaned_lines,
        comment_lines=comment_lines,
    )
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
        max_nesting = measure_control_nesting(
            lines,
            span.body_start_line,
            span.end_line,
            body_start_col=span.body_start_col,
            end_col=span.end_col,
        )
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
            if NESTED_FN_PATTERN.search(
                lines[line_no - 1][: span.end_col] if line_no == span.end_line else lines[line_no - 1]
            )
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


def check_implicit_state_machines(
    lines: list[str],
    file_path: str,
    directives: dict[int, IgnoreDirective],
    *,
    comment_lines: set[int],
) -> list[Violation]:
    violations: list[Violation] = []
    state = StateMachineScan(
        scope_stack=[],
        closed_scopes=[],
        pending_flags=[],
        pending_controls=[],
    )
    for idx, line_text in enumerate(lines):
        advance_state_machine_line(line_text, idx + 1, state)
    for start_line, open_line, prev_end_line, flags in state.closed_scopes:
        unique_flags = list(dict.fromkeys(flags))
        leading = collect_contiguous_header_lines(
            lines, start_line, prev_end_line=prev_end_line, comment_lines=comment_lines
        )
        header_lines = leading | set(range(start_line, open_line + 1))
        is_ignored = any(
            (directive := directives.get(check_line)) is not None
            and directive.has_reason
            and (directive.is_wildcard or "CANON-03" in directive.rules)
            for check_line in header_lines
        )
        if len(unique_flags) >= 2 and not is_ignored:
            violations.append(
                Violation(
                    file_path,
                    open_line,
                    "CANON-03",
                    f"Implicit state machine ({', '.join(unique_flags)}) -> replace parallel lifecycle booleans with an explicit Rust enum.",
                )
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
        if CFG_TEST_PATTERN.search(
            cleaned_lines[line_no - 1][: span.end_col] if line_no == span.end_line else cleaned_lines[line_no - 1]
        )
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


def lex_rust_source(source: str) -> tuple[str, list[tuple[int, str]], set[int]]:
    chars: list[str] = []
    comments: list[tuple[int, str]] = []
    raw_comment_lines: set[int] = set()
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
            raw_comment_lines.add(line_no)
            idx = end_idx
        elif ch == "/" and nxt == "*":
            end_idx = find_nested_block_comment_end(source, idx + 2)
            newlines = source[idx:end_idx].count("\n")
            raw_comment_lines.update(range(line_no, line_no + newlines + 1))
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
    cleaned = "".join(chars)
    cleaned_lines = cleaned.split("\n")
    comment_lines = {
        ln for ln in raw_comment_lines if 1 <= ln <= len(cleaned_lines) and not cleaned_lines[ln - 1].strip()
    }
    return cleaned, comments, comment_lines


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
        cfg_match = CFG_TEST_PATTERN.search(line_text) if (not in_test_block and not pending_test_attr) else None
        prefix = line_text[: cfg_match.start()] if cfg_match else ""
        suffix = line_text[cfg_match.start() :] if cfg_match else ""
        depth_before_cfg = file_brace_depth + prefix.count("{") - prefix.count("}")
        has_cfg_test = cfg_match is not None and depth_before_cfg == 0
        if has_cfg_test and suffix.count("{") > 0:
            file_brace_depth = 0
            test_brace_depth = suffix.count("{") - suffix.count("}")
            in_test_block = test_brace_depth > 0
            masked.append(prefix.rstrip())
            continue
        if has_cfg_test and suffix.strip().endswith(";"):
            file_brace_depth = 0
            masked.append(prefix.rstrip())
            continue
        if has_cfg_test:
            file_brace_depth = 0
            pending_test_attr = True
            masked.append(prefix.rstrip())
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
    state = FnScanState(spans=[])
    for idx, line_text in enumerate(lines):
        advance_fn_span_line(line_text, idx + 1, state)
    return state.spans


def advance_fn_span_line(line_text: str, line_no: int, state: FnScanState) -> None:
    seg_start = 0
    for match in FN_EVENT_PATTERN.finditer(line_text):
        pos = match.start()
        ch = match.group(0)
        segment = line_text[seg_start:pos].lstrip("}; \t")
        if state.active_start == -1 and state.pending_start == -1 and FN_DECL_PATTERN.search(segment):
            state.pending_start = line_no
        if ch == "(":
            state.paren_depth += 1
        elif ch == ")":
            state.paren_depth = max(0, state.paren_depth - 1)
        elif ch == "[":
            state.bracket_depth += 1
        elif ch == "]":
            state.bracket_depth = max(0, state.bracket_depth - 1)
            seg_start = pos + 1 if (state.bracket_depth == 0 and state.active_start == -1 and state.pending_start == -1) else seg_start
        elif ch == ";":
            state.pending_start = -1 if (state.bracket_depth == 0 and state.paren_depth == 0) else state.pending_start
            seg_start = pos + 1 if (state.active_start == -1 and state.pending_start == -1) else seg_start
        elif ch == "{" and state.pending_start != -1:
            state.active_start = state.pending_start
            state.active_body_start = line_no
            state.active_body_col = pos
            state.active_base = state.brace_depth
            state.pending_start = -1
        if ch == "{":
            state.brace_depth += 1
            seg_start = pos + 1
        if ch == "}":
            state.brace_depth = max(0, state.brace_depth - 1)
            seg_start = pos + 1
        if ch == "}" and state.active_start != -1 and state.brace_depth <= state.active_base:
            state.spans.append(
                FnSpan(
                    state.active_start,
                    state.active_body_start,
                    line_no,
                    body_start_col=state.active_body_col,
                    end_col=pos + 1,
                )
            )
            state.active_start = -1
    tail = line_text[seg_start:].lstrip("}; \t")
    if state.active_start == -1 and state.pending_start == -1 and FN_DECL_PATTERN.search(tail):
        state.pending_start = line_no


def advance_state_machine_line(line_text: str, line_no: int, state: StateMachineScan) -> None:
    cursor = 0
    for match in STATE_MACHINE_TOKEN_PATTERN.finditer(line_text):
        pos, end_pos = match.start(), match.end()
        tok = match.group(0)
        if state.pending_start == -1 and line_text[cursor:pos].strip():
            state.pending_start = line_no
        cursor = end_pos
        if tok.startswith("//"):
            continue
        if tok not in {"}", ";"} and state.pending_start == -1 and tok.strip():
            state.pending_start = line_no
        if tok in "()[]":
            state.paren_depth = max(0, state.paren_depth + {"(": 1, ")": -1}.get(tok, 0))
            state.bracket_depth = max(0, state.bracket_depth + {"[": 1, "]": -1}.get(tok, 0))
            continue
        top = state.pending_controls[-1] if state.pending_controls else None
        is_top_depth = top is not None and top[:3] == [state.paren_depth, state.bracket_depth, state.condition_brace_depth]
        if tok == "=>":
            state.pending_controls = state.pending_controls[:-1] if is_top_depth else state.pending_controls
            continue
        if tok in {"=", "in", "let"} and is_top_depth:
            top[4 if tok == "in" else 3] = tok == "let"
            continue
        if tok == ";" and (state.paren_depth > 0 or state.bracket_depth > 0 or state.condition_brace_depth > 0):
            continue
        if tok == ";" and state.scope_stack:
            state.scope_stack[-1][2].extend(state.pending_flags)
        if tok == ";":
            state.pending_flags.clear()
            state.pending_start = -1
            state.in_impl_header = False
            state.pending_controls = state.pending_controls[:-1] if is_top_depth else state.pending_controls
            continue
        if tok == "}" and state.condition_brace_depth > 0:
            state.condition_brace_depth -= 1
            continue
        if tok == "}" and state.scope_stack and state.paren_depth == 0 and state.pending_flags:
            state.scope_stack[-1][2].extend(state.pending_flags)
            state.pending_flags.clear()
        if tok == "}" and state.scope_stack:
            start_line, open_line, flags, ctrl_flag, prev_end = state.scope_stack.pop()
            target_flags = state.scope_stack[-1][2] if (ctrl_flag and state.scope_stack) else flags
            target_flags.extend(flags if target_flags is not flags else [])
            state.closed_scopes.append((start_line, open_line, prev_end, [] if ctrl_flag else flags))
            state.last_closed_end = line_no
        if tok == "}":
            state.pending_start = -1
            continue
        is_ctrl_open = tok == "{" and is_top_depth and not top[3] and not top[4]
        if tok.endswith("{") and top is not None and not is_ctrl_open:
            state.condition_brace_depth += 1
            state.in_impl_header = False
            continue
        if tok.endswith("{"):
            scope_start = state.pending_start if state.pending_start != -1 else line_no
            state.scope_stack.append([scope_start, line_no, list(state.pending_flags), is_ctrl_open, state.last_closed_end])
            state.pending_start = -1
            state.pending_flags.clear()
            state.in_impl_header = False
            state.pending_controls = state.pending_controls[:-1] if is_ctrl_open else state.pending_controls
            continue
        if tok == "impl":
            state.in_impl_header = True
            continue
        is_ctrl_kw = tok == "else" or (tok in RUST_CONTROL_WORDS and not (tok == "for" and state.in_impl_header))
        if is_ctrl_kw and not is_top_depth:
            state.pending_controls.append([state.paren_depth, state.bracket_depth, state.condition_brace_depth, False, tok == "for"])
            continue
        bool_match = LIFECYCLE_BOOL_DECL_PATTERN.match(tok)
        if bool_match and state.scope_stack and state.paren_depth == 0:
            state.scope_stack[-1][2].append(bool_match.group(1))
        elif bool_match:
            state.pending_flags.append(bool_match.group(1))
    if state.pending_start == -1 and line_text[cursor:].strip():
        state.pending_start = line_no


def measure_control_nesting(
    lines: list[str],
    body_start_line: int,
    end_line: int,
    *,
    body_start_col: int = 0,
    end_col: int | None = None,
) -> int:
    body_text = "\n".join(
        lines[ln - 1][(body_start_col if ln == body_start_line else 0) : (end_col if ln == end_line else None)]
        for ln in range(body_start_line, end_line + 1)
    )
    control_stack: list[int] = []
    pending_controls: list[list[int | bool]] = []
    brace_depth = paren_depth = bracket_depth = condition_brace_depth = max_nesting = 0
    let_depth: int | None = None
    in_impl_header = False
    for match in RUST_NESTING_TOKEN_PATTERN.finditer(body_text):
        tok = match.group(0)
        if tok.startswith("//") or (brace_depth == 0 and tok != "{"):
            continue
        if tok in "()[]":
            paren_depth = max(0, paren_depth + {"(": 1, ")": -1}.get(tok, 0))
            bracket_depth = max(0, bracket_depth + {"[": 1, "]": -1}.get(tok, 0))
            continue
        top = pending_controls[-1] if pending_controls else None
        is_top_depth = top is not None and top[:3] == [paren_depth, bracket_depth, condition_brace_depth]
        if tok == "=>":
            pending_controls = pending_controls[:-1] if is_top_depth else pending_controls
            continue
        if tok in {"=", "in", "let"} and is_top_depth:
            top[5 if tok == "in" else 4] = tok == "let"
            continue
        if tok == ";":
            let_depth = None if (let_depth is not None and brace_depth == let_depth and paren_depth == 0) else let_depth
            in_impl_header = False if (paren_depth == 0 and condition_brace_depth == 0) else in_impl_header
            pending_controls = pending_controls[:-1] if is_top_depth else pending_controls
            continue
        if tok == "}" and condition_brace_depth > 0:
            condition_brace_depth -= 1
            continue
        if tok == "}":
            brace_depth = max(0, brace_depth - 1)
        while control_stack and brace_depth <= control_stack[-1]:
            control_stack.pop()
        if tok == "}":
            continue
        is_ctrl_open = tok == "{" and is_top_depth and not top[4] and not top[5]
        if is_ctrl_open and not top[3]:
            control_stack.append(brace_depth)
            max_nesting = max(max_nesting, len(control_stack))
        if tok.endswith("{"):
            condition_brace_depth += 1 if (top is not None and not is_ctrl_open) else 0
            let_depth = None if (is_ctrl_open and top[3]) else let_depth
            pending_controls = pending_controls[:-1] if is_ctrl_open else pending_controls
            brace_depth += 1
            in_impl_header = False
            continue
        if tok == "impl":
            in_impl_header = True
            continue
        if tok == "let" and top is None and let_depth is None:
            let_depth = brace_depth
            continue
        is_ctrl_kw = tok in RUST_CONTROL_WORDS and not (tok == "for" and in_impl_header)
        if is_ctrl_kw and let_depth is not None and brace_depth == let_depth and paren_depth == 0:
            let_depth = None
        if tok == "else" and not is_top_depth:
            is_le = let_depth is not None and brace_depth == let_depth and paren_depth == 0 and condition_brace_depth == 0
            pending_controls.append([paren_depth, bracket_depth, condition_brace_depth, is_le, False, False])
        elif is_ctrl_kw and is_top_depth:
            top[3] = False
        elif is_ctrl_kw:
            pending_controls.append([paren_depth, bracket_depth, condition_brace_depth, False, False, tok == "for"])
    return max_nesting


def filter_suppressed(
    violations: list[Violation],
    fn_spans: list[FnSpan],
    directives: dict[int, IgnoreDirective],
    *,
    cleaned_lines: list[str],
    comment_lines: set[int],
) -> list[Violation]:
    span_headers: list[tuple[int, int, set[int]]] = []
    for idx, span in enumerate(fn_spans):
        prev_end = max(
            (other.end_line for other in fn_spans[:idx] if other.end_line <= span.start_line),
            default=0,
        )
        leading = collect_contiguous_header_lines(
            cleaned_lines, span.start_line, prev_end_line=prev_end, comment_lines=comment_lines
        )
        span_headers.append((span.start_line, span.end_line, leading | {span.start_line}))
    decl_start_lines = {start_line for start_line, _, _ in span_headers}
    kept: list[Violation] = []
    for entry in violations:
        span_lines = {
            line_num
            for start_line, end_line, header_set in span_headers
            if start_line <= entry.line <= end_line
            and not (start_line < end_line and entry.line == end_line and entry.line in decl_start_lines)
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


def collect_contiguous_header_lines(
    cleaned_lines: list[str],
    outer_start: int,
    *,
    prev_end_line: int,
    comment_lines: set[int],
) -> set[int]:
    collected: set[int] = set()
    attr_stack: list[str] = []
    attr_lines: list[int] = []
    pending_delims: list[str] = []
    cur_code = ""
    line_no = outer_start - 1
    while (line_no > prev_end_line and line_no >= 1) or pending_delims:
        if pending_delims and pending_delims[-1] in "])}":
            attr_stack.append(pending_delims.pop())
            continue
        if pending_delims and (not attr_stack or ATTR_PAIR_MAP[attr_stack[-1]] != pending_delims[-1]):
            return collected
        if pending_delims:
            pending_delims.pop()
            attr_stack.pop()
            continue
        if attr_lines and not attr_stack and cur_code.startswith("#[") and not cur_code.startswith("#!["):
            collected.update(attr_lines)
            attr_lines.clear()
            continue
        if attr_lines and not attr_stack:
            break
        if line_no <= prev_end_line or line_no < 1:
            break
        raw_line = cleaned_lines[line_no - 1]
        if not attr_stack and line_no in comment_lines:
            collected.add(line_no)
            line_no -= 1
            continue
        cur_code = ATTR_NOISE_PATTERN.sub(" ", raw_line).strip()
        if not attr_stack and not cur_code.endswith("]"):
            break
        attr_lines.append(line_no)
        pending_delims = [ch for ch in cur_code if ch in "[](){}"]
        line_no -= 1
    return collected


if __name__ == "__main__":
    raise SystemExit(main())
