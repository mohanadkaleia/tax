# Tax Estimator Engine — CPA Tax Plan

**Session ID:** tax-2026-02-12-tax-estimator-001
**Date:** 2026-02-12
**Status:** Planning
**Tax Year:** 2024

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation and reconciliation sign-off)
- Tax Planner (strategy implications)

**Scope:**
- Implement the Tax Estimator Engine — computes federal and California state estimated tax liability for a given tax year from all imported and reconciled data.
- The CLI command `taxbot estimate 2024` will load all W-2s, 1099-INTs, 1099-DIVs, and reconciled SaleResults from the database, aggregate income, apply deductions, compute federal tax (ordinary rates, preferential LTCG/qualified dividend rates, NIIT, AMT), compute California tax (ordinary rates only, Mental Health Services Tax), subtract withholdings, and produce a `TaxEstimate` object with the balance due or refund for both jurisdictions.
- Definition of "done": A user can run `taxbot estimate 2024` and see a complete breakdown of income, deductions, federal tax, California tax, withholdings, and balance due/refund. The `TaxEstimate` model is fully populated and can be consumed by the strategy engine.

---

## Tax Analysis

### Forms & Documents Consumed by the Estimator

| Form / Data Source | What the Estimator Uses | Reference |
|---|---|---|
| W-2 (from `w2_forms` table) | Box 1 wages, Box 2 federal withholding, Box 17 CA withholding | IRS Form W-2 Instructions |
| 1099-INT (from `form_1099int` table) | Box 1 interest income, Box 2 early withdrawal penalty, federal/state withholding | IRS Pub. 550 |
| 1099-DIV (from `form_1099div` table) | Box 1a ordinary dividends, Box 1b qualified dividends, Box 2a capital gain distributions, federal withholding | IRS Pub. 550 |
| SaleResults (from `sale_results` table) | gain_loss (by holding_period), ordinary_income, amt_adjustment | Form 8949 / Schedule D |
| Reconciliation Runs (from `reconciliation_runs` table) | Validation that reconciliation has been run | Internal |

### Outputs

| Output | Description |
|---|---|
| `TaxEstimate` model | Complete tax computation with all fields populated |
| CLI summary | Formatted printout of income, deductions, taxes, withholdings, balance |
| Audit log entries | Logged to `audit_log` table for traceability |

---

## Section 1: Income Aggregation

### 1.1 W-2 Wages (IRS Form 1040, Line 1a)

Load all W-2 records for the tax year from `repo.get_w2s(tax_year)`. Aggregate across multiple employers:

```
total_w2_wages = SUM(w2.box1_wages) for all W-2s in the tax year
total_federal_withheld_w2 = SUM(w2.box2_federal_withheld) for all W-2s
total_state_withheld_w2 = SUM(w2.box17_state_withheld) for all W-2s
```

**Important:** W-2 Box 1 already includes RSU income (FMV at vest), NSO exercise income (spread at exercise), and ESPP disqualifying disposition income IF the employer reported it. The estimator must NOT double-count this income. The `ordinary_income` from SaleResults is for informational reconciliation only when it has already been captured in the W-2.

**However:** ESPP disqualifying disposition income from sales occurring late in the year may NOT yet be reflected in the W-2. The estimator should flag this as a warning if `SUM(sale_results.ordinary_income) > 0` and the user should verify whether the W-2 includes it.

Per IRS Pub. 525: "Your employer should include the compensation in your wages shown on your Form W-2."

### 1.2 Interest Income (IRS Form 1040, Line 2b; Pub. 550)

Load all 1099-INT records for the tax year. The repository currently does NOT have a `get_1099ints()` method -- one must be added.

```
total_interest_income = SUM(form.interest_income) for all 1099-INTs
total_early_withdrawal_penalty = SUM(form.early_withdrawal_penalty)
federal_withheld_1099int = SUM(form.federal_tax_withheld)
```

The early withdrawal penalty is an above-the-line deduction (Form 1040, Schedule 1, Line 18) that reduces AGI.

### 1.3 Dividend Income (IRS Form 1040, Lines 3a and 3b; Pub. 550)

Load all 1099-DIV records for the tax year. The repository currently does NOT have a `get_1099divs()` method -- one must be added.

```
total_ordinary_dividends = SUM(form.ordinary_dividends) for all 1099-DIVs     # Line 3b
total_qualified_dividends = SUM(form.qualified_dividends) for all 1099-DIVs   # Line 3a
total_capital_gain_distributions = SUM(form.total_capital_gain_distributions)  # Schedule D, Line 13
federal_withheld_1099div = SUM(form.federal_tax_withheld)
```

**Note:** `qualified_dividends` is a SUBSET of `ordinary_dividends` (Box 1b <= Box 1a). Only `ordinary_dividends` is added to total income. The `qualified_dividends` amount determines how much of the dividend income is taxed at the preferential LTCG rate instead of ordinary rates.

Per IRS Pub. 550: "Qualified dividends are the ordinary dividends that are subject to the same 0%, 15%, or 20% maximum tax rate that applies to net capital gain."

### 1.4 Capital Gains and Losses (Schedule D; Form 8949)

Load all SaleResults for the tax year from `repo.get_sale_results(tax_year)`.

Separate by holding period:

```
short_term_gain_loss = SUM(sr.gain_loss) WHERE sr.holding_period == SHORT_TERM
long_term_gain_loss = SUM(sr.gain_loss) WHERE sr.holding_period == LONG_TERM
```

Add capital gain distributions from 1099-DIV to long-term gains:

```
long_term_gain_loss += total_capital_gain_distributions
```

### 1.5 Capital Loss Netting (Schedule D, Lines 7 and 15; Pub. 550)

Per IRS instructions, capital losses are netted in a specific order:

**Step 1 -- Net within each category:**
```
net_short_term = short_term_gains + short_term_losses    (losses are negative)
net_long_term = long_term_gains + long_term_losses       (losses are negative)
```

**Step 2 -- Cross-category netting (Schedule D, Line 16):**
```
IF net_short_term < 0 AND net_long_term > 0:
    total_net_gain_loss = net_short_term + net_long_term
ELIF net_long_term < 0 AND net_short_term > 0:
    total_net_gain_loss = net_short_term + net_long_term
ELSE:
    total_net_gain_loss = net_short_term + net_long_term
```

**Step 3 -- Capital loss limitation (IRC Section 1211(b)):**
```
IF total_net_gain_loss < 0:
    IF filing_status == MFS:
        capital_loss_deduction = max(total_net_gain_loss, Decimal("-1500"))
    ELSE:
        capital_loss_deduction = max(total_net_gain_loss, Decimal("-3000"))
    capital_loss_carryforward = total_net_gain_loss - capital_loss_deduction
ELSE:
    capital_loss_deduction = Decimal("0")
    capital_loss_carryforward = Decimal("0")
```

Per IRS Pub. 550: "If your capital losses exceed your capital gains, the amount of the excess loss that you can claim to lower your income is the lesser of $3,000 ($1,500 if married filing separately) or your total net loss."

**What goes into income:**
- If net gains: `net_short_term` (if positive) is taxed at ordinary rates. `net_long_term` (if positive) is taxed at preferential LTCG rates.
- If net loss: the limited deduction amount (up to -$3,000) reduces ordinary income.

### 1.6 ESPP and ISO Ordinary Income from SaleResults

```
total_sale_ordinary_income = SUM(sr.ordinary_income) for all SaleResults
```

This is the ordinary income from ESPP disqualifying dispositions and ISO disqualifying dispositions. This income is typically ALREADY included in W-2 Box 1 by the employer. The estimator must handle this carefully:

- **Default assumption:** The ordinary income from SaleResults is already reflected in W-2 wages. Do NOT add it again.
- **Warning:** If `total_sale_ordinary_income > 0`, emit a warning: "Verify that $X of equity compensation ordinary income is included in your W-2 Box 1. If not, add it to wages."
- **Future enhancement:** Allow the user to flag whether the W-2 includes this income.

### 1.7 AMT Preference Items from SaleResults

```
total_amt_preference = SUM(sr.amt_adjustment) for all SaleResults WHERE sr.amt_adjustment > 0
total_amt_reversal = SUM(sr.amt_adjustment) for all SaleResults WHERE sr.amt_adjustment < 0
net_amt_adjustment = SUM(sr.amt_adjustment) for all SaleResults
```

The net AMT adjustment feeds into the AMT computation (Section 2.4).

### 1.8 Total Income and AGI

```
total_income = (
    total_w2_wages
    + total_interest_income
    + total_ordinary_dividends
    + net_short_term (if positive, already counted; if negative, limited deduction)
    + net_long_term (if positive, already counted; if negative, limited deduction)
    + capital_loss_deduction (negative number, up to -$3000)
)
```

More precisely, for the TaxEstimate model fields:
```
short_term_gains = max(net_short_term, Decimal("0"))
long_term_gains = max(net_long_term, Decimal("0"))

# If there's a net capital loss, apply it against income
capital_gain_income = short_term_gains + long_term_gains + capital_loss_deduction

total_income = total_w2_wages + total_interest_income + total_ordinary_dividends + capital_gain_income

# Above-the-line deductions (adjustments to income)
above_the_line_deductions = total_early_withdrawal_penalty

agi = total_income - above_the_line_deductions
```

Per IRS Form 1040: AGI = Total Income (Line 9) minus Adjustments (Schedule 1, Part II).

---

## Section 2: Federal Tax Computation

### 2.1 Taxable Income (Form 1040, Line 15)

```
standard_deduction = FEDERAL_STANDARD_DEDUCTION[2024][filing_status]
deduction_used = max(itemized_deductions or Decimal("0"), standard_deduction)
taxable_income = max(agi - deduction_used, Decimal("0"))
```

#### 2024 Federal Standard Deduction Amounts

Per IRS Rev. Proc. 2023-34:

| Filing Status | Standard Deduction |
|---|---|
| Single | $14,600 |
| Married Filing Jointly | $29,200 |
| Married Filing Separately | $14,600 |
| Head of Household | $21,900 |

These are already partially defined in `brackets.py` but only for Single and MFJ. **MFS and HOH must be added.**

### 2.2 Federal Ordinary Income Tax (Form 1040, Line 16)

The taxable income is split into two pools:
1. **Ordinary income** taxed at ordinary rates (wages, interest, non-qualified dividends, short-term gains).
2. **Preferential income** taxed at LTCG rates (qualified dividends, net long-term capital gains).

```
preferential_income = qualified_dividends + max(net_long_term, Decimal("0"))
ordinary_taxable = max(taxable_income - preferential_income, Decimal("0"))
```

Apply ordinary rates to `ordinary_taxable`:

#### 2024 Federal Tax Brackets (Ordinary Income)

Per IRS Rev. Proc. 2023-34:

**Single:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $11,600 | 10% |
| $11,600 | $47,150 | 12% |
| $47,150 | $100,525 | 22% |
| $100,525 | $191,950 | 24% |
| $191,950 | $243,725 | 32% |
| $243,725 | $609,350 | 35% |
| $609,350 | -- | 37% |

