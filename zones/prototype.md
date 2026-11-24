# Topdown Zone Profile: Prototype & Spike (`src/experiments/**`, `prototypes/**`)

- **Scope:** Isolated spike or feature-flagged directories (`src/experiments/**`, `prototypes/**`).
- **Strict Boundary:** Production modules (`src/core/**`, `src/domain/**`) remain 100% strict `core/base.md` and must never import from prototype directories.

## Rule Overrides

| Rule | Override | Guardrail |
|---|---|---|
| **`CANON-01`** *(Relax — Function Size)* | Raise the per-function soft cap from 60–80 lines to **100–120 lines** while iterating on throwaway prototypes. | Split or delete before graduating the spike into production; keep `CANON-05` flat control flow intact. |
| **`CANON-03`** *(Relax — Boundary Types & Params)* | Allow untyped maps and `> 3` positional arguments inside prototype-only files while interfaces are still shifting. | Define explicit domain types and named parameters before moving logic into `src/core/**` or `src/domain/**`. |
| **`CANON-02`** *(Tighten — One-Way Import Boundary)* | Production modules (`src/core/**`, `src/domain/**`) must **never** import from prototype directories. | Prototype code may depend on production modules, never the reverse; treat prototype modules as disposable shells. |

## Enforcement Note

Checkers are zone-agnostic by default. Teams opting into `topdown.toml` can enforce the one-way import boundary by listing prototype directories in `shell_dirs` (using bare directory prefixes like `"prototypes"` or `"src/experiments"` — **no `/**` glob suffix**) alongside `pure_dirs`, and optionally add prototype paths to `exclude_dirs` (or scope hooks) when relaxing `CANON-01`/`CANON-03`.
