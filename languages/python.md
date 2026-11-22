# Topdown Language Pack: Python 3.12+

## 1. Idiom Mapping

| Rule | Python 3.12+ Idiom |
|---|---|
| `CANON-01` | Format first with `ruff format`, then measure the 60–80 line cap. |
| `CANON-02` | Keep I/O (`pathlib`, `sqlite3`, `httpx`, `datetime.now`) in `L0` or thin `fetch_*`/`save_*` adapters; keep `L1`/`L2`/`L3` pure. |
| `CANON-03` / `LLM-04` | Use `@dataclass(frozen=True)` or `TypedDict` at boundaries—never raw `dict[str, Any]`. Use `*` keyword-only arguments for 4–6 params instead of one-off DTOs. |
| `CANON-04` | Prefer `@dataclass(frozen=True)`, `tuple`, and `frozenset` over mutating caller lists/dicts. |
| `CANON-05` | Use guard clauses and exhaustive `match` statements; ban nested `def` / `async def` and nested conditional expressions (`a if x else (b if y else c)`). |
| `LLM-02` | Ban bare `except:` and broad `except Exception: pass` / `return None` inside pure logic. |
| `LLM-06` | Catch dead code with `vulture src/ --min-confidence 80`. |
| `CANON-08` | `# topdown-ignore(CANON-05, reason="...")` on the `def` line, preceding line, or target line. |

## 2. Standard Linter Config (`pyproject.toml` — Ruff)

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "C901", "PLR0912", "PLR0913", "PLR0915"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.pylint]
max-args = 6
max-branches = 8
max-statements = 50
```

## 3. Structural Checker

Reference AST checker (zero dependencies, Python 3.12+ stdlib):

```bash
python3 enforcement/check_python.py [--fix] [--dry-run] src/
```

Checks `CANON-01` (topological order, L0–L3 depth, 80-line cap, safe-subset `--fix`), `CANON-03` (positional/total args), `CANON-05` (nesting depth, inner `def`, nested ternary), `LLM-02` (error swallowing), `LLM-03` (vague/single-char names), `LLM-05` (anti-gaming names), `LLM-07` (test-runner branching), and `CANON-08` (`topdown-ignore` reason).
