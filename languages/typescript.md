# Topdown Language Pack: TypeScript / JavaScript

## 1. Idiom Mapping

| Rule | TypeScript / JS Idiom |
|---|---|
| `CANON-01` | Format first with `prettier` or `oxfmt`, then measure the 60–80 line cap. |
| `CANON-02` | Keep `fetch`, FS, DB, and `Date.now()` in `L0` or thin I/O adapters; keep `L1`/`L2`/`L3` pure. |
| `CANON-03` / `LLM-04` | Ban `any`, untyped `Record<string, unknown>`, and parallel lifecycle booleans (`isLoading` + `isError`) across internal boundaries; use `readonly` interfaces, discriminated unions (`{ status: "loading" } \| { status: "error"; message: string }`), and destructured options objects for 4–6 params. |
| `CANON-04` | Mark fields `readonly` / `readonly T[]`; return new arrays/objects instead of mutating arguments. |
| `CANON-05` | Early returns over nested `if`; ban nested `function` declarations and nested ternaries (`no-nested-ternary`). Single-line `.map()` / `.filter()` callbacks are allowed. |
| `LLM-02` / `LLM-06` / `LLM-07` | Ban empty `catch {}` blocks (prefer typed union/result returns in `L1–L3`), unused exports (`npx knip`), and `process.env.NODE_ENV === "test"` branching in production code. |
| `LLM-08` | Inspect module layout with `--outline` before adding helpers; reuse existing 6+ line `L3` leaf primitives rather than adding duplicates. |
| `CANON-08` | `// topdown-ignore(CANON-05, reason="...")` on the function line, preceding line, or target line. |

## 2. Standard Linter Config

**Oxlint (`.oxlintrc.json` — sub-100ms, recommended for `PostToolUse` hooks):**
```json
{
  "rules": {
    "max-lines-per-function": ["error", { "max": 80, "skipBlankLines": false, "skipComments": false }],
    "max-depth": ["error", 2],
    "complexity": ["error", 10],
    "max-params": ["error", 3],
    "no-nested-ternary": "error",
    "no-unused-vars": ["error", { "argsIgnorePattern": "^_" }]
  }
}
```

**ESLint (`eslint.config.js` — flat config):**
```js
export default [{ rules: {
  "max-lines-per-function": ["error", { max: 80, skipBlankLines: false, skipComments: false }],
  "max-depth": ["error", 2], "complexity": ["error", 10], "max-params": ["error", 3],
  "no-nested-ternary": "error", "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
}}];
```

## 3. Structural Checker

```bash
node enforcement/check_ts.mjs [--backend=auto|ast|heuristic] [--fix] [--dry-run] [--outline] [--json] src/
```

Uses optional `typescript` AST (`ts.createSourceFile`) when installed for full parity + `[L0]..[L3]` tier tag verification + `LLM-08` duplicate-leaf detection + `--outline` pre-edit X-Ray (auto-appended by `run_hook.py` on `CANON-01`/`LLM-08` failures) + safe-subset `--fix` on hoisted `function` declarations; falls back to zero-dep heuristics otherwise.
