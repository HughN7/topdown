# Topdown Zone Profile: Systems Hot Path

- **Scope:** Explicitly designated zero-allocation or latency-critical modules (e.g., parsers, codecs, ring buffers, packet/frame encoders).
- **Strict Boundary:** All non-hot-path business logic remains 100% strict `core/base.md`.

## Rule Overrides

| Rule | Override | Guardrail |
|---|---|---|
| **`CANON-04`** *(Relax — Mutation)* | Functions may mutate caller-provided buffers in place (e.g., `&mut [u8]`, pre-allocated `bytearray`, typed arrays) to avoid heap allocations. | **Mandatory Benchmark Gate:** Every hot-path module must include a reproducible benchmark proving the allocation or latency win. Keep `CANON-01`, `CANON-05`, and `CANON-07` ("WHY" comments explaining invariants) intact. |

## Enforcement Note (v0.1)

Document the hot-path boundary in the module header comment and link the benchmark command; use `topdown-ignore(CANON-04, reason="hot-path buffer reuse; bench: ...")` if flagged.