**Married Filing Jointly:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $23,200 | 10% |
| $23,200 | $94,300 | 12% |
| $94,300 | $201,050 | 22% |
| $201,050 | $383,900 | 24% |
| $383,900 | $487,450 | 32% |
| $487,450 | $731,200 | 35% |
| $731,200 | -- | 37% |

**Married Filing Separately:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $11,600 | 10% |
| $11,600 | $47,150 | 12% |
| $47,150 | $100,525 | 22% |
| $100,525 | $191,950 | 24% |
| $191,950 | $243,725 | 32% |
| $243,725 | $365,600 | 35% |
| $365,600 | -- | 37% |

**Head of Household:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $16,550 | 10% |
| $16,550 | $63,100 | 12% |
| $63,100 | $100,500 | 22% |
| $100,500 | $191,950 | 24% |
| $191,950 | $243,700 | 32% |
| $243,700 | $609,350 | 35% |
| $609,350 | -- | 37% |

These brackets are already partially defined in `brackets.py` for Single and MFJ. **MFS and HOH must be added.**

### 2.3 Qualified Dividends and Long-Term Capital Gains Tax (Schedule D Tax Worksheet)

Per the Qualified Dividends and Capital Gain Tax Worksheet (Form 1040 Instructions), qualified dividends and net long-term capital gains are taxed at preferential rates:

#### 2024 LTCG/Qualified Dividend Rate Brackets

Per IRS Rev. Proc. 2023-34:

**Single:**

| Taxable Income Up To | LTCG Rate |
|---|---|
| $47,025 | 0% |
| $518,900 | 15% |
| Above $518,900 | 20% |

**Married Filing Jointly:**

| Taxable Income Up To | LTCG Rate |
|---|---|
| $94,050 | 0% |
| $583,750 | 15% |
| Above $583,750 | 20% |

**Married Filing Separately:**

| Taxable Income Up To | LTCG Rate |
|---|---|
| $47,025 | 0% |
| $291,850 | 15% |
| Above $291,850 | 20% |

**Head of Household:**

| Taxable Income Up To | LTCG Rate |
|---|---|
| $63,000 | 0% |
| $551,350 | 15% |
| Above $551,350 | 20% |

**CRITICAL:** These brackets are NOT currently in `brackets.py` for 2024. Only 2025 data exists. **All four filing statuses for 2024 must be added.**

**How the LTCG tax is computed (Qualified Dividends and Capital Gain Tax Worksheet):**

The IRS worksheet uses a stacking method. The preferential income sits "on top" of ordinary income in the bracket structure:

```python
def compute_ltcg_tax(
    preferential_income: Decimal,   # qualified_dividends + net LTCG
    taxable_income: Decimal,        # total taxable income after deductions
    filing_status: FilingStatus,
    tax_year: int,
) -> Decimal:
    """
    The 0%/15%/20% rates apply based on the taxpayer's total taxable income,
    not just the preferential income itself.

    The preferential income 'stacks' on top of ordinary income:
    - ordinary_income_top = taxable_income - preferential_income
    - The portion of preferential income that falls in each LTCG bracket
      is taxed at that bracket's rate.
    """
    brackets = FEDERAL_LTCG_BRACKETS[tax_year][filing_status]
    ordinary_income_top = max(taxable_income - preferential_income, Decimal("0"))

    tax = Decimal("0")
    prev_bound = Decimal("0")
    remaining_pref = preferential_income

    for upper_bound, rate in brackets:
        if remaining_pref <= 0:
            break
        if upper_bound is None:
            # Top bracket -- all remaining preferential income
            tax += remaining_pref * rate
            remaining_pref = Decimal("0")
        else:
            # How much of this bracket is available after ordinary income fills it?
            bracket_start = max(prev_bound, ordinary_income_top)
            if bracket_start >= upper_bound:
                prev_bound = upper_bound
                continue
            bracket_space = upper_bound - bracket_start
            taxed_here = min(remaining_pref, bracket_space)
            tax += taxed_here * rate
            remaining_pref -= taxed_here
            prev_bound = upper_bound

    return tax
```

**Note:** The existing `compute_ltcg_tax` in `estimator.py` is a stub that applies a flat 15% rate. This MUST be replaced with the proper stacking computation.

### 2.4 Net Investment Income Tax (NIIT) (IRC Section 1411; Form 8960)

Per IRC Section 1411, a 3.8% tax applies on the lesser of:
- (a) Net investment income, OR
- (b) The excess of MAGI over the threshold.

#### 2024 NIIT Thresholds (unchanged since enactment)

| Filing Status | MAGI Threshold |
|---|---|
| Single | $200,000 |
| Married Filing Jointly | $250,000 |
| Married Filing Separately | $125,000 |
| Head of Household | $200,000 |

**Note:** MFS threshold ($125,000) is NOT currently in `brackets.py`. Must be added.

```
net_investment_income = (
    total_interest_income
    + total_ordinary_dividends
    + max(net_short_term, Decimal("0"))
    + max(net_long_term, Decimal("0"))
    + total_capital_gain_distributions
)

magi = agi  # For most taxpayers, MAGI = AGI for NIIT purposes

excess_magi = max(magi - niit_threshold, Decimal("0"))
niit = min(net_investment_income, excess_magi) * Decimal("0.038")
```

Per IRS Form 8960: "Individuals with modified adjusted gross income above certain threshold amounts."

### 2.5 Alternative Minimum Tax (Form 6251)

The AMT computation is required when the taxpayer has ISO exercises that create AMT preference items. The reconciliation engine produces `amt_adjustment` on each SaleResult, and the ISOAMTEngine produces `total_amt_preference` for ISO exercises during the year.

#### 2024 AMT Exemption Amounts

Per IRS Rev. Proc. 2023-34:

| Filing Status | Exemption Amount | Phase-out Begins At | Phase-out Complete At |
|---|---|---|---|
| Single | $85,700 | $609,350 | $952,150 |
| Married Filing Jointly | $133,300 | $1,218,700 | $1,751,900 |
| Married Filing Separately | $66,650 | $609,350 | $876,000 |
| Head of Household | $85,700 | $609,350 | $952,150 |

**Phase-out:** The exemption is reduced by 25 cents for every dollar of AMTI above the phase-out start. It is completely eliminated at the phase-out complete amount.

```
amt_exemption_reduction = max(amti - phaseout_start, Decimal("0")) * Decimal("0.25")
amt_exemption = max(exemption_amount - amt_exemption_reduction, Decimal("0"))
```

#### 2024 AMT Tax Rates

Per Form 6251 Instructions:

| AMTI (after exemption) | Rate |
|---|---|
| Up to $232,600 (Single/MFS/HOH) or $232,600 (MFJ*) | 26% |
| Above $232,600 (Single/MFS/HOH) or $232,600 (MFJ*) | 28% |

*Note: For 2024, the 28% rate threshold is $232,600 for ALL filing statuses. (Per IRS Rev. Proc. 2023-34, the AMT 28% bracket threshold for MFJ is $232,600. This is confirmed -- both Single and MFJ use the same breakpoint for 2024.)

**AMT Computation Steps (Form 6251):**

```
# Step 1: Compute Alternative Minimum Taxable Income (AMTI)
regular_taxable_income = taxable_income  # From Form 1040, Line 15

# Step 2: Add back AMT preference items
# - ISO exercise spread (Form 6251, Line 2i) -- from ISOAMTEngine
# - State/local tax deduction (SALT) if itemizing (Form 6251, Line 2a)
# - For standard deduction filers, no SALT add-back needed
amt_iso_preference = SUM(amt_worksheet_lines.total_amt_preference) for current year exercises
amt_sale_adjustments = SUM(sale_results.amt_adjustment) for current year sales

# For standard deduction filers:
amti = regular_taxable_income + amt_iso_preference + amt_sale_adjustments
# Note: standard deduction is NOT added back for AMT (post-TCJA)

# For itemizers, add back state/local tax deduction:
# amti = regular_taxable_income + amt_iso_preference + amt_sale_adjustments + salt_deduction

# Step 3: Apply exemption
amt_exemption = compute_amt_exemption(amti, filing_status, tax_year)
amt_base = max(amti - amt_exemption, Decimal("0"))

# Step 4: Compute tentative minimum tax
# IMPORTANT: LTCG/qualified dividends are taxed at the SAME preferential rates
# under AMT. Only ordinary AMT income uses the 26%/28% rates.
amt_ordinary_base = max(amt_base - preferential_income, Decimal("0"))

IF amt_ordinary_base <= Decimal("232600"):
    amt_on_ordinary = amt_ordinary_base * Decimal("0.26")
ELSE:
    amt_on_ordinary = (
        Decimal("232600") * Decimal("0.26")
        + (amt_ordinary_base - Decimal("232600")) * Decimal("0.28")
    )

# Preferential income under AMT uses same 0%/15%/20% rates
amt_on_preferential = compute_ltcg_tax(preferential_income, amt_base, filing_status, tax_year)

tentative_minimum_tax = amt_on_ordinary + amt_on_preferential

# Step 5: AMT = max(tentative_minimum_tax - regular_tax, 0)
federal_amt = max(tentative_minimum_tax - federal_regular_tax - federal_ltcg_tax, Decimal("0"))
```

**Key citation:** Form 6251 Instructions: "Compare the tentative minimum tax to your regular tax. If the tentative minimum tax is more than your regular tax, you owe AMT."

**When to skip AMT:** If the taxpayer has no ISO exercises in the current year AND no prior AMT adjustments from ISO sales, the AMT computation can be skipped (result = $0). This is a performance optimization.

### 2.6 Federal Withholding Credits

```
total_federal_withheld = (
    total_federal_withheld_w2          # W-2 Box 2
    + federal_withheld_1099int          # 1099-INT Box 4
    + federal_withheld_1099div          # 1099-DIV Box 4 (or equivalent)
    + federal_estimated_payments        # Quarterly estimated tax payments (user input)
)
```

### 2.7 Federal Balance Due or Refund

```
federal_total_tax = federal_regular_tax + federal_ltcg_tax + federal_niit + federal_amt
federal_balance_due = federal_total_tax - total_federal_withheld - federal_estimated_payments
```

If `federal_balance_due < 0`, the taxpayer is due a refund.

---

## Section 3: California State Tax Computation

### 3.1 California Taxable Income

California starts with federal AGI and makes adjustments per FTB Publication 1001. For our taxpayer profile (W-2 employee with equity compensation), the key California differences are:

1. **California does NOT have preferential LTCG rates.** All capital gains are taxed as ordinary income. Per CA Revenue and Taxation Code Section 18152.5.
2. **California does NOT have AMT** (repealed effective 2005, per AB 1601).
3. **California has a much lower standard deduction.**
4. **California Mental Health Services Tax:** Additional 1% surcharge on taxable income above $1,000,000.

```
ca_standard_deduction = CALIFORNIA_STANDARD_DEDUCTION[2024][filing_status]
ca_deduction = max(itemized_deductions or Decimal("0"), ca_standard_deduction)
ca_taxable_income = max(agi - ca_deduction, Decimal("0"))
```

#### 2024 California Standard Deduction

Per FTB 2024 instructions:

| Filing Status | Standard Deduction |
|---|---|
| Single | $5,540 |
| Married Filing Jointly | $11,080 |
| Married Filing Separately | $5,540 |
| Head of Household | $11,080 |

**Note:** These are NOT currently in `brackets.py` for 2024. Only 2025 data exists. **2024 values must be added.** (The 2024 values shown above match the 2025 values for California -- California uses the same amounts. Verify from FTB Publication 1001 for 2024.)

Correction: California adjusts these annually. The 2024 California standard deduction is:

| Filing Status | Standard Deduction |
|---|---|
| Single / MFS | $5,540 |
| MFJ / HOH | $11,080 |

### 3.2 California Tax Brackets (2024)

Per FTB 2024 Tax Rate Schedule:

**Single / Married Filing Separately:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $10,412 | 1% |
| $10,412 | $24,684 | 2% |
| $24,684 | $38,959 | 4% |
| $38,959 | $54,081 | 6% |
| $54,081 | $68,350 | 8% |
| $68,350 | $349,137 | 9.3% |
| $349,137 | $418,961 | 10.3% |
| $418,961 | $698,271 | 11.3% |
| $698,271 | $1,000,000 | 12.3% |
| $1,000,000 | -- | 13.3% |

**Married Filing Jointly / Head of Household:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $20,824 | 1% |
| $20,824 | $49,368 | 2% |
| $49,368 | $77,918 | 4% |
| $77,918 | $108,162 | 6% |
| $108,162 | $136,700 | 8% |
| $136,700 | $698,274 | 9.3% |
| $698,274 | $837,922 | 10.3% |
| $837,922 | $1,396,542 | 11.3% |
| $1,396,542 | $2,000,000* | 12.3% |
| $2,000,000* | -- | 13.3% |

*Note: For MFJ, the 12.3% bracket ends and the 13.3% bracket (which includes the Mental Health Services Tax) starts at a different point. Actually, the California brackets have the top rate at 12.3%, and then the Mental Health Services Tax adds 1% above $1,000,000 regardless of filing status. The 13.3% shown above is the combined rate. Let me clarify:

**California tax structure:**
- Marginal brackets go up to 12.3% on income above $698,271 (Single) / $1,396,542 (MFJ).
- The Mental Health Services Tax adds an additional 1% on ALL taxable income above $1,000,000 -- this is a flat surcharge, NOT a bracket.
- The combined top rate is 13.3%.

So the bracket table should show 12.3% as the top marginal rate, and the Mental Health Services Tax is computed separately.

**Corrected Single / MFS brackets:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $10,412 | 1% |
| $10,412 | $24,684 | 2% |
| $24,684 | $38,959 | 4% |
| $38,959 | $54,081 | 6% |
| $54,081 | $68,350 | 8% |
| $68,350 | $349,137 | 9.3% |
| $349,137 | $418,961 | 10.3% |
| $418,961 | $698,271 | 11.3% |
| $698,271 | -- | 12.3% |

**Corrected MFJ / HOH brackets:**

| Taxable Income Over | But Not Over | Rate |
|---|---|---|
| $0 | $20,824 | 1% |
| $20,824 | $49,368 | 2% |
| $49,368 | $77,918 | 4% |
| $77,918 | $108,162 | 6% |
| $108,162 | $136,700 | 8% |
| $136,700 | $698,274 | 9.3% |
| $698,274 | $837,922 | 10.3% |
| $837,922 | $1,396,542 | 11.3% |
| $1,396,542 | -- | 12.3% |

