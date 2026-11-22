# Topdown Zone Profile: Frontend UI (`src/components/**`)

- **Scope:** Reactive UI components (`src/components/**`, React/Vue/Svelte/Solid views).
- **Strict Boundary:** `domain/`, `state/`, `stores/`, and `services/` remain 100% strict `core/base.md`.

## Rule Overrides

| Rule | Override | Guardrail |
|---|---|---|
| **`CANON-05`** *(Relax — Closures)* | Inline event handlers (`onClick`, `onChange`) and framework hook callbacks (`useEffect`, `useMemo`, `computed`) may capture component props/state. | Keep callback bodies `<= 8` lines and nesting depth `<= 2`; extract any business logic or multi-step state transforms to pure module-level helpers in `domain/` or `state/`. |

## Enforcement Note (v0.1)

Checkers are zone-agnostic in v0.1. Point `enforcement/check_ts.mjs` at strict directories (`src/domain/`, `src/state/`) and use ESLint flat-config file overrides for `src/components/**`.
