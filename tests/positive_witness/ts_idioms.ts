// Positive-witness TS idioms: must pass check_ts.mjs with 0 violations and 0 waivers.

export type PaymentEvent =
  | { readonly kind: "settled"; readonly netCents: number }
  | { readonly kind: "failed"; readonly reason: string };

export interface FormatOptions {
  readonly currencySymbol?: string;
  readonly fallbackLabel?: string;
}

const CURRENCY_REGEX = /^\$[0-9]+(?:\.[0-9]{2})?$/;

export function describeEvent(event: PaymentEvent, options?: FormatOptions): string {
  const symbol = options?.currencySymbol ?? "$";
  switch (event.kind) {
    case "settled":
      return formatAmount(event.netCents, symbol);
    case "failed":
      return options?.fallbackLabel ?? event.reason;
  }
}

export function isFormattedUsd(label: string): boolean {
  return CURRENCY_REGEX.test(label);
}

function formatAmount(amountCents: number, symbol: string): string {
  const sign = amountCents < 0 ? "-" : "";
  const formatted = (Math.abs(amountCents) / 100).toFixed(2);
  return `${sign}${symbol}${formatted}`;
}
