// Before (Unconstrained Agent Output): Payout batch processor

pub fn process_payouts(amounts: &[Option<i64>], is_vip: bool) -> Vec<String> {
    fn compute_fee(amount_cents: i64, is_vip: bool) -> i64 {
        if is_vip {
            amount_cents / 100
        } else {
            amount_cents / 40
        }
    }

    let mut summaries = Vec::new();
    for entry in amounts {
        if entry.is_some() {
            let gross = entry.unwrap();
            if gross > 0 {
                if gross < 10_000_000 {
                    let fee = compute_fee(gross, is_vip);
                    let fallback = entry.unwrap_or_default();
                    summaries.push(format!("${:.2}", (fallback - fee) as f64 / 100.0));
                }
            }
        }
    }
    summaries
}
