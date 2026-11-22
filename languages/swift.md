# Topdown Language Pack: Swift (macOS / iOS)

## 1. Idiom Mapping

| Rule | Swift 5.9+ / 6 Idiom |
|---|---|
| `CANON-01` | Format first with `swift-format`, then measure the 60–80 line cap. |
| `CANON-02` | Keep I/O (`URLSession`, `FileManager`, `UserDefaults`, `Date.now`, SwiftData) in `L0` or thin adapters; keep `L1`/`L2`/`L3` pure (`Sendable` value types in -> out). |
| `CANON-03` / `LLM-04` | Ban `[String: Any]` and `Any` across internal boundaries; use `struct`s and `enum`s with associated values. Swift's labeled parameters cover 4–6 named args natively without one-off DTO structs. |
| `CANON-04` | Prefer `struct` value types and `let` properties; avoid `inout` and mutating `class` state outside `zones/systems-hotpath.md`. |
| `CANON-05` | Use `guard ... else { return }` early exits and exhaustive `switch`; ban nested `func` declarations and nested ternaries. *(For SwiftUI `View` closures, apply `zones/frontend-ui.md` to `Views/` while keeping `Domain/` strict.)* |
| `LLM-02` | Ban force-unwrapping (`!`), `try!`, and silent `try?` / `catch {}` error swallowing in non-test code. |
| `CANON-08` | `// topdown-ignore(CANON-05, reason="...")` on the `func` line, preceding line, or target line. |

## 2. Standard Linter Config (`.swiftlint.yml` — SwiftLint)

```yaml
opt_in_rules:
  - force_unwrapping

function_body_length:
  warning: 60
  error: 80

cyclomatic_complexity:
  warning: 8
  error: 10

nesting:
  type_level: 1
  function_level: 2

function_parameter_count:
  warning: 4
  error: 6

force_try: error
force_cast: error
```

## 3. Structural Checker

Linter-only in v0.1 — enforce with `swiftlint --strict`, `swift-format`, and [`enforcement/ci_escape_hatch.yml`](../enforcement/ci_escape_hatch.yml).
