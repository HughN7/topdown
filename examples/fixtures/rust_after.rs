// After (topdown L0-L3): Payout batch processor

pub struct PayoutRequest {
    pub merchant_id: String,
    pub gross_cents: i64,
    pub is_active: bool,
    pub is_cleared: bool,
}

const VIP_FEE_BPS: i64 = 100;
const STANDARD_FEE_BPS: i64 = 250;
const SMALL_PAYOUT_THRESHOLD_CENTS: i64 = 5_000;

// [L0] Orchestrator
pub fn process_payout_batch(
    requests: &[PayoutRequest],
    is_vip: bool,
    min_fee_cents: i64,
) -> Result<Vec<String>, &'static str> {
    if min_fee_cents < 0 {
        return Err("min_fee_cents must be non-negative");
    }
    Ok(settle_eligible_payouts(requests, is_vip, min_fee_cents))
}

// [L1] Pure Workflow
fn settle_eligible_payouts(
    requests: &[PayoutRequest],
    is_vip: bool,
    min_fee_cents: i64,
) -> Vec<String> {
    let mut summaries = Vec::new();
    for request in requests {
        let is_eligible = request.is_active && request.is_cleared && request.gross_cents > 0;
        if !is_eligible {
            continue;
        }
        let fee_cents = calculate_fee_cents(request.gross_cents, is_vip, min_fee_cents);
        let net_cents = request.gross_cents - fee_cents;
        summaries.push(format!("{}: {}", request.merchant_id, format_cents_usd(net_cents)));
    }
    summaries
}

// [L2] Pure Rule
fn calculate_fee_cents(gross_cents: i64, is_vip: bool, min_fee_cents: i64) -> i64 {
    if is_vip {
        return (gross_cents * VIP_FEE_BPS) / 10_000;
    }
    if gross_cents < SMALL_PAYOUT_THRESHOLD_CENTS {
        return min_fee_cents;
    }
    (gross_cents * STANDARD_FEE_BPS) / 10_000
}

// [L3] Leaf Primitive
fn format_cents_usd(amount_cents: i64) -> String {
    format!("${:.2}", amount_cents as f64 / 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settles_vip_payout() {
        let reqs = [PayoutRequest {
            merchant_id: "m_1".to_string(),
            gross_cents: 10_000,
            is_active: true,
            is_cleared: true,
        }];
        let summaries = process_payout_batch(&reqs, true, 50).unwrap();
        assert_eq!(summaries, vec!["m_1: $99.00"]);
    }
}
