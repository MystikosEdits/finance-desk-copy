"""Golden numbers, worked by hand against the 2025 rates file."""

import taxcalc


def test_60k_net_single_no_w2_2025():
    r = taxcalc.load_rates(2025)
    est = taxcalc.compute(taxcalc.TaxInputs(year=2025, filing_status="single",
                                            business_income_cents=8000000, business_expenses_cents=2000000), r)
    assert est["net_profit_cents"] == 6000000
    assert est["se_base_cents"] == 5541000                 # 60,000 x 92.35%
    assert est["se_tax_cents"] == 847773                   # 6,870.84 SS + 1,606.89 Medicare
    assert est["half_se_deduction_cents"] == 423886
    assert est["agi_cents"] == 5576114
    assert est["deduction_cents"] == 1575000 and est["deduction_kind"] == "standard"
    assert est["qbi_deduction_cents"] == 800223            # 20% of taxable-before-QBI (the smaller)
    assert est["taxable_income_cents"] == 3200891
    assert est["federal_income_tax_cents"] == 360257       # 10% of 11,925 + 12% of the rest
    assert est["additional_medicare_cents"] == 0
    assert est["federal_total_cents"] == 360257 + 847773
    assert est["state_tax_cents"] == 0
    assert est["set_aside_cents"] == est["federal_total_cents"]
    assert est["marginal_rate_pct"] == 12
    assert est["safe_harbor"]["prior_year_target_cents"] is None
    assert est["safe_harbor"]["annual_target_cents"] == round(est["federal_total_cents"] * 0.9)
    assert [q["due"] for q in est["quarters"]][0] == "2025-04-15"


def test_w2_hits_the_wage_base_and_additional_medicare_2025():
    r = taxcalc.load_rates(2025)
    est = taxcalc.compute(taxcalc.TaxInputs(year=2025, filing_status="single",
                                            business_income_cents=15000000, business_expenses_cents=3000000,
                                            w2_wages_cents=15000000, w2_withholding_cents=3000000,
                                            prior_year_tax_cents=4000000, prior_year_agi_cents=20000000), r)
    assert est["se_base_cents"] == 11082000
    # SS only on the room left under the wage base: 176,100 - 150,000 = 26,100
    assert est["se_tax_cents"] == 323640 + 321378
    assert est["additional_medicare_cents"] == 54738     # (150,000 + 110,820 - 200,000) x 0.9%
    assert est["safe_harbor"]["high_agi"] is True
    assert est["safe_harbor"]["prior_year_target_cents"] == 4400000
    assert est["withholding_cents"] == 3000000
    assert est["balance_federal_cents"] == est["federal_total_cents"] - 3000000
    assert est["taxable_income_cents"] == 22766993            # 266,774.91 AGI - 15,750 std - 23,354.98 QBI
    assert est["marginal_rate_pct"] == 32


def test_state_flat_rate_payments_and_projection():
    r = taxcalc.load_rates(2026)
    est = taxcalc.compute(taxcalc.TaxInputs(year=2026, business_income_cents=3000000, state_flat_rate_bp=500,
                                            payments_federal_cents=100000, payments_state_cents=20000,
                                            payments_by_quarter={1: {"federal": 100000, "state": 20000}},
                                            year_fraction=0.5), r)
    assert est["state_tax_cents"] == round(est["taxable_income_cents"] * 0.05)
    assert est["balance_federal_cents"] == est["federal_total_cents"] - 100000
    assert est["projected_total_tax_cents"] == est["total_tax_cents"] * 2
    assert est["quarters"][0]["federal_paid_cents"] == 100000 and est["quarters"][1]["federal_cumulative_paid_cents"] == 100000


def test_itemized_beats_standard_and_no_negative_taxable():
    r = taxcalc.load_rates(2025)
    est = taxcalc.compute(taxcalc.TaxInputs(year=2025, business_income_cents=1000000, itemized_cents=2000000), r)
    assert est["deduction_kind"] == "itemized" and est["taxable_income_cents"] == 0 and est["federal_income_tax_cents"] == 0
    assert est["se_tax_cents"] > 0


def test_rates_fallback_to_newest_file():
    r = taxcalc.load_rates(2031)
    assert r["_fallback"] is True and r["year"] >= 2026
