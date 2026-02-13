# Tax Strategy Engine — CPA Tax Plan

**Session ID:** tax-2026-02-13-tax-strategy-001
**Date:** 2026-02-13
**Status:** Planning
**Tax Year:** 2024 (with multi-year forward-looking analysis)

**Participants:**
- Tax Expert CPA (lead)
- Tax Planner (strategy design and validation)
- Python Engineer (primary implementor)
- Accountant (numerical validation and reconciliation sign-off)

**Scope:**
- Implement the Tax Strategy Engine (`app/engines/strategy.py`) -- analyzes a taxpayer's current financial position and generates actionable, quantified recommendations to reduce current-year and future tax liability.
- The CLI command `taxbot strategy 2024` will load all imported data (W-2s, lots, sales, sale results, equity events), run the existing tax estimator as a baseline, then evaluate each strategy by running "what-if" scenarios through the estimator with modified parameters.
- Each recommendation includes: strategy name, current situation analysis, mechanism of action, quantified dollar impact (computed via estimator delta), specific action steps, deadline, risk level, and California-specific implications.
- Definition of "done": A user can run `taxbot strategy 2024` and receive a prioritized list of strategy recommendations, each with a quantified estimated tax savings, action steps, and deadline. The output is consumable both on the CLI and as a `StrategyReport` model for the report engine.

---

## Tax Analysis Overview

### Taxpayer Profile Assumptions

This engine is designed for a California-resident W-2 employee with the following characteristics:
- High W-2 income (~$400k-$700k+ from tech employer)
- Equity compensation: RSUs (primary), possibly ESPP and/or ISOs
- Brokerage accounts at Morgan Stanley Shareworks and/or Robinhood
- Filing status: typically Single or MFJ
- Federal marginal rate: 35% or 37%
- California marginal rate: 9.3% to 12.3% (plus 1% Mental Health Services Tax if income > $1M)
- Combined marginal rate on ordinary income: ~44% to ~50%
- Subject to NIIT (3.8%) on investment income above AGI threshold
- May be subject to AMT if exercising ISOs

### Effective Tax Rate Context

For a California tech worker with $600k W-2 income (Single filer, 2024):
- Federal marginal rate on next dollar of ordinary income: 35% (income is in the $243,725-$609,350 bracket)
- Federal LTCG rate: 15% (income is in the $47,025-$518,900 LTCG bracket) or 20% if total taxable income exceeds $518,900
- NIIT: 3.8% applies (AGI > $200,000 threshold)
- California marginal rate: 9.3% (income is in the $68,350-$349,137 bracket) to 12.3% (above $698,271)
- Combined ordinary rate: ~44.3% to ~49.3%
- Combined LTCG rate: ~28.1% to ~33.1% (federal 15-20% + NIIT 3.8% + CA 9.3-12.3%)
- Ordinary-to-LTCG rate differential: ~16.2% -- this is the value of converting income from ordinary to long-term capital gains

This rate differential drives many of the strategies below.

---

## Section 1: Strategy Categories and Decision Logic

### Category A: Current-Year Tax Reduction Strategies

---

#### A.1 Tax-Loss Harvesting (Capital Loss Harvesting)

**IRS Authority:** IRC Section 1211(b) (capital loss deduction limit), IRC Section 1212(b) (capital loss carryforward), IRC Section 1091 (wash sale rule), IRS Publication 550 (Investment Income and Expenses).

**Mechanism:** Realize unrealized losses in open positions to offset realized capital gains. Net capital losses up to $3,000 ($1,500 MFS) can offset ordinary income. Excess carries forward indefinitely.

**Data Requirements:**
- `repo.get_lots()` -- all open lots with `shares_remaining > 0`
- `repo.get_sale_results(tax_year)` -- realized gains/losses for the year
- Current market prices for open positions (user input or API)
- `repo.get_sales(tax_year)` -- to check for wash sale risk (sales in last 30 days)

**Algorithm:**

```
FUNCTION analyze_tax_loss_harvesting(lots, sale_results, current_prices, tax_year):
    # Step 1: Compute current realized gain/loss position
    realized_st_gains = SUM(sr.gain_loss) WHERE sr.holding_period == SHORT_TERM
    realized_lt_gains = SUM(sr.gain_loss) WHERE sr.holding_period == LONG_TERM
    net_realized = realized_st_gains + realized_lt_gains

    # Step 2: Identify lots with unrealized losses
    harvesting_candidates = []
    FOR lot IN lots WHERE lot.shares_remaining > 0:
        current_price = current_prices[lot.security.ticker]
        unrealized_gain_loss = (current_price - lot.cost_per_share) * lot.shares_remaining
        holding = determine_holding_period(lot.acquisition_date, today)

        IF unrealized_gain_loss < 0:
            harvesting_candidates.append({
                lot: lot,
                unrealized_loss: unrealized_gain_loss,
                holding_period: holding,
                tax_savings: compute_tax_savings(unrealized_gain_loss, holding, realized_st_gains, realized_lt_gains),
                wash_sale_risk: check_wash_sale_risk(lot, sales, tax_year),
            })

    # Step 3: Prioritize candidates
    # Priority 1: Short-term losses that offset short-term gains (saves at ordinary rates ~44%)
    # Priority 2: Long-term losses that offset long-term gains (saves at LTCG rates ~28%)
    # Priority 3: Losses that create/increase the $3,000 ordinary income offset
    SORT harvesting_candidates BY tax_savings DESC

    # Step 4: Compute incremental benefit via estimator
    FOR candidate IN harvesting_candidates:
        baseline_estimate = estimator.estimate(... current parameters ...)
        harvested_estimate = estimator.estimate(... with candidate loss realized ...)
        candidate.dollar_impact = baseline_estimate.total_tax - harvested_estimate.total_tax

    # Step 5: Check wash sale 30-day window
    FOR candidate IN harvesting_candidates:
        IF any sale of substantially identical security within 30 days before or after:
            candidate.wash_sale_warning = True

    RETURN harvesting_candidates
```

**Tax Savings Computation:**

```
FUNCTION compute_tax_savings(loss, holding, existing_st_gains, existing_lt_gains):
    # Losses offset gains in this order (IRC Section 1211):
    # 1. ST losses offset ST gains first
    # 2. LT losses offset LT gains first
    # 3. Net ST losses offset LT gains (or vice versa)
    # 4. Net overall losses up to $3,000 offset ordinary income

    IF holding == SHORT_TERM:
        # ST loss offsets ST gains at ordinary rates
        offset_at_ordinary = min(abs(loss), max(existing_st_gains, 0))
        remaining = abs(loss) - offset_at_ordinary
        # Remaining offsets LT gains at LTCG rates
        offset_at_ltcg = min(remaining, max(existing_lt_gains, 0))
        remaining -= offset_at_ltcg
        # Remaining up to $3,000 offsets ordinary income
        offset_ordinary_income = min(remaining, max(3000 - existing_loss_deduction, 0))
        savings = (
            offset_at_ordinary * combined_ordinary_rate
            + offset_at_ltcg * combined_ltcg_rate
            + offset_ordinary_income * combined_ordinary_rate
        )
    ELIF holding == LONG_TERM:
        # LT loss offsets LT gains first at LTCG rates
        offset_at_ltcg = min(abs(loss), max(existing_lt_gains, 0))
        remaining = abs(loss) - offset_at_ltcg
        # Remaining offsets ST gains at ordinary rates
        offset_at_ordinary = min(remaining, max(existing_st_gains, 0))
        remaining -= offset_at_ordinary
        offset_ordinary_income = min(remaining, max(3000 - existing_loss_deduction, 0))
        savings = (
            offset_at_ltcg * combined_ltcg_rate
            + offset_at_ordinary * combined_ordinary_rate
            + offset_ordinary_income * combined_ordinary_rate
        )

    RETURN savings
```

**Wash Sale Rule (IRC Section 1091):**
- Cannot purchase "substantially identical" securities within 30 days before or after the sale at a loss.
- If violated, the loss is disallowed and added to the basis of the replacement shares.
- The engine must scan `repo.get_sales()` and `repo.get_events()` for purchases of the same ticker within the 61-day window (30 days before through 30 days after).

**Edge Cases:**
- RSU vests within 30 days of a loss sale of the same stock trigger wash sale rules. Per IRS Notice 2008-1 and consistent IRS guidance, RSU vest is an acquisition of substantially identical stock.
- ESPP purchases within 30 days of a loss sale of the same stock also trigger wash sale rules.
- Losses in one account (e.g., Robinhood) can be used to offset gains in another (e.g., Shareworks).
- If net capital losses already exceed $3,000, additional harvesting only creates carryforward -- lower priority but still valuable.

**Example Scenario:**

```
Input:
  - Realized ST gains from RSU sales: $50,000
  - Realized LT gains from RSU sales: $20,000
  - Open lot: 100 shares COIN, acquired 2024-03-15 at $250/share, current price $180/share
  - Unrealized loss: (180 - 250) * 100 = -$7,000 (short-term)
  - Filing status: Single, W-2 wages: $600,000

Analysis:
  - $7,000 ST loss offsets $7,000 of the $50,000 ST gains
  - ST gains are taxed at ordinary rates: federal 35% + CA 9.3% + NIIT 3.8% = 48.1%
  - Tax savings: $7,000 * 0.481 = $3,367

Recommendation:
  Name: "Tax-Loss Harvest COIN Position"
  Situation: "You have $7,000 unrealized short-term loss on 100 shares of COIN"
  Mechanism: "Sell to realize the loss, offsetting $7,000 of short-term capital gains"
  Quantified Impact: "Estimated tax savings: $3,367"
  Action Steps:
    1. "Sell 100 shares of COIN before Dec 31, 2024"
    2. "Wait 31 days before repurchasing to avoid wash sale"
    3. "Consider buying a correlated but not identical ETF during the waiting period"
  Deadline: "December 31, 2024 (must settle by year-end)"
  Risk Level: "Low"
  California Impact: "CA treats capital gains as ordinary income; savings include CA tax reduction"
```

---

#### A.2 Retirement Contribution Optimization

**IRS Authority:** IRC Section 401(k) (elective deferrals), IRC Section 219 (IRA deductions), IRC Section 408A (Roth IRA), IRC Section 402A (designated Roth contributions), IRS Publication 590-A (Contributions to IRAs), IRS Notice 2023-75 (2024 contribution limits).

**Mechanism:** Maximize pre-tax contributions to reduce taxable income. Each dollar contributed at the marginal rate generates immediate tax savings.

**Data Requirements:**
- `repo.get_w2s(tax_year)` -- W-2 Box 12 code D (401k contributions), code E (403b), code AA (Roth 401k)
- User input: current 401k contribution amount, employer match details, IRA contributions made
- User input: age (for catch-up contribution eligibility, 50+)

