# Topdown Zone Profile: Svelte 5 Reactive State (`**/*.svelte.ts`)

- **Scope:** Reactive state orchestrators using Svelte 5 runes (`$state`, `$derived`) in `src/lib/**/state.svelte.ts`.
- **Strict Boundary:** Pure domain logic and non-reactive helpers stay in standard `.ts` modules under 100% strict `core/base.md` (no `.svelte.ts` unless `$state` fields are required).

## Rule Overrides

| Rule | Override | Guardrail |
|---|---|---|
| **`CANON-04`** *(Relax — Encapsulated Reactive State)* | Reactive orchestrator modules may use a **class singleton** (`#private` helpers, public entrypoint methods, and one exported `const` instance) to hold `$state` fields. | No other module-level mutable state; keep business calculations in pure, directly unit-testable functions outside the singleton class. |
| **`CANON-05`** *(Relax — Reactive Callbacks)* | Inline reactive effects and derived callbacks may capture class `$state` fields. | Keep callback bodies `<= 8` lines and nesting depth `<= 2`. |
