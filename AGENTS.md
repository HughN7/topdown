# Topdown Repository Agent Guide

This repository develops **Topdown** and strictly dogfoods every rule on its own codebase.

## Architecture Specs (Follow in Precedence Order)

1. Core invariants (all languages): [`core/base.md`](core/base.md) (`<= 50` lines)
2. Language packs: [`languages/python.md`](languages/python.md), [`languages/typescript.md`](languages/typescript.md), [`languages/rust.md`](languages/rust.md), [`languages/swift.md`](languages/swift.md)
3. Zone overrides (directory-scoped): [`zones/`](zones/) (`frontend-ui.md`, `svelte-state.md`, `systems-hotpath.md`, `security-critical.md`, `prototype.md`)
4. Human escape hatches: `topdown-ignore(RULE-ID, reason="...")` gated via [`.github/workflows/topdown-escape-hatch.yml`](.github/workflows/topdown-escape-hatch.yml)

## Maintenance & Repository Invariants

- **2-Tier Checker Boundary (see [`CONTRIBUTING.md`](CONTRIBUTING.md)):**
  - **Tier 1 (`enforcement/check_python.py`, `check_ts.mjs`, `check_rust.py`):** Feature-frozen at v0.5 (bug fixes only) single-file `PostToolUse` hooks. Do not duplicate new full-repo workflows into Tier-1 scripts.
  - **Tier 2 (`engine/`):** Single home for new multi-language rules, cross-file boundary checks, and repo CLI workflows (`init`, `--outline`, `--receipt`).
- **Zero External-Codebase Assumptions:** Keep cross-file `CANON-02` boundary checks strictly opt-in via `topdown.toml` (`pure_dirs`), and keep `L1–L3` exception guidance in docs rather than hard AST bans.
- **Docs Line Budgets:** `core/base.md <= 50` lines; `core/base.md + any 1 language pack + any 1 zone <= 150` lines.
- **Pre-Edit Layout:** Inspect existing module structure with `--outline` before adding helpers to avoid `LLM-08` duplicates.
- **Copy & Casing:** Always use `Topdown` (brand) and `Antigravity` (agent harness).

## Delivery Gate (Run Before Declaring Done)

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_check_ts.mjs
python3 enforcement/check_python.py enforcement/check_python.py enforcement/check_rust.py enforcement/run_hook.py examples/verify_examples.py engine/
node enforcement/check_ts.mjs --backend=heuristic enforcement/check_ts.mjs .agents/run_hook.mjs
node enforcement/check_ts.mjs --backend=ast enforcement/check_ts.mjs .agents/run_hook.mjs
python3 -m engine.cli enforcement/ engine/ .agents/run_hook.mjs examples/verify_examples.py examples/fixtures/python_after.py examples/fixtures/ts_after.ts examples/fixtures/rust_after.rs tests/positive_witness/ tests/checkpoint_evals/cp1_base.py tests/checkpoint_evals/cp2_clean.py tests/checkpoint_evals/cp3_clean.py
python3 examples/verify_examples.py
```
