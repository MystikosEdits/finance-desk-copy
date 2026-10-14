"""The tax estimate. Pure: TaxInputs in, a dict out, rates from rates/<year>.json.
No database, no clock. Everything here is an ESTIMATE for a self-employed
filer on a personal 1040 with a Schedule C, and the UI says so next to every
number. The rates files carry `verified: false` until the accountant has
looked at them.

What is modelled
  net profit        = business income - deductible business expenses - home office - mileage
  SE tax            = net profit x 92.35% x (12.4% up to the wage base net of W-2 wages + 2.9%)
  half-SE deduction = half of that, above the line
  QBI               = 20% of (net profit - half SE), capped at 20% of taxable income before QBI
  AGI               = W-2 wages + other income + net profit - half SE
  taxable           = AGI - max(standard, itemized) - QBI
  federal           = brackets over taxable  (+ 0.9% additional Medicare over the threshold)
  state             = a flat rate over taxable income, from tax_years.state_flat_rate_bp
  set-aside         = liability so far - withholding - estimated payments made

What is NOT modelled, on purpose: credits, capital gains rates, AMT, NIIT,
QBI phase-outs, state brackets, depreciation schedules. Those are the
accountant's, and the export gives them what they need.
"""

import io
import json
import os
from dataclasses import dataclass, field

import config


def load_rates(year):
    path = os.path.join(config.RATES_DIR, "%d.json" % year)
    if not os.path.exists(path):
        # Fall back to the newest file we have, and say so.
        files = sorted(f for f in os.listdir(config.RATES_DIR) if f.endswith(".json"))
        if not files:
            raise FileNotFoundError("no rates files")
        path = os.path.join(config.RATES_DIR, files[-1])
    with io.open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    r["_fallback"] = r["year"] != year
    return r


@dataclass
class TaxInputs:
    year: int
    filing_status: str = "single"
    business_income_cents: int = 0          # Schedule C line 1 (gross receipts)
    business_expenses_cents: int = 0        # deductible total across lines (positive number)
    home_office_cents: int = 0
    mileage_cents: int = 0
    w2_wages_cents: int = 0
    w2_withholding_cents: int = 0
    other_income_cents: int = 0
    itemized_cents: int | None = None
    state_flat_rate_bp: int = 0
    prior_year_tax_cents: int = 0
    prior_year_agi_cents: int = 0
    payments_federal_cents: int = 0
    payments_state_cents: int = 0
    payments_by_quarter: dict = field(default_factory=dict)   # {q: {"federal": c, "state": c}}
    year_fraction: float = 1.0              # how much of the year the inputs cover (for the projection)


def _bracket_tax(taxable, brackets):
    tax = 0
    lower = 0
    for cap, pct in brackets:
        upper = taxable if cap is None else min(taxable, cap)
        if upper > lower:
            tax += (upper - lower) * pct / 100
        if cap is None or taxable <= cap:
            break
        lower = cap
    return int(round(tax))


