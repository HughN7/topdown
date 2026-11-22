# Topdown Language Pack: Rust

## 1. Idiom Mapping

| Rule | Rust Idiom |
|---|---|
| `CANON-01` | Format first with `rustfmt`, then measure the 60–80 line cap. |
| `CANON-02` | Keep I/O (`std::fs`, DB, HTTP, `SystemTime`) in `L0` or thin adapter functions; keep `L1`/`L2`/`L3` pure. |
| `CANON-03` / `LLM-04` | Ban `serde_json::Value` across internal boundaries; use explicit structs/enums. Prefer borrowing (`&T`, `&[T]`) over `.clone()` spam. Use an idiomatic options/input struct for 4–6 params. |
| `CANON-04` | Take `&T` and return owned values; reserve `&mut` for `zones/systems-hotpath.md`. |
| `CANON-05` | Use `?` early-return propagation and `let ... else`; ban nested `fn` declarations and nested `if/else` value expressions. |
| `LLM-02` | Ban `.unwrap()`, `.expect()` without invariant proof, and `.unwrap_or_default()` in non-test code (`#[cfg(test)]` exempt). |
| `CANON-08` | `// topdown-ignore(LLM-02, reason="...")` on the `fn` line, preceding line, or target line. |

## 2. Standard Linter Config (`clippy.toml`)

```toml
cognitive-complexity-threshold = 10
too-many-arguments-threshold = 4
too-many-lines-threshold = 80
```

Enable in `Cargo.toml` or crate root: `#![deny(clippy::unwrap_used, dead_code)]`.

## 3. Structural Checker

Lightweight zero-dependency heuristic complement to Clippy (Python 3.12+ stdlib):

```bash
python3 enforcement/check_rust.py src/
```

Ignores top-level `#[cfg(test)]` modules; checks `CANON-01` (80-line cap), `CANON-05` (nesting depth, nested `fn`), `LLM-02` (`.unwrap()` / `.unwrap_or_default()`), `LLM-07` (inline `#[cfg(test)]`), and `CANON-08` (`topdown-ignore` reason). For full AST call-order/depth checks, use the optional `engine/` (`topdown src/`). Pair with Clippy for compiler-level checks.
