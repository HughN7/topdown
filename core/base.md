# Topdown Core Specification (Universal Invariants)

> **Precedence:** `Base (core/base.md)` -> `Language (languages/<lang>.md)` -> `Zone (zones/<zone>.md)` -> `Human Override (topdown-ignore)`

Every file reads top-down like a technical memo, separates I/O from pure business logic, and exposes explicit domain types so any engineer can read, unit-test without mocks, and modify rules on Day 1.

---

## Part 1: Canonical Engineering Principles (`CANON-01` – `CANON-08`)

| ID | Principle | Rule |
|---|---|---|
| **`CANON-01`** | **Newspaper Layout & Depth Ceiling** | Order functions top-down in topological caller-above-callee order (`L0 Orchestrator -> L1 Workflow -> L2 Rule -> L3 Leaf Primitive`; optional `# [L0]..[L3]` / `// [L0]..[L3]` tier signposts are verified against the call graph when present). Shared helpers sit below all their callers. Cap business-logic call depth at 2 levels below `L0` (`L0 -> L1 -> L2`), plus `L3` stateless leaf primitives that make **0 outbound calls** to project-defined functions. Target **60–80 lines soft cap** per function (measured *after* standard auto-formatting). **Never** extract helpers shorter than ~6–8 lines unless reused in 2+ places or isolating non-trivial math/logic; keep trivial guard clauses inline. |
| **`CANON-02`** | **I/O Shell, Pure Core** | `L0` orchestrators coordinate I/O (DB, HTTP, filesystem, clock/env) and contain zero business math or nested loops; `L0` may call thin single-purpose I/O adapter leaves (`fetch_*`, `save_*`, eligible for `[L0]`) to stay within the line cap. Keep I/O control flags (`is_dry_run`, `timeout_ms`) at `L0`—never pass them into pure `L1–L3` helpers. `L1` and `L2` business helpers are 100% pure (data in -> data out), unit-testable without mocks. `L3` leaf primitives are pure utilities with zero outbound calls to project functions. |
| **`CANON-03`** | **Explicit Boundary Types, State & Parameters** | Ban untyped maps, raw JSON bags, and wildcard escape types (e.g., raw `dict`, `any`, `map[string]any`, `serde_json::Value`) across internal function boundaries; use explicit domain types. **Ban implicit state machines:** model mutually exclusive lifecycle phases with explicit discriminated unions or sum-type enums (`status: "idle" \| "loading" \| "ready" \| "error"`), never parallel lifecycle booleans (`is_loading` + `is_error`). Max **3 positional arguments**; allow 4–6 arguments only via the language's idiomatic named/structured parameter form. |
| **`CANON-04`** | **Zero Implicit Mutation** | Accept explicit inputs and return new values; never mutate passed-in arguments in place unless governed by `zones/systems-hotpath.md`. |
| **`CANON-05`** | **Flat Control Flow & No Lexical Nesting** | Max block nesting depth of **2** (use early-return guard clauses or error-propagation operators). Cognitive complexity `<= 10`. **Ban nested inner functions and variable-capturing closures** (except trivial single-line `.map()` / `.filter()` / iterator lambdas); functions live at module top level, flush left. **Ban nested ternaries / inline conditionals** (single-line scalar selection is allowed). **Ban unbounded recursion**; tree/graph traversal recursion requires an explicit `max_depth` parameter. |
| **`CANON-06`** | **Self-Documenting Naming** | Positive booleans only (`is_valid`, never `not is_invalid`). Extract compound conditions (`> 2` clauses) into named predicate booleans. Bake units into numeric names (`_cents`, `_ms`, `_bytes`, `_bps`). |
| **`CANON-07`** | **Load-Bearing "WHY" Comments Only** | Ban syntax-narrating comments (`# Loop over items`). Bless `[L0]..[L3]` tier signposts and mandate comments explaining non-obvious business rules, compliance constraints, or historical bugs. |
| **`CANON-08`** | **Human-Only Escape Hatches** | Override a rule only with `topdown-ignore(RULE-ID, reason="...")` in the language's native line-comment syntax (`RULE-ID` optional to ignore all rules on that target; non-empty `reason="..."` required). Every new override in a PR requires human sign-off via CI. |

---

## Part 2: Field-Observed LLM Code Tells (`LLM-01` – `LLM-07`)

| ID | Anti-Pattern | Rule |
|---|---|---|
| **`LLM-01`** | **No Hollow Trampoline Wrappers** | Ban pass-through functions where `fn_a(x)` forwards `x` unchanged to `fn_b(x)` (Shape-Change Rule). A deeper call is valid only if it transforms types, fans out across helpers, or crosses an I/O boundary. |
| **`LLM-02`** | **No Defensive Error Swallowing** | Never wrap internal pure logic in broad catch-all blocks or silent fallback chains that hide bugs. |
| **`LLM-03`** | **No Vague or Single-Char Names** | Ban vague container nouns (`data`, `info`, `item`, `temp`, `result`, `handler`) and single-character parameter/variable names (`i`, `n`, `x`, `v`) outside `_` for ignored values (e.g., `for index, name in enumerate(names)`, not `for i, n in ...`). |
| **`LLM-04`** | **No Single-Use DTO Bloat** | Use the language's idiomatic named/structured parameter mechanism instead of inventing one-off parameter wrapper types. |
| **`LLM-05`** | **No Linter-Gaming Compression** | Never strip blank lines, pack multiple statements onto one line, or abbreviate identifiers to pass line caps; always format first, measure second. |
| **`LLM-06`** | **No Dead Code or Unlinked TODOs** | Ban commented-out code, unused exports, and `TODO`/`FIXME` comments without a linked issue or ticket reference. |
| **`LLM-07`** | **No Test-Runner Branching** | Never branch on test environment flags (`PYTEST_CURRENT_TEST`, `NODE_ENV === "test"`, `unittest` in `sys.modules`) inside production code; inject test behavior via explicit typed parameters at the `L0` I/O shell. |

---

## Delivery Gate

Before declaring a task complete, format modified files with the language's standard auto-formatter, run the configured language linter and matching `enforcement/` checker (if present), and resolve all violations without adding unapproved `topdown-ignore` overrides.