**2024 Contribution Limits (per IRS Notice 2023-75):**

| Account | Under 50 | Age 50+ |
|---|---|---|
| 401(k) employee elective deferrals | $23,000 | $30,500 |
| IRA (Traditional + Roth combined) | $7,000 | $8,000 |
| HSA (Self-only) | $4,150 | $5,150 |
| HSA (Family) | $8,300 | $9,300 |

**2025 Contribution Limits (per IRS Notice 2024-80):**

| Account | Under 50 | Age 50+ | Age 60-63 |
|---|---|---|---|
| 401(k) employee elective deferrals | $23,500 | $31,000 | $34,750 |
| IRA (Traditional + Roth combined) | $7,000 | $8,000 | $8,000 |
| HSA (Self-only) | $4,300 | $5,300 | $5,300 |
| HSA (Family) | $8,550 | $9,550 | $9,550 |

**Note:** SECURE 2.0 Act introduced enhanced catch-up for ages 60-63 starting in 2025 (IRC Section 414(v)(2)(E)).

**Algorithm:**

```
FUNCTION analyze_retirement_contributions(w2s, tax_year, user_inputs):
    # Step 1: Determine current 401k contributions from W-2 Box 12
    current_401k = SUM(w2.box12_codes.get("D", 0)) for all W-2s
    current_roth_401k = SUM(w2.box12_codes.get("AA", 0)) for all W-2s

    # Step 2: Compute remaining 401k room
    age = user_inputs.age
    limit_401k = 23000 if age < 50 else 30500  # 2024
    remaining_401k = max(limit_401k - current_401k - current_roth_401k, 0)

    # Step 3: Compute tax impact of maximizing 401k
    IF remaining_401k > 0:
        baseline = estimator.estimate(w2_wages=current_wages, ...)
        reduced_wages = current_wages - remaining_401k
        optimized = estimator.estimate(w2_wages=reduced_wages, ...)
        tax_savings = baseline.total_tax - optimized.total_tax

        recommendation = StrategyRecommendation(
            name="Maximize 401(k) Contributions",
            situation=f"Current 401(k) contributions: ${current_401k}. Limit: ${limit_401k}. Room: ${remaining_401k}.",
            mechanism="Pre-tax 401(k) contributions reduce taxable income dollar-for-dollar at your marginal rate.",
            quantified_impact=f"Estimated tax savings: ${tax_savings} ({remaining_401k} * ~{marginal_rate}%)",
            action_steps=[
                f"Increase 401(k) contribution rate to reach ${limit_401k} by year-end",
                "Contact HR/payroll to adjust contribution percentage",
                "If employer offers mega backdoor Roth, consider after-tax contributions up to the $69,000 total limit (2024 IRC Section 415(c))"
            ],
            deadline=f"Last paycheck of {tax_year} (typically mid-December)",
            risk_level="Low",
            california_impact="CA follows federal 401(k) treatment. Full state tax reduction."
        )

    # Step 4: IRA analysis
    # For high-income taxpayers (AGI > $87,000 Single / $143,000 MFJ in 2024),
    # Traditional IRA contributions are NOT deductible if covered by employer plan
    # Per IRC Section 219(g) and IRS Pub. 590-A
    IF agi > traditional_ira_phaseout:
        recommendation_ira = "Traditional IRA deduction phased out at your income level. Consider backdoor Roth IRA instead."
    ELSE:
        # Compute IRA tax savings
        ira_savings = remaining_ira_room * marginal_rate

    # Step 5: Backdoor Roth IRA analysis
    # Per IRC Section 408A(c)(3)(B), Roth IRA income limits for 2024:
    #   Single: phase-out $146,000-$161,000
    #   MFJ: phase-out $230,000-$240,000
    # Backdoor Roth: contribute to Traditional IRA (non-deductible), immediately convert to Roth
    # Per IRS guidance and Tax Court case Bobrow v. Commissioner (2014)
    IF agi > roth_ira_phaseout:
        recommendation_roth = StrategyRecommendation(
            name="Backdoor Roth IRA",
            situation="Income exceeds Roth IRA contribution limits",
            mechanism="Contribute to non-deductible Traditional IRA, then convert to Roth. No current tax deduction, but future growth is tax-free.",
            quantified_impact="No current-year tax savings, but tax-free growth on $7,000 ($8,000 if 50+)",
            action_steps=[
                "Verify no existing Traditional IRA balance (to avoid pro-rata rule per IRC Section 408(d)(2))",
                "Contribute $7,000 to Traditional IRA (non-deductible)",
                "Convert to Roth IRA immediately",
                "File Form 8606 to report non-deductible contribution"
            ],
            deadline="April 15 of the following year (IRA contribution deadline)",
            risk_level="Low",
            california_impact="CA conforms to federal Roth IRA treatment"
        )

    RETURN recommendations
```

**Mega Backdoor Roth (IRC Section 415(c)):**
- Total 401(k) contributions (employee + employer) limit: $69,000 (2024) / $70,000 (2025)
- If employer plan allows after-tax contributions and in-plan Roth conversions, the taxpayer can contribute up to $69,000 - employee_elective - employer_match in after-tax contributions and convert to Roth.
- This is plan-specific -- the engine should flag this as a "check with your plan administrator" recommendation.

**Example Scenario:**

```
Input:
  - W-2 wages: $600,000
  - Current 401k contributions (Box 12, code D): $15,000
  - Age: 35
  - Filing status: Single

Analysis:
  - 401k limit: $23,000
  - Remaining room: $23,000 - $15,000 = $8,000
  - Marginal federal rate: 35%
  - Marginal CA rate: 9.3%
  - Combined marginal rate: 44.3%
  - Tax savings: $8,000 * 0.443 = $3,544

Recommendation:
  "Increase 401(k) contributions by $8,000 to reach the $23,000 limit.
   Estimated tax savings: $3,544."
```

---

#### A.3 HSA Maximization

**IRS Authority:** IRC Section 223, IRS Publication 969 (Health Savings Accounts), IRS Rev. Proc. 2023-34 (2024 limits).

**Mechanism:** HSA contributions are triple-tax-advantaged: deductible when contributed, grow tax-free, and withdrawals for medical expenses are tax-free. For high-income earners, the HSA functions as an additional retirement account.

**Data Requirements:**
- User input: whether enrolled in HDHP (high-deductible health plan)
- User input: current HSA contributions (may be in W-2 Box 12, code W)
- User input: self-only or family coverage

**Algorithm:**

```
FUNCTION analyze_hsa(w2s, user_inputs):
    IF NOT user_inputs.has_hdhp:
        RETURN None  # Not eligible

    current_hsa = SUM(w2.box12_codes.get("W", 0)) for all W-2s
    limit = 4150 if user_inputs.coverage == "self" else 8300  # 2024
    IF user_inputs.age >= 55:
        limit += 1000  # Catch-up contribution

    remaining = max(limit - current_hsa, 0)
    IF remaining > 0:
        # HSA contributions reduce FICA (if via payroll) AND income tax
        # If contributed directly (not via payroll), only income tax benefit
        tax_savings = remaining * combined_marginal_rate

        RETURN StrategyRecommendation(
            name="Maximize HSA Contributions",
            quantified_impact=f"Estimated tax savings: ${tax_savings}",
            action_steps=[
                f"Contribute additional ${remaining} to HSA before Dec 31",
                "Payroll contributions save FICA (7.65%); direct contributions do not",
                "Keep receipts for medical expenses -- withdraw tax-free anytime"
            ],
            deadline=f"April 15, {tax_year + 1} (but payroll contributions must be by Dec 31)",
            risk_level="Low",
            california_impact="CALIFORNIA DOES NOT CONFORM to federal HSA treatment. "
                            "CA taxes HSA contributions as income and taxes HSA earnings annually. "
                            "Per CA R&TC Section 17215. Federal savings still apply."
        )
```

**California HSA Warning:** Per CA Revenue and Taxation Code Section 17215, California does NOT recognize HSA tax benefits. Contributions are not deductible for CA purposes, and HSA earnings are taxable to CA annually. The strategy engine must clearly flag this -- the federal savings are real, but the CA savings are $0.

---

#### A.4 Charitable Giving Optimization (Bunching Strategy)

**IRS Authority:** IRC Section 170 (charitable deductions), IRC Section 4966 (donor-advised funds), IRS Publication 526 (Charitable Contributions).

**Mechanism:** For taxpayers near the standard/itemized deduction threshold, "bunching" multiple years of charitable donations into a single year can push itemized deductions above the standard deduction, creating tax savings that would not exist with level annual giving.

**Data Requirements:**
- User input: annual charitable giving amount
- User input: state and local taxes paid (for SALT deduction, capped at $10,000 per IRC Section 164(b)(6))
- User input: mortgage interest (if any)
- `estimator.estimate()` -- to compare standard vs. itemized scenarios

**SALT Cap Consideration (IRC Section 164(b)(6)):** The $10,000 SALT cap (enacted by TCJA) means many high-income California taxpayers cannot itemize because their CA state tax alone exceeds $10,000 but is capped. This makes bunching more relevant.

**Algorithm:**

```
FUNCTION analyze_charitable_bunching(tax_estimate, user_inputs):
    # Step 1: Compute current itemized deductions
    salt_paid = min(user_inputs.state_local_taxes, 10000)  # SALT cap
    mortgage_interest = user_inputs.mortgage_interest or 0
    charitable = user_inputs.charitable_giving or 0
    other_itemized = user_inputs.other_itemized or 0

    total_itemized = salt_paid + mortgage_interest + charitable + other_itemized
    standard_deduction = FEDERAL_STANDARD_DEDUCTION[tax_year][filing_status]

    # Step 2: Check if already itemizing
    IF total_itemized > standard_deduction:
        # Already itemizing -- additional charitable gifts save at marginal rate
        incremental_savings = additional_charitable * marginal_rate
    ELSE:
        # Currently taking standard deduction
        # Bunching strategy: donate 2-3 years of charitable gifts this year
        bunched_charitable = charitable * 3  # e.g., bunch 3 years
        bunched_itemized = salt_paid + mortgage_interest + bunched_charitable + other_itemized

        IF bunched_itemized > standard_deduction:
            # Bunching creates itemized excess
            excess = bunched_itemized - standard_deduction
            tax_savings = excess * combined_marginal_rate
            # Compare to: 3 years of standard deduction (no charitable benefit)
            # vs: 1 year itemized + 2 years standard deduction

            recommendation = StrategyRecommendation(
                name="Charitable Giving Bunching Strategy",
                mechanism="Donate 2-3 years of planned gifts in one year via a donor-advised fund (DAF). "
                         "Itemize this year, take standard deduction in off years.",
                quantified_impact=f"Estimated additional deduction vs. level giving: ${excess}. "
                                 f"Tax savings: ${tax_savings} over 3-year cycle.",
                action_steps=[
                    f"Open a donor-advised fund (DAF) at Fidelity, Schwab, or Vanguard",
                    f"Contribute ${bunched_charitable} to DAF before Dec 31 (gets full deduction this year)",
                    "Distribute grants from DAF to charities over the next 2-3 years",
                    "Take standard deduction in years 2 and 3"
                ],
                deadline="December 31 of the bunching year",
                risk_level="Low",
                california_impact="CA conforms to federal charitable deduction rules. "
                                "CA standard deduction is much lower ($5,540 Single) so bunching "
                                "may already push you to itemize for CA even without bunching for federal."
            )

    # Step 3: Appreciated stock donation
    # Per IRC Section 170(e)(1)(A): Donating appreciated LTCG property to a
    # public charity allows deduction at FMV without recognizing the gain.
    # Maximum: 30% of AGI for appreciated property (vs 60% for cash)
    FOR lot IN lots WHERE lot.shares_remaining > 0:
        unrealized_gain = (current_price - lot.cost_per_share) * lot.shares_remaining
        holding = determine_holding_period(lot.acquisition_date, today)
        IF unrealized_gain > 0 AND holding == LONG_TERM:
            # Donating avoids capital gains tax on the appreciation
            avoided_tax = unrealized_gain * combined_ltcg_rate
            deduction_value = current_price * lot.shares_remaining * combined_marginal_rate
            total_benefit = avoided_tax + deduction_value

    RETURN recommendations
```

