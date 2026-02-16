# Remaining Tax Features — CPA Implementation Plan

**Session ID:** tax-2026-02-15-remaining-features-001
**Date:** 2026-02-15
**Status:** Planning
**Tax Year:** 2024

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation and reconciliation sign-off)
- Tax Planner (strategy implications)

**Scope:**
Implement all remaining tax computation features required for an accurate federal and California tax estimate. This plan covers Additional Medicare Tax (Form 8959), capital loss carryover, ISO AMT full liability computation (Form 6251), ISO AMT credit carryforward (Form 8801), report generation CLI, Foreign Tax Credit, QBI deduction, CA VPDI auto-add, Robinhood adapter, and minor improvements.

**Definition of Done:**
Running `taxbot estimate 2024` produces a tax estimate that matches the user's IRS transcript within $50. All Form 8959, Schedule D, Form 6251, and Form 8801 computations are implemented, tested, and wired into the CLI output.

---

## Executive Summary

The current estimator produces a solid baseline but is missing several features that cause the federal estimate to diverge from the IRS transcript by approximately $3,000-$6,000. The biggest gaps are:

1. **Additional Medicare Tax (+$3,046 in tax)** — Not computed at all. This is the single largest discrepancy.
2. **Additional Medicare Tax Withholding Credit (+$3,046 credit)** — The withholding credit offsets the tax but must be computed separately from W-2 Box 5/6 data.
3. **Capital Loss Carryover (-$25,292 applied to gains)** — No mechanism to accept prior-year carryover; the estimator only nets current-year gains/losses.
4. **ISO AMT Full Computation** — The `compute_amt()` method in `estimator.py` already works for basic cases, but `compute_amt_liability()` in `iso_amt.py` is a stub. The estimator's inline AMT is functional but should be unified.
5. **ISO AMT Credit Carryforward (Form 8801)** — `compute_amt_credit()` is a stub. No carryforward tracking.
6. **Report Generation** — The `report` CLI command is a stub. Generators exist but are not wired up.
7. **Foreign Tax Credit, QBI, VPDI** — Small-dollar items ($3-$12 each) but needed for accuracy.
8. **Robinhood Adapter** — Stub exists; needed for multi-brokerage support.

---

## Implementation Order & Dependencies

```
Phase 1 (HIGH — directly affects tax estimate accuracy):
  Feature 1: Additional Medicare Tax (Form 8959)
      |
      v
  Feature 3: Additional Medicare Tax Withholding Credit
      (depends on Feature 1; same Form 8959 computation)
      |
      v
  Feature 2: Capital Loss Carryover
      (independent; can be done in parallel with Features 1/3)
      |
      v
  Feature 4: ISO AMT Liability (Form 6251)
      |
      v
  Feature 5: ISO AMT Credit Carryforward (Form 8801)
      (depends on Feature 4)
      |
      v
  Feature 6: Report Generation CLI

Phase 2 (MEDIUM — small-dollar accuracy + new adapter):
  Feature 7: Foreign Tax Credit (already partially implemented)
  Feature 8: QBI Deduction (already partially implemented)
  Feature 9: VPDI Auto-Add to SALT
  Feature 10: Robinhood Adapter

Phase 3 (LOW — cleanup and future-proofing):
  Event validation/dedup, DB migrations, MFS/HOH 2025 brackets,
  Schedule D generator, audit trail enhancements
```

---

## Feature 1: Additional Medicare Tax (Form 8959)

### IRS Authority
- **IRC Section 3101(b)(2)** — Imposes an additional 0.9% Hospital Insurance (Medicare) tax on wages exceeding threshold.
- **Form 8959** — Used to compute and report the Additional Medicare Tax.
- **Form 8959 Instructions** — Line-by-line computation guide.

### Tax Law Rules

The Additional Medicare Tax is 0.9% on Medicare wages (W-2 Box 5) exceeding the threshold:

| Filing Status | Threshold |
|---|---|
| Single | $200,000 |
| MFJ | $250,000 |
| MFS | $125,000 |
| HOH | $200,000 |

**Computation (Form 8959, Lines 7-18):**
```
Line 7:  Medicare wages (sum of all W-2 Box 5)                   = $538,489
Line 8:  Unreported Medicare wages (rare)                         = $0
Line 9:  Railroad tier 1 compensation (N/A)                      = $0
Line 10: Total Medicare wages (Lines 7 + 8 + 9)                  = $538,489
Line 11: Threshold for filing status                              = $200,000
Line 12: Subtract Line 11 from Line 10 (if > 0)                  = $338,489
Line 13: Additional Medicare Tax on wages (0.9% x Line 12)       = $3,046.40

Lines 14-17: Self-employment income (N/A for W-2 employee)       = $0

Line 18: Total Additional Medicare Tax                            = $3,046
```

The Additional Medicare Tax is added to federal total tax. It is NOT reduced by the AMT exemption or any credits except the withholding credit.

### Withholding Credit (Form 8959, Lines 19-24)

Employers withhold 1.45% Medicare tax on ALL wages (not just the excess). The Additional Medicare Tax withholding credit = the amount by which Medicare tax withheld exceeds the "regular" 1.45% rate applied to Medicare wages:

