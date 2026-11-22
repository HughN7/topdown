"""Before (Unconstrained Agent Output): Payout batch processor."""

from typing import Any


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