**Example Scenario:**

```
Input:
  - Filing status: Single
  - Standard deduction: $14,600 (2024)
  - SALT paid: $10,000 (capped)
  - Mortgage interest: $0
  - Annual charitable giving: $5,000
  - Total itemized without bunching: $15,000 (barely above standard deduction)
  - 3-year bunched charitable: $15,000
  - Total itemized with bunching: $25,000

Analysis:
  Without bunching (3 years):
    Year 1: itemized $15,000, excess over standard: $400. Tax savings: $400 * 0.443 = $177
    Year 2: itemized $15,000, excess over standard: $400. Tax savings: $177
    Year 3: itemized $15,000, excess over standard: $400. Tax savings: $177
    Total 3-year savings: $531

  With bunching:
    Year 1: itemized $25,000, excess over standard: $10,400. Tax savings: $10,400 * 0.443 = $4,607
    Year 2: standard deduction $14,600. Tax savings: $0 from charity
    Year 3: standard deduction $14,600. Tax savings: $0 from charity
    Total 3-year savings: $4,607

  Net benefit of bunching: $4,607 - $531 = $4,076 over 3 years
```

---

#### A.5 SALT Deduction Optimization

**IRS Authority:** IRC Section 164(b)(6) (SALT cap), IRS Publication 5307 (Tax Reform Basics).

**Mechanism:** The SALT deduction is capped at $10,000 ($5,000 MFS). For California high-income earners, CA state income tax alone far exceeds this cap. The strategy engine should quantify the "wasted" SALT and consider it in other strategies.

**Data Requirements:**
- `repo.get_w2s(tax_year)` -- W-2 Box 17 (CA withholding)
- Tax estimate: `ca_total_tax` for the year
- User input: property taxes paid, other state/local taxes

**Algorithm:**

```
FUNCTION analyze_salt(tax_estimate, user_inputs):
    ca_income_tax = tax_estimate.ca_total_tax
    property_tax = user_inputs.property_tax or 0
    total_salt = ca_income_tax + property_tax

    salt_cap = 10000 if filing_status != MFS else 5000
    wasted_salt = max(total_salt - salt_cap, 0)

    IF wasted_salt > 0:
        # The wasted SALT is a key factor in:
        # 1. Whether to itemize vs standard deduction
        # 2. Whether charitable bunching helps
        # 3. AMT analysis (SALT add-back for itemizers)

        recommendation = StrategyRecommendation(
            name="SALT Cap Analysis",
            situation=f"Total state/local taxes: ${total_salt}. SALT cap: ${salt_cap}. "
                     f"Excess not deductible: ${wasted_salt}.",
            mechanism="The $10,000 SALT cap limits your state tax deduction. This affects "
                     "whether itemizing is beneficial and how other strategies interact.",
            quantified_impact=f"${wasted_salt} in state/local taxes provides no federal tax benefit. "
                             f"Lost deduction value: ${wasted_salt * federal_marginal_rate}.",
            action_steps=[
                "This is informational -- the SALT cap cannot be avoided for W-2 employees",
                "Consider this when evaluating charitable bunching and other itemized deductions",
                "If self-employed income exists, consider SALT workarounds (PTE elections) -- not applicable for W-2 only"
            ],
            risk_level="Low (informational)",
            california_impact="CA does not have a SALT cap for CA return. Full CA tax is deductible on CA Schedule CA if itemizing."
        )

    RETURN recommendation
```

---

### Category B: Equity Compensation Strategies

---

#### B.1 ESPP Holding Period Optimization

**IRS Authority:** IRC Section 423 (ESPPs), IRC Section 422(a)(1) (holding period requirements), IRS Publication 525 (ESPP section), Form 3922 Instructions.

**Mechanism:** ESPP shares have two disposition types with different tax treatment:
- **Qualifying disposition** (held > 2 years from offering date AND > 1 year from purchase date): Ordinary income = lesser of (actual gain, discount at offering date). Remainder is LTCG.
- **Disqualifying disposition** (sold before holding period met): Ordinary income = spread at purchase date (FMV - purchase price). Remainder is capital gain/loss based on holding period.

The engine analyzes when it makes financial sense to hold for qualifying treatment vs. sell immediately.

**Data Requirements:**
- `repo.get_lots()` WHERE equity_type == "ESPP" AND shares_remaining > 0
- `repo.get_events()` WHERE equity_type == "ESPP" -- for offering date and purchase details
- Current market prices for open ESPP lots
- Form 3922 data: offering date FMV, purchase date FMV, purchase price

**Algorithm:**

```
FUNCTION analyze_espp_holding(espp_lots, espp_events, current_prices, form3922s):
    FOR lot IN espp_lots WHERE lot.shares_remaining > 0:
        form3922 = find_matching_form3922(lot)
        current_price = current_prices[lot.security.ticker]

        # Compute tax under disqualifying disposition (sell now)
        disq_ordinary = (form3922.fmv_on_purchase_date - form3922.purchase_price_per_share) * lot.shares_remaining
        disq_capital_gain = (current_price - form3922.fmv_on_purchase_date) * lot.shares_remaining
        disq_holding = determine_holding_period(form3922.purchase_date, today)
        disq_tax = compute_tax_on_income(disq_ordinary, "ordinary") + compute_tax_on_income(disq_capital_gain, disq_holding)

        # Compute tax under qualifying disposition (hold until eligible)
        qualifying_date = max(
            add_years(form3922.offering_date, 2),
            add_years(form3922.purchase_date, 1)
        )
        days_to_qualifying = (qualifying_date - today).days

        IF days_to_qualifying <= 0:
            # Already qualifies -- always better to use qualifying treatment
            qual_ordinary = min(
                current_price - form3922.purchase_price_per_share,  # actual gain
                form3922.fmv_on_offering_date - form3922.purchase_price_per_share  # discount at offering
            ) * lot.shares_remaining
            qual_ordinary = max(qual_ordinary, 0)
            qual_capital = (current_price * lot.shares_remaining) - (form3922.purchase_price_per_share * lot.shares_remaining) - qual_ordinary
            qual_tax = compute_tax_on_income(qual_ordinary, "ordinary") + compute_tax_on_income(qual_capital, "LONG_TERM")
        ELSE:
            # Must estimate future price risk
            # Conservative: assume price stays flat
            future_price = current_price
            qual_ordinary = min(
                future_price - form3922.purchase_price_per_share,
                form3922.fmv_on_offering_date - form3922.purchase_price_per_share
            ) * lot.shares_remaining
            qual_ordinary = max(qual_ordinary, 0)
            qual_capital = (future_price * lot.shares_remaining) - (form3922.purchase_price_per_share * lot.shares_remaining) - qual_ordinary
            qual_tax = compute_tax_on_income(qual_ordinary, "ordinary") + compute_tax_on_income(qual_capital, "LONG_TERM")

        tax_savings = disq_tax - qual_tax

        # Risk analysis: price decline risk during holding period
        # If stock declines below purchase price, qualifying/disqualifying distinction is moot
        at_risk_amount = current_price * lot.shares_remaining
        breakeven_decline = tax_savings / lot.shares_remaining  # price decline that wipes out tax savings

        recommendation = StrategyRecommendation(
            name=f"ESPP Holding Period Analysis: {lot.security.ticker}",
            situation=f"ESPP shares purchased {form3922.purchase_date}. "
                     f"Qualifying date: {qualifying_date}. Days remaining: {max(days_to_qualifying, 0)}.",
            mechanism="Holding for qualifying disposition converts ordinary income to LTCG, "
                     "reducing the tax rate on the ESPP discount.",
            quantified_impact=f"Estimated tax savings from holding: ${tax_savings}. "
                             f"Breakeven: stock can decline ${breakeven_decline}/share before tax savings are eliminated.",
            action_steps=generate_espp_action_steps(days_to_qualifying, tax_savings, at_risk_amount),
            deadline=str(qualifying_date),
            risk_level="Moderate" if days_to_qualifying > 180 else "Low",
            california_impact="CA taxes all gains as ordinary income regardless, "
                            "so qualifying treatment saves federal tax only. "
                            "The CA benefit is limited to the reduced ordinary income amount."
        )
```

**Key Decision Rule:** The holding period analysis should recommend selling immediately (disqualifying) when:
1. The stock is highly concentrated (> 10% of net worth in one stock)
2. The tax savings from holding is small relative to the price risk
3. The stock has declined below the purchase price (no ordinary income in either case)
4. The remaining holding period is long (> 12 months) and the stock is volatile

**Example Scenario:**

```
Input:
  - ESPP purchase: 100 shares at $200/share (purchase price with 15% discount)
  - FMV at purchase: $235/share
  - FMV at offering: $220/share
  - Current price: $250/share
  - Purchase date: 2024-03-15
  - Offering date: 2023-09-15
  - Qualifying date: 2025-09-15 (2 years from offering)
  - Today: 2024-12-15 (9 months remaining)
  - Filing status: Single, W-2: $600,000

Disqualifying (sell now):
  - Ordinary income: ($235 - $200) * 100 = $3,500
  - Capital gain: ($250 - $235) * 100 = $1,500 (short-term since < 1 year from purchase)
  - Tax: $3,500 * 0.443 + $1,500 * 0.443 = $2,215 (all ordinary/ST rates)

Qualifying (hold until 2025-09-15, assume price stays at $250):
  - Ordinary income: min($250 - $200, $220 - $200) * 100 = min($50, $20) * 100 = $2,000
  - LTCG: ($250 * 100) - ($200 * 100) - $2,000 = $3,000
  - Tax: $2,000 * 0.443 + $3,000 * 0.281 = $886 + $843 = $1,729

Tax savings from holding: $2,215 - $1,729 = $486
Breakeven decline: $486 / 100 = $4.86/share (stock can drop from $250 to $245.14)

Recommendation: "Moderate benefit ($486) from holding 9 more months. Stock must stay above
$245.14 for the strategy to pay off. Consider concentration risk."
```

