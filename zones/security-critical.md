# Topdown Zone Profile: Security & Money-Critical (`src/auth/**`, `src/payments/**`)

- **Scope:** Authentication, authorization, cryptography, and ledger/payment settlement modules.
- **Baseline:** Inherits all `core/base.md` rules and tightens limits for auditability.

## Rule Overrides

| Rule | Override | Guardrail |
|---|---|---|
| **`CANON-01`** *(Tighten — Function Size)* | Lower the per-function soft cap from 60–80 lines to **40–50 lines** so every security or ledger invariant fits in one screen. | Split multi-step verification into small, named `L1`/`L2` pure checks. |
| **`CANON-03`** *(Tighten — Domain Types)* | Wrap raw primitives (tokens, secrets, money amounts, account IDs) in dedicated opaque/newtype domain types—never pass raw `str` or `float`. | Prevents accidental logging of secrets and currency/unit mix-ups. |
| **`CANON-08`** *(Tighten — Escape Hatches)* | Disallow `topdown-ignore` overrides unless approved by a designated security/domain owner in PR review. | Enforce via CODEOWNERS + `enforcement/ci_escape_hatch.yml`. |
