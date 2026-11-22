// After (topdown L0-L3): Payout batch processor

export interface PayoutRequest {
  readonly merchantId: string;
  readonly grossCents: number;
  readonly isActive: boolean;
  readonly isCleared: boolean;
}

export interface PayoutOptions {
  readonly tier: "vip" | "standard";
  readonly minFeeCents: number;
}

export interface SettledPayout {
  readonly merchantId: string;
  readonly netCents: number;
  readonly formattedNet: string;
}

export interface BatchSummary {
  readonly totalNetCents: number;
  readonly formattedTotal: string;
  readonly settledPayouts: readonly SettledPayout[];
}

const VIP_FEE_BPS = 100;
const STANDARD_FEE_BPS = 250;
const SMALL_PAYOUT_THRESHOLD_CENTS = 5_000;

export function processPayoutBatch(
  requests: readonly PayoutRequest[],
  options: PayoutOptions,
): BatchSummary {
  const settledPayouts = settleEligiblePayouts(requests, options);
  const totalNetCents = settledPayouts.reduce((sum, entry) => sum + entry.netCents, 0);
  return {
    totalNetCents,
    formattedTotal: formatCentsUsd(totalNetCents),
    settledPayouts,
  };
}

function settleEligiblePayouts(
  requests: readonly PayoutRequest[],
  options: PayoutOptions,
): readonly SettledPayout[] {
  const settled: SettledPayout[] = [];
  for (const request of requests) {
    const isEligible = request.isActive && request.isCleared && request.grossCents > 0;
    if (!isEligible) {
      continue;
    }
    const feeCents = calculateFeeCents(request.grossCents, options);
    const netCents = request.grossCents - feeCents;
    settled.push({
      merchantId: request.merchantId,
      netCents,
      formattedNet: formatCentsUsd(netCents),
    });
  }
  return settled;
}

function calculateFeeCents(grossCents: number, options: PayoutOptions): number {
  if (options.tier === "vip") {
    return Math.floor((grossCents * VIP_FEE_BPS) / 10_000);
  }
  if (grossCents < SMALL_PAYOUT_THRESHOLD_CENTS) {
    return options.minFeeCents;
  }
  return Math.floor((grossCents * STANDARD_FEE_BPS) / 10_000);
}

function formatCentsUsd(amountCents: number): string {
  return `$${(amountCents / 100).toFixed(2)}`;
}