---

#### B.2 ISO Exercise Timing and AMT Optimization

**IRS Authority:** IRC Section 421-424 (ISOs), IRC Section 55-59 (AMT), Form 6251 Instructions, Form 3921 Instructions, Form 8801 Instructions (AMT credit).

**Mechanism:** ISO exercises create AMT preference items equal to the spread (FMV - strike price) at exercise. The strategy engine should analyze how many ISOs can be exercised in a given year without triggering AMT (or minimizing AMT), and when to exercise to maximize AMT credit carryforward utilization.

**Data Requirements:**
- `repo.get_events()` WHERE equity_type == "ISO" -- unexercised options (grant date, strike price, expiration date)
- `repo.get_lots()` WHERE equity_type == "ISO" -- already-exercised ISOs
- Current stock price (for computing potential spread)
- Current year baseline tax estimate (to compute AMT headroom)
- Prior year AMT credit carryforward (user input)

**Algorithm:**

```
FUNCTION analyze_iso_exercise(iso_events, lots, tax_estimate, current_prices, user_inputs):
    # Step 1: Compute AMT headroom
    # AMT headroom = how much AMT preference can be added before AMT exceeds regular tax
    # This requires iterative computation via the estimator

    baseline = tax_estimate  # Already computed
    amt_exemption = AMT_EXEMPTION[tax_year][filing_status]
    amt_phaseout_start = AMT_PHASEOUT_START[tax_year][filing_status]

    # Binary search for maximum ISO spread that produces $0 AMT
    low = Decimal("0")
    high = Decimal("500000")  # Upper bound for spread

    WHILE high - low > Decimal("100"):
        mid = (low + high) / 2
        test_estimate = estimator.estimate(
            ... baseline params ...,
            amt_iso_preference=mid
        )
        IF test_estimate.federal_amt > 0:
            high = mid
        ELSE:
            low = mid

    amt_headroom = low  # Maximum spread before AMT kicks in

    # Step 2: Analyze each unexercised ISO grant
    FOR event IN iso_events WHERE event.event_type == "EXERCISE" is not yet done:
        # (In practice, unexercised ISOs would need to be tracked separately.
        #  The current model tracks exercises as events. We need the grants.)
        potential_spread = (current_price - event.strike_price) * event.shares
        IF potential_spread <= 0:
            CONTINUE  # Underwater options -- no benefit to exercising

        # Can this exercise fit within AMT headroom?
        IF potential_spread <= amt_headroom:
            # Exercise triggers NO AMT
            recommendation = "Exercise within AMT headroom -- no additional tax"
        ELSE:
            # Exercise will trigger AMT
            amt_cost = estimator.estimate(... amt_iso_preference=potential_spread ...).federal_amt
            # BUT: AMT paid generates a credit (Form 8801) carried forward
            # The credit offsets future regular tax in years when AMT < regular tax

            recommendation = StrategyRecommendation(
                name=f"ISO Exercise Analysis: {event.security.ticker}",
                situation=f"Unexercised ISOs: {event.shares} shares at ${event.strike_price}. "
                         f"Current FMV: ${current_price}. Potential spread: ${potential_spread}.",
                mechanism="ISO exercise creates AMT preference item. Exercising within AMT headroom "
                         f"(${amt_headroom}) triggers no additional tax. Excess triggers AMT at 26-28%, "
                         "but generates a carryforward credit.",
                quantified_impact=f"AMT cost if exercised: ${amt_cost}. "
                                 f"This generates a ${amt_cost} AMT credit carryforward (Form 8801).",
                action_steps=generate_iso_action_steps(potential_spread, amt_headroom, amt_cost),
                deadline="December 31 (exercise must settle by year-end for current-year treatment)",
                risk_level="High" if amt_cost > 10000 else "Moderate",
                california_impact="California does NOT have AMT (repealed 2005). "
                                "ISO exercise has no CA tax impact until sale. "
                                "At sale, CA treats ISOs like NSOs for state purposes."
            )

    # Step 3: AMT credit carryforward utilization
    prior_amt_credit = user_inputs.amt_credit_carryforward or 0
    IF prior_amt_credit > 0:
        # The AMT credit from prior years can offset regular tax this year
        # Per Form 8801: credit = prior AMT from deferral items (ISO) * (regular_tax - TMT)
        # In practice: if regular_tax > tentative_minimum_tax, the excess is available for credit
        credit_usable = max(
            baseline.federal_regular_tax + baseline.federal_ltcg_tax - tentative_minimum_tax,
            0
        )
        credit_used = min(prior_amt_credit, credit_usable)

        IF credit_used > 0:
            recommendation = StrategyRecommendation(
                name="AMT Credit Carryforward Utilization",
                situation=f"Prior AMT credit carryforward: ${prior_amt_credit}. "
                         f"Usable this year: ${credit_used}.",
                mechanism="AMT credits from prior ISO exercises offset current regular tax. "
                         "Per Form 8801, the credit equals the excess of regular tax over TMT.",
                quantified_impact=f"${credit_used} AMT credit reduces federal tax this year.",
                action_steps=[
                    "File Form 8801 with your return to claim the credit",
                    f"Remaining carryforward: ${prior_amt_credit - credit_used}"
                ],
                risk_level="Low"
            )

    RETURN recommendations
```

**ISO Exercise Optimization Decision Matrix:**

| Scenario | Recommendation | Rationale |
|---|---|---|
| Spread < AMT headroom | Exercise now | No AMT triggered; start long-term holding period clock |
| Spread > AMT headroom, stock expected to rise | Exercise up to headroom | Minimize AMT while capturing some upside |
| Spread > AMT headroom, options expiring soon | Exercise all, accept AMT | AMT credit carries forward; losing options is worse |
| Stock price < strike price | Do not exercise | Underwater options have no value to exercise |
| Within 1 year of ISO expiration | Evaluate urgently | Options expire worthless if not exercised |

**Edge Case -- Same-Day Sale (Disqualifying Disposition):**
If ISO shares are exercised and sold in the same year (disqualifying disposition), the spread becomes ordinary income (not AMT preference). This eliminates AMT but creates ordinary income taxed at the highest rates. The engine should compare:
- Exercise + hold (AMT on spread) vs. Exercise + sell (ordinary income on spread)

---

#### B.3 RSU Tax-Loss Harvesting Coordination

**IRS Authority:** IRC Section 1091 (wash sale), IRS Publication 550.

**Mechanism:** RSU vests create new lots at FMV. If the stock declines after vesting, those lots have unrealized losses that can be harvested. However, upcoming RSU vests create wash sale risk.

**Data Requirements:**
- `repo.get_lots()` WHERE equity_type == "RSU" -- vested RSU lots
- `repo.get_events()` WHERE equity_type == "RSU" AND event_type == "VEST" -- vest schedule
- Current market prices
- Future vest schedule (user input -- upcoming vest dates and estimated share counts)

**Algorithm:**

```
FUNCTION analyze_rsu_harvesting(rsu_lots, rsu_vests, current_prices, future_vests):
    # Step 1: Identify RSU lots with unrealized losses
    loss_lots = []
    FOR lot IN rsu_lots WHERE lot.shares_remaining > 0:
        current_price = current_prices[lot.security.ticker]
        unrealized = (current_price - lot.cost_per_share) * lot.shares_remaining
        IF unrealized < 0:
            loss_lots.append(lot)

    # Step 2: Check for upcoming vests (wash sale risk)
    FOR lot IN loss_lots:
        upcoming_vest_dates = [v.date for v in future_vests
                               WHERE v.ticker == lot.security.ticker
                               AND v.date >= today
                               AND v.date <= today + 30 days]

        IF upcoming_vest_dates:
            lot.wash_sale_risk = True
            lot.warning = f"RSU vest on {upcoming_vest_dates[0]} will trigger wash sale. "
                         f"Wait until after the vest to harvest this loss, or harvest >30 days before vest."
        ELSE:
            lot.wash_sale_risk = False

    # Step 3: Sell-to-cover vs. hold analysis for new RSU vests
    # When RSUs vest, the default is "sell to cover" taxes.
    # Alternative: hold all shares and pay taxes from cash.
    # Analysis: is the stock likely to appreciate enough to justify the concentration risk?
    FOR vest IN upcoming_vests:
        shares = vest.shares
        fmv = current_prices[vest.ticker]
        ordinary_income = shares * fmv  # Taxed at ordinary rates via W-2

        # Sell-to-cover: sell ~40-45% of shares to cover taxes
        # Hold all: keep all shares, pay estimated tax from cash
        # The "hold" strategy is only beneficial if the stock appreciates
        # and you hold > 1 year for LTCG treatment

    RETURN recommendations
```

**Example Scenario:**

```
Input:
  - RSU lot: 200 shares COIN, vested 2024-06-15 at $260/share
  - Current price: $190/share
  - Unrealized loss: ($190 - $260) * 200 = -$14,000
  - Upcoming RSU vest: 2025-01-15 (100 shares COIN)
  - Realized ST gains from other sales: $30,000

Analysis:
  - Can harvest $14,000 loss to offset $14,000 of the $30,000 ST gains
  - Tax savings: $14,000 * 0.481 = $6,734
  - BUT: RSU vest on Jan 15 is within 30 days -- WASH SALE RISK
  - Solution: Harvest before Dec 15 (>30 days before Jan 15 vest)

Recommendation:
  "Sell 200 shares of COIN before December 15 to harvest $14,000 short-term loss.
   Tax savings: $6,734. Must sell by Dec 15 to avoid wash sale from Jan 15 RSU vest."
```

---

#### B.4 NSO Exercise Timing

**IRS Authority:** IRC Section 83 (property transferred in connection with services), IRS Publication 525 (NSOs).

**Mechanism:** NSO exercise spread is ordinary income regardless of when sold. Timing the exercise relative to income levels can optimize the marginal rate.

**Data Requirements:**
- User input: unexercised NSO grants (strike price, shares, expiration date)
- Current stock price
- Current-year income projection
- Multi-year income projection

**Algorithm:**

