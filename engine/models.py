"""Shared data models and type aliases for the universal Topdown engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, NamedTuple

MAX_FUNCTION_LINES = 80
MAX_NESTING_DEPTH = 2
MAX_BUSINESS_DEPTH = 2
MAX_MODULE_CODE_LINES = 450
MAX_MODULE_FUNCTIONS = 15
GAMING_NAME_PATTERN = re.compile(r"(?:_part\d+|_helper\d*|Part\d+|Helper\d*)$")
TEST_PATH_PATTERN = re.compile(r"(?:^|[/\\])(?:tests?|__tests__|specs?)(?:[/\\]|$)|(?:^|[/\\])test_[^/\\]+\.|\.(?:test|spec)\.[^/\\]+$|_test\.[^/\\]+$", re.IGNORECASE)

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



class ChainFrame(NamedTuple):
    """DFS frame for measuring maximum local call-chain depth."""

    fn_key: FnKey
    depth: int
    visited: frozenset[FnKey]


class PathFrame(NamedTuple):
    """DFS frame for all-pairs longest call-path search."""

    src: FnKey
    current: FnKey
    length: int
    visited: frozenset[FnKey]


class TypeComplexity(NamedTuple):
    """Measured inline generic nesting depth and anonymous tuple width."""

    max_depth: int
    max_tuple_width: int


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

