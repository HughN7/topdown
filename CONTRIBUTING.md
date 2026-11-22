# Contributing to Topdown

Topdown stays effective by staying **small, opinionated, and zero-dependency**. Every new pack or rule must earn its token budget.

---

## Adding a Language Pack (`languages/<lang>.md`)

`core/base.md` is language-neutral. To contribute a new language (e.g., Go, Kotlin, Zig, C#):

1. Create `languages/<lang>.md` following the 3-section contract:
   - **`## 1. Idiom Mapping`** — Table mapping `CANON-01..08` and `LLM-01..07` to the language's idioms (auto-formatter, boundary types, named/options parameters, error propagation, dead-code tool, comment syntax).
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

- Keep `enforcement/` checkers zero-dependency by default (optional AST backends like `typescript` must fall back gracefully unless `--backend=ast` is explicitly requested).
- To add a new language to the optional universal `tree-sitter` engine, add a single `LanguageSpec` entry to [`engine/specs.py`](engine/specs.py) and a test case in `tests/test_engine.py`.
- **Safe `--fix` rule:** Only implement `--fix` / `--dry-run` on AST-backed checkers; never rewrite user source code from regex/brace heuristics.
- Add unit tests in `tests/` and idiomatic zero-waiver cases in `tests/positive_witness/`.
- **Dogfood everything before opening a PR:**

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_check_ts.mjs
python3 enforcement/check_python.py enforcement/check_python.py enforcement/check_rust.py enforcement/run_hook.py examples/verify_examples.py engine/
node enforcement/check_ts.mjs enforcement/check_ts.mjs
python3 examples/verify_examples.py
```
