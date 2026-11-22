# Topdown Language Pack: TypeScript / JavaScript

## 1. Idiom Mapping

| Rule | TypeScript / JS Idiom |
|---|---|
| `CANON-01` | Format first with `prettier`, then measure the 60–80 line cap. |
| `CANON-02` | Keep `fetch`, FS, DB, and `Date.now()` in `L0` or thin I/O adapters; keep `L1`/`L2`/`L3` pure. |
| `CANON-03` / `LLM-04` | Ban `any` and untyped `Record<string, unknown>` across internal boundaries; use `readonly` interfaces and discriminated unions. Use destructured options objects for 4–6 params. |
| `CANON-04` | Mark fields `readonly` / `readonly T[]`; return new arrays/objects instead of mutating arguments. |
| `CANON-05` | Early returns over nested `if`; ban nested `function` declarations and nested ternaries (`no-nested-ternary`). Single-line `.map()` / `.filter()` callbacks are allowed. |
| `LLM-02` | Ban empty `catch {}` blocks or silent fallback returns inside pure logic. |
| `LLM-06` | Catch unused exports and dead files with `npx knip`. |
| `CANON-08` | `// topdown-ignore(CANON-05, reason="...")` on the function line, preceding line, or target line. |

## 2. Standard Linter Config (`eslint.config.js` — Flat Config)

```js
export default [
  {
    rules: {
      "max-lines-per-function": ["error", { max: 80, skipBlankLines: false, skipComments: false }],
      "max-depth": ["error", 2],
      "complexity": ["error", 10],
      "max-params": ["error", 3],
      "no-nested-ternary": "error",
    },
  },
];
```

## 3. Structural Checker

Lightweight zero-dependency heuristic complement to ESLint (Node.js 20+):

```bash
node enforcement/check_ts.mjs src/
```

Reliably checks `CANON-01` (80-line cap), `CANON-05` (nesting depth, nested ternary, nested `function`), `LLM-02` (empty `catch`), and `CANON-08` (`topdown-ignore` reason). Pair with ESLint for full AST coverage.
