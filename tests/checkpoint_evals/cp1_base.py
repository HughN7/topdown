"""Checkpoint 1 (Base): Topdown-compliant batch payout processor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

VIP_FEE_BPS = 100
STANDARD_FEE_BPS = 250
SMALL_PAYOUT_THRESHOLD_CENTS = 5_000


@dataclass(frozen=True)
class PayoutRequest:
    merchant_id: str
    gross_cents: int
    is_active: bool
    is_cleared: bool


@dataclass(frozen=True)
class SettledPayout:
    merchant_id: str
    net_cents: int
    formatted_net: str


@dataclass(frozen=True)
class BatchSummary:
    is_dry_run: bool
    total_net_cents: int
    formatted_total: str
    settled_payouts: tuple[SettledPayout, ...]


# [L0] Orchestrator (I/O Shell)
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
        formatted_total=format_usd_cents(total_net_cents),
        settled_payouts=settled,
    )
    if not is_dry_run and save_summary is not None:
        save_summary(summary)
    return summary


# [L1] Pure Workflow
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
        settled.append(build_settled_payout(request, tier=tier, min_fee_cents=min_fee_cents))
    return tuple(settled)


# [L2] Pure Rule
def build_settled_payout(
    request: PayoutRequest,
    *,
    tier: str,
    min_fee_cents: int,
) -> SettledPayout:
    if tier == "vip":
        fee_cents = (request.gross_cents * VIP_FEE_BPS) // 10_000
    elif request.gross_cents < SMALL_PAYOUT_THRESHOLD_CENTS:
        fee_cents = min_fee_cents
    else:
        fee_cents = (request.gross_cents * STANDARD_FEE_BPS) // 10_000
    net_cents = request.gross_cents - fee_cents
    return SettledPayout(
        merchant_id=request.merchant_id,
        net_cents=net_cents,
        formatted_net=format_usd_cents(net_cents),
    )


# [L3] Leaf Primitive
def format_usd_cents(amount_cents: int) -> str:
    sign_prefix = "-" if amount_cents < 0 else ""
    abs_cents = abs(amount_cents)
    whole_units = abs_cents // 100
    fractional_cents = abs_cents % 100
    decimal_text = f"{whole_units:,}.{fractional_cents:02d}"
    return f"{sign_prefix}${decimal_text}"