```
Line 19: Medicare tax withheld (sum of all W-2 Box 6)            = $10,854
Line 20: Regular Medicare tax (1.45% x W-2 Box 5 wages)         = $7,808.10
         ($538,489 x 0.0145 = $7,807.09, rounded)
Line 21: Additional Medicare Tax withholding (Line 19 - Line 20) = $3,045.91
```

This withholding credit of $3,046 is ADDED to the total federal withholding. The user's IRS transcript confirms: total federal withheld = $109,772 (Box 2) + $3,046 (Addl Medicare withholding) = $112,818.

### Data Inputs Needed

| Input | Source | Field |
|---|---|---|
| Medicare wages | W-2 Box 5 | `w2.box5_medicare_wages` |
| Medicare tax withheld | W-2 Box 6 | `w2.box6_medicare_withheld` |
| Filing status | CLI argument | `filing_status` |

**Note:** The W-2 model (`app/models/tax_forms.py`) already has `box5_medicare_wages` and `box6_medicare_withheld` fields. The `estimate_from_db()` method in `estimator.py` currently reads W-2s but only extracts `box1_wages`, `box2_federal_withheld`, and `box17_state_withheld`. It must also extract Box 5 and Box 6.

### Files to Modify

| File | Change |
|---|---|
| `app/engines/brackets.py` | Add `ADDITIONAL_MEDICARE_TAX_RATE = Decimal("0.009")` and `ADDITIONAL_MEDICARE_TAX_THRESHOLD` dict keyed by `FilingStatus` |
| `app/engines/estimator.py` | **Method `estimate()`:** Add parameters `medicare_wages` and `medicare_withheld`. Add method `compute_additional_medicare_tax()`. Compute the tax and withholding credit. Add both to `TaxEstimate`. **Method `estimate_from_db()`:** Extract Box 5 and Box 6 from W-2 records and pass to `estimate()`. |
| `app/models/reports.py` | Add fields to `TaxEstimate`: `additional_medicare_tax`, `additional_medicare_withholding_credit`, update `federal_total_tax` and `federal_withheld` computation to include these. |
| `app/cli.py` | Display Additional Medicare Tax line in the FEDERAL TAX section of the `estimate` command output. |
| `tests/test_engines/test_estimator.py` | Add test cases for Additional Medicare Tax computation. |

### Computation Method (pseudocode)

```python
def compute_additional_medicare_tax(
    self,
    medicare_wages: Decimal,
    medicare_withheld: Decimal,
    filing_status: FilingStatus,
) -> tuple[Decimal, Decimal]:
    """Compute Additional Medicare Tax and withholding credit per Form 8959.

    Returns:
        (additional_tax, withholding_credit)
    """
    threshold = ADDITIONAL_MEDICARE_TAX_THRESHOLD[filing_status]
    excess = max(medicare_wages - threshold, Decimal("0"))
    additional_tax = excess * ADDITIONAL_MEDICARE_TAX_RATE

    # Withholding credit: employer withheld 1.45% on all wages,
    # but employee only owes 1.45% on first $threshold + 2.35% on excess.
    # Credit = Box 6 withheld - (1.45% x Box 5 wages)
    regular_medicare = medicare_wages * Decimal("0.0145")
    withholding_credit = max(medicare_withheld - regular_medicare, Decimal("0"))

    return additional_tax, withholding_credit
```

### Integration into `estimate()`

In the `estimate()` method, after computing NIIT and AMT:

```python
# --- Additional Medicare Tax (Form 8959) ---
additional_medicare, medicare_withholding_credit = self.compute_additional_medicare_tax(
    medicare_wages, medicare_withheld, filing_status
)

# Update federal totals
federal_total = federal_regular + federal_ltcg + federal_niit + federal_amt + additional_medicare - federal_foreign_tax_credit
effective_federal_withheld = federal_withheld + medicare_withholding_credit
federal_balance = federal_total - effective_federal_withheld - federal_estimated_payments
```

### Test Scenarios

| # | Scenario | Medicare Wages | Threshold | Expected Tax | Expected Credit |
|---|---|---|---|---|---|
| 1 | User's actual data (Single) | $538,489 | $200,000 | $3,046.40 | $3,046 |
| 2 | Below threshold (Single) | $180,000 | $200,000 | $0 | $0 |
| 3 | At threshold exactly | $200,000 | $200,000 | $0 | $0 |
| 4 | MFJ threshold | $300,000 | $250,000 | $450 | varies |
| 5 | MFS threshold | $150,000 | $125,000 | $225 | varies |
| 6 | Multiple W-2s | $300K + $250K | $200,000 | $3,150 | varies |
| 7 | Zero Medicare wages | $0 | $200,000 | $0 | $0 |

---

## Feature 2: Capital Loss Carryover

### IRS Authority
- **IRC Section 1212(b)** — Carryover of net capital losses.
- **Schedule D Instructions** — Capital Loss Carryover Worksheet.
- **IRS Publication 550** — Investment Income and Expenses, Chapter 4.

