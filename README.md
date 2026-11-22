# Topdown

**An opinionated, language-agnostic architecture and style guide for agent-accelerated codebases.**

Unconstrained coding agents tend to produce 300-line monolithic functions, deeply nested conditionals, tangled closures, and untyped dictionaries. **Topdown** pairs a terse set of opinionated structural rules with standard linter configs and zero-dependency checkers so every file reads top-down like a technical memo—easy to scan, unit-test without mocks, and maintain on Day 1. Adopt it as-is, fork it, or override specific directories (tighter or looser) via `zones/` and `topdown-ignore`.

---

## Quickstart

No installer or CLI required. Pick only the files your repo uses to keep agent context lean (~80–120 lines total):

1. **Add rules to `AGENTS.md`:** Copy or reference [`core/base.md`](core/base.md), your language pack from [`languages/`](languages/), and any applicable profile from [`zones/`](zones/).
2. **Enable linters & checks:** Copy the linter snippet from your language pack (`pyproject.toml`, `eslint.config.js`, or `clippy.toml`), optionally drop in the matching script from [`enforcement/`](enforcement/), and add [`enforcement/ci_escape_hatch.yml`](enforcement/ci_escape_hatch.yml) to `.github/workflows/`.
3. **Or hand it to your agent:**
   > Read `https://github.com/HughN7/topdown` and adopt `core/base.md` plus the matching `languages/` pack, `zones/` profile, and linter config into this repo's `AGENTS.md`.

---

## How It's Structured

Precedence: **`core/base.md` → `languages/<lang>.md` → `zones/<zone>.md` → `topdown-ignore`**

| Layer | Path | Purpose |
|---|---|---|
| **Core** | [`core/base.md`](core/base.md) | 14 language-agnostic rules (`CANON-01..08`, `LLM-01..06`): top-down `L0→L3` order, I/O shell / pure core, 60–80 line cap, max nesting depth 2, explicit types, human-gated overrides. |
| **Languages** | [`languages/`](languages/) | Idiom mappings + runnable linter configs. Ships with [Python](languages/python.md), [TypeScript/JS](languages/typescript.md), [Rust](languages/rust.md), and [Swift](languages/swift.md); extensible to any language. |
| **Zones** | [`zones/`](zones/) | Optional directory-scoped rule overrides—either **relaxing** rules where framework/systems mechanics require it ([`frontend-ui.md`](zones/frontend-ui.md), [`svelte-state.md`](zones/svelte-state.md), [`systems-hotpath.md`](zones/systems-hotpath.md)) or **tightening** them for high-stakes paths ([`security-critical.md`](zones/security-critical.md)). |
| **Enforcement** | [`enforcement/`](enforcement/) | Zero-dependency checkers (`check_python.py` full AST; `check_ts.mjs` and `check_rust.py` scoped heuristics), `run_hook.py` (`PostToolUse` dispatcher), and PR escape-hatch workflow. |

### Optional: Auto-Check on Every Edit (`PostToolUse` Hook)

Wire [`enforcement/run_hook.py`](enforcement/run_hook.py) into Claude Code / Cursor / Gemini CLI so every `Edit` or `Write` gets sub-100ms structural feedback (exits `2` with refactor guidance on violations):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "python3 enforcement/run_hook.py" }]
      }
    ]
  }
}
```

---

## Before vs. After

### Before — Unconstrained Agent Output ([`python_before.py`](examples/fixtures/python_before.py) · 9 violations)

```python
def _fmt(val: int) -> str:
    return f"${val / 100:.2f}"

def process_payouts(data: list[dict[str, Any]], tier: str, min_fee: int, dry_run: bool) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    total = 0

    def calc_fee(amount: int, is_vip: bool) -> int:
        return int(amount * 0.01) if is_vip else (min_fee if amount < 5000 else int(amount * 0.025))

    for item in data:
        try:
            if item.get("active"):
                amount = int(item.get("amount_cents", 0))
                if amount > 0:
                    if not item.get("is_blocked"):
                        fee = calc_fee(amount, tier == "vip")
                        net = amount - fee
                        total += net
                        result.append({"id": item["id"], "net": _fmt(net)})
        except Exception:
            pass
    return {"dry_run": dry_run, "total": _fmt(total), "payouts": result}
```

### After — Topdown L0–L3 ([`python_after.py`](examples/fixtures/python_after.py) · 0 violations)

```python
# L0 Orchestrator
def process_payout_batch(
    requests: tuple[PayoutRequest, ...],
    *,
    tier: str,
    min_fee_cents: int,
    is_dry_run: bool,
) -> BatchSummary:
    settled = settle_eligible_payouts(requests, tier=tier, min_fee_cents=min_fee_cents)
    total_net_cents = sum(payout.net_cents for payout in settled)
    return BatchSummary(
        is_dry_run=is_dry_run,
        total_net_cents=total_net_cents,
        formatted_total=format_cents_usd(total_net_cents),
        settled_payouts=settled,
    )

# L1 Pure Workflow -> L2 Pure Rule -> L3 Leaf Primitive
def settle_eligible_payouts(
    requests: tuple[PayoutRequest, ...],
    *,
    tier: str,
    min_fee_cents: int,
) -> tuple[SettledPayout, ...]:
    settled: list[SettledPayout] = []
    for request in requests:
        is_eligible = request.is_active and request.is_cleared and request.gross_cents > 0
        if not is_eligible:
            continue
        fee_cents = calculate_fee_cents(request.gross_cents, tier=tier, min_fee_cents=min_fee_cents)
        net_cents = request.gross_cents - fee_cents
        settled.append(SettledPayout(request.merchant_id, net_cents, format_cents_usd(net_cents)))
    return tuple(settled)

def calculate_fee_cents(gross_cents: int, *, tier: str, min_fee_cents: int) -> int:
    if tier == "vip":
        return (gross_cents * VIP_FEE_BPS) // 10_000
    if gross_cents < SMALL_PAYOUT_THRESHOLD_CENTS:
        return min_fee_cents
    return (gross_cents * STANDARD_FEE_BPS) // 10_000

def format_cents_usd(amount_cents: int) -> str:
    return f"${amount_cents / 100:.2f}"
```

### Before / After Violation Check (`python3 examples/verify_examples.py`)

Runs the checkers against the example pairs in [`examples/fixtures/`](examples/fixtures/) to verify violations drop to zero after refactoring:

| Language | Before Violations | After Violations | Delta | Status |
|---|---:|---:|---:|---|
| Python | 9 | 0 | -9 | PASS |
| TypeScript | 4 | 0 | -4 | PASS |
| Rust | 4 | 0 | -4 | PASS |

---

## Adding a Language or Zone

- **New Language (`languages/<lang>.md`):** `core/base.md` is language-neutral. Add Go, Swift, Kotlin, etc. with one file containing: (1) **Idiom Mapping** (`CANON-01..08` / `LLM-01..05`), (2) **Standard Linter Config** (e.g., `.golangci.yml`, `.swiftlint.yml`), and (3) **Structural Checker** (command or linter-only note).
- **New Zone (`zones/<zone>.md`):** Define the directory scope, a **Rule Overrides** table (each rule marked `Relax` or `Tighten`), and any required guardrail.

---

## Verify Locally

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_check_ts.mjs
python3 enforcement/check_python.py enforcement/check_python.py enforcement/check_rust.py enforcement/run_hook.py examples/verify_examples.py
node enforcement/check_ts.mjs enforcement/check_ts.mjs
python3 examples/verify_examples.py
```

## License

[MIT](LICENSE)