```
FUNCTION analyze_nso_timing(nso_grants, current_price, income_projection):
    FOR grant IN nso_grants:
        spread = (current_price - grant.strike_price) * grant.shares
        IF spread <= 0:
            CONTINUE  # Underwater

        # Option 1: Exercise this year
        current_year_income = income_projection[tax_year]
        marginal_rate_this_year = lookup_marginal_rate(current_year_income + spread, filing_status)
        tax_this_year = spread * marginal_rate_this_year

        # Option 2: Exercise next year (if income is expected to be lower)
        next_year_income = income_projection[tax_year + 1]
        marginal_rate_next_year = lookup_marginal_rate(next_year_income + spread, filing_status)
        tax_next_year = spread * marginal_rate_next_year

        # Factor in time value of money and stock price risk
        IF marginal_rate_next_year < marginal_rate_this_year:
            savings = (marginal_rate_this_year - marginal_rate_next_year) * spread
            recommendation = f"Defer exercise to {tax_year + 1}. Estimated savings: ${savings}"
        ELSE:
            recommendation = "Exercise this year (no benefit from deferral)"

        # Check expiration date urgency
        IF grant.expiration_date - today < timedelta(days=90):
            recommendation += " WARNING: Options expire soon. Exercise before expiration."

    RETURN recommendations
```

---

### Category C: Capital Gains Management

---

#### C.1 Short-Term vs. Long-Term Holding Period Analysis

**IRS Authority:** IRC Section 1222 (holding period definitions), IRC Section 1(h) (preferential rates), IRS Publication 550.

**Mechanism:** Lots approaching the 1-year mark should be identified. Holding past 1 year converts short-term gains (taxed at ordinary rates, ~44%) to long-term gains (taxed at preferential rates, ~28%). The rate differential is ~16 percentage points.

**Data Requirements:**
- `repo.get_lots()` WHERE shares_remaining > 0
- Current market prices

**Algorithm:**

```
FUNCTION analyze_holding_periods(lots, current_prices):
    approaching_ltcg = []
    FOR lot IN lots WHERE lot.shares_remaining > 0:
        current_price = current_prices[lot.security.ticker]
        unrealized_gain = (current_price - lot.cost_per_share) * lot.shares_remaining
        IF unrealized_gain <= 0:
            CONTINUE  # Only relevant for gains

        one_year_date = add_years(lot.acquisition_date, 1) + timedelta(days=1)
        days_to_ltcg = (one_year_date - today).days

        IF 0 < days_to_ltcg <= 90:  # Within 90 days of LTCG qualification
            rate_differential = combined_ordinary_rate - combined_ltcg_rate
            tax_savings = unrealized_gain * rate_differential

            approaching_ltcg.append(StrategyRecommendation(
                name=f"Hold {lot.security.ticker} for Long-Term Treatment",
                situation=f"{lot.shares_remaining} shares acquired {lot.acquisition_date}. "
                         f"LTCG date: {one_year_date}. Days remaining: {days_to_ltcg}.",
                mechanism="Holding past 1 year converts short-term gain to long-term gain, "
                         f"reducing tax rate from ~{combined_ordinary_rate*100}% to ~{combined_ltcg_rate*100}%.",
                quantified_impact=f"Unrealized gain: ${unrealized_gain}. "
                                 f"Tax savings from LTCG treatment: ${tax_savings}.",
                action_steps=[
                    f"Hold until {one_year_date} (do NOT sell before this date)",
                    "Set a calendar reminder for the LTCG date",
                    "Monitor for any corporate events that might force a sale"
                ],
                deadline=str(one_year_date),
                risk_level="Low" if days_to_ltcg <= 30 else "Moderate",
                california_impact="CA taxes all gains at ordinary rates. "
                                "Holding benefit is FEDERAL ONLY (~16% rate differential)."
            ))

    RETURN approaching_ltcg
```

---

#### C.2 Tax-Lot Selection Optimization

**IRS Authority:** IRC Section 1012 (basis determination), IRS Publication 550 (specific identification), Treas. Reg. 1.1012-1(c).

**Mechanism:** When selling shares, the taxpayer can choose which lots to sell (specific identification). The engine recommends optimal lot selection based on tax impact.

**Data Requirements:**
- `repo.get_lots()` filtered by ticker
- Current market prices

**Algorithm:**

```
FUNCTION analyze_lot_selection(lots_by_ticker, current_prices, planned_sales):
    FOR ticker, lots IN lots_by_ticker:
        IF ticker NOT IN planned_sales:
            CONTINUE

        shares_to_sell = planned_sales[ticker]
        current_price = current_prices[ticker]

        # Strategy 1: Highest basis first (minimize gain)
        sorted_by_basis_desc = SORT lots BY cost_per_share DESC
        # Strategy 2: Long-term lots first (preferential rate)
        sorted_by_holding = SORT lots BY acquisition_date ASC (oldest first = LT)
        # Strategy 3: Loss lots first (realize losses)
        sorted_by_gain = SORT lots BY unrealized_gain ASC (biggest losses first)
        # Strategy 4: Short-term losses first (offset ST gains at highest rate)

        # Compute tax for each strategy
        FOR strategy IN [highest_basis, ltcg_first, losses_first]:
            selected_lots = select_lots(strategy, shares_to_sell)
            tax = compute_tax_on_sale(selected_lots, current_price)

        # Recommend the strategy with lowest tax
        best = MIN(strategies, key=lambda s: s.tax)

        recommendation = StrategyRecommendation(
            name=f"Tax-Lot Selection for {ticker} Sale",
            quantified_impact=f"Best strategy ({best.name}) saves ${best_tax - worst_tax} vs. FIFO default",
            action_steps=[
                f"Use specific identification when selling {ticker}",
                f"Specify lot(s): {best.lots}",
                "Notify broker of specific lot selection BEFORE or AT time of trade"
            ],
            risk_level="Low"
        )

    RETURN recommendations
```

---

#### C.3 Wash Sale Avoidance Planning

**IRS Authority:** IRC Section 1091, IRS Publication 550.

**Algorithm:**

```
FUNCTION analyze_wash_sale_risk(lots, sales, events):
    # Scan for potential wash sale violations
    FOR sale IN sales WHERE sale.gain_loss < 0:
        # Look for purchases of substantially identical securities
        # within 30 days before or 30 days after the sale
        window_start = sale.sale_date - timedelta(days=30)
        window_end = sale.sale_date + timedelta(days=30)

        conflicting_purchases = [
            event FOR event IN events
            WHERE event.security.ticker == sale.security.ticker
            AND event.event_type IN (VEST, EXERCISE, PURCHASE)
            AND window_start <= event.event_date <= window_end
        ]

        IF conflicting_purchases:
            recommendation = StrategyRecommendation(
                name=f"Wash Sale Warning: {sale.security.ticker}",
                situation=f"Loss sale on {sale.sale_date} conflicts with "
                         f"purchase/vest on {conflicting_purchases[0].event_date}.",
                mechanism="Wash sale rule disallows the loss. The disallowed loss "
                         "is added to the basis of the replacement shares.",
                risk_level="High"
            )

    RETURN recommendations
```

---

#### C.4 NIIT Threshold Management

**IRS Authority:** IRC Section 1411, IRS Form 8960.

**Mechanism:** The 3.8% NIIT applies to the lesser of net investment income or AGI exceeding the threshold ($200k Single, $250k MFJ). For taxpayers near the threshold, deferring investment income to a future year (or accelerating deductions) can avoid NIIT.

**Algorithm:**

```
FUNCTION analyze_niit(tax_estimate):
    threshold = NIIT_THRESHOLD[filing_status]
    excess_agi = tax_estimate.agi - threshold

    IF excess_agi <= 0:
        RETURN None  # Not subject to NIIT

    IF excess_agi <= 50000:  # Near-threshold -- opportunity to manage
        niit_paid = tax_estimate.federal_niit
        recommendation = StrategyRecommendation(
            name="NIIT Threshold Management",
            situation=f"AGI exceeds NIIT threshold by ${excess_agi}. NIIT: ${niit_paid}.",
            mechanism="Deferring investment income or increasing above-the-line deductions "
                     "could reduce AGI below the NIIT threshold.",
            quantified_impact=f"Eliminating NIIT entirely would save ${niit_paid}.",
            action_steps=[
                f"Increase 401(k) contributions to reduce AGI by ${excess_agi}",
                "Defer capital gain realizations to next year if possible",
                "Consider HSA contributions (federal deduction reduces AGI)"
            ],
            risk_level="Moderate"
        )
    ELSE:
        # Well above threshold -- NIIT is unavoidable, but tax-loss harvesting reduces the base
        recommendation = StrategyRecommendation(
            name="NIIT Impact Analysis",
            situation=f"AGI exceeds NIIT threshold by ${excess_agi}. "
                     "NIIT cannot be avoided at this income level.",
            mechanism="Tax-loss harvesting reduces net investment income, reducing NIIT base.",
            quantified_impact=f"Current NIIT: ${tax_estimate.federal_niit}. "
                             "Each $1,000 of harvested losses saves $38 in NIIT.",
            risk_level="Low (informational)"
        )

    RETURN recommendation
```

---

### Category D: Multi-Year Planning

---

#### D.1 Income Shifting Between Tax Years

**IRS Authority:** IRC Section 451 (timing of income), IRC Section 461 (timing of deductions).

**Mechanism:** If income is expected to be lower next year (e.g., job change, sabbatical, equity cliff), deferring income or accelerating deductions saves tax at the rate differential.

**Data Requirements:**
- User input: projected income for next 1-2 years
- Current year tax estimate
- Projected future year tax estimate

**Algorithm:**

```
FUNCTION analyze_income_shifting(current_estimate, projected_income_next_year):
    current_marginal = lookup_marginal_rate(current_estimate.agi, filing_status)
    next_year_marginal = lookup_marginal_rate(projected_income_next_year, filing_status)

    IF current_marginal > next_year_marginal:
        rate_diff = current_marginal - next_year_marginal
        # Strategies to defer income:
        strategies = [
            "Defer RSU sell-to-cover to January (if employer allows)",
            "Defer capital gain realizations to next year",
            "Accelerate deductible expenses (charitable, property tax pre-pay) to this year",
            "Maximize 401(k) this year, potentially reduce next year if marginal rate is lower"
        ]
        recommendation = StrategyRecommendation(
            name="Income Shifting: Defer to Lower-Rate Year",
            quantified_impact=f"Each $1,000 shifted saves ${rate_diff * 1000} in tax.",
        )

    ELIF next_year_marginal > current_marginal:
        # Rare for high-income, but possible if expecting large RSU cliff
        recommendation = StrategyRecommendation(
            name="Income Shifting: Accelerate to Lower-Rate Year",
            quantified_impact=f"Each $1,000 accelerated saves ${(next_year_marginal - current_marginal) * 1000}.",
        )

    RETURN recommendation
```

---

#### D.2 AMT Credit Carryforward Tracking

**IRS Authority:** IRC Section 53 (minimum tax credit), Form 8801 Instructions.