### Tax Law Rules

1. Net capital losses exceeding $3,000/year ($1,500 MFS) carry forward to future years indefinitely.
2. Short-term carryover offsets short-term gains first; long-term carryover offsets long-term gains first.
3. The carryover retains its character (short-term or long-term).
4. The user's CPA applied a **-$25,292 capital loss carryover** from prior years.
5. For simplicity in this implementation, we accept the carryover as a single total amount (combined ST + LT). A future enhancement can split it into ST and LT components.

### Data Inputs Needed

| Input | Source | Field |
|---|---|---|
| Capital loss carryover (total) | CLI argument | `--capital-loss-carryover` |
| Short-term portion (optional) | CLI argument | `--st-loss-carryover` (future) |
| Long-term portion (optional) | CLI argument | `--lt-loss-carryover` (future) |

### Files to Modify

| File | Change |
|---|---|
| `app/cli.py` | Add `--capital-loss-carryover` option to the `estimate` command. Pass it through to `estimate_from_db()`. |
| `app/engines/estimator.py` | **Method `estimate_from_db()`:** Accept `capital_loss_carryover: Decimal = Decimal("0")` parameter. Apply the carryover to capital gains BEFORE the $3,000 loss netting. The carryover reduces gains, then any remaining excess flows into the loss netting logic. **Method `estimate()`:** Accept the carryover and apply it. |
| `app/models/reports.py` | Add fields to `TaxEstimate`: `capital_loss_carryover_applied: Decimal = Decimal("0")`, `capital_loss_carryforward_to_next_year: Decimal = Decimal("0")`. |
| `tests/test_engines/test_estimator.py` | Add test cases. |

### Computation Logic

In `estimate_from_db()`, after aggregating capital gains from sale results but BEFORE the capital loss netting section:

```python
# Apply capital loss carryover from prior years
if capital_loss_carryover > Decimal("0"):
    # Carryover is a positive number representing losses.
    # Apply to short-term gains first (as if it were short-term carryover),
    # then long-term gains.
    remaining_carryover = capital_loss_carryover

    if short_term_gains > Decimal("0"):
        st_offset = min(remaining_carryover, short_term_gains)
        short_term_gains -= st_offset
        remaining_carryover -= st_offset

    if remaining_carryover > Decimal("0") and long_term_gains > Decimal("0"):
        lt_offset = min(remaining_carryover, long_term_gains)
        long_term_gains -= lt_offset
        remaining_carryover -= lt_offset

    # If carryover still remains, it adds to current-year losses
    if remaining_carryover > Decimal("0"):
        # Add to short-term (conservative treatment)
        short_term_gains -= remaining_carryover

    self.warnings.append(
        f"Applied ${capital_loss_carryover:,.2f} capital loss carryover from prior years."
    )
```

Then the existing `$3,000 loss netting` logic runs on the updated gains, which may produce a new carryforward.

### Test Scenarios

| # | Scenario | ST Gains | LT Gains | Carryover | Expected Net ST | Expected Net LT | New Carryforward |
|---|---|---|---|---|---|---|---|
| 1 | Carryover fully absorbs gains | $5,000 | $10,000 | $25,292 | -$3,000 | $0 | $7,292 |
| 2 | Carryover partially offsets | $20,000 | $10,000 | $5,000 | $15,000 | $10,000 | $0 |
| 3 | No gains to offset | -$2,000 | -$1,000 | $5,000 | -$3,000 | $0 | $5,000 |
| 4 | Zero carryover | $10,000 | $5,000 | $0 | $10,000 | $5,000 | $0 |
| 5 | Carryover exactly equals gains | $15,000 | $10,292 | $25,292 | $0 | $0 | $0 |
| 6 | MFS loss limit ($1,500) | -$2,000 | -$1,000 | $5,000 | -$1,500 | $0 | $6,500 |

---

## Feature 3: Additional Medicare Tax Withholding Credit

This feature is **part of Feature 1** (Form 8959). See Feature 1 for the full computation.

### Key Integration Point

The current `estimate()` method returns `federal_withheld` as a straight pass-through from W-2 Box 2 (plus 1099-DIV/INT withholding). The Additional Medicare Tax withholding credit must be **added** to the effective federal withholding:

```
Effective federal withheld = W-2 Box 2 + 1099 withholding + Additional Medicare withholding credit

From user's transcript:
  $109,772 (Box 2) + $3,046 (Form 8959 credit) = $112,818
```

### Files to Modify

Same as Feature 1. The `TaxEstimate` model should include:
- `additional_medicare_withholding_credit: Decimal = Decimal("0")`
- The `federal_withheld` field should reflect the total including the credit, OR a separate `effective_federal_withheld` field should be added.

### CPA Recommendation

Add the credit as a separate line item in `TaxEstimate` and in the CLI output. The `federal_withheld` field stays as W-2 Box 2 + 1099 withholding (the "raw" amount). A new `federal_total_credits` or `federal_effective_withheld` field captures the total:

```
FEDERAL TAX
  ...
  Additional Medicare Tax:   $       3,046
  ...
  Total Federal Tax:         $     XXX,XXX
  Federal Withheld (W-2/1099): $   109,772
  Addl Medicare Credit:      $       3,046
  Est. Payments:             $           0
  Federal Balance Due:       $       X,XXX
```

---

## Feature 4: ISO AMT Liability (Form 6251)

### IRS Authority
- **IRC Sections 55-59** — Alternative Minimum Tax.
- **Form 6251 Instructions** — Alternative Minimum Tax -- Individuals.
- **IRS Rev. Proc. 2023-34** (2024 amounts), **Rev. Proc. 2024-40** (2025 amounts).

### Current State

The `estimator.py` already has a working `compute_amt()` method (Lines 590-662) that:
1. Computes AMTI = taxable_income + amt_preference
2. Applies exemption with phase-out
3. Computes tentative minimum tax using 26%/28% brackets
4. Handles preferential income (LTCG) under AMT
5. Returns max(TMT - regular_tax, 0)

The `iso_amt.py` has a separate `compute_amt_liability()` stub that raises `NotImplementedError`.

### CPA Recommendation

The `compute_amt()` method in `estimator.py` is already functional and correct for the user's scenario. The `compute_amt_liability()` in `iso_amt.py` should be implemented to match the same logic, for use in standalone AMT worksheet generation and the strategy engine.

### Missing AMT Adjustments (Beyond ISO)

For a complete Form 6251, these adjustments would apply but are currently NOT modeled:
- **SALT deduction add-back (Line 2a):** The SALT deduction claimed on Schedule A must be added back for AMT purposes. For the user, this means adding back $10,000 (the SALT cap amount).
- **Standard deduction add-back (Line 2b):** If standard deduction is used, it's added back. (The user itemizes, so N/A.)
- **Tax-exempt interest from private activity bonds (Line 2g):** Not applicable.

The SALT add-back is a significant adjustment that could trigger AMT for high-income taxpayers who itemize. However, for the user's 2024 tax year, the ISO AMT preference ($0 if no ISO exercises occurred, or the spread amount if exercises occurred) is the primary driver.

### Files to Modify

| File | Change |
|---|---|
| `app/engines/iso_amt.py` | Implement `compute_amt_liability()` using the same logic as `estimator.py:compute_amt()`. Accept additional parameters for SALT add-back and standard deduction add-back. |
| `app/engines/estimator.py` | Optionally refactor `compute_amt()` to delegate to `ISOAMTEngine.compute_amt_liability()` to avoid duplication. Add SALT add-back to AMTI computation when taxpayer itemizes. |
| `app/engines/brackets.py` | Constants already exist: `AMT_EXEMPTION`, `AMT_PHASEOUT_START`, `AMT_28_PERCENT_THRESHOLD`. No changes needed. |
| `app/models/reports.py` | Add to `TaxEstimate`: `amti: Decimal = Decimal("0")`, `amt_exemption: Decimal = Decimal("0")`, `amt_tentative_minimum_tax: Decimal = Decimal("0")`. These provide transparency for the AMT worksheet. |
| `tests/test_engines/test_iso_amt.py` | Add tests for `compute_amt_liability()`. |
| `tests/test_engines/test_estimator.py` | Add tests for AMT with SALT add-back. |

### Computation (Form 6251 Lines)

```
AMTI Computation:
  Line 1:  Regular taxable income                              = taxable_income
  Line 2a: SALT deduction add-back (if itemizing)              = federal_salt_deduction
  Line 2i: ISO exercise spread (AMT preference)                = amt_iso_preference
  Line 3:  AMTI = Line 1 + Line 2a + Line 2i + ...            = amti

Exemption:
  Line 5:  AMT exemption for filing status                     = $85,700 (Single, 2024)
  Line 6:  Phase-out: if AMTI > $609,350, reduce exemption
           by 25 cents per dollar over threshold
  Line 7:  Exemption after phase-out                           = amt_exemption

Tax Computation:
  Line 8:  AMT base = AMTI - exemption                        = amt_base
  Line 9:  26% on first $232,600                               (or 28% on excess)
           LTCG/QDivs still get preferential rates under AMT
  Line 10: Tentative minimum tax                               = tmt
  Line 11: Regular tax (ordinary + LTCG)                       = regular_tax
  Line 12: AMT = max(0, TMT - regular_tax)                    = amt
```

### Implementation for `iso_amt.py`

```python
def compute_amt_liability(
    self,
    taxable_income: Decimal,
    preferences: list[AMTWorksheetLine],
    salt_addback: Decimal,
    standard_deduction_addback: Decimal,
    preferential_income: Decimal,
    regular_tax: Decimal,
    filing_status: FilingStatus,
    tax_year: int,
) -> dict:
    """Compute full AMT per Form 6251.

    Returns dict with:
      amti, exemption, amt_base, tmt, regular_tax, amt,
      and per-line breakdowns for the AMT worksheet.
    """
    total_preference = sum(p.total_amt_preference for p in preferences)
    amti = taxable_income + total_preference + salt_addback + standard_deduction_addback
    # ... exemption, base, tmt, amt computation (same as estimator.compute_amt)
```

