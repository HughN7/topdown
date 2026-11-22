// Before (Unconstrained Agent Output): Payout batch processor

export function processPayouts(records: any[], tier: string, minFeeCents: number): string[] {
  const output: string[] = [];

  function computeFee(amountCents: number): number {
    return tier === "vip" ? Math.floor(amountCents * 0.01) : amountCents < 5000 ? minFeeCents : Math.floor(amountCents * 0.025);
  }

  for (const record of records) {
    try {
      if (record.isActive) {
        if (record.isCleared) {
          if (record.grossCents > 0) {
            const netCents = record.grossCents - computeFee(record.grossCents);
            output.push(`${record.merchantId}: $${(netCents / 100).toFixed(2)}`);
          }
        }
      }
    } catch (err) {}
  }
  return output;
}