**Algorithm:** See B.2 above. The multi-year component tracks:
- Total AMT paid from ISO exercises (by year)
- AMT credit generated each year
- AMT credit utilized each year
- Remaining carryforward balance

```
FUNCTION track_amt_credit(amt_history, current_estimate):
    total_credit_available = SUM(amt_history.credit_generated) - SUM(amt_history.credit_used)

    # Credit usable this year = max(regular_tax - tentative_minimum_tax, 0)
    # This requires computing TMT without ISO preferences
    tmt = compute_tentative_minimum_tax(current_estimate, amt_preference=0)
    regular_tax = current_estimate.federal_regular_tax + current_estimate.federal_ltcg_tax
    credit_usable = max(regular_tax - tmt, 0)
    credit_claimed = min(total_credit_available, credit_usable)

    RETURN StrategyRecommendation(
        name="AMT Credit Carryforward",
        situation=f"Available AMT credit: ${total_credit_available}. Usable this year: ${credit_claimed}.",
        quantified_impact=f"Claim ${credit_claimed} AMT credit on Form 8801.",
        action_steps=["File Form 8801 with your return", f"Remaining carryforward: ${total_credit_available - credit_claimed}"],
        risk_level="Low"
    )
```

---

#### D.3 Capital Loss Carryforward Optimization

**IRS Authority:** IRC Section 1212(b).

**Algorithm:**

```
FUNCTION analyze_loss_carryforward(sale_results, prior_carryforward):
    net_capital = SUM(sr.gain_loss for sr in sale_results)
    total_including_carryforward = net_capital + prior_carryforward  # carryforward is negative

    IF total_including_carryforward < -3000:
        new_carryforward = total_including_carryforward + 3000
        recommendation = StrategyRecommendation(
            name="Capital Loss Carryforward",
            situation=f"Net capital losses (including ${abs(prior_carryforward)} carryforward): "
                     f"${abs(total_including_carryforward)}. "
                     f"Deductible this year: $3,000. Carryforward: ${abs(new_carryforward)}.",
            mechanism="Excess capital losses carry forward indefinitely per IRC Section 1212(b). "
                     "Consider realizing gains to utilize the carryforward at preferential rates.",
            quantified_impact=f"Carryforward of ${abs(new_carryforward)} available to offset future gains.",
            action_steps=[
                "Consider realizing long-term gains to use the carryforward",
                "Track carryforward on Schedule D, line 6 (ST) and line 14 (LT)",
            ],
            risk_level="Low"
        )

    RETURN recommendation
```

---

#### D.4 Estimated Tax Payment Planning

**IRS Authority:** IRC Section 6654 (underpayment penalty), IRS Publication 505 (Tax Withholding and Estimated Tax), IRS Form 2210 Instructions.

**Mechanism:** Avoid underpayment penalties by ensuring withholding + estimated payments meet safe harbor requirements.

**Safe Harbor Rules (IRC Section 6654(d)):**
- Pay at least 90% of current year tax liability, OR
- Pay 100% of prior year tax liability (110% if prior year AGI > $150,000)
- For California: similar rules per CA R&TC Section 19136

**Algorithm:**

```
FUNCTION analyze_estimated_payments(current_estimate, prior_year_tax, payments_made):
    # Federal safe harbor
    current_year_90pct = current_estimate.federal_total_tax * Decimal("0.90")
    prior_year_110pct = prior_year_tax.federal_total_tax * Decimal("1.10")  # 110% for high income
    federal_safe_harbor = min(current_year_90pct, prior_year_110pct)

    total_federal_paid = current_estimate.federal_withheld + payments_made.federal_estimated
    federal_shortfall = max(federal_safe_harbor - total_federal_paid, 0)

    IF federal_shortfall > 0:
        # Determine when to make payment
        remaining_quarters = determine_remaining_quarters(today)
        payment_per_quarter = federal_shortfall / remaining_quarters

        recommendation = StrategyRecommendation(
            name="Estimated Tax Payment Required",
            situation=f"Federal safe harbor: ${federal_safe_harbor}. "
                     f"Total paid: ${total_federal_paid}. Shortfall: ${federal_shortfall}.",
            mechanism="Underpayment penalty applies if withholding + estimated payments "
                     "are below the safe harbor amount.",
            quantified_impact=f"Penalty risk: ~{federal_shortfall * Decimal('0.08')} "
                             f"(estimated at ~8% annualized rate).",
            action_steps=[
                f"Pay ${payment_per_quarter} per remaining quarter via IRS Direct Pay or EFTPS",
                "Alternatively, increase W-4 withholding with employer",
                f"Federal quarterly due dates: Apr 15, Jun 15, Sep 15, Jan 15"
            ],
            deadline="Next quarterly due date",
            risk_level="High" if federal_shortfall > 10000 else "Moderate"
        )

    # California safe harbor
    ca_safe_harbor_100pct = current_estimate.ca_total_tax * Decimal("0.90")
    ca_prior_110pct = prior_year_tax.ca_total_tax * Decimal("1.10")
    ca_safe_harbor = min(ca_safe_harbor_100pct, ca_prior_110pct)
    total_ca_paid = current_estimate.ca_withheld + payments_made.state_estimated
    ca_shortfall = max(ca_safe_harbor - total_ca_paid, 0)

    # CA quarterly dates differ: Apr 15, Jun 15, Sep 15 (with 2 installments), Jan 15
    # Actually CA uses: Apr 15 (30%), Jun 15 (40%), Sep 15 (0%), Jan 15 (30%)
    # Per CA R&TC Section 19136

    RETURN recommendations
```

---

## Section 2: Data Requirements Summary

| Strategy | Database Data | User Input Required |
|---|---|---|
| A.1 Tax-Loss Harvesting | lots, sale_results, sales, events | Current market prices |
| A.2 Retirement Contributions | W-2s (Box 12) | Age, IRA contributions, employer match |
| A.3 HSA Maximization | W-2s (Box 12, code W) | HDHP enrollment, coverage type |
| A.4 Charitable Bunching | Tax estimate | Charitable giving, mortgage interest, property tax |
| A.5 SALT Optimization | W-2s, tax estimate | Property taxes |
| B.1 ESPP Holding Period | lots (ESPP), events (ESPP), Form 3922 | Current market prices |
| B.2 ISO Exercise Timing | events (ISO), lots (ISO) | Current prices, AMT credit carryforward |
| B.3 RSU Harvesting | lots (RSU), events (RSU) | Current prices, future vest schedule |
| B.4 NSO Timing | NSO grants | Current prices, multi-year income projection |
| C.1 Holding Period | lots | Current market prices |
| C.2 Lot Selection | lots | Planned sales, current prices |
| C.3 Wash Sale | lots, sales, events | (none) |
| C.4 NIIT Management | Tax estimate | (none) |
| D.1 Income Shifting | Tax estimate | Projected income next year |
| D.2 AMT Credit | Tax estimate | AMT credit carryforward history |
| D.3 Loss Carryforward | sale_results | Prior year carryforward amount |
| D.4 Estimated Payments | Tax estimate, W-2s | Prior year tax, payments made |

---

## Section 3: Architecture

### 3.1 Integration with Existing Estimator

The strategy engine's core mechanism is **"what-if" scenario analysis**. Each strategy modifies one or more parameters of the tax estimate and calls `TaxEstimator.estimate()` with the modified inputs. The dollar impact is the delta between the baseline estimate and the modified estimate.

```
baseline = estimator.estimate(actual_params)
modified = estimator.estimate(what_if_params)
impact = baseline.total_tax - modified.total_tax
```

This approach ensures that all strategy impacts account for:
- Progressive bracket effects
- NIIT threshold effects
- AMT interactions
- California tax implications
- Deduction phase-outs

### 3.2 Input/Output Models (Pydantic)

```python
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.models.enums import EquityType, FilingStatus
from app.models.reports import TaxEstimate


class StrategyCategory(StrEnum):
    CURRENT_YEAR = "CURRENT_YEAR"
    EQUITY_COMPENSATION = "EQUITY_COMPENSATION"
    CAPITAL_GAINS = "CAPITAL_GAINS"
    MULTI_YEAR = "MULTI_YEAR"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class Priority(StrEnum):
    CRITICAL = "CRITICAL"   # Must act now (e.g., estimated payment shortfall)
    HIGH = "HIGH"           # Large dollar impact, easy to implement
    MEDIUM = "MEDIUM"       # Moderate impact or requires planning
    LOW = "LOW"             # Small impact or informational


class StrategyRecommendation(BaseModel):
    """A single tax strategy recommendation."""
    name: str
    category: StrategyCategory
    priority: Priority
    situation: str                           # Current situation description
    mechanism: str                           # How the strategy works
    quantified_impact: str                   # Human-readable impact
    estimated_savings: Decimal               # Dollar savings (computed via estimator delta)
    action_steps: list[str]                  # Ordered action items
    deadline: date | None = None             # Action deadline
    risk_level: RiskLevel
    california_impact: str | None = None     # CA-specific notes
    irs_authority: str | None = None         # IRC section / Pub reference
    warnings: list[str] = []                 # Risk warnings or caveats
    interactions: list[str] = []             # Strategies this interacts with


class UserInputs(BaseModel):
    """User-provided inputs that cannot be derived from the database."""
    age: int | None = None
    has_hdhp: bool = False
    hsa_coverage: str | None = None          # "self" or "family"
    current_hsa_contributions: Decimal = Decimal("0")
    annual_charitable_giving: Decimal = Decimal("0")
    property_tax: Decimal = Decimal("0")
    mortgage_interest: Decimal = Decimal("0")
    other_itemized_deductions: Decimal = Decimal("0")
    prior_year_federal_tax: Decimal | None = None
    prior_year_state_tax: Decimal | None = None
    amt_credit_carryforward: Decimal = Decimal("0")
    capital_loss_carryforward: Decimal = Decimal("0")
    projected_income_next_year: Decimal | None = None
    future_vest_dates: list[dict] | None = None   # [{ticker, date, shares}]
    current_market_prices: dict[str, Decimal] = {} # {ticker: price}
    planned_sales: dict[str, Decimal] = {}         # {ticker: shares_to_sell}


class StrategyReport(BaseModel):
    """Complete strategy analysis output."""
    tax_year: int
    filing_status: FilingStatus
    baseline_estimate: TaxEstimate
    recommendations: list[StrategyRecommendation]
    total_potential_savings: Decimal          # Sum of all estimated_savings
    generated_at: str                        # ISO timestamp
    warnings: list[str] = []
    data_completeness: dict[str, bool] = {}  # Which data sources were available
```

### 3.3 Engine Class Structure