### Test Scenarios

| # | Scenario | AMTI | Exemption | AMT Base | TMT | Regular Tax | AMT |
|---|---|---|---|---|---|---|---|
| 1 | No ISO exercises, no SALT addback | $400,000 | $85,700 | $314,300 | ~$85K | ~$90K | $0 |
| 2 | ISO exercise ($50K spread) | $450,000 | $85,700 | $364,300 | ~$98K | ~$90K | ~$8K |
| 3 | Exemption phase-out | $750,000 | $50,537 | $699,463 | ~$190K | ~$170K | ~$20K |
| 4 | Exemption fully phased out | $1,000,000 | $0 | $1,000,000 | ~$270K | ~$260K | ~$10K |
| 5 | SALT add-back triggers AMT | $400,000 + $10K SALT | $85,700 | $324,300 | ~$88K | ~$87K | ~$1K |
| 6 | LTCG under AMT (preferential rate) | $300K ord + $200K LTCG | $85,700 | $414,300 | varies | varies | varies |

---

## Feature 5: ISO AMT Credit Carryforward (Form 8801)

### IRS Authority
- **IRC Section 53** — Credit for prior year minimum tax.
- **Form 8801** — Credit for Prior Year Minimum Tax -- Individuals.
- **Form 8801 Instructions** — Line-by-line guide.

### Tax Law Rules

1. AMT paid on **deferral items** (ISO exercises) generates a minimum tax credit that carries forward indefinitely.
2. AMT paid on **exclusion items** (tax-exempt interest on private activity bonds, certain SALT items) does NOT generate a credit.
3. The credit is used in future years when regular tax exceeds tentative minimum tax (i.e., no AMT owed).
4. Credit available = min(prior_year_amt_on_deferral_items, regular_tax - TMT)
5. Unused credit carries forward to the next year.

### Deferral vs. Exclusion Items

For the user's scenario:
- **ISO exercise spread** = deferral item (generates credit)
- **SALT add-back** = exclusion item (does NOT generate credit)

This distinction is critical. The Form 8801 computation splits the prior-year AMT into deferral and exclusion components.

### Files to Modify

| File | Change |
|---|---|
| `app/engines/iso_amt.py` | Implement `compute_amt_credit()`. Accept prior-year AMT, prior-year AMT from deferral items, current-year regular tax, and current-year TMT. Return the credit usable this year and the remaining carryforward. |
| `app/engines/estimator.py` | Accept `prior_year_amt_credit: Decimal = Decimal("0")` parameter. Apply the credit after computing AMT (reduces federal total tax). |
| `app/models/reports.py` | Add to `TaxEstimate`: `amt_credit_used: Decimal = Decimal("0")`, `amt_credit_carryforward: Decimal = Decimal("0")`. |
| `app/cli.py` | Add `--amt-credit` option to the `estimate` command (the `strategy` command already has `--amt-credit`). Display in output. |
| `app/db/migrations.py` | Optional: Add `amt_credit_carryforward` table for persistent tracking across years. |
| `tests/test_engines/test_iso_amt.py` | Add tests for `compute_amt_credit()`. |

### Computation (Form 8801 Summary)

```
Step 1: Determine prior-year AMT on deferral items only
  Prior-year AMTI (deferral only) = regular taxable income + ISO spread
  Prior-year TMT (deferral only) = TMT computed on deferral-only AMTI
  AMT on deferral items = TMT(deferral) - regular_tax  (if > 0)

Step 2: Compute current-year credit allowable
  Current-year regular tax = regular_tax
  Current-year TMT = tmt  (may be $0 if no current AMT)
  Credit allowable = min(amt_credit_available, regular_tax - tmt)
  (Only usable when regular_tax > tmt, i.e., no AMT owed this year)

Step 3: Carryforward
  Remaining credit = amt_credit_available - credit_used
```

### Simplified Implementation (Phase 1)