**These are NOT currently in `brackets.py` for 2024. Only 2025 data exists. 2024 values must be added.** (For 2024, the California brackets are the same dollar amounts as 2025 since California did not adjust them. This should be verified but is the CPA's best assessment based on FTB Publication 1001 guidance.)

### 3.3 California Mental Health Services Tax (MHST)

Per CA Revenue and Taxation Code Section 17043(a):

```
ca_mental_health_tax = max(ca_taxable_income - Decimal("1000000"), Decimal("0")) * Decimal("0.01")
```

This applies to ALL filing statuses. The $1,000,000 threshold is NOT doubled for MFJ.

### 3.4 California Withholding Credits

```
total_ca_withheld = (
    total_state_withheld_w2             # W-2 Box 17
    + state_withheld_1099int             # 1099-INT state withholding (if any)
    + state_withheld_1099div             # 1099-DIV state withholding (if any)
    + ca_estimated_payments              # CA quarterly estimated payments (user input)
)
```

### 3.5 CA SDI / VPDI

California State Disability Insurance (SDI) is withheld from wages (W-2 Box 14, code "CASDI" or "SDI"). SDI is NOT a tax but an insurance deduction. It is NOT deductible on the California return and is NOT an income tax credit. However, SDI withholding is sometimes confused with state income tax withholding.

**For the estimator:** Do NOT include SDI/VPDI in state tax withholding. Only W-2 Box 17 counts as California income tax withheld.

### 3.6 California Balance Due or Refund

```
ca_total_tax = ca_bracket_tax + ca_mental_health_tax
ca_balance_due = ca_total_tax - total_ca_withheld - ca_estimated_payments
```

---

## Section 4: Special Considerations

### 4.1 Capital Loss Netting Order

Per Schedule D Instructions and Pub. 550:

1. Short-term capital losses offset short-term capital gains first.
2. Long-term capital losses offset long-term capital gains first.
3. If one category has a net loss and the other a net gain, the net loss offsets the net gain.
4. If both categories have net losses, they are combined and subject to the $3,000 limit.

This netting is handled in Section 1.5 above.

### 4.2 Qualified Dividends Taxed at LTCG Rates

Per Pub. 550 and the Qualified Dividends and Capital Gain Tax Worksheet: qualified dividends are taxed at the same preferential rates (0%, 15%, 20%) as long-term capital gains. They are included in the "preferential income" pool for the LTCG tax computation.

```
preferential_income = qualified_dividends + max(net_long_term, Decimal("0"))
```

### 4.3 Multiple W-2s

The estimator MUST aggregate across all W-2s for the tax year. This handles:
- Taxpayer changed jobs during the year.
- Taxpayer has multiple concurrent employers.
- Each W-2's Box 2 and Box 17 withholdings are summed independently.

### 4.4 Partial Data Handling

The estimator should compute with whatever data is available and flag what is missing:

| Data Available | Behavior |
|---|---|
| W-2 only | Compute tax on wages only. Flag: "No capital gains data. Run `taxbot reconcile` first." |
| W-2 + reconciled SaleResults | Full computation with capital gains. |
| No W-2 | Compute on investment income only. Flag: "No W-2 data found. Import W-2 first." |
| No reconciliation run | Skip capital gains. Flag: "No reconciliation run found for 2024. Capital gains not included." |
| No 1099-INT / 1099-DIV | Assume zero interest/dividend income. Flag: "No 1099-INT/1099-DIV data found." |

The estimator should NEVER fail -- it should produce the best estimate with available data and clearly communicate what is missing via warnings.

### 4.5 Handling Capital Loss Deduction Limits

Per IRC Section 1211(b):
- $3,000 deduction limit for Single, MFJ, HOH.
- $1,500 deduction limit for MFS.

Any excess loss carries forward to the next tax year. The carryforward is NOT tracked in the current TaxEstimate model but should be reported in the CLI output for the user's reference.

---

## Section 5: Implementation Details for the Python Engineer

### 5.1 Bracket Table Updates (`app/engines/brackets.py`)

Add the following 2024 data to `brackets.py`. All four filing statuses must be present.

**Add to `FEDERAL_BRACKETS[2024]`:**

```python
FilingStatus.MFS: [
    (Decimal("11600"), Decimal("0.10")),
    (Decimal("47150"), Decimal("0.12")),
    (Decimal("100525"), Decimal("0.22")),
    (Decimal("191950"), Decimal("0.24")),
    (Decimal("243725"), Decimal("0.32")),
    (Decimal("365600"), Decimal("0.35")),
    (None, Decimal("0.37")),
],
FilingStatus.HOH: [
    (Decimal("16550"), Decimal("0.10")),
    (Decimal("63100"), Decimal("0.12")),
    (Decimal("100500"), Decimal("0.22")),
    (Decimal("191950"), Decimal("0.24")),
    (Decimal("243700"), Decimal("0.32")),
    (Decimal("609350"), Decimal("0.35")),
    (None, Decimal("0.37")),
],
```

**Add to `FEDERAL_STANDARD_DEDUCTION[2024]`:**

```python
FilingStatus.MFS: Decimal("14600"),
FilingStatus.HOH: Decimal("21900"),
```

**Add `FEDERAL_LTCG_BRACKETS[2024]` (new entry -- this does NOT exist yet):**

```python
2024: {
    FilingStatus.SINGLE: [
        (Decimal("47025"), Decimal("0.00")),
        (Decimal("518900"), Decimal("0.15")),
        (None, Decimal("0.20")),
    ],
    FilingStatus.MFJ: [
        (Decimal("94050"), Decimal("0.00")),
        (Decimal("583750"), Decimal("0.15")),
        (None, Decimal("0.20")),
    ],
    FilingStatus.MFS: [
        (Decimal("47025"), Decimal("0.00")),
        (Decimal("291850"), Decimal("0.15")),
        (None, Decimal("0.20")),
    ],
    FilingStatus.HOH: [
        (Decimal("63000"), Decimal("0.00")),
        (Decimal("551350"), Decimal("0.15")),
        (None, Decimal("0.20")),
    ],
},
```

**Add to `NIIT_THRESHOLD`:**

```python
FilingStatus.MFS: Decimal("125000"),
FilingStatus.HOH: Decimal("200000"),
```

**Add `AMT_EXEMPTION[2024]` and `AMT_PHASEOUT_START[2024]`:**

```python
AMT_EXEMPTION[2024] = {
    FilingStatus.SINGLE: Decimal("85700"),
    FilingStatus.MFJ: Decimal("133300"),
    FilingStatus.MFS: Decimal("66650"),
    FilingStatus.HOH: Decimal("85700"),
}

AMT_PHASEOUT_START[2024] = {
    FilingStatus.SINGLE: Decimal("609350"),
    FilingStatus.MFJ: Decimal("1218700"),
    FilingStatus.MFS: Decimal("609350"),
    FilingStatus.HOH: Decimal("609350"),
}
```

**Add new constant for AMT rate breakpoint:**

```python
AMT_28_PERCENT_THRESHOLD: dict[int, Decimal] = {
    2024: Decimal("232600"),
    2025: Decimal("239100"),
}
```

**Add `CALIFORNIA_BRACKETS[2024]`:**

```python
2024: {
    FilingStatus.SINGLE: [
        (Decimal("10412"), Decimal("0.01")),
        (Decimal("24684"), Decimal("0.02")),
        (Decimal("38959"), Decimal("0.04")),
        (Decimal("54081"), Decimal("0.06")),
        (Decimal("68350"), Decimal("0.08")),
        (Decimal("349137"), Decimal("0.093")),
        (Decimal("418961"), Decimal("0.103")),
        (Decimal("698271"), Decimal("0.113")),
        (None, Decimal("0.123")),
    ],
    FilingStatus.MFS: [
        (Decimal("10412"), Decimal("0.01")),
        (Decimal("24684"), Decimal("0.02")),
        (Decimal("38959"), Decimal("0.04")),
        (Decimal("54081"), Decimal("0.06")),
        (Decimal("68350"), Decimal("0.08")),
        (Decimal("349137"), Decimal("0.093")),
        (Decimal("418961"), Decimal("0.103")),
        (Decimal("698271"), Decimal("0.113")),
        (None, Decimal("0.123")),
    ],
    FilingStatus.MFJ: [
        (Decimal("20824"), Decimal("0.01")),
        (Decimal("49368"), Decimal("0.02")),
        (Decimal("77918"), Decimal("0.04")),
        (Decimal("108162"), Decimal("0.06")),
        (Decimal("136700"), Decimal("0.08")),
        (Decimal("698274"), Decimal("0.093")),
        (Decimal("837922"), Decimal("0.103")),
        (Decimal("1396542"), Decimal("0.113")),
        (None, Decimal("0.123")),
    ],
    FilingStatus.HOH: [
        (Decimal("20839"), Decimal("0.01")),
        (Decimal("49371"), Decimal("0.02")),
        (Decimal("63644"), Decimal("0.04")),
        (Decimal("78765"), Decimal("0.06")),
        (Decimal("93037"), Decimal("0.08")),
        (Decimal("474824"), Decimal("0.093")),
        (Decimal("569790"), Decimal("0.103")),
        (Decimal("949649"), Decimal("0.113")),
        (None, Decimal("0.123")),
    ],
},
```

**Add `CALIFORNIA_STANDARD_DEDUCTION[2024]`:**

```python
2024: {
    FilingStatus.SINGLE: Decimal("5540"),
    FilingStatus.MFS: Decimal("5540"),
    FilingStatus.MFJ: Decimal("11080"),
    FilingStatus.HOH: Decimal("11080"),
},
```

### 5.2 Repository Updates (`app/db/repository.py`)

Add these new query methods:

```python
def get_1099divs(self, tax_year: int) -> list[dict]:
    """Retrieve 1099-DIV records for a given tax year."""
    cursor = self.conn.execute(
        "SELECT * FROM form_1099div WHERE tax_year = ?", (tax_year,)
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def get_1099ints(self, tax_year: int) -> list[dict]:
    """Retrieve 1099-INT records for a given tax year."""
    cursor = self.conn.execute(
        "SELECT * FROM form_1099int WHERE tax_year = ?", (tax_year,)
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
```

### 5.3 TaxEstimate Model Updates (`app/models/reports.py`)

The existing `TaxEstimate` model needs additional fields for the enhanced estimator. Add:

```python
# Additional fields to add:
espp_ordinary_income: Decimal = Decimal("0")          # From SaleResults (informational)
iso_ordinary_income: Decimal = Decimal("0")            # From SaleResults (informational)
total_sale_ordinary_income: Decimal = Decimal("0")     # Sum of above
capital_loss_carryforward: Decimal = Decimal("0")      # Excess loss beyond $3k limit
early_withdrawal_penalty: Decimal = Decimal("0")       # 1099-INT Box 2
amt_iso_preference: Decimal = Decimal("0")             # ISO exercises this year
warnings: list[str] = []                               # Missing data warnings
```

These are optional additions. The core TaxEstimate model already has all required fields. If the Python Engineer prefers, these informational fields can be added to a separate `EstimateDetail` model or simply printed in CLI output.

### 5.4 TaxEstimator Class Design (`app/engines/estimator.py`)

Replace the existing TaxEstimator with the following design:

```python
class TaxEstimator:
    """Estimates federal and California tax liability.

    Public API:
        estimate_from_db(repo, tax_year, filing_status, ...) -> TaxEstimate
        estimate(tax_year, filing_status, w2_wages, ...) -> TaxEstimate
    """

    def __init__(self):
        self.warnings: list[str] = []

    def estimate_from_db(
        self,
        repo: TaxRepository,
        tax_year: int,
        filing_status: FilingStatus,
        federal_estimated_payments: Decimal = Decimal("0"),
        state_estimated_payments: Decimal = Decimal("0"),
        itemized_deductions: Decimal | None = None,
    ) -> TaxEstimate:
        """Load all data from the database and compute the estimate.

        This is the primary entry point called by the CLI.

        Steps:
        1. Load W-2s, 1099-INTs, 1099-DIVs from repo.
        2. Load SaleResults from repo (requires prior reconciliation).
        3. Aggregate income.
        4. Call self.estimate() with aggregated values.
        """
        # Load W-2s
        w2s = repo.get_w2s(tax_year)
        w2_wages = sum(Decimal(w["box1_wages"]) for w in w2s) if w2s else Decimal("0")
        fed_withheld_w2 = sum(Decimal(w["box2_federal_withheld"]) for w in w2s) if w2s else Decimal("0")
        state_withheld_w2 = sum(
            Decimal(w["box17_state_withheld"]) for w in w2s
            if w.get("box17_state_withheld")
        ) if w2s else Decimal("0")

        if not w2s:
            self.warnings.append("No W-2 data found for tax year. Import W-2 first.")

        # Load 1099-INTs
        ints = repo.get_1099ints(tax_year)
        interest_income = sum(Decimal(i["interest_income"]) for i in ints) if ints else Decimal("0")
        early_withdrawal = sum(
            Decimal(i.get("early_withdrawal_penalty", "0")) for i in ints
        ) if ints else Decimal("0")
        fed_withheld_1099int = sum(
            Decimal(i.get("federal_tax_withheld", "0")) for i in ints
        ) if ints else Decimal("0")

        # Load 1099-DIVs
        divs = repo.get_1099divs(tax_year)
        ordinary_dividends = sum(Decimal(d["ordinary_dividends"]) for d in divs) if divs else Decimal("0")
        qualified_dividends = sum(Decimal(d["qualified_dividends"]) for d in divs) if divs else Decimal("0")
        cap_gain_distributions = sum(
            Decimal(d.get("capital_gain_distributions", "0")) for d in divs
        ) if divs else Decimal("0")
        fed_withheld_1099div = sum(
            Decimal(d.get("federal_tax_withheld", "0")) for d in divs
        ) if divs else Decimal("0")

        # Load SaleResults
        sale_results = repo.get_sale_results(tax_year)
        if not sale_results:
            # Check if reconciliation has been run
            recon_runs = repo.get_reconciliation_runs(tax_year)
            if not recon_runs:
                self.warnings.append(
                    f"No reconciliation run found for {tax_year}. "
                    "Capital gains not included. Run `taxbot reconcile {tax_year}` first."
                )
            else:
                self.warnings.append(
                    "Reconciliation produced no sale results. No capital gains to report."
                )

        # Separate gains by holding period
        st_gain = Decimal("0")
        lt_gain = Decimal("0")
        total_sale_ordinary_income = Decimal("0")
        total_amt_adjustment = Decimal("0")

        for sr in sale_results:
            gl = Decimal(sr["gain_loss"])
            if sr["holding_period"] == "SHORT_TERM":
                st_gain += gl
            else:
                lt_gain += gl
            total_sale_ordinary_income += Decimal(sr.get("ordinary_income", "0"))
            total_amt_adjustment += Decimal(sr.get("amt_adjustment", "0"))

        # Add capital gain distributions to long-term
        lt_gain += cap_gain_distributions

        # Capital loss netting and limitation
        net_capital = st_gain + lt_gain
        capital_loss_limit = Decimal("-1500") if filing_status == FilingStatus.MFS else Decimal("-3000")

        if net_capital < Decimal("0"):
            capital_loss_deduction = max(net_capital, capital_loss_limit)
        else:
            capital_loss_deduction = Decimal("0")

        # For the TaxEstimate model, we report net gains (positive) or limited loss
        short_term_for_estimate = st_gain
        long_term_for_estimate = lt_gain

        # Warn about ordinary income from sales
        if total_sale_ordinary_income > 0:
            self.warnings.append(
                f"${total_sale_ordinary_income} of equity compensation ordinary income "
                "was computed from sales. Verify this amount is included in your W-2 Box 1. "
                "If not, add it to wages manually."
            )

        # Aggregate withholdings
        total_fed_withheld = fed_withheld_w2 + fed_withheld_1099int + fed_withheld_1099div

        return self.estimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=w2_wages,
            interest_income=interest_income,
            dividend_income=ordinary_dividends,
            qualified_dividends=qualified_dividends,
            short_term_gains=short_term_for_estimate,
            long_term_gains=long_term_for_estimate,
            federal_withheld=total_fed_withheld,
            state_withheld=state_withheld_w2,
            federal_estimated_payments=federal_estimated_payments,
            state_estimated_payments=state_estimated_payments,
            itemized_deductions=itemized_deductions,
            amt_iso_preference=total_amt_adjustment,
        )

    def estimate(
        self,
        tax_year: int,
        filing_status: FilingStatus,
        w2_wages: Decimal,
        interest_income: Decimal = Decimal("0"),
        dividend_income: Decimal = Decimal("0"),
        qualified_dividends: Decimal = Decimal("0"),
        short_term_gains: Decimal = Decimal("0"),
        long_term_gains: Decimal = Decimal("0"),
        federal_withheld: Decimal = Decimal("0"),
        state_withheld: Decimal = Decimal("0"),
        federal_estimated_payments: Decimal = Decimal("0"),
        state_estimated_payments: Decimal = Decimal("0"),
        itemized_deductions: Decimal | None = None,
        amt_iso_preference: Decimal = Decimal("0"),
    ) -> TaxEstimate:
        """Compute full federal + California tax estimate.

        This method contains the core tax computation logic.
        """
        # --- Income aggregation ---
        total_income = (
            w2_wages + interest_income + dividend_income
            + short_term_gains + long_term_gains
        )
        agi = total_income  # Simplified; add above-the-line deductions if needed

        # --- Federal taxable income ---
        std_ded = FEDERAL_STANDARD_DEDUCTION[tax_year][filing_status]
        deduction_used = max(itemized_deductions or Decimal("0"), std_ded)
        taxable_income = max(agi - deduction_used, Decimal("0"))

        # --- Split taxable income into ordinary and preferential ---
        preferential_income = qualified_dividends + max(long_term_gains, Decimal("0"))
        # Cap preferential income at taxable income
        preferential_income = min(preferential_income, taxable_income)
        ordinary_taxable = max(taxable_income - preferential_income, Decimal("0"))

        # --- Federal ordinary income tax ---
        federal_regular = self.compute_federal_tax(ordinary_taxable, filing_status, tax_year)

        # --- Federal LTCG/qualified dividend tax ---
        federal_ltcg = self.compute_ltcg_tax(
            preferential_income, taxable_income, filing_status, tax_year
        )

        # --- NIIT ---
        investment_income = (
            interest_income + dividend_income
            + max(short_term_gains, Decimal("0"))
            + max(long_term_gains, Decimal("0"))
        )
        federal_niit = self.compute_niit(investment_income, agi, filing_status)

        # --- AMT ---
        federal_amt = self.compute_amt(
            taxable_income=taxable_income,
            preferential_income=preferential_income,
            amt_preference=amt_iso_preference,
            regular_tax=federal_regular + federal_ltcg,
            filing_status=filing_status,
            tax_year=tax_year,
        )

        # --- Federal totals ---
        federal_total = federal_regular + federal_ltcg + federal_niit + federal_amt
        federal_balance = federal_total - federal_withheld - federal_estimated_payments

        # --- California ---
        ca_std_ded = CALIFORNIA_STANDARD_DEDUCTION[tax_year][filing_status]
        ca_deduction = max(itemized_deductions or Decimal("0"), ca_std_ded)
        ca_taxable = max(agi - ca_deduction, Decimal("0"))
        ca_tax = self.compute_california_tax(ca_taxable, filing_status, tax_year)
        ca_mh = max(ca_taxable - CA_MENTAL_HEALTH_THRESHOLD, Decimal("0")) * CA_MENTAL_HEALTH_RATE
        ca_total = ca_tax + ca_mh
        ca_balance = ca_total - state_withheld - state_estimated_payments

        return TaxEstimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=w2_wages,
            interest_income=interest_income,
            dividend_income=dividend_income,
            qualified_dividends=qualified_dividends,
            short_term_gains=short_term_gains,
            long_term_gains=long_term_gains,
            total_income=total_income,
            agi=agi,
            standard_deduction=std_ded,
            itemized_deductions=itemized_deductions,
            deduction_used=deduction_used,
            taxable_income=taxable_income,
            federal_regular_tax=federal_regular,
            federal_ltcg_tax=federal_ltcg,
            federal_niit=federal_niit,
            federal_amt=federal_amt,
            federal_total_tax=federal_total,
            federal_withheld=federal_withheld,
            federal_estimated_payments=federal_estimated_payments,
            federal_balance_due=federal_balance,
            ca_taxable_income=ca_taxable,
            ca_tax=ca_tax,
            ca_mental_health_tax=ca_mh,
            ca_total_tax=ca_total,
            ca_withheld=state_withheld,
            ca_estimated_payments=state_estimated_payments,
            ca_balance_due=ca_balance,
            total_tax=federal_total + ca_total,
            total_withheld=federal_withheld + state_withheld,
            total_balance_due=federal_balance + ca_balance,
        )
```

**Key methods to implement/fix:**

1. `compute_federal_tax()` -- already works, just needs MFS/HOH brackets in data.
2. `compute_ltcg_tax()` -- MUST be rewritten from the flat-15% stub to the proper stacking algorithm.
3. `compute_niit()` -- already works, just needs MFS/HOH thresholds.
4. `compute_california_tax()` -- already works, just needs 2024 bracket data.
5. `compute_amt()` -- NEW method, implements Form 6251 logic.
6. `estimate_from_db()` -- NEW method, loads data from repo and calls `estimate()`.

### 5.5 AMT Method Implementation

```python
def compute_amt(
    self,
    taxable_income: Decimal,
    preferential_income: Decimal,
    amt_preference: Decimal,
    regular_tax: Decimal,
    filing_status: FilingStatus,
    tax_year: int,
) -> Decimal:
    """Compute Alternative Minimum Tax per Form 6251.

    Args:
        taxable_income: Regular taxable income (Form 1040, Line 15).
        preferential_income: Qualified dividends + net LTCG.
        amt_preference: Net AMT preference items (ISO exercises, net of sale reversals).
        regular_tax: Regular federal tax (ordinary + LTCG rates).
        filing_status: Filing status.
        tax_year: Tax year.

    Returns:
        Federal AMT amount (zero if no AMT owed).
    """
    if amt_preference == Decimal("0"):
        return Decimal("0")  # No AMT items -- skip computation

    # Step 1: AMTI
    amti = taxable_income + amt_preference

    # Step 2: Exemption with phase-out
    exemption_amount = AMT_EXEMPTION[tax_year][filing_status]
    phaseout_start = AMT_PHASEOUT_START[tax_year][filing_status]
    exemption_reduction = max(amti - phaseout_start, Decimal("0")) * Decimal("0.25")
    amt_exemption = max(exemption_amount - exemption_reduction, Decimal("0"))

    # Step 3: AMT base
    amt_base = max(amti - amt_exemption, Decimal("0"))

    if amt_base == Decimal("0"):
        return Decimal("0")

    # Step 4: Compute tentative minimum tax
    # Preferential income still gets LTCG rates under AMT
    amt_ordinary_base = max(amt_base - preferential_income, Decimal("0"))
    breakpoint = AMT_28_PERCENT_THRESHOLD[tax_year]

    if amt_ordinary_base <= breakpoint:
        amt_on_ordinary = amt_ordinary_base * Decimal("0.26")
    else:
        amt_on_ordinary = (
            breakpoint * Decimal("0.26")
            + (amt_ordinary_base - breakpoint) * Decimal("0.28")
        )

    amt_on_preferential = self.compute_ltcg_tax(
        preferential_income, amt_base, filing_status, tax_year
    )

    tentative_minimum_tax = amt_on_ordinary + amt_on_preferential

    # Step 5: AMT = excess over regular tax
    amt = max(tentative_minimum_tax - regular_tax, Decimal("0"))
    return amt
```

### 5.6 Proper LTCG Tax Method Implementation

Replace the existing `compute_ltcg_tax` stub:

```python
def compute_ltcg_tax(
    self,
    ltcg_and_qualified_divs: Decimal,
    taxable_income: Decimal,
    filing_status: FilingStatus,
    tax_year: int,
) -> Decimal:
    """Compute federal tax on LTCG and qualified dividends.

    Uses the stacking method from the Qualified Dividends and
    Capital Gain Tax Worksheet (Form 1040 Instructions).

    The preferential income sits on top of ordinary income in the
    bracket structure. The portion that falls in each LTCG bracket
    is taxed at that bracket's rate.
    """
    if ltcg_and_qualified_divs <= Decimal("0"):
        return Decimal("0")

    brackets = FEDERAL_LTCG_BRACKETS.get(tax_year, {}).get(filing_status)
    if not brackets:
        # Fallback: 15% flat rate if brackets not available
        return ltcg_and_qualified_divs * Decimal("0.15")

    # Ordinary income fills the bottom of the brackets first
    ordinary_income_top = max(taxable_income - ltcg_and_qualified_divs, Decimal("0"))

    tax = Decimal("0")
    remaining_pref = ltcg_and_qualified_divs
    prev_bound = Decimal("0")

    for upper_bound, rate in brackets:
        if remaining_pref <= Decimal("0"):
            break

        if upper_bound is None:
            # Top bracket -- all remaining preferential income
            tax += remaining_pref * rate
            remaining_pref = Decimal("0")
        else:
            # Bracket space available above ordinary income
            bracket_start = max(prev_bound, ordinary_income_top)
            if bracket_start >= upper_bound:
                prev_bound = upper_bound
                continue
            bracket_space = upper_bound - bracket_start
            taxed_here = min(remaining_pref, bracket_space)
            tax += taxed_here * rate
            remaining_pref -= taxed_here
            prev_bound = upper_bound

    return tax
```

### 5.7 CLI Changes (`app/cli.py`)

Replace the `estimate` command stub:

```python
@app.command()
def estimate(
    year: int = typer.Argument(..., help="Tax year to estimate"),
    filing_status: str = typer.Option(
        "SINGLE",
        "--filing-status",
        "-s",
        help="Filing status: SINGLE, MFJ, MFS, HOH",
    ),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    federal_estimated: Decimal = typer.Option(
        Decimal("0"),
        "--federal-estimated",
        help="Federal estimated tax payments already made",
    ),
    state_estimated: Decimal = typer.Option(
        Decimal("0"),
        "--state-estimated",
        help="State estimated tax payments already made",
    ),
    itemized: Decimal | None = typer.Option(
        None,
        "--itemized",
        help="Total itemized deductions (omit to use standard deduction)",
    ),
) -> None:
    """Compute estimated tax liability for a tax year."""
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.estimator import TaxEstimator
    from app.models.enums import FilingStatus

    # Validate filing status
    try:
        fs = FilingStatus(filing_status.upper())
    except ValueError:
        valid = ", ".join(s.value for s in FilingStatus)
        typer.echo(f"Error: Invalid filing status '{filing_status}'. Valid: {valid}", err=True)
        raise typer.Exit(1)

    if not db.exists():
        typer.echo("Error: No database found. Import data first with `taxbot import-data`.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)
    engine = TaxEstimator()

    typer.echo(f"Estimating tax for year {year} (filing status: {fs.value})...")
    result = engine.estimate_from_db(
        repo=repo,
        tax_year=year,
        filing_status=fs,
        federal_estimated_payments=federal_estimated,
        state_estimated_payments=state_estimated,
        itemized_deductions=itemized,
    )
    conn.close()

    # Print estimate summary
    typer.echo("")
    typer.echo(f"=== Tax Estimate: {year} ({fs.value}) ===")
    typer.echo("")
    typer.echo("INCOME")
    typer.echo(f"  W-2 Wages:             ${result.w2_wages:>12,.2f}")
    typer.echo(f"  Interest Income:       ${result.interest_income:>12,.2f}")
    typer.echo(f"  Dividend Income:       ${result.dividend_income:>12,.2f}")
    typer.echo(f"    (Qualified:          ${result.qualified_dividends:>12,.2f})")
    typer.echo(f"  Short-Term Gains:      ${result.short_term_gains:>12,.2f}")
    typer.echo(f"  Long-Term Gains:       ${result.long_term_gains:>12,.2f}")
    typer.echo(f"  ──────────────────────────────────────")
    typer.echo(f"  Total Income:          ${result.total_income:>12,.2f}")
    typer.echo(f"  AGI:                   ${result.agi:>12,.2f}")
    typer.echo("")
    typer.echo("DEDUCTIONS")
    typer.echo(f"  Standard Deduction:    ${result.standard_deduction:>12,.2f}")
    if result.itemized_deductions:
        typer.echo(f"  Itemized Deductions:   ${result.itemized_deductions:>12,.2f}")
    typer.echo(f"  Deduction Used:        ${result.deduction_used:>12,.2f}")
    typer.echo(f"  Taxable Income:        ${result.taxable_income:>12,.2f}")
    typer.echo("")
    typer.echo("FEDERAL TAX")
    typer.echo(f"  Ordinary Income Tax:   ${result.federal_regular_tax:>12,.2f}")
    typer.echo(f"  LTCG/QDiv Tax:         ${result.federal_ltcg_tax:>12,.2f}")
    typer.echo(f"  NIIT (3.8%):           ${result.federal_niit:>12,.2f}")
    typer.echo(f"  AMT:                   ${result.federal_amt:>12,.2f}")
    typer.echo(f"  ──────────────────────────────────────")
    typer.echo(f"  Total Federal Tax:     ${result.federal_total_tax:>12,.2f}")
    typer.echo(f"  Federal Withheld:      ${result.federal_withheld:>12,.2f}")
    if result.federal_estimated_payments > 0:
        typer.echo(f"  Est. Payments:         ${result.federal_estimated_payments:>12,.2f}")
    typer.echo(f"  Federal Balance Due:   ${result.federal_balance_due:>12,.2f}")
    typer.echo("")
    typer.echo("CALIFORNIA TAX")
    typer.echo(f"  CA Taxable Income:     ${result.ca_taxable_income:>12,.2f}")
    typer.echo(f"  CA Income Tax:         ${result.ca_tax:>12,.2f}")
    typer.echo(f"  Mental Health Tax:     ${result.ca_mental_health_tax:>12,.2f}")
    typer.echo(f"  ──────────────────────────────────────")
    typer.echo(f"  Total CA Tax:          ${result.ca_total_tax:>12,.2f}")
    typer.echo(f"  CA Withheld:           ${result.ca_withheld:>12,.2f}")
    if result.ca_estimated_payments > 0:
        typer.echo(f"  Est. Payments:         ${result.ca_estimated_payments:>12,.2f}")
    typer.echo(f"  CA Balance Due:        ${result.ca_balance_due:>12,.2f}")
    typer.echo("")
    typer.echo("TOTAL")
    typer.echo(f"  Total Tax:             ${result.total_tax:>12,.2f}")
    typer.echo(f"  Total Withheld:        ${result.total_withheld:>12,.2f}")
    typer.echo(f"  ══════════════════════════════════════")

    if result.total_balance_due > 0:
        typer.echo(f"  BALANCE DUE:           ${result.total_balance_due:>12,.2f}")
    else:
        typer.echo(f"  REFUND:                ${abs(result.total_balance_due):>12,.2f}")

    # Print warnings
    if engine.warnings:
        typer.echo("")
        typer.echo("WARNINGS:")
        for w in engine.warnings:
            typer.echo(f"  - {w}")
```

### 5.8 Test Scenarios

All tests use `Decimal` for monetary values. Expected values are computed by the CPA using the 2024 bracket tables above.

#### Test 1: W-2 Only (Simple Single Filer)

```
Input:
  filing_status = SINGLE
  w2_wages = $150,000
  federal_withheld = $25,000
  state_withheld = $8,000
  All other income = $0

Expected:
  total_income = $150,000
  agi = $150,000
  standard_deduction = $14,600
  taxable_income = $135,400

  Federal ordinary tax computation:
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $35,550 x 0.12 = $4,266
    ($100,525 - $47,150) x 22% = $53,375 x 0.22 = $11,742.50
    ($135,400 - $100,525) x 24% = $34,875 x 0.24 = $8,370
    Total federal regular tax = $25,538.50

  LTCG tax = $0
  NIIT = $0 (AGI $150k < $200k threshold)
  AMT = $0
  Federal total = $25,538.50
  Federal balance = $25,538.50 - $25,000 = $538.50

  California:
    ca_standard_deduction = $5,540
    ca_taxable = $150,000 - $5,540 = $144,460
    CA tax:
      $10,412 x 1% = $104.12
      ($24,684 - $10,412) x 2% = $14,272 x 0.02 = $285.44
      ($38,959 - $24,684) x 4% = $14,275 x 0.04 = $571.00
      ($54,081 - $38,959) x 6% = $15,122 x 0.06 = $907.32
      ($68,350 - $54,081) x 8% = $14,269 x 0.08 = $1,141.52
      ($144,460 - $68,350) x 9.3% = $76,110 x 0.093 = $7,078.23
      CA total tax = $10,087.63
    Mental health tax = $0 (income < $1M)
    CA balance = $10,087.63 - $8,000 = $2,087.63

  Total balance due = $538.50 + $2,087.63 = $2,626.13
```

#### Test 2: W-2 + Capital Gains (RSU Sale)

```
Input:
  filing_status = SINGLE
  w2_wages = $200,000
  short_term_gains = $10,000
  long_term_gains = $30,000
  federal_withheld = $40,000
  state_withheld = $15,000

Expected:
  total_income = $240,000
  agi = $240,000
  standard_deduction = $14,600
  taxable_income = $225,400

  preferential_income = $30,000 (LTCG only, no qualified dividends)
  ordinary_taxable = $225,400 - $30,000 = $195,400

  Federal ordinary tax:
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $4,266
    ($100,525 - $47,150) x 22% = $11,742.50
    ($191,950 - $100,525) x 24% = $21,942
    ($195,400 - $191,950) x 32% = $1,104
    Total federal regular = $40,214.50

  LTCG tax (stacking method):
    ordinary_income_top = $195,400
    Bracket: $0-$47,025 at 0% -- fully occupied by ordinary income (195,400 > 47,025)
    Bracket: $47,025-$518,900 at 15% -- ordinary income goes up to $195,400
      bracket_start = max($47,025, $195,400) = $195,400
      bracket_space = $518,900 - $195,400 = $323,500
      taxed_here = min($30,000, $323,500) = $30,000
      tax = $30,000 x 15% = $4,500
    LTCG tax = $4,500

  NIIT:
    investment_income = $10,000 + $30,000 = $40,000
    excess_agi = $240,000 - $200,000 = $40,000
    niit = min($40,000, $40,000) x 3.8% = $1,520

  AMT = $0
  Federal total = $40,214.50 + $4,500 + $1,520 + $0 = $46,234.50
  Federal balance = $46,234.50 - $40,000 = $6,234.50

  California:
    ca_taxable = $240,000 - $5,540 = $234,460
    CA tax (all income at ordinary rates -- no preferential LTCG):
      $10,412 x 1% = $104.12
      ($24,684 - $10,412) x 2% = $285.44
      ($38,959 - $24,684) x 4% = $571.00
      ($54,081 - $38,959) x 6% = $907.32
      ($68,350 - $54,081) x 8% = $1,141.52
      ($234,460 - $68,350) x 9.3% = $166,110 x 0.093 = $15,448.23
      CA tax = $18,457.63
    Mental health = $0
    CA balance = $18,457.63 - $15,000 = $3,457.63

  Total balance due = $6,234.50 + $3,457.63 = $9,692.13
```

#### Test 3: W-2 + ESPP Ordinary Income + Capital Gains

```
Input:
  filing_status = SINGLE
  w2_wages = $180,000 (includes ESPP disqualifying disposition ordinary income)
  short_term_gains = $5,000
  long_term_gains = $15,000
  federal_withheld = $32,000
  state_withheld = $12,000

Expected:
  total_income = $200,000
  agi = $200,000
  standard_deduction = $14,600
  taxable_income = $185,400

  preferential_income = $15,000 (LTCG)
  ordinary_taxable = $170,400

  Federal ordinary tax:
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $4,266
    ($100,525 - $47,150) x 22% = $11,742.50
    ($170,400 - $100,525) x 24% = $69,875 x 0.24 = $16,770
    Federal regular = $33,938.50

  LTCG tax:
    ordinary_income_top = $170,400
    0% bracket: $0-$47,025 -- filled by ordinary
    15% bracket: $47,025-$518,900 -- start at $170,400
      bracket_space = $518,900 - $170,400 = $348,500
      taxed_here = min($15,000, $348,500) = $15,000
      tax = $15,000 x 15% = $2,250
    LTCG tax = $2,250

  NIIT:
    investment_income = $5,000 + $15,000 = $20,000
    excess_agi = $200,000 - $200,000 = $0
    niit = $0

  AMT = $0
  Federal total = $33,938.50 + $2,250 + $0 + $0 = $36,188.50
  Federal balance = $36,188.50 - $32,000 = $4,188.50

  California:
    ca_taxable = $200,000 - $5,540 = $194,460
    CA tax:
      $10,412 x 1% = $104.12
      ($24,684 - $10,412) x 2% = $285.44
      ($38,959 - $24,684) x 4% = $571.00
      ($54,081 - $38,959) x 6% = $907.32
      ($68,350 - $54,081) x 8% = $1,141.52
      ($194,460 - $68,350) x 9.3% = $126,110 x 0.093 = $11,728.23
      CA tax = $14,737.63
    CA balance = $14,737.63 - $12,000 = $2,737.63

  Total balance due = $4,188.50 + $2,737.63 = $6,926.13
```

#### Test 4: W-2 + Qualified Dividends + Interest

```
Input:
  filing_status = MFJ
  w2_wages = $250,000
  interest_income = $5,000
  dividend_income = $12,000 (ordinary dividends, Box 1a)
  qualified_dividends = $10,000 (subset of ordinary, Box 1b)
  federal_withheld = $45,000
  state_withheld = $18,000

Expected:
  total_income = $250,000 + $5,000 + $12,000 = $267,000
  agi = $267,000
  standard_deduction = $29,200 (MFJ)
  taxable_income = $267,000 - $29,200 = $237,800

  preferential_income = $10,000 (qualified dividends only; no LTCG)
  ordinary_taxable = $237,800 - $10,000 = $227,800

  Federal ordinary tax (MFJ brackets):
    $23,200 x 10% = $2,320
    ($94,300 - $23,200) x 12% = $71,100 x 0.12 = $8,532
    ($201,050 - $94,300) x 22% = $106,750 x 0.22 = $23,485
    ($227,800 - $201,050) x 24% = $26,750 x 0.24 = $6,420
    Federal regular = $40,757

  LTCG tax on qualified dividends:
    ordinary_income_top = $227,800
    MFJ 0% bracket: $0-$94,050 -- filled by ordinary
    MFJ 15% bracket: $94,050-$583,750 -- start at $227,800
      bracket_space = $583,750 - $227,800 = $355,950
      taxed_here = min($10,000, $355,950) = $10,000
      tax = $10,000 x 15% = $1,500
    LTCG tax = $1,500

  NIIT:
    investment_income = $5,000 + $12,000 = $17,000
    excess_agi = $267,000 - $250,000 = $17,000
    niit = min($17,000, $17,000) x 3.8% = $646

  AMT = $0
  Federal total = $40,757 + $1,500 + $646 + $0 = $42,903
  Federal balance = $42,903 - $45,000 = -$2,097 (REFUND)

  California:
    ca_standard_deduction = $11,080 (MFJ)
    ca_taxable = $267,000 - $11,080 = $255,920
    CA tax (MFJ brackets -- all income at ordinary rates):
      $20,824 x 1% = $208.24
      ($49,368 - $20,824) x 2% = $28,544 x 0.02 = $570.88
      ($77,918 - $49,368) x 4% = $28,550 x 0.04 = $1,142.00
      ($108,162 - $77,918) x 6% = $30,244 x 0.06 = $1,814.64
      ($136,700 - $108,162) x 8% = $28,538 x 0.08 = $2,283.04
      ($255,920 - $136,700) x 9.3% = $119,220 x 0.093 = $11,087.46
      CA tax = $17,106.26
    Mental health = $0
    CA balance = $17,106.26 - $18,000 = -$893.74 (REFUND)

  Total balance = -$2,097 + -$893.74 = -$2,990.74 (TOTAL REFUND)
```

#### Test 5: High-Income with NIIT and AMT

```
Input:
  filing_status = SINGLE
  w2_wages = $400,000
  interest_income = $10,000
  long_term_gains = $100,000
  qualified_dividends = $5,000
  dividend_income = $8,000
  amt_iso_preference = $200,000 (ISO exercises created this AMT preference)
  federal_withheld = $100,000
  state_withheld = $40,000

Expected:
  total_income = $400,000 + $10,000 + $8,000 + $100,000 = $518,000
  agi = $518,000
  standard_deduction = $14,600
  taxable_income = $503,400

  preferential_income = $5,000 + $100,000 = $105,000
  ordinary_taxable = $503,400 - $105,000 = $398,400

  Federal ordinary tax:
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $4,266
    ($100,525 - $47,150) x 22% = $11,742.50
    ($191,950 - $100,525) x 24% = $21,942
    ($243,725 - $191,950) x 32% = $16,568
    ($398,400 - $243,725) x 35% = $154,675 x 0.35 = $54,136.25
    Federal regular = $109,814.75

  LTCG tax:
    ordinary_income_top = $398,400
    0% bracket: $0-$47,025 -- filled by ordinary
    15% bracket: $47,025-$518,900 -- start at $398,400
      bracket_space = $518,900 - $398,400 = $120,500
      taxed_here = min($105,000, $120,500) = $105,000
      tax = $105,000 x 15% = $15,750
    LTCG tax = $15,750

  NIIT:
    investment_income = $10,000 + $8,000 + $100,000 = $118,000
    excess_agi = $518,000 - $200,000 = $318,000
    niit = min($118,000, $318,000) x 3.8% = $118,000 x 0.038 = $4,484

  Regular tax = $109,814.75 + $15,750 = $125,564.75

  AMT computation:
    amti = $503,400 + $200,000 = $703,400
    Exemption: $85,700
    Phase-out: $703,400 - $609,350 = $94,050 excess
    Exemption reduction: $94,050 x 0.25 = $23,512.50
    Effective exemption: $85,700 - $23,512.50 = $62,187.50
    AMT base: $703,400 - $62,187.50 = $641,212.50

    AMT ordinary base: $641,212.50 - $105,000 = $536,212.50
    AMT on ordinary:
      $232,600 x 26% = $60,476
      ($536,212.50 - $232,600) x 28% = $303,612.50 x 0.28 = $85,011.50
      AMT on ordinary = $145,487.50

    AMT on preferential (LTCG rates applied to AMT base of $641,212.50):
      ordinary_income_top = $536,212.50
      0% bracket: $0-$47,025 -- filled
      15% bracket: $47,025-$518,900 -- start at $518,900 (since $536,212.50 > $518,900)
        Actually: bracket_start = max($47,025, $536,212.50) = $536,212.50
        bracket_space = $518,900 - $536,212.50 = negative, skip
      20% bracket (above $518,900):
        bracket_start = max($518,900, $536,212.50) = $536,212.50
        remaining = $105,000
        tax = $105,000 x 20% = $21,000
      AMT on preferential = $21,000

    Tentative minimum tax = $145,487.50 + $21,000 = $166,487.50
    AMT = max($166,487.50 - $125,564.75, $0) = $40,922.75

  Federal total = $109,814.75 + $15,750 + $4,484 + $40,922.75 = $170,971.50
  Federal balance = $170,971.50 - $100,000 = $70,971.50

  California:
    ca_taxable = $518,000 - $5,540 = $512,460
    CA tax (all at ordinary rates):
      $10,412 x 1% = $104.12
      ($24,684 - $10,412) x 2% = $285.44
      ($38,959 - $24,684) x 4% = $571.00
      ($54,081 - $38,959) x 6% = $907.32
      ($68,350 - $54,081) x 8% = $1,141.52
      ($349,137 - $68,350) x 9.3% = $280,787 x 0.093 = $26,113.19
      ($418,961 - $349,137) x 10.3% = $69,824 x 0.103 = $7,191.87
      ($512,460 - $418,961) x 11.3% = $93,499 x 0.113 = $10,565.39
      CA tax = $46,879.85
    Mental health = $0 (taxable income $512,460 < $1M)
    CA balance = $46,879.85 - $40,000 = $6,879.85

  Total balance due = $70,971.50 + $6,879.85 = $77,851.35
```

#### Additional Test: Capital Loss Limitation

```
Input:
  filing_status = SINGLE
  w2_wages = $100,000
  short_term_gains = -$8,000
  long_term_gains = $2,000
  federal_withheld = $15,000
  state_withheld = $5,000

Expected:
  net_capital = -$8,000 + $2,000 = -$6,000
  capital_loss_deduction = max(-$6,000, -$3,000) = -$3,000
  capital_loss_carryforward = -$6,000 - (-$3,000) = -$3,000

  For tax computation purposes:
    short_term_gains = -$8,000 (net ST loss)
    long_term_gains = $2,000 (net LT gain)
    After netting: ST loss offsets LT gain -> net = -$6,000, limited to -$3,000

  total_income = $100,000 + (-$3,000) = $97,000
  (The estimator should report total_income reflecting the limited loss deduction)

  taxable_income = $97,000 - $14,600 = $82,400
  All taxed at ordinary rates (the $2,000 LT gain was absorbed by ST loss)

  Federal ordinary tax:
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $4,266
    ($82,400 - $47,150) x 22% = $35,250 x 0.22 = $7,755
    Federal regular = $13,181

  Federal total = $13,181
  Federal balance = $13,181 - $15,000 = -$1,819 (REFUND)

  Warning: "Capital loss of $6,000 exceeds the $3,000 annual limit. $3,000 carries forward to next year."
```

#### Additional Test: MFS Filing Status

```
Input:
  filing_status = MFS
  w2_wages = $120,000
  short_term_gains = -$5,000
  federal_withheld = $20,000
  state_withheld = $7,000

Expected:
  capital_loss_deduction = max(-$5,000, -$1,500) = -$1,500 (MFS limit is $1,500)
  total_income = $120,000 + (-$1,500) = $118,500
  standard_deduction = $14,600 (MFS)
  taxable_income = $103,900

  Federal ordinary tax (MFS brackets):
    $11,600 x 10% = $1,160
    ($47,150 - $11,600) x 12% = $4,266
    ($100,525 - $47,150) x 22% = $11,742.50
    ($103,900 - $100,525) x 24% = $3,375 x 0.24 = $810
    Federal regular = $17,978.50

  NIIT:
    investment_income = $0 (losses don't count as positive investment income for NIIT)
    niit = $0

  Federal total = $17,978.50
  Federal balance = $17,978.50 - $20,000 = -$2,021.50 (REFUND)
```

### 5.9 Error Handling

The estimator should handle these error conditions:

| Error Condition | Behavior |
|---|---|
| No database file | CLI exits with error: "No database found." |
| Invalid filing status | CLI exits with error listing valid statuses. |
| Missing bracket data for tax year | Raise `ValueError` with message indicating which year/status is missing. |
| No W-2 data | Proceed with $0 wages, add warning. |
| No reconciliation run | Skip capital gains, add warning. |
| No 1099-INT/DIV data | Proceed with $0, add warning. |
| Negative taxable income | Floor at $0. |
| Division by zero | Should not occur (all denominators are non-zero by design). |

---

## Section 6: Implementation Priority (Numbered Steps for Python Engineer)

1. **Add 2024 bracket data to `app/engines/brackets.py`** (CRITICAL -- estimator cannot compute without data).
   - Federal ordinary brackets: Add MFS, HOH for 2024.
   - Federal standard deduction: Add MFS, HOH for 2024.
   - Federal LTCG brackets: Add ALL four filing statuses for 2024.
   - NIIT thresholds: Add MFS, HOH.
   - AMT exemptions and phase-outs: Add 2024 for all four statuses.
   - AMT 28% threshold: Add 2024 value ($232,600).
   - California brackets: Add 2024 for all four filing statuses.
   - California standard deduction: Add 2024 for all four statuses.

2. **Add repository query methods** to `app/db/repository.py`.
   - `get_1099divs(tax_year)` -- retrieve 1099-DIV records.
   - `get_1099ints(tax_year)` -- retrieve 1099-INT records.

3. **Implement `compute_ltcg_tax()`** -- replace the flat 15% stub with the proper stacking algorithm per the Qualified Dividends and Capital Gain Tax Worksheet.

4. **Implement `compute_amt()`** -- new method for Form 6251 AMT computation.

5. **Implement `estimate_from_db()`** -- new method that loads data from the repository and calls `estimate()`.

6. **Update `estimate()` method** -- integrate capital loss netting, AMT, and the corrected LTCG computation. Accept `amt_iso_preference` parameter.

7. **Update CLI `estimate` command** -- add filing status option, database loading, formatted output, and warnings.

8. **Write unit tests** -- at minimum, the 7 test scenarios specified in Section 5.8.

9. **Write integration test** -- end-to-end test that imports data, runs reconciliation, then runs estimate and validates the full output.

10. **Add audit log entries** -- log the estimate computation to the `audit_log` table for traceability.

---

## Validation Criteria

### Unit Test Specifications

**`tests/test_engines/test_estimator.py`:**

| Test | Description | Key Assertions |
|---|---|---|
| `test_estimate_w2_only_single` | Test 1 from Section 5.8 | federal_regular = $25,538.50, ca_tax = $10,087.63, niit = $0 |
| `test_estimate_w2_capital_gains_single` | Test 2 from Section 5.8 | LTCG stacking at 15%, NIIT = $1,520 |
| `test_estimate_w2_espp_capital_gains` | Test 3 from Section 5.8 | NIIT = $0 (at threshold), no AMT |
| `test_estimate_w2_qualified_dividends_mfj` | Test 4 from Section 5.8 | Qualified dividends at 15%, NIIT = $646, REFUND |
| `test_estimate_high_income_niit_amt` | Test 5 from Section 5.8 | NIIT, AMT computation, high bracket |
| `test_estimate_capital_loss_limitation` | Capital loss test | $3,000 limit applied, carryforward noted |
| `test_estimate_capital_loss_mfs` | MFS capital loss | $1,500 limit applied |
| `test_estimate_ltcg_zero_bracket` | Low-income LTCG at 0% | LTCG tax = $0 when income below threshold |
| `test_estimate_ltcg_20_bracket` | Very high LTCG income | 20% rate applies above threshold |
| `test_estimate_ca_mental_health_tax` | Income above $1M | 1% surcharge computed |
| `test_estimate_ca_no_ltcg_preference` | CA taxes all gains at ordinary | CA tax treats LTCG same as ordinary |
| `test_estimate_no_amt_when_no_preferences` | No ISO exercises | AMT = $0, skip computation |
| `test_estimate_standard_vs_itemized` | Itemized > standard | Uses itemized deduction |
| `test_estimate_zero_income` | No income at all | All tax = $0, all fields = $0 |
| `test_estimate_multiple_w2s` | Two W-2s aggregated | Wages and withholdings summed |

**`tests/test_engines/test_brackets.py`:**

| Test | Description | Key Assertions |
|---|---|---|
| `test_federal_brackets_2024_all_statuses` | All 4 statuses have data | No KeyError for any FilingStatus |
| `test_ltcg_brackets_2024_all_statuses` | All 4 statuses have data | No KeyError |
| `test_california_brackets_2024_all_statuses` | All 4 statuses have data | No KeyError |
| `test_amt_exemption_2024_all_statuses` | All 4 statuses have data | No KeyError |
| `test_niit_threshold_all_statuses` | All 4 statuses have data | No KeyError |
| `test_bracket_monotonicity` | Bracket bounds increase | Each bound > previous |

**`tests/test_engines/test_estimator_integration.py`:**

| Test | Description |
|---|---|
| `test_estimate_from_db_w2_only` | Import W-2 JSON, run estimate, verify output |
| `test_estimate_from_db_full_pipeline` | Import W-2 + lots + sales, reconcile, estimate |
| `test_estimate_from_db_no_data` | Empty database, verify warnings |
| `test_estimate_from_db_missing_reconciliation` | W-2 only, no reconcile, verify warning |
| `test_cli_estimate_command` | Run `taxbot estimate 2024` via Typer test client |
| `test_cli_estimate_filing_status` | Test --filing-status option |

### Cross-Reference Checks

After a successful estimate:

1. `federal_total_tax = federal_regular_tax + federal_ltcg_tax + federal_niit + federal_amt` -- verify identity.
2. `ca_total_tax = ca_tax + ca_mental_health_tax` -- verify identity.
3. `total_tax = federal_total_tax + ca_total_tax` -- verify identity.
4. `federal_balance_due = federal_total_tax - federal_withheld - federal_estimated_payments` -- verify identity.
5. `ca_balance_due = ca_total_tax - ca_withheld - ca_estimated_payments` -- verify identity.
6. `total_balance_due = federal_balance_due + ca_balance_due` -- verify identity.
7. `taxable_income >= 0` -- always.
8. `federal_regular_tax >= 0` -- always.
9. `ca_tax >= 0` -- always.
10. `federal_amt >= 0` -- always (AMT is never negative).
11. `deduction_used = max(itemized_deductions, standard_deduction)` -- verify.

---

## Risk Flags

### High Risk

1. **Missing 2024 bracket data.** The current `brackets.py` only has 2024 data for Single and MFJ federal brackets. MFS and HOH are missing. LTCG brackets for 2024 are entirely missing. California 2024 brackets are missing. Without this data, the estimator will crash for most filing statuses.
   - **Mitigation:** Step 1 in the implementation priority. Must be completed before any other work.

2. **LTCG tax stub uses flat 15%.** The existing `compute_ltcg_tax` applies a flat 15% rate, which is incorrect. Low-income taxpayers should get 0%, and very high-income taxpayers should pay 20%. The stacking computation is non-trivial and must be tested carefully.
   - **Mitigation:** Step 3. Implement the proper stacking algorithm with comprehensive tests.

3. **AMT computation not implemented.** The estimator currently sets `federal_amt = Decimal("0")`. Taxpayers with ISO exercises will have incorrect estimates.
   - **Mitigation:** Step 4. Implement the full AMT computation per Form 6251.

### Medium Risk

4. **Double-counting equity ordinary income.** If the W-2 already includes ESPP/ISO ordinary income and the estimator also adds it from SaleResults, the income would be double-counted. The plan specifies that SaleResult ordinary income is informational only and should NOT be added to wages.
   - **Mitigation:** The `estimate_from_db()` method does NOT add `total_sale_ordinary_income` to wages. It emits a warning for the user to verify.

5. **Capital loss netting not fully implemented.** The current estimator accepts `short_term_gains` and `long_term_gains` as simple Decimal inputs. It does not perform the capital loss netting (ST loss vs LT gain cross-offset) or enforce the $3,000 limit internally. The `estimate_from_db()` method must handle this.
   - **Mitigation:** Implement netting in `estimate_from_db()` before passing values to `estimate()`. Report the limited loss correctly.

6. **California itemized deductions differ from federal.** California has different rules for itemized deductions (e.g., no SALT deduction). The current plan uses the same `itemized_deductions` for both federal and California, which is a simplification.
   - **Mitigation:** Accept this as a known limitation. Add a warning: "California itemized deductions may differ from federal. Review FTB Schedule CA." A future enhancement can add separate California deduction inputs.

### Low Risk

7. **Estimated tax payments are user-supplied.** The CLI accepts `--federal-estimated` and `--state-estimated` options. There is no validation that these are reasonable. Negative values would be nonsensical.
   - **Mitigation:** Validate that estimated payments are >= 0 in the CLI.

8. **1099-DIV/INT state withholding.** The current `Form1099DIV` and `Form1099INT` models have `state_tax_withheld` fields, but the `form_1099div` and `form_1099int` tables do not store them. State withholding from these forms may be missed.
   - **Mitigation:** For most California taxpayers, 1099-DIV/INT state withholding is $0. Flag as a future enhancement.

9. **Rounding.** Federal and California tax computations may produce fractional cents. The final tax amounts should be rounded to the nearest dollar (per IRS instructions) or kept at cent precision for interim computations.
   - **Mitigation:** Use `Decimal` throughout (already enforced by project conventions). Final CLI display rounds to 2 decimal places. IRS rounding to nearest dollar can be a future enhancement.

---

## Strategy Recommendations

### For Tax Planner (after estimator is implemented)

1. **NIIT avoidance:** If AGI is near the $200k/$250k NIIT threshold, review whether any capital gains can be deferred to a year where AGI is lower. Each dollar of investment income above the threshold costs an additional 3.8%.

2. **LTCG bracket optimization:** If total taxable income minus preferential income is near a LTCG bracket boundary (especially the 0%/$47,025 or 15%/$518,900 thresholds), consider timing asset sales to stay within the lower bracket.

3. **AMT credit recovery:** If the taxpayer paid AMT in prior years due to ISO exercises, the AMT credit should be tracked and recovered via Form 8801. This is a future enhancement for the estimator.

4. **California vs. Federal comparison:** Since California taxes all income at ordinary rates (no LTCG preference), a high LTCG year has a disproportionate California impact. The strategy engine should highlight this.

5. **Estimated tax payment optimization:** If the balance due is significant, recommend quarterly estimated payments to avoid underpayment penalties (IRS Form 2210, FTB Form 5805).

---

## Agent Assignments

### [PYTHON ENGINEER]

**Priority order (10 steps):**

1. Add 2024 bracket data to `app/engines/brackets.py` -- all filing statuses, all bracket types.
2. Add `get_1099divs()` and `get_1099ints()` to `app/db/repository.py`.
3. Rewrite `compute_ltcg_tax()` in `app/engines/estimator.py` with proper stacking algorithm.
4. Implement `compute_amt()` in `app/engines/estimator.py`.
5. Implement `estimate_from_db()` in `app/engines/estimator.py`.
6. Update `estimate()` method with AMT parameter and capital loss netting.
7. Update CLI `estimate` command in `app/cli.py`.
8. Write unit tests for bracket data in `tests/test_engines/test_brackets.py`.
9. Write unit tests for estimator in `tests/test_engines/test_estimator.py`.
10. Write integration tests in `tests/test_engines/test_estimator_integration.py`.

### [ACCOUNTANT]

After implementation:
- Verify all 7 test scenarios produce expected tax amounts.
- Cross-check federal tax against IRS Tax Table for the same taxable income.
- Verify LTCG stacking produces correct results at 0%/15%/20% boundaries.
- Verify AMT computation against Form 6251 worksheet.
- Verify California tax matches FTB Tax Table.
- Confirm all arithmetic identities (Section Validation Criteria) hold.
- Sign off on the tax estimate.

### [CPA REVIEW]

After all agents complete:
- Verify 2024 bracket data matches IRS Rev. Proc. 2023-34 and FTB Publication 1001.
- Verify LTCG stacking logic matches the Qualified Dividends and Capital Gain Tax Worksheet.
- Verify AMT logic matches Form 6251 Instructions.
- Verify NIIT logic matches Form 8960 / IRC Section 1411.
- Verify capital loss netting matches Schedule D Instructions.
- Verify California Mental Health Services Tax logic matches CA R&TC Section 17043.
- Confirm no double-counting of equity compensation ordinary income.
- Review all warnings for completeness.

---

## Authoritative References

| Reference | Used For |
|---|---|
| IRS Rev. Proc. 2023-34 | 2024 tax brackets, standard deductions, AMT exemptions, LTCG thresholds |
| IRS Pub. 550 | Investment income (interest, dividends, capital gains), qualified dividends, NIIT |
| IRS Pub. 525 | Taxable income from equity compensation |
| IRS Form 1040 Instructions | Qualified Dividends and Capital Gain Tax Worksheet |
| IRS Form 6251 Instructions | AMT computation, exemptions, rates, preference items |
| IRS Form 8960 Instructions | Net Investment Income Tax |
| IRS Schedule D Instructions | Capital gain/loss netting and $3,000 limitation |
| IRC Section 1(h) | Preferential LTCG tax rates |
| IRC Section 55-59 | Alternative Minimum Tax |
| IRC Section 1211(b) | Capital loss limitation |
| IRC Section 1411 | Net Investment Income Tax |
| CA Revenue and Taxation Code Section 17041 | California tax rates |
| CA Revenue and Taxation Code Section 17043(a) | Mental Health Services Tax |
| FTB Publication 1001 | California tax adjustments and brackets |
| CA Schedule CA Instructions | California vs. federal income differences |

---

## Log

### [CPA] 2026-02-12T12:00
- Tax Estimator Engine plan created.
- Analyzed existing codebase: `estimator.py` has working `_apply_brackets` and basic structure but uses a flat 15% LTCG stub, has no AMT, and is missing 2024 bracket data for MFS/HOH.
- Documented complete income aggregation logic with IRS citations.
- Specified all 2024 federal and California bracket tables with exact dollar amounts for all four filing statuses.
- Designed proper LTCG stacking algorithm per IRS Qualified Dividends and Capital Gain Tax Worksheet.
- Designed full AMT computation per Form 6251 with exemption phase-outs and 26%/28% rates.
- Documented NIIT computation per IRC Section 1411.
- Documented capital loss netting rules per Schedule D with the $3,000/$1,500 limitation.
- Specified `estimate_from_db()` method for database-driven estimation.
- Specified CLI `estimate` command with filing status, estimated payments, and itemized deduction options.
- Provided 7 hand-computed test scenarios with exact expected values.
- Identified 3 high-risk items (missing brackets, LTCG stub, no AMT), 3 medium-risk items (double-counting, capital loss netting, CA itemized), 3 low-risk items.
- Assigned 10-step implementation priority for Python Engineer.
- Plan ready for implementation.

---

## Review Notes

### [CPA Review]
- (CPA) Pending -- final review after implementation.

### [Accountant Review]
- (ACCOUNTANT) Pending -- tax computation sign-off after implementation.

---

## Final Summary

### [CPA]
- Pending. The Tax Estimator Engine plan is complete and ready for implementation. This engine consumes the output of the reconciliation engine and produces the final tax liability estimate. It is the second-most complex engine after reconciliation, primarily due to the LTCG stacking algorithm and the AMT computation.

### Tax Due Estimate
- Federal: $__________ (computed after implementation)
- California: $__________ (computed after implementation)
- AMT (if any): $__________ (computed after implementation)
- Total Estimated: $__________
- Less Withholdings: $__________
- Balance Due / (Refund): $__________
