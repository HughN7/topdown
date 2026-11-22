# Topdown

**An opinionated, language-agnostic architecture and style guide for agent-accelerated codebases.**

Unconstrained coding agents tend to produce 300-line monolithic functions, deeply nested conditionals, tangled closures, and untyped dictionaries. **Topdown** pairs a terse set of opinionated structural rules with standard linter configs and zero-dependency checkers so every file reads top-down like a technical memo—easy to scan, unit-test without mocks, and maintain on Day 1. Adopt it as-is, fork it, or override specific directories (tighter or looser) via `zones/` and `topdown-ignore`.

## Quickstart

No installer or CLI required. Pick only the files your repo uses to keep agent context lean (~80–120 lines total):

1. **Add rules to `AGENTS.md`:** Copy or reference [`core/base.md`](core/base.md), your language pack from [`languages/`](languages/), and any applicable profile from [`zones/`](zones/).
2. **Enable linters & checks:** Copy the linter snippet from your language pack (`pyproject.toml`, `.oxlintrc.json` / `eslint.config.js`, `clippy.toml`, or `.swiftlint.yml`), optionally drop in the matching script from [`enforcement/`](enforcement/), and add [`enforcement/ci_escape_hatch.yml`](enforcement/ci_escape_hatch.yml) to `.github/workflows/`.
3. **Or hand it to your agent:**
   > Read `https://github.com/HughN7/topdown` and adopt `core/base.md` plus the matching `languages/` pack, `zones/` profile, and linter config into this repo's `AGENTS.md`.

## How It's Structured

Precedence: **`core/base.md` → `languages/<lang>.md` → `zones/<zone>.md` → `topdown-ignore`**

| Layer | Path | Purpose |
|---|---|---|
| **Core** | [`core/base.md`](core/base.md) | 15 language-agnostic rules (`CANON-01..08`, `LLM-01..07`): top-down `L0→L3` order, I/O shell / pure core, 60–80 line cap, max nesting depth 2, explicit types, no test-runner branching, human-gated overrides. |
| **Languages** | [`languages/`](languages/) | Idiom mappings + runnable linter configs (see support matrix below). |
| **Zones** | [`zones/`](zones/) | Optional directory-scoped rule overrides—either **relaxing** rules where framework/systems mechanics require it ([`frontend-ui.md`](zones/frontend-ui.md), [`svelte-state.md`](zones/svelte-state.md), [`systems-hotpath.md`](zones/systems-hotpath.md)) or **tightening** them for high-stakes paths ([`security-critical.md`](zones/security-critical.md)). |
| **Enforcement** | [`enforcement/`](enforcement/) | Zero-dependency checkers (`check_python.py`, `check_ts.mjs`, `check_rust.py`), `run_hook.py` (`PostToolUse` dispatcher), and PR escape-hatch workflow. |

### Language Support Matrix

| Language | Pack | Linter | Zero-Dep Checker | Universal AST Engine (`engine/`) | `--fix` Re-sort |
|---|---|---|---|---|---|
| **Python** | [`python.md`](languages/python.md) | Ruff | Stdlib AST (`check_python.py`) | Yes | Safe subset (`--fix` / `--dry-run`) |
| **TypeScript / JS** | [`typescript.md`](languages/typescript.md) | Oxlint / ESLint | Heuristic / Optional TS AST (`check_ts.mjs`) | Yes | Safe subset (`--backend=ast`) |
| **Rust** | [`rust.md`](languages/rust.md) | Clippy | Heuristic (`check_rust.py`) | Yes | No *(requires AST rewriter)* |
| **Swift** | [`swift.md`](languages/swift.md) | SwiftLint | Linter-only | Yes | No *(requires AST rewriter)* |
| **Go** | *(use `core/base.md`)* | golangci-lint | Linter-only | Yes | No *(requires AST rewriter)* |

*Why isn't `--fix` universal?* Reordering code safely requires a real AST and language-specific hoisting rules (e.g., JS `const` arrow functions are not hoisted; Python decorators bind at import time). Heuristic and linter-only paths intentionally omit `--fix` and rely on actionable `CANON-01` diagnostics instead.

