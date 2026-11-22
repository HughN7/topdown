// Positive-witness Rust idioms: must pass check_rust.py with 0 violations and 0 waivers.

pub struct Invoice {
    pub invoice_id: String,
    pub amount_cents: i64,
}

pub trait Summarizable {
    fn summary_line(&self) -> Result<String, &'static str>;
}

impl Summarizable for Invoice {
    fn summary_line(&self) -> Result<String, &'static str> {
        let valid_cents = validate_positive(self.amount_cents)?;
        let pattern = r#"^inv_[0-9]+$"#;
        let _ = pattern;
        Ok(format!("{}: {}", self.invoice_id, format_usd(valid_cents)))
    }
}

fn validate_positive(amount_cents: i64) -> Result<i64, &'static str> {
    if amount_cents <= 0 {
        return Err("amount_cents must be positive");
    }
    Ok(amount_cents)
}

fn format_usd(amount_cents: i64) -> String {
    format!("${:.2}", amount_cents as f64 / 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_invoice_summary() {
        let invoice = Invoice {
            invoice_id: "inv_1".to_string(),
            amount_cents: 2_500,
        };
        assert_eq!(invoice.summary_line().unwrap(), "inv_1: $25.00");
    }
}
