"""Comment lexing, header walking, and CANON-08 suppression helpers."""

from __future__ import annotations

import re
from typing import Any

from engine.models import FunctionEntry, IgnoreDirective, OverloadSpan, SpanHeader, Violation
from engine.specs import LanguageSpec

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