For the initial implementation, accept the AMT credit carryforward as a CLI input (the user or their CPA knows the amount from the prior year's Form 8801). The computation of "AMT on deferral items" is complex and can be deferred to Phase 2.

```python
def compute_amt_credit(
    self,
    prior_year_amt_credit: Decimal,
    current_regular_tax: Decimal,
    current_tmt: Decimal,
) -> tuple[Decimal, Decimal]:
    """Compute minimum tax credit per Form 8801.

    Args:
        prior_year_amt_credit: Credit carried forward from prior years.
        current_regular_tax: This year's regular tax (ordinary + LTCG).
        current_tmt: This year's tentative minimum tax.

    Returns:
        (credit_used, credit_remaining)
    """
    if prior_year_amt_credit <= Decimal("0"):
        return Decimal("0"), Decimal("0")

    # Credit only usable when regular tax > TMT (net regular tax liability)
    net_regular_liability = max(current_regular_tax - current_tmt, Decimal("0"))
    credit_used = min(prior_year_amt_credit, net_regular_liability)
    credit_remaining = prior_year_amt_credit - credit_used

    return credit_used, credit_remaining
```

### Test Scenarios

| # | Scenario | Credit Avail | Regular Tax | TMT | Credit Used | Remaining |
|---|---|---|---|---|---|---|
| 1 | No credit available | $0 | $100,000 | $80,000 | $0 | $0 |
| 2 | Credit fully used | $5,000 | $100,000 | $90,000 | $5,000 | $0 |
| 3 | Credit partially used | $15,000 | $100,000 | $90,000 | $10,000 | $5,000 |
| 4 | AMT owed (regular < TMT) | $5,000 | $80,000 | $100,000 | $0 | $5,000 |
| 5 | Regular = TMT exactly | $5,000 | $100,000 | $100,000 | $0 | $5,000 |

---

## Feature 6: Report Generation CLI Command

### Current State

The `report` CLI command at `app/cli.py:673-680` is a stub:
```python
@app.command()
def report(year, output):
    typer.echo("Report generation not yet implemented.")
```

The following report generators already exist with `render()` methods:
- `app/reports/form8949.py` — `Form8949Generator` (has `generate_lines()` + `render()`)
- `app/reports/espp_report.py` — `ESPPReportGenerator` (has `render()`)
- `app/reports/amt_worksheet.py` — `AMTWorksheetGenerator` (has `render()`)
- `app/reports/reconciliation.py` — `ReconciliationReportGenerator` (has `render()`)
- `app/reports/strategy_report.py` — `StrategyReportGenerator` (has `render()`)

All generators use Jinja2 templates in `app/reports/templates/`.

### Missing Generators

| Report | Status | Notes |
|---|---|---|
| Schedule D Summary | Missing | Aggregates Form 8949 by category (A-F), computes totals. |
| AMT Full Worksheet (Form 6251) | Template exists | Needs data pipeline from estimator/iso_amt. |
| Reconciliation | Template exists | Needs data pipeline from reconciliation engine. |
| Tax Estimate Summary | Missing | Formatted version of `TaxEstimate` for filing review. |

### Files to Modify

| File | Change |
|---|---|
| `app/cli.py` | Implement the `report` command: load data from DB, run each generator, write output files. Add `--report-type` option to generate specific reports. |
| `app/reports/schedule_d.py` | New file. `ScheduleDGenerator` that aggregates Form 8949 lines into Schedule D format. |
| `app/reports/tax_summary.py` | New file. `TaxSummaryGenerator` that renders a `TaxEstimate` into a printable summary. |
| `app/reports/templates/schedule_d.txt` | New Jinja2 template for Schedule D. |
| `app/reports/templates/tax_summary.txt` | New Jinja2 template for tax estimate summary. |
| `tests/test_reports/` | Add tests for new generators. |

### CLI Design

```bash
# Generate all reports
taxbot report 2024 --output reports/

# Generate specific report
taxbot report 2024 --output reports/ --type form8949
taxbot report 2024 --output reports/ --type schedule-d
taxbot report 2024 --output reports/ --type espp
taxbot report 2024 --output reports/ --type amt
taxbot report 2024 --output reports/ --type reconciliation
taxbot report 2024 --output reports/ --type summary
```

### Implementation Flow

```python
@app.command()
def report(year, output, report_type, filing_status, db):
    conn = create_schema(db)
    repo = TaxRepository(conn)

    # Load sale results for Form 8949 and Schedule D
    sale_results = repo.get_sale_results(year)

    if report_type in ("all", "form8949"):
        gen = Form8949Generator()
        lines = gen.generate_lines(sale_results_as_models)
        content = gen.render(lines)
        write_report(output / f"form8949_{year}.txt", content)

    if report_type in ("all", "reconciliation"):
        gen = ReconciliationReportGenerator()
        recon_lines = build_reconciliation_lines(sale_results)
        content = gen.render(recon_lines)
        write_report(output / f"reconciliation_{year}.txt", content)

    # ... etc for each report type
```

### Test Scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | Generate all reports | All report files created in output directory |
| 2 | Generate Form 8949 only | Only form8949_2024.txt created |
| 3 | No data available | Graceful message: "No sales data found for year X" |
| 4 | Output directory does not exist | Creates directory automatically |

---

## Feature 7: Foreign Tax Credit (IRC Section 901)

### IRS Authority
- **IRC Section 901** — Credit for taxes paid to foreign countries.
- **Form 1116** — Foreign Tax Credit.
- **IRC Section 904** — Limitation on credit.

### Current State

The estimator already computes a basic foreign tax credit (Lines 142-148 of `estimator.py`):
```python
federal_foreign_tax_credit = min(foreign_tax_paid, federal_pre_credit)
```

The `TaxEstimate` model already has `federal_foreign_tax_credit`. The `estimate_from_db()` method already aggregates `foreign_tax_paid` from 1099-DIV records.

### What's Missing

1. **CLI display** — The foreign tax credit is computed but NOT displayed in the CLI output. Add a line.
2. **Direct credit vs. Form 1116** — For amounts under $300 (Single) / $600 (MFJ), the taxpayer can claim a direct credit without Form 1116 (IRC 904(j)). The current implementation is correct for this scenario (user has $12 foreign tax).
3. **Limitation** — The credit cannot exceed (foreign source income / worldwide income) x US tax. For $12 on $500K+ income, this is never a binding constraint. The current `min(foreign_tax_paid, federal_pre_credit)` is sufficient.

### Files to Modify

| File | Change |
|---|---|
| `app/cli.py` | Add display line for foreign tax credit in estimate output. |
| `app/engines/brackets.py` | Add `FOREIGN_TAX_CREDIT_FORM_1116_THRESHOLD` dict: `{SINGLE: Decimal("300"), MFJ: Decimal("600")}`. |
| `tests/test_engines/test_estimator.py` | Add test verifying $12 credit applied. |

### Test Scenarios

| # | Scenario | Foreign Tax | Expected Credit |
|---|---|---|---|
| 1 | Small amount (user's case) | $12 | $12 |
| 2 | Zero foreign tax | $0 | $0 |
| 3 | Credit exceeds tax | $200,000 (hypothetical) | Limited to total tax |

---

## Feature 8: QBI Deduction (Section 199A)

### IRS Authority
- **IRC Section 199A** — Qualified Business Income Deduction.
- **Form 8995** — Qualified Business Income Deduction Simplified Computation.

### Current State

The estimator already computes the QBI deduction (Line 82 of `estimator.py`):
```python
section_199a_deduction = section_199a_dividends * Decimal("0.20")
```

The `TaxEstimate` model has `section_199a_deduction`. The `estimate_from_db()` aggregates `section_199a_dividends` from 1099-DIV records.

### What's Missing

1. **CLI display** — The deduction is computed but not shown in the DEDUCTIONS section. It should appear as a separate line since it's a below-the-line deduction independent of standard/itemized.
2. **Income limitation** — For high-income taxpayers (>$191,950 Single / $383,900 MFJ in 2024), the 199A deduction phases out for specified service trades or businesses. However, REIT dividends are NOT from a specified service trade, so the phase-out does NOT apply to the user's $13.71 in Section 199A dividends. The current flat 20% is correct.

### Files to Modify

| File | Change |
|---|---|
| `app/cli.py` | Add display line: `Section 199A QBI:  $X.XX` in the DEDUCTIONS section. |
| `tests/test_engines/test_estimator.py` | Add test verifying $13.71 x 20% = $2.74 deduction. |

### Test Scenarios

| # | Scenario | 199A Dividends | Expected Deduction |
|---|---|---|---|
| 1 | User's case | $13.71 | $2.74 |
| 2 | Zero | $0 | $0 |
| 3 | Larger REIT income | $10,000 | $2,000 |

---

## Feature 9: VPDI Auto-Add to SALT from W-2 Box 14

### IRS/CA Authority
- **CA Revenue & Taxation Code Section 17061** — California deductible taxes.
- **FTB Publication 1001** — Supplemental Guidelines.
- **IRS Publication 17** — W-2 Box 14 "Other" codes.

### Tax Law Rules

California Voluntary Plan Disability Insurance (VPDI) contributions shown in W-2 Box 14 are deductible as a state tax paid. They should be included in the SALT computation for itemized deductions.

The user's W-2 Box 14 shows `VPDI: $1,760`.

**Currently:** The `estimate_from_db()` method reads W-2 records but does not extract Box 14 values. The `ItemizedDeductions` model has a `state_income_tax_paid` field, but the VPDI is not included automatically.

### Files to Modify

| File | Change |
|---|---|
| `app/engines/estimator.py` | In `estimate_from_db()`, extract VPDI from W-2 Box 14 data. Auto-add to the `state_income_tax_paid` value when computing itemized deductions. Add a warning explaining the adjustment. |
| `app/models/tax_forms.py` | The W-2 model already has `box14_other: dict[str, Decimal]`. No change needed. |
| `tests/test_engines/test_estimator.py` | Add test verifying VPDI is included in SALT. |

### Implementation

In `estimate_from_db()`, after W-2 aggregation:

```python
# Extract VPDI from W-2 Box 14 for SALT deduction
vpdi_total = Decimal("0")
for w2 in w2_records:
    box14 = w2.get("box14_other", {})
    if isinstance(box14, str):
        import json
        box14 = json.loads(box14) if box14 else {}
    for key, value in box14.items():
        if key.upper() in ("VPDI", "CA VPDI", "SDI", "CA SDI"):
            vpdi_total += Decimal(str(value))

if vpdi_total > Decimal("0") and itemized_detail is not None:
    itemized_detail.state_income_tax_paid += vpdi_total
    self.warnings.append(
        f"Added ${vpdi_total:,.2f} CA VPDI (W-2 Box 14) to SALT deduction."
    )
```

### Test Scenarios

| # | Scenario | VPDI | Expected SALT Increase |
|---|---|---|---|
| 1 | User's case | $1,760 | +$1,760 (but still subject to $10K SALT cap) |
| 2 | No VPDI in Box 14 | $0 | $0 |
| 3 | Multiple W-2s with VPDI | $1,760 + $500 | +$2,260 |
| 4 | Standard deduction used | $1,760 | No effect (VPDI only matters when itemizing) |

---

## Feature 10: Robinhood Adapter

### Current State

The `app/ingestion/robinhood.py` file contains a stub `RobinhoodAdapter` class with `parse()` and `validate()` both raising `NotImplementedError`. The CLI already recognizes "robinhood" as a valid source but exits with an error message.

### Robinhood Consolidated 1099 Format

Robinhood's consolidated 1099 includes:
- **1099-B** — Brokerage proceeds (may include crypto)
- **1099-DIV** — Dividends
- **1099-INT** — Interest
- **1099-MISC** — Miscellaneous income (referral bonuses, etc.)

The PDF is typically exported from the Robinhood app. The data can also be downloaded as CSV from Robinhood's tax document center.

### Files to Modify

| File | Change |
|---|---|
| `app/ingestion/robinhood.py` | Implement `parse()` to handle Robinhood 1099 PDF or CSV. Use the same parse-and-extract pattern as the Shareworks adapter. For CSV: parse column headers and extract 1099-B sale records. For PDF: use pdfplumber or vision extraction. |
| `app/cli.py` | Remove the early exit for "robinhood" source (Line 88-90). |
| `tests/test_ingestion/test_robinhood.py` | New test file with sample data. |

### Implementation Approach

**Phase 1 (CSV):**
1. Parse Robinhood 1099-B CSV export.
2. Map columns to `Form1099B` fields.
3. Handle Robinhood-specific quirks: combined wash sale reporting, crypto sales (treat as property), fractional shares.

**Phase 2 (PDF):**
1. Use the existing `parse` command infrastructure with vision extraction.
2. Add Robinhood-specific form detection patterns.

### Data Mapping (Robinhood CSV to Form1099B)

| Robinhood Column | Form1099B Field |
|---|---|
| Description | `description` |
| Date Acquired | `date_acquired` |
| Date Sold | `date_sold` |
| Proceeds | `proceeds` |
| Cost Basis | `cost_basis` |
| Wash Sale Loss Disallowed | `wash_sale_loss_disallowed` |
| Reporting Category | `box_type` (maps to A/B/D/E) |

### Test Scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | Parse valid CSV with 5 sales | 5 `Form1099B` objects created |
| 2 | CSV with wash sale entries | `wash_sale_loss_disallowed` populated |
| 3 | CSV with crypto sales | Treated as property; short/long-term computed |
| 4 | Missing required columns | Validation error returned |
| 5 | Empty CSV | Empty `ImportResult` with warning |

---

## Low Priority Items (Phase 3)

### Event Validation/Deduplication
- The `EventNormalizer._validate()` and `_deduplicate()` methods in `app/normalization/events.py` are stubs.
- Implement: date range validation, share count > 0, price > 0, duplicate detection by (event_type, date, shares, ticker) tuple.

### DB Migrations
- `app/db/migrations.py` has a stub `migrate()` function.
- As new features add fields to `TaxEstimate` or new tables (AMT credit tracking), migrations must be implemented.
- Pattern: check current schema version, apply ALTER TABLE statements, update schema_version.

### Report Generators
- Schedule D generator (aggregates Form 8949 by category).
- Full AMT worksheet generator (Form 6251 line-by-line).
- Reconciliation summary generator.

### MFS/HOH 2025 Brackets
- `brackets.py` has 2025 brackets for SINGLE and MFJ only.
- Add MFS, HOH for 2025 when IRS publishes Rev. Proc. 2024-40 final amounts.

### Audit Trail
- The `AuditEntry` model exists and is used by the reconciliation engine.
- Extend to log estimator runs, strategy analyses, and report generations.

---

## Verification Steps

After implementing each feature, verify against the user's known tax data:

### User's IRS Transcript (2024, Single)

| Item | IRS Transcript | Current Estimate | Gap |
|---|---|---|---|
| Medicare wages (Box 5) | $538,489 | Not used | Feature 1 |
| Medicare withheld (Box 6) | $10,854 | Not used | Feature 1 |
| Additional Medicare Tax | $3,046 | $0 | Feature 1 |
| Addl Medicare withholding credit | $3,046 | $0 | Feature 3 |
| Capital loss carryover applied | -$25,292 | $0 | Feature 2 |
| Foreign tax credit | $12 | $12 (computed, not displayed) | Feature 7 |
| QBI deduction | $3 | $3 (computed, not displayed) | Feature 8 |
| Total federal tax | ~$112,818 | needs verification | All |
| Total federal withheld | $112,818 | $109,772 | Feature 3 |

### Verification Procedure

1. Import all 2024 tax data.
2. Run `taxbot reconcile 2024`.
3. Run `taxbot estimate 2024 --capital-loss-carryover 25292 --deductions-file deductions.json`.
4. Compare each line item to the IRS transcript.
5. The federal balance due should match within $50.
6. Run `taxbot report 2024 --output reports/` and verify all reports generate without errors.

---

## Log

| Date | Agent | Entry |
|---|---|---|
| 2026-02-15 | Tax Expert CPA | Initial plan written. Covers 10 features ordered by priority. |