```python
class StrategyEngine:
    """Analyzes tax situation and produces strategy recommendations."""

    def __init__(self, estimator: TaxEstimator, repo: TaxRepository):
        self.estimator = estimator
        self.repo = repo
        self.warnings: list[str] = []

    def analyze(
        self,
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> StrategyReport:
        """Run all strategy analyses and return prioritized recommendations."""
        # Step 1: Compute baseline tax estimate
        baseline = self.estimator.estimate_from_db(self.repo, tax_year, filing_status, ...)

        # Step 2: Load all data from repository
        lots = self.repo.get_lots()
        sale_results = self.repo.get_sale_results(tax_year)
        events = self.repo.get_events()
        w2s = self.repo.get_w2s(tax_year)

        # Step 3: Run each strategy analyzer
        recommendations = []
        recommendations.extend(self._analyze_tax_loss_harvesting(baseline, lots, sale_results, user_inputs))
        recommendations.extend(self._analyze_retirement_contributions(baseline, w2s, user_inputs))
        recommendations.extend(self._analyze_hsa(baseline, w2s, user_inputs))
        recommendations.extend(self._analyze_charitable_bunching(baseline, lots, user_inputs))
        recommendations.extend(self._analyze_salt(baseline, user_inputs))
        recommendations.extend(self._analyze_espp_holding(baseline, lots, events, user_inputs))
        recommendations.extend(self._analyze_iso_exercise(baseline, lots, events, user_inputs))
        recommendations.extend(self._analyze_rsu_harvesting(baseline, lots, events, user_inputs))
        recommendations.extend(self._analyze_holding_periods(lots, user_inputs))
        recommendations.extend(self._analyze_lot_selection(lots, user_inputs))
        recommendations.extend(self._analyze_wash_sale_risk(lots, sale_results, events))
        recommendations.extend(self._analyze_niit(baseline))
        recommendations.extend(self._analyze_income_shifting(baseline, user_inputs))
        recommendations.extend(self._analyze_amt_credit(baseline, user_inputs))
        recommendations.extend(self._analyze_loss_carryforward(sale_results, user_inputs))
        recommendations.extend(self._analyze_estimated_payments(baseline, user_inputs))

        # Step 4: Sort by priority and estimated_savings
        recommendations.sort(key=lambda r: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[r.priority],
            -r.estimated_savings
        ))

        # Step 5: Flag interactions between strategies
        self._flag_interactions(recommendations)

        total_savings = sum(r.estimated_savings for r in recommendations)

        return StrategyReport(
            tax_year=tax_year,
            filing_status=filing_status,
            baseline_estimate=baseline,
            recommendations=recommendations,
            total_potential_savings=total_savings,
            generated_at=datetime.now().isoformat(),
            warnings=self.warnings,
            data_completeness=self._check_data_completeness(w2s, lots, sale_results, events, user_inputs),
        )

    def _flag_interactions(self, recommendations):
        """Flag strategies that interact with each other."""
        # Example: Tax-loss harvesting affects NIIT base
        # Example: 401k contributions affect AMT headroom
        # Example: ISO exercise affects NIIT, AMT, and estimated payments
        interaction_map = {
            "Tax-Loss Harvest": ["NIIT Impact", "Capital Loss Carryforward"],
            "Maximize 401(k)": ["NIIT Threshold", "AMT Headroom", "Estimated Payments"],
            "ISO Exercise": ["AMT", "NIIT", "Estimated Payments", "AMT Credit Carryforward"],
            "ESPP Holding": ["Tax-Loss Harvest", "Wash Sale"],
        }
        # Apply interaction flags
        ...
```

---

## Section 4: CLI Integration

### 4.1 Strategy Command

```python
@app.command()
def strategy(
    year: int = typer.Argument(..., help="Tax year for strategy analysis"),
    filing_status: str = typer.Option(
        "SINGLE", "--filing-status", "-s",
        help="Filing status: SINGLE, MFJ, MFS, HOH",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db", "--db",
        help="Path to the SQLite database file",
    ),
    # User inputs (optional -- strategies that need these will be skipped if not provided)
    age: int | None = typer.Option(None, "--age", help="Taxpayer age (for catch-up contributions)"),
    prices_file: Path | None = typer.Option(
        None, "--prices", help="JSON file with current market prices: {ticker: price}",
    ),
    charitable: float = typer.Option(0, "--charitable", help="Annual charitable giving amount"),
    property_tax: float = typer.Option(0, "--property-tax", help="Annual property tax"),
    mortgage_interest: float = typer.Option(0, "--mortgage-interest", help="Annual mortgage interest"),
    prior_year_tax: float | None = typer.Option(None, "--prior-year-tax", help="Prior year total tax (for safe harbor)"),
    amt_credit: float = typer.Option(0, "--amt-credit", help="AMT credit carryforward from prior years"),
    loss_carryforward: float = typer.Option(0, "--loss-carryforward", help="Capital loss carryforward from prior years"),
    projected_income: float | None = typer.Option(None, "--projected-income", help="Projected income next year"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON instead of formatted text"),
    top_n: int = typer.Option(10, "--top", "-n", help="Show top N recommendations"),
) -> None:
    """Run tax strategy analysis and recommendations."""
    ...
```

### 4.2 CLI Output Format

```
$ taxbot strategy 2024 --filing-status SINGLE --prices prices.json --age 35

=== Tax Strategy Analysis: 2024 (SINGLE) ===

Baseline Tax: $185,432 (Federal: $142,891 + California: $42,541)
Total Potential Savings: $18,247

 # | Priority | Strategy                              | Savings   | Deadline
---|----------|---------------------------------------|-----------|-------------
 1 | CRITICAL | Estimated Tax Payment Shortfall       | (penalty) | Sep 15, 2024
 2 | HIGH     | Maximize 401(k) Contributions         | $3,544    | Dec 31, 2024
 3 | HIGH     | Tax-Loss Harvest: COIN (-$14,000)     | $6,734    | Dec 15, 2024
 4 | HIGH     | Hold COIN RSU Lot for LTCG (23 days)  | $4,200    | Jan 8, 2025
 5 | MEDIUM   | ESPP Holding Period: COIN             | $486      | Sep 15, 2025
 6 | MEDIUM   | Charitable Bunching via DAF            | $2,100    | Dec 31, 2024
 7 | LOW      | Backdoor Roth IRA                     | (future)  | Apr 15, 2025
 8 | LOW      | NIIT Impact Analysis                  | $38/1k    | (ongoing)

DETAILS:

[1] CRITICAL: Estimated Tax Payment Shortfall
    Situation: Federal safe harbor: $157,000. Total paid: $130,000. Shortfall: $27,000.
    Action: Pay $13,500 by Sep 15 and $13,500 by Jan 15 via IRS Direct Pay.
    Risk: High -- underpayment penalty of ~$2,160 if not addressed.

[2] HIGH: Maximize 401(k) Contributions
    Situation: Current 401(k): $15,000. Limit: $23,000. Room: $8,000.
    Mechanism: Pre-tax 401(k) reduces taxable income at 44.3% combined marginal rate.
    Savings: $8,000 * 44.3% = $3,544
    Action: Increase contribution rate with HR/payroll to max out by Dec 31.
    CA Impact: CA follows federal 401(k) treatment. Full state tax reduction.

...

WARNINGS:
  - Market prices required for tax-loss harvesting analysis. Use --prices to provide.
  - HSA analysis skipped (--age not provided).
  - Prior year tax not provided; estimated payment analysis may be incomplete.

DATA COMPLETENESS:
  [x] W-2 data         [x] Sale results    [ ] Market prices
  [x] Lots             [x] Events          [ ] Prior year tax
```

### 4.3 JSON Output Mode

When `--json` is passed, the CLI outputs the `StrategyReport` model serialized as JSON. This enables integration with the report engine and programmatic consumption.

---

## Section 5: Implementation Priority

Ordered by: (1) data availability -- strategies that work with data already in the database; (2) dollar impact -- highest savings first; (3) implementation complexity -- simpler first.

| Priority | Strategy | Impact | Complexity | Data Available |
|---|---|---|---|---|
| 1 | C.1 Holding Period Analysis | High ($4k-$15k) | Low | Yes (lots) |
| 2 | A.1 Tax-Loss Harvesting | High ($3k-$20k) | Medium | Partial (needs prices) |
| 3 | B.1 ESPP Holding Period | Medium ($500-$5k) | Medium | Yes (lots, events) |
| 4 | C.3 Wash Sale Avoidance | High (avoid loss) | Low | Yes (lots, sales, events) |
| 5 | A.2 Retirement Contributions | High ($3k-$10k) | Low | Yes (W-2 Box 12) |
| 6 | D.4 Estimated Payments | High (penalty avoid) | Medium | Partial (needs prior year) |
| 7 | C.4 NIIT Analysis | Medium ($1k-$5k) | Low | Yes (tax estimate) |
| 8 | A.5 SALT Analysis | Low (informational) | Low | Yes (tax estimate) |
| 9 | B.2 ISO Exercise Timing | High ($5k-$50k) | High | Partial (needs grants) |
| 10 | B.3 RSU Harvesting Coordination | High ($3k-$15k) | Medium | Partial (needs prices, vest schedule) |
| 11 | A.3 HSA Maximization | Low ($1k-$2k) | Low | Partial (needs user inputs) |
| 12 | A.4 Charitable Bunching | Medium ($1k-$5k) | Medium | Needs user inputs |
| 13 | C.2 Lot Selection | Medium ($1k-$5k) | Medium | Needs planned sales |
| 14 | B.4 NSO Timing | Medium ($2k-$10k) | Medium | Needs grants, projections |
| 15 | D.1 Income Shifting | Medium ($1k-$5k) | Low | Needs projections |
| 16 | D.2 AMT Credit Tracking | Medium ($1k-$10k) | Medium | Needs history |
| 17 | D.3 Loss Carryforward | Low ($1k) | Low | Needs prior year |

### Recommended Implementation Phases

**Phase 1 (MVP):** Strategies 1-8 (all use data already in the database or tax estimate)
- Holding period analysis
- Tax-loss harvesting (with user-provided prices)
- ESPP holding period
- Wash sale detection
- Retirement contribution analysis
- Estimated payment analysis
- NIIT analysis
- SALT analysis

**Phase 2:** Strategies 9-13 (require additional user inputs)
- ISO exercise timing
- RSU harvesting coordination
- HSA maximization
- Charitable bunching
- Lot selection optimization

**Phase 3:** Strategies 14-17 (require multi-year data and projections)
- NSO timing
- Income shifting
- AMT credit tracking
- Loss carryforward optimization

---

## Section 6: Test Scenarios

### Test 1: High-Income Single Filer with RSU Gains and Unrealized Losses

