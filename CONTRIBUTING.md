# Contributing to Topdown

Topdown stays effective by staying **small, opinionated, and zero-dependency**. Every new pack or rule must earn its token budget.

---

## Adding a Language Pack (`languages/<lang>.md`)

`core/base.md` is language-neutral. To contribute a new language (e.g., Go, Kotlin, Zig, C#):

1. Create `languages/<lang>.md` following the 3-section contract:
   - **`## 1. Idiom Mapping`** — Table mapping `CANON-01..08` and `LLM-01..08` to the language's idioms (auto-formatter, boundary types, named/options parameters, error propagation, dead-code tool, comment syntax).
   - **`## 2. Standard Linter Config`** — Copy-pasteable config for that ecosystem's standard linter (e.g., `.golangci.yml`, `.swiftlint.yml`).
   - **`## 3. Structural Checker`** — Command to run a custom checker if one exists, or note that enforcement is linter-only.
2. Add a row for the language to the **Language Support Matrix** in [`README.md`](README.md).
3. Keep the file concise so `core/base.md + languages/<lang>.md + any zone <= 150 lines` (verified by `tests/test_docs_budget.py`).

## Adding a Zone Profile (`zones/<zone>.md`)

1. Create `zones/<zone>.md` defining:
   - **Scope** (directory glob or file pattern) and strict boundary.
   - **`## Rule Overrides`** table marking each override as **`Relax`** or **`Tighten`**, paired with a concrete guardrail.
2. Link the zone in [`README.md`](README.md) and ensure `tests/test_docs_budget.py` still passes.

## Adding or Updating Enforcement Checkers

Topdown separates enforcement into **two maintenance tiers** so new features never have to be duplicated across 3–4 separate scripts:

- **Tier 1 — Standalone `PostToolUse` Hooks (Feature-Frozen at v0.5):** [`enforcement/check_python.py`](enforcement/check_python.py), [`enforcement/check_ts.mjs`](enforcement/check_ts.mjs), and [`enforcement/check_rust.py`](enforcement/check_rust.py) stay zero-dependency single-file hooks (`bug fixes only`). Do not add new full-repo workflows or cross-file rules to standalone scripts (note: [`enforcement/check_ts.mjs`](enforcement/check_ts.mjs) preserves its optional `typescript` AST fallback unless `--backend=ast` is requested).
- **Tier 2 — Universal Full-Repo Engine (`engine/`):** All new multi-language rules, cross-file boundary checks, and full-repo CLI workflows (`init`, `--outline`, `--receipt`) live exclusively in `engine/`.
- To add a new language to `engine/`, add a single `LanguageSpec` entry to [`engine/specs.py`](engine/specs.py) (`function_nodes`, `call_nodes`, `comment_nodes` automatically enable `[L0..L3]` tier tag verification and `topdown --outline`) and a test case in `tests/test_engine.py`.
- **AST-only call-graph & duplicate-leaf rule (`--fix`, `--outline`, `[L0..L3]` verification, `LLM-08`, complex inline types):** Only implement structural AST passes on AST-backed checkers (`engine/` and Tier-1 AST modes); never rewrite source code or guess call graphs or duplicate skeletons from regex/brace heuristics.
- **Contiguous header rule (all checkers):** Every checker's `filter_suppressed` must walk contiguous leading comments, `[L0..L3]` signposts, JSDoc/rustdoc block comments, and outer attributes/decorators (`#[...]`, `@decorator`) above a declaration so stacking `topdown-ignore` and `[L0..L3]` works uniformly across AST and heuristic paths.
- Add unit tests in `tests/` and idiomatic zero-waiver cases in `tests/positive_witness/`.
- **Dogfood everything before opening a PR:**

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_check_ts.mjs
python3 enforcement/check_python.py enforcement/check_python.py enforcement/check_rust.py enforcement/run_hook.py examples/verify_examples.py engine/
node enforcement/check_ts.mjs enforcement/check_ts.mjs
python3 examples/verify_examples.py
```