*Using another language (Kotlin, Zig, C#, etc.)?* Point your agent at [`core/base.md`](core/base.md) plus any [`languages/*.md`](languages/) file as a template to generate a pack directly in your repo's `AGENTS.md`—or open a PR per [`CONTRIBUTING.md`](CONTRIBUTING.md).

### Optional: Universal Tree-sitter Engine & Opt-In `CANON-02` Boundaries

If you want one real AST engine across Python, TypeScript/JS, Rust, Swift, and Go—or opt-in cross-file I/O boundary checks—run or install it directly from GitHub (no PyPI release needed):

```bash
# Run ephemerally via uvx (zero install) or install with pip
uvx --from git+https://github.com/HughN7/topdown.git topdown src/
pip install git+https://github.com/HughN7/topdown.git

topdown src/                          # Single-file AST checks across all 5 languages (zero config)
topdown --config topdown.toml src/    # Adds opt-in cross-file CANON-02 I/O boundary checks
```

Topdown **never assumes directory names** (`core/`, `domain/`, etc.) on external repos. Cross-file `CANON-02` checks run *only* when you explicitly declare `pure_dirs` in `topdown.toml`:

```toml
exclude_dirs = [".svelte-kit", "migrations"]   # optional: skip generated code

[boundaries]
pure_dirs = ["src/domain"]        # required to enable cross-file CANON-02 checks
shell_dirs = ["src/adapters"]     # optional: pure_dirs cannot import from shell_dirs
extra_io_imports = ["stripe"]     # optional: project-specific I/O packages
```

### Optional: Auto-Check on Every Edit (`PostToolUse` Hook)

Wire [`enforcement/run_hook.py`](enforcement/run_hook.py) into Claude Code / Cursor / Antigravity so every `Edit` or `Write` gets sub-100ms structural feedback (exits `2` with refactor guidance on violations):

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
# L0 Orchestrator (I/O Shell)
def process_payout_batch(
    requests: tuple[PayoutRequest, ...],
    *,
    tier: str,
    min_fee_cents: int,
    is_dry_run: bool,
    save_summary: Callable[[BatchSummary], None] | None = None,
) -> BatchSummary:
    settled = settle_eligible_payouts(requests, tier=tier, min_fee_cents=min_fee_cents)
    total_net_cents = sum(payout.net_cents for payout in settled)
    summary = BatchSummary(
        is_dry_run=is_dry_run,
        total_net_cents=total_net_cents,
        formatted_total=format_cents_usd(total_net_cents),
        settled_payouts=settled,
    )
    if not is_dry_run and save_summary is not None:
        save_summary(summary)
    return summary

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

## Legacy Codebase Audit Prompt (Implicit State Machines & Topdown Migration)

Paste this prompt into your coding agent to audit an existing codebase for implicit state machines and structural debt while preserving external behavior:

```text
Audit this codebase under Topdown (core/base.md + matching languages/*.md and zones/*.md):
1. Run the matching Topdown checker in enforcement/ to surface automated CANON-* and LLM-* violations (including CANON-03 parallel lifecycle boolean flags).
2. Find all remaining implicit state machines across the app: parallel lifecycle booleans (isLoading/isError/isSuccess), dependent nullable fields that are only valid in certain phases, and scattered ad-hoc state mutations across UI handlers or L0 functions.
3. Refactor each implicit state machine into an explicit discriminated union or sum-type enum (making impossible states unrepresentable) with pure L1/L2 transition functions, while keeping L0 I/O orchestrators thin and top-down (L0 -> L1 -> L2 -> L3).
4. Make the decision that elevates UX (no impossible/flickering UI states), DX (exhaustive type checking, zero-mock unit tests), and AX (top-down readability) without breaking public APIs or existing tests.
```

## Contributing & License

Want to contribute a new language pack, zone profile, or checker? See [`CONTRIBUTING.md`](CONTRIBUTING.md).

Licensed under [MIT](LICENSE).