```
Input:
  Tax year: 2024
  Filing status: Single
  W-2 wages: $600,000 (Coinbase, includes RSU income)
  W-2 Box 12 code D: $15,000 (401k contributions)
  Federal withheld: $180,000
  CA withheld: $50,000
  Realized ST gains (RSU sales): $50,000
  Realized LT gains (RSU sales): $20,000
  Open lot: 200 shares COIN, acquired 2024-06-15 at $260, current price $190
    Unrealized loss: -$14,000 (short-term)
  No ESPP, no ISOs
  Age: 35

Expected Recommendations (top 5):
  1. Tax-Loss Harvest COIN: Sell 200 shares for $14,000 loss.
     Savings: $14,000 * 48.1% (fed 35% + NIIT 3.8% + CA 9.3%) = $6,734
     Deadline: Dec 31, 2024. Watch for RSU vest wash sale.

  2. Maximize 401(k): $23,000 - $15,000 = $8,000 remaining.
     Savings: $8,000 * 44.3% (fed 35% + CA 9.3%) = $3,544
     Note: 401k reduces W-2 wages, which reduces both fed and CA tax.

  3. Backdoor Roth IRA: No current-year savings but $7,000 tax-free growth.
     Priority: Low (future benefit only).

  4. NIIT Analysis: AGI ~$670,000. NIIT threshold $200,000. Excess: $470,000.
     NIIT on investment income: ~$2,660. Informational.

  5. SALT Cap Analysis: CA tax ~$52,000. SALT cap: $10,000. Wasted: $42,000.
     Informational -- drives decision to take standard deduction.

Total potential savings: ~$10,278 (current year)
```

### Test 2: ESPP Holding Period Decision

```
Input:
  Tax year: 2024
  Filing status: Single
  W-2 wages: $500,000
  ESPP lot: 150 shares, purchased 2024-02-15
    Purchase price: $170/share (15% discount)
    FMV at purchase: $200/share
    FMV at offering: $190/share
    Offering date: 2023-08-15
    Current price: $230/share
  Qualifying date: 2025-08-15 (2 years from offering)

Expected Recommendations:
  1. ESPP Holding Period Analysis:
     Disqualifying (sell now):
       Ordinary income: ($200 - $170) * 150 = $4,500
       ST capital gain: ($230 - $200) * 150 = $4,500
       Tax: $4,500 * 0.443 + $4,500 * 0.443 = $3,987

     Qualifying (hold to 2025-08-15, price stays $230):
       Ordinary income: min($230-$170, $190-$170) * 150 = min($60, $20) * 150 = $3,000
       LTCG: ($230*150) - ($170*150) - $3,000 = $6,000
       Tax: $3,000 * 0.443 + $6,000 * 0.281 = $1,329 + $1,686 = $3,015

     Savings: $3,987 - $3,015 = $972
     Hold 8 more months for $972 savings on $34,500 position.
     Breakeven: stock can decline $972/150 = $6.48/share (to $223.52).
     Risk level: Moderate (8-month hold, single-stock concentration).
```

### Test 3: ISO Exercise AMT Analysis

```
Input:
  Tax year: 2024
  Filing status: Single
  W-2 wages: $400,000
  Taxable income (after standard deduction): $385,400
  Unexercised ISOs: 1,000 shares at $50 strike, current FMV: $150
  Potential spread: ($150 - $50) * 1000 = $100,000
  No prior AMT credit

Expected Recommendations:
  1. ISO Exercise Analysis:
     AMT headroom computation:
       Regular tax on $385,400: ~$90,939 (fed ordinary + LTCG)
       AMT exemption: $85,700 (Single, 2024)
       AMTI without ISO: $385,400. AMT base: $385,400 - $85,700 = $299,700
       TMT without ISO: $299,700 * 26% (first $232,600) + ($299,700 - $232,600) * 28%
         = $60,476 + $18,788 = $79,264
       Regular tax ($90,939) > TMT ($79,264), so no AMT without ISO.
       Headroom: ~$44,000 of ISO spread before AMT kicks in.
         (Binary search: add $44,000 to AMTI, recompute TMT, confirm TMT < regular_tax)

     If exercise all 1,000 shares ($100,000 spread):
       AMTI = $385,400 + $100,000 = $485,400
       AMT exemption: $85,700 (no phase-out, since $485,400 < $609,350)
       AMT base: $399,700
       TMT: $232,600 * 0.26 + ($399,700 - $232,600) * 0.28 = $60,476 + $46,788 = $107,264
       AMT = $107,264 - $90,939 = $16,325
       This generates $16,325 AMT credit carryforward.

     Recommendation: "Exercise 440 shares within AMT headroom ($44,000 spread) with zero
     additional tax. If exercising all 1,000 shares, AMT cost is $16,325 which generates
     an equivalent credit carryforward for future use."
```

### Test 4: Estimated Tax Payment Shortfall

```
Input:
  Tax year: 2024
  Filing status: Single
  W-2 wages: $600,000
  Federal withheld: $130,000
  Federal estimated payments: $0
  Prior year total federal tax: $160,000

Expected Recommendations:
  1. CRITICAL: Estimated Tax Payment Shortfall
     Current year estimated federal tax: ~$165,000
     Safe harbor (110% of prior year): $160,000 * 1.10 = $176,000
     Safe harbor (90% of current year): $165,000 * 0.90 = $148,500
     Lower safe harbor: $148,500
     Total paid: $130,000
     Shortfall: $148,500 - $130,000 = $18,500

     Action: Make estimated payment of $18,500 before next quarterly deadline.
     Penalty risk: ~$1,480 (8% annualized on shortfall).
     Risk level: CRITICAL
```

### Test 5: Multi-Strategy Interaction (RSU Vest + Tax-Loss Harvest + Wash Sale)

```
Input:
  Tax year: 2024
  Filing status: Single
  W-2 wages: $550,000
  Realized ST gains: $30,000 (from RSU sell-to-cover)
  Open RSU lot: 300 shares COIN, vested 2024-04-15 at $280, current price $210
    Unrealized loss: -$21,000 (short-term)
  Upcoming RSU vest: 100 shares COIN on 2025-01-10
  No other lots

Expected Recommendations:
  1. Tax-Loss Harvest COIN RSU: $21,000 loss to offset $21,000 of $30,000 ST gains.
     Savings: $21,000 * 0.481 = $10,101
     CRITICAL INTERACTION: RSU vest on Jan 10 is within 30 days of year-end.

  2. Wash Sale Warning:
     If COIN shares sold Dec 11-31, the Jan 10 RSU vest (100 shares) triggers wash sale
     on up to 100 shares of the loss sale.
     Solution: Sell before Dec 11 (31+ days before Jan 10 vest).
     Alternatively: Accept partial wash sale on 100 of 300 shares.

  3. Revised strategy:
     Sell all 300 shares before Dec 10, 2024.
     Loss: -$21,000 total
     Wash sale applies to 100 shares: $7,000 loss disallowed, added to basis of Jan 10 vest shares.
     Net deductible loss: -$14,000
     Tax savings: $14,000 * 0.481 = $6,734
     Plus: $7,000 loss embedded in new RSU lot basis (deferred, not lost).

     OR: Sell 300 shares by Dec 10 (>30 days before Jan 10 vest):
     All $21,000 loss is allowed. No wash sale.
     Tax savings: $21,000 * 0.481 = $10,101

  Recommendation: "Sell 300 shares of COIN BEFORE December 10 to avoid wash sale
  with the January 10 RSU vest. Tax savings: $10,101."
```

---

## Section 7: Strategy Interactions Matrix

Strategies do not operate in isolation. The engine must account for these interactions:

| Strategy A | Interacts With | Nature of Interaction |
|---|---|---|
| Tax-Loss Harvesting | NIIT | Harvested losses reduce net investment income, reducing NIIT |
| Tax-Loss Harvesting | Wash Sale | Must check 61-day window for all loss sales |
| Tax-Loss Harvesting | RSU Vests | Upcoming RSU vests can trigger wash sales |
| 401(k) Max | NIIT | Reduced AGI may reduce NIIT (if near threshold) |
| 401(k) Max | AMT | Reduced income may increase AMT headroom |
| 401(k) Max | Estimated Payments | Reduced income changes safe harbor amount |
| ISO Exercise | AMT | Exercise creates AMT preference |
| ISO Exercise | NIIT | ISO exercise does NOT create investment income (not subject to NIIT until sale) |
| ISO Exercise | Estimated Payments | AMT increases tax liability, affecting safe harbor |
| ESPP Holding | Tax-Loss Harvesting | If ESPP stock declines, can harvest after holding period analysis |
| Charitable Bunching | SALT | SALT cap affects whether bunching pushes above standard deduction |
| Income Shifting | All Strategies | Changing income level changes marginal rates for all other strategies |

The engine should compute strategies in dependency order and re-evaluate when earlier strategies change the baseline.

---

## Section 8: Authoritative References Index

| Strategy | IRS Authority | CA Authority |
|---|---|---|
| Tax-Loss Harvesting | IRC Sec. 1211(b), 1212(b), 1091; Pub. 550 | CA R&TC Sec. 18152.5 |
| Retirement Contributions | IRC Sec. 401(k), 219, 408A, 402A; Pub. 590-A | CA conforms |
| HSA | IRC Sec. 223; Pub. 969 | CA R&TC Sec. 17215 (non-conformity) |
| Charitable Giving | IRC Sec. 170, 4966; Pub. 526 | CA conforms |
| SALT Cap | IRC Sec. 164(b)(6) | Not applicable (no cap in CA) |
| ESPP Holding | IRC Sec. 423, 422(a)(1); Pub. 525; Form 3922 | CA R&TC Sec. 17502 |
| ISO Exercise / AMT | IRC Sec. 421-424, 55-59; Form 6251; Form 8801 | CA repealed AMT (AB 1601, 2005) |
| Wash Sale | IRC Sec. 1091; Pub. 550 | CA conforms |
| NIIT | IRC Sec. 1411; Form 8960 | Not applicable (no NIIT in CA) |
| Holding Period | IRC Sec. 1222, 1(h); Pub. 550 | CA R&TC Sec. 18152.5 (no LTCG rate) |
| Estimated Payments | IRC Sec. 6654; Pub. 505; Form 2210 | CA R&TC Sec. 19136 |
| AMT Credit | IRC Sec. 53; Form 8801 | Not applicable |
| Loss Carryforward | IRC Sec. 1212(b); Schedule D | CA conforms |
| Lot Selection | IRC Sec. 1012; Treas. Reg. 1.1012-1(c); Pub. 550 | CA conforms |

---

## Log

| Timestamp | Agent | Action |
|---|---|---|
| 2026-02-13 | Tax Expert CPA | Initial plan written. All strategy categories defined with algorithms, IRS citations, example scenarios, and test cases. |
| | Python Engineer | (pending) Implementation per this plan. |
| | Accountant | (pending) Numerical validation of test scenarios. |
| | Tax Planner | (pending) Review strategy recommendations for completeness. |