def compute(i: TaxInputs, rates=None):
    r = rates or load_rates(i.year)
    fs = i.filing_status if i.filing_status in r["brackets"] else "single"
    se = r["se"]

    net_profit = i.business_income_cents - i.business_expenses_cents - i.home_office_cents - i.mileage_cents
    se_base = int(round(max(net_profit, 0) * se["net_earnings_factor"]))
    ss_room = max(se["social_security_wage_base_cents"] - i.w2_wages_cents, 0)
    ss_tax = int(round(min(se_base, ss_room) * se["social_security_rate"]))
    medicare_tax = int(round(se_base * se["medicare_rate"]))
    se_tax = ss_tax + medicare_tax
    half_se = se_tax // 2

    agi = i.w2_wages_cents + i.other_income_cents + net_profit - half_se
    std = r["standard_deduction_cents"][fs]
    deduction = max(std, i.itemized_cents or 0)
    taxable_before_qbi = max(agi - deduction, 0)
    qbi_income = max(net_profit - half_se, 0)
    qbi = int(round(min(qbi_income * r["qbi"]["rate"], taxable_before_qbi * r["qbi"]["rate"])))
    taxable = max(taxable_before_qbi - qbi, 0)
    federal_income_tax = _bracket_tax(taxable, r["brackets"][fs])

    addl_threshold = se["additional_medicare_threshold_cents"][fs]
    addl_base = max(i.w2_wages_cents + se_base - addl_threshold, 0)
    additional_medicare = int(round(addl_base * se["additional_medicare_rate"]))

    state_tax = int(round(taxable * i.state_flat_rate_bp / 10000)) if i.state_flat_rate_bp else 0

    federal_total = federal_income_tax + se_tax + additional_medicare
    total = federal_total + state_tax
    federal_after_withholding = federal_total - i.w2_withholding_cents
    balance_federal = federal_after_withholding - i.payments_federal_cents
    balance_state = state_tax - i.payments_state_cents

    # Safe harbor: the smaller of 100/110% of last year's tax and 90% of this year's.
    sh = r["safe_harbor"]
    high = i.prior_year_agi_cents > sh["high_agi_threshold_cents"][fs]
    prior_pct = sh["prior_year_pct_high_agi"] if high else sh["prior_year_pct"]
    prior_target = int(round(i.prior_year_tax_cents * prior_pct / 100)) if i.prior_year_tax_cents else None
    current_target = int(round(federal_total * sh["current_year_pct"] / 100))
    annual_target = min(x for x in (prior_target, current_target) if x is not None)
    required_after_withholding = max(annual_target - i.w2_withholding_cents, 0)

    quarters = []
    cum_paid = 0
    for q in r["quarters"]:
        paid = i.payments_by_quarter.get(q["q"], {}) if i.payments_by_quarter else {}
        fed_paid = paid.get("federal", 0)
        cum_paid += fed_paid
        quarters.append({
            "q": q["q"], "due": q["due"], "covers_through": q["covers_through"],
            "federal_target_cents": required_after_withholding // 4,
            "federal_cumulative_target_cents": required_after_withholding * q["q"] // 4,
            "federal_paid_cents": fed_paid, "state_paid_cents": paid.get("state", 0),
            "federal_cumulative_paid_cents": cum_paid,
        })

    projected_total = int(round(total / i.year_fraction)) if 0 < i.year_fraction < 1 else total
    effective = (total / (agi + half_se)) if (agi + half_se) > 0 else 0.0
    marginal = next((pct for cap, pct in r["brackets"][fs] if cap is None or taxable <= cap), 37)

    return {
        "year": i.year, "filing_status": fs, "rates_version": r.get("version"), "rates_verified": bool(r.get("verified")),
        "rates_fallback": r.get("_fallback", False),
        "net_profit_cents": net_profit,
        "business_income_cents": i.business_income_cents, "business_expenses_cents": i.business_expenses_cents,
        "home_office_cents": i.home_office_cents, "mileage_cents": i.mileage_cents,
        "se_base_cents": se_base, "se_tax_cents": se_tax, "half_se_deduction_cents": half_se,
        "agi_cents": agi, "deduction_cents": deduction, "deduction_kind": "itemized" if (i.itemized_cents or 0) > std else "standard",
        "qbi_deduction_cents": qbi, "taxable_income_cents": taxable,
        "federal_income_tax_cents": federal_income_tax, "additional_medicare_cents": additional_medicare,
        "federal_total_cents": federal_total, "state_tax_cents": state_tax, "total_tax_cents": total,
        "withholding_cents": i.w2_withholding_cents,
        "payments_federal_cents": i.payments_federal_cents, "payments_state_cents": i.payments_state_cents,
        "balance_federal_cents": balance_federal, "balance_state_cents": balance_state,
        "set_aside_cents": max(balance_federal, 0) + max(balance_state, 0),
        "effective_rate": round(effective, 4), "marginal_rate_pct": marginal,
        "safe_harbor": {"prior_year_target_cents": prior_target, "current_year_target_cents": current_target,
                        "annual_target_cents": annual_target, "required_after_withholding_cents": required_after_withholding,
                        "high_agi": high},
        "quarters": quarters,
        "year_fraction": i.year_fraction, "projected_total_tax_cents": projected_total,
    }
