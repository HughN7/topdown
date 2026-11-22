"""After (topdown L0-L3): Payout batch processor."""

from __future__ import annotations

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


# L1 Pure Workflow
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
        settled.append(
            SettledPayout(
                merchant_id=request.merchant_id,
                net_cents=net_cents,
                formatted_net=format_cents_usd(net_cents),
            )
        )
    return tuple(settled)


# L2 Pure Rule
def calculate_fee_cents(gross_cents: int, *, tier: str, min_fee_cents: int) -> int:
    if tier == "vip":
        return (gross_cents * VIP_FEE_BPS) // 10_000
    if gross_cents < SMALL_PAYOUT_THRESHOLD_CENTS:
        return min_fee_cents
    return (gross_cents * STANDARD_FEE_BPS) // 10_000


# L3 Leaf Primitive (0 outbound local calls)
def format_cents_usd(amount_cents: int) -> str:
    return f"${amount_cents / 100:.2f}"
