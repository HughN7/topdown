"""Positive-witness Python idioms: must pass check_python.py with 0 violations and 0 waivers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class LedgerEntry:
    account_id: str
    amount_cents: int
    status: str

    @classmethod
    def from_cents(cls, account_id: str, amount_cents: int) -> LedgerEntry:
        status = "credit" if amount_cents >= 0 else "debit"
        return cls(account_id=account_id, amount_cents=amount_cents, status=status)


@asynccontextmanager
async def open_audit_scope(scope_name: str) -> AsyncIterator[str]:
    yield f"scope:{scope_name}"


def summarize_credits(entries: tuple[LedgerEntry, ...]) -> list[str]:
    amounts = [
        entry.amount_cents
        for entry in entries
        if entry.status == "credit"
    ]
    return [format_usd(amount_cents) for amount_cents in amounts]


def classify_tier(amount_cents: int) -> str:
    match amount_cents:
        case value if value >= 100_000:
            return "enterprise"
        case value if value >= 10_000:
            return "growth"
        case _:
            return "starter"


def format_usd(amount_cents: int) -> str:
    return f"${amount_cents / 100:.2f}"
