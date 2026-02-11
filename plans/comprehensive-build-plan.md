# EquityTax Reconciler — Comprehensive Build Plan

**Session ID:** tax-2026-02-10-build-plan-002
**Date:** 2026-02-10
**Author:** Tax Expert CPA (Lead Agent)
**Status:** APPROVED WITH ADDENDA (Accountant + Tax Planner findings incorporated)
**Tax Year:** 2025

**Participants:**
- Tax Expert CPA (lead — plan author)
- Accountant (verification — lot tracking, reconciliation, GAAP)
- Tax Planner (verification — strategy engine, projections)
- Python Engineer (future — implementation per this plan)

**Scope:**
Build the EquityTax Reconciler from its current skeleton into a fully functional end-to-end system. This plan breaks the remaining work into 10 phases, each independently implementable and testable.

**Definition of Done:**
A user can import raw Shareworks/Robinhood/manual data, run reconciliation, receive a corrected Form 8949, get a federal + California tax estimate, and review strategy recommendations — all through the CLI.

---

## Current State Assessment

### What Works
- All Pydantic models (enums, equity events, tax forms, reports)
- Exception hierarchy
- RSU and NSO basis correction engines
- ESPP qualifying/disqualifying disposition engine
- ISO AMT preference computation
- Federal + California tax estimator (simplified LTCG)
- FIFO and specific-ID lot matching
- Tax bracket configuration (2024-2025, federal + CA)
- SQLite schema and basic CRUD repository
- All 5 report generators with Jinja2 templates
- CLI structure with 5 commands (all stubbed)
- 37 passing tests

### What's Missing
- All ingestion adapters (Shareworks, Robinhood, manual)
- Event validation rules in normalizer
- ESPP basis correction integration with ESPPEngine
- ISO dual-basis correction
- AMT liability computation (Form 6251)
- AMT credit carryforward (Form 8801)
- Proper LTCG bracket computation
- Wash sale detection
- Strategy engine (all logic)
- CLI wiring to engines and database
- Additional repository query methods
- End-to-end integration tests

---

## Phase 1: Manual Data Entry Adapter

**Priority:** CRITICAL — Without data ingestion, nothing else works.
**Estimated Scope:** ~200 lines of code + tests

### Tax Context
The manual adapter is the foundation. Before we can process Shareworks or Robinhood exports, we need the ability to ingest structured data for forms that have no CSV export: W-2, Form 3921, Form 3922, and manually corrected 1099-B entries.

### Requirements

#### 1.1 JSON Input Schema
Define JSON schemas for each form type that a user can create manually:

**W-2 Input (`w2.json`):**
```json
{
  "tax_year": 2025,
  "employer_name": "Acme Corp",
  "employer_ein": "12-3456789",
  "box1_wages": "250000.00",
  "box2_federal_withheld": "55000.00",
  "box12_codes": {"V": "5000.00"},
  "box14_other": {"RSU": "50000.00", "ESPP": "3000.00"},
  "box16_state_wages": "250000.00",
  "box17_state_withheld": "22000.00",
  "state": "CA"
}
```

**Form 3921 Input (`form3921.json`):**
```json
[
  {
    "tax_year": 2025,
    "grant_date": "2022-01-15",
    "exercise_date": "2025-03-01",
    "exercise_price_per_share": "50.00",
    "fmv_on_exercise_date": "120.00",
    "shares_transferred": "200",
    "employer_name": "Acme Corp"
  }
]
```

**Form 3922 Input (`form3922.json`):**
```json
[
  {
    "tax_year": 2025,
    "offering_date": "2024-01-01",
    "purchase_date": "2024-06-30",
    "fmv_on_offering_date": "140.00",
    "fmv_on_purchase_date": "150.00",
    "purchase_price_per_share": "127.50",
    "shares_transferred": "50",
    "employer_name": "Acme Corp"
  }
]
```

**1099-B Manual Override (`1099b_manual.json`):**
```json
[
  {
    "tax_year": 2025,
    "description": "100 sh ACME",
    "date_acquired": "2024-03-15",
    "date_sold": "2025-06-01",
    "proceeds": "17500.00",
    "cost_basis": "0.00",
    "basis_reported_to_irs": true,
    "broker_source": "MANUAL"
  }
]
```

#### 1.2 ManualAdapter Implementation
- Parse each JSON file type into the corresponding Pydantic model.
- Validate all required fields are present.
- Validate all monetary values parse as `Decimal`.
- Validate all dates parse as `date` in ISO format.
- Return validation errors as a list of human-readable strings.
- For Form 3921/3922, convert records into `EquityEvent` objects:
  - Form 3921 → `EquityEvent(event_type=EXERCISE, equity_type=ISO, ...)`
  - Form 3922 → `EquityEvent(event_type=PURCHASE, equity_type=ESPP, ...)`

#### 1.3 Test Cases
- Valid W-2 parses correctly.
- Valid Form 3921 produces EquityEvent with correct spread.
- Valid Form 3922 produces EquityEvent with correct discount.
- Missing required field returns validation error.
- Invalid date format returns validation error.
- Invalid Decimal value returns validation error.
- Empty file returns empty list.

### IRS Citations
- W-2: IRS Instructions for Forms W-2 and W-3
- Form 3921: IRS Instructions for Forms 3921 and 3922, Box descriptions
- Form 3922: IRS Instructions for Forms 3921 and 3922, Box descriptions

---

## Phase 2: Shareworks Ingestion Adapter

**Priority:** HIGH — Primary brokerage for equity compensation.
**Estimated Scope:** ~300 lines of code + tests

### Tax Context
Morgan Stanley Shareworks (formerly E*TRADE Equity Edge) produces 1099-B exports and supplemental gain/loss reports as CSV files. The supplemental data contains critical information not on the standard 1099-B: vest dates, grant dates, exercise prices, and lot-level detail.

### Requirements

#### 2.1 Shareworks 1099-B CSV Parser
The Shareworks 1099-B CSV typically contains:

| Column | Maps To | Notes |
|---|---|---|
| Description of Property | `Form1099B.description` | e.g. "100 SH ACME CORP" |
| Date Acquired | `Form1099B.date_acquired` | May be "Various" |
| Date Sold or Disposed | `Form1099B.date_sold` | |
| Proceeds | `Form1099B.proceeds` | Gross proceeds |
| Cost or Other Basis | `Form1099B.cost_basis` | Often $0 or blank for equity comp |
| Wash Sale Loss Disallowed | `Form1099B.wash_sale_loss_disallowed` | |
| Reported to IRS | `Form1099B.basis_reported_to_irs` | Checkbox or Y/N |
| Short-term/Long-term | `Form1099B.box_type` | ST or LT |

Implementation:
- Use `pandas.read_csv()` with appropriate dtypes.
- Strip whitespace from headers and values.
- Parse monetary values: strip `$`, `,`, `()` for negative values → `Decimal`.
- Parse dates: handle multiple formats (MM/DD/YYYY, YYYY-MM-DD, "Various").
- Parse boolean fields: "Yes"/"No", "Y"/"N", "X"/blank.
- Each row produces a `Form1099B` object.

#### 2.2 Shareworks Supplemental Gain/Loss CSV Parser
The supplemental report adds lot-level detail:

| Column | Maps To | Notes |
|---|---|---|
| Plan Type | `EquityType` | RSU, ISO, ESPP, NQSO |
| Grant Date | `EquityEvent.grant_date` | For options/ESPP |
| Vest Date | `EquityEvent.event_date` | For RSUs |
| Exercise Date | `EquityEvent.event_date` | For ISO/NSO |
| Purchase Date | `EquityEvent.event_date` | For ESPP |
| Shares | quantity | |
| FMV at Vest/Exercise | `EquityEvent.price_per_share` | |
| Grant/Strike Price | `EquityEvent.strike_price` | For options |
| Purchase Price | `EquityEvent.purchase_price` | For ESPP |
| Sale Date | `Sale.sale_date` | |
| Sale Price | `Sale.proceeds_per_share` | |
| Ordinary Income | `EquityEvent.ordinary_income` | |

Implementation:
- Parse the CSV into `EquityEvent` objects for acquisitions.
- Parse sale rows into `Sale` objects.
- Cross-reference supplemental data with 1099-B rows by matching sale date, shares, and proceeds.
- When supplemental data provides a vest/exercise date, use it to correct the 1099-B "Date Acquired" field (which is often "Various").

#### 2.3 Adapter Orchestration
`ShareworksAdapter.parse(file_path)` should:
1. Detect whether the file is a 1099-B or supplemental report (by header row).
2. Parse accordingly.
3. Return both `Form1099B` and `EquityEvent` objects.

#### 2.4 Test Cases
- Standard RSU 1099-B row parses correctly.
- Supplemental gain/loss row for RSU vest produces correct EquityEvent.
- ESPP supplemental row with offering date and purchase date.
- ISO supplemental row with grant date and exercise date.
- "Various" date acquired handled gracefully.
- Negative values in parentheses parsed as negative Decimal.
- Wash sale amount captured.
- Mismatched column headers produce validation error.
- Monetary values with `$` and `,` parse correctly.

### IRS Citations
- 1099-B: IRS Instructions for Form 1099-B, Boxes 1a-1g
- Basis reporting: IRC Section 6045(g) — covered vs. noncovered securities

---

## Phase 3: Robinhood Ingestion Adapter

**Priority:** HIGH — Secondary brokerage for general trading.
**Estimated Scope:** ~200 lines of code + tests

### Tax Context
Robinhood produces a consolidated 1099 document. The 1099-B section contains capital gains/losses from stock and options trades. Robinhood also reports dividends (1099-DIV) and interest (1099-INT) in the same document.

### Requirements

#### 3.1 Robinhood 1099-B CSV Parser
Robinhood's CSV export format:

| Column | Maps To | Notes |
|---|---|---|
| Description | `Form1099B.description` | |
| Date Acquired | `Form1099B.date_acquired` | |
| Date Sold | `Form1099B.date_sold` | |
| Proceeds | `Form1099B.proceeds` | |
| Cost Basis | `Form1099B.cost_basis` | Usually accurate for non-equity-comp trades |
| Gain/Loss | computed | Verify: proceeds - basis |
| Wash Sale Loss Disallowed | `Form1099B.wash_sale_loss_disallowed` | |
| Type | `Form1099B.box_type` | Short-term / Long-term |
| Basis Reported to IRS | `Form1099B.basis_reported_to_irs` | |

#### 3.2 Robinhood 1099-DIV Parser
| Column | Maps To |
|---|---|
| Payer | `Form1099DIV.broker_name` |
| Ordinary Dividends | `Form1099DIV.ordinary_dividends` (Box 1a) |
| Qualified Dividends | `Form1099DIV.qualified_dividends` (Box 1b) |
| Capital Gain Distributions | `Form1099DIV.total_capital_gain_distributions` (Box 2a) |
| Federal Tax Withheld | `Form1099DIV.federal_tax_withheld` |

#### 3.3 Robinhood 1099-INT Parser
| Column | Maps To |
|---|---|
| Payer | `Form1099INT.payer_name` |
| Interest Income | `Form1099INT.interest_income` (Box 1) |
| Federal Tax Withheld | `Form1099INT.federal_tax_withheld` |

#### 3.4 Test Cases
- Standard stock sale parses correctly.
- Options trade with adjusted basis.
- 1099-DIV with ordinary and qualified dividends.
- 1099-INT with interest.
- Wash sale captured.
- Verify gain/loss = proceeds - cost_basis.

### IRS Citations
- 1099-B: IRS Instructions for Form 1099-B
- 1099-DIV: IRS Instructions for Form 1099-DIV
- 1099-INT: IRS Instructions for Form 1099-INT

---

## Phase 4: Event Validation and Normalization

**Priority:** HIGH — Data quality gate before engines process anything.
**Estimated Scope:** ~250 lines of code + tests

### Tax Context
Before any tax computation runs, all imported data must be validated for completeness and consistency. The most dangerous errors in equity tax are:
1. Missing vest dates (causes incorrect holding period → wrong ST/LT classification).
2. Missing or $0 cost basis (causes overstated gains).
3. Shares sold exceeding shares acquired (data corruption).
4. Duplicate imports of the same transactions.

### Requirements

#### 4.1 Event Validation Rules (`app/normalization/events.py`)
Implement `_validate()` with these checks:

**Mandatory Field Validation:**
- Every event must have: id, event_type, equity_type, security, event_date, shares > 0, price_per_share >= 0.
- EXERCISE events must have: strike_price.
- PURCHASE events must have: purchase_price, offering_date.
- VEST events must have: price_per_share > 0 (FMV at vest).

**Date Validation:**
- event_date must not be in the future.
- For ESPP: offering_date < purchase_date.
- For ISO/NSO: grant_date < exercise_date.
- sale_date >= acquisition_date (cannot sell before acquiring).

**Cross-Validation (across all events):**
- Total shares sold for a security must not exceed total shares acquired.
- W-2 equity income should approximately match the sum of ordinary income from individual equity events (flag discrepancies > 5%).
- If Form 3921 exists, every ISO exercise should have a matching EquityEvent.
- If Form 3922 exists, every ESPP purchase should have a matching EquityEvent.

#### 4.2 Enhanced Deduplication
Current deduplication is ID-based only. Enhance it:
- If two events from different sources (e.g., Shareworks supplemental + manual entry) describe the same economic event (same date, security, shares, price), merge them — keeping the richer record.
- Log all deduplication decisions in the audit trail.

#### 4.3 W-2 Cross-Reference Engine
Create `app/normalization/w2_reconciler.py`:
- Given a W-2 and a list of EquityEvents, verify:
  - Sum of RSU ordinary income ≈ W-2 Box 14 "RSU" value.
  - Sum of NSO ordinary income ≈ W-2 Box 12 Code V value.
  - Sum of ESPP ordinary income (if disqualifying dispositions occurred in the same year) should be reflected in W-2 wages.
- Flag any discrepancy with a `ReconciliationError`.

#### 4.4 Test Cases
- Event with missing required field fails validation.
- Future date fails validation.
- ESPP with offering_date > purchase_date fails.
- Shares sold > shares acquired flags error.
- Duplicate events from different sources are merged.
- W-2 equity income matches within 5% tolerance → passes.
- W-2 equity income mismatch > 5% → flags warning.
- Validation passes for clean dataset.

### IRS Citations
- W-2 Box 12 Code V: IRC Section 83, Pub. 525 "Nonstatutory Stock Options"
- W-2 Box 14: Employer-specific reporting for RSU income
- Cross-validation: Pub. 525 general principle that all compensation income appears on W-2

---

## Phase 5: Complete Basis Correction Engine

**Priority:** CRITICAL — The core value proposition of the system.
**Estimated Scope:** ~300 lines of code + tests

### Tax Context
This phase completes the two remaining basis correction methods: ESPP and ISO. These are the most complex because:
- **ESPP** requires computing ordinary income (which depends on qualifying vs. disqualifying) before the basis can be determined.
- **ISO** requires maintaining two parallel basis values: regular tax basis and AMT basis.

### Requirements

#### 5.1 ESPP Basis Correction (`basis.py` → `correct_espp_basis()`)

**Integration with ESPPEngine:**
1. Look up the Form 3922 for this lot (by purchase date and security).
2. Call `ESPPEngine.compute_disposition()` to get ordinary income and disposition type.
3. Compute correct basis:
   - `correct_basis = (purchase_price_per_share + ordinary_income_per_share) * shares_sold`
   - Where `ordinary_income_per_share = total_ordinary_income / shares_sold`
4. Generate Form 8949 adjustment:
   - If broker reported $0 or purchase_price only → `adjustment_code = B`
   - `adjustment_amount = correct_basis - broker_reported_basis`

**Per Pub. 525 ("Employee Stock Purchase Plans"):**
- Qualifying disposition: Ordinary income = lesser of (a) actual gain, (b) discount at offering date. Basis adjusted upward by this amount. Remainder = LTCG.
- Disqualifying disposition: Ordinary income = spread at purchase date. Basis adjusted upward by this amount. Remainder = STCG or LTCG.
- CRITICAL: Without basis adjustment, the taxpayer pays tax twice on the ordinary income portion (once as W-2 wages, once as capital gain).

**Formulas:**
```
# Qualifying Disposition
ordinary_income_per_share = min(
    sale_price - purchase_price,                    # actual gain
    fmv_at_offering * discount_rate                 # discount at offering (typically 15%)
)
ordinary_income_per_share = max(ordinary_income_per_share, 0)  # cannot be negative
adjusted_basis_per_share = purchase_price + ordinary_income_per_share
capital_gain = sale_price - adjusted_basis_per_share  # LTCG

# Disqualifying Disposition
ordinary_income_per_share = fmv_at_purchase - purchase_price   # spread at purchase
adjusted_basis_per_share = purchase_price + ordinary_income_per_share  # = fmv_at_purchase
capital_gain = sale_price - adjusted_basis_per_share           # STCG or LTCG
```

#### 5.2 ISO Basis Correction (`basis.py` → `correct_iso_basis()`)

**Dual-Basis Tracking (per Form 6251 Instructions):**
1. Look up the Form 3921 for this lot (by exercise date and security).
2. Determine if this is a qualifying or disqualifying disposition:
   - Qualifying: held > 2 years from grant date AND > 1 year from exercise date.
   - Disqualifying: failed either holding period.
3. For **qualifying disposition**:
   - Regular tax basis = strike_price * shares (no ordinary income recognized).
   - AMT basis = fmv_at_exercise * shares.
   - Report on Form 8949 with regular basis.
   - AMT adjustment = (regular_gain - amt_gain) — reduces AMT income.
4. For **disqualifying disposition**:
   - Ordinary income = (fmv_at_exercise - strike_price) * shares (or actual gain if less per Pub. 525).
   - Adjusted basis (regular) = strike_price + ordinary_income = fmv_at_exercise (or actual sale price if sold at a loss).
   - No AMT adjustment needed (the disqualifying disposition eliminates the preference item).
   - If the disqualifying disposition generates ordinary income, the W-2 should reflect this.

**Formulas:**
```
# Qualifying ISO Disposition
regular_basis = strike_price * shares
amt_basis = fmv_at_exercise * shares
regular_gain = proceeds - regular_basis                  # all LTCG
amt_gain = proceeds - amt_basis
amt_adjustment = amt_gain - regular_gain                 # negative number (reduces AMTI)

# Disqualifying ISO Disposition
spread = fmv_at_exercise - strike_price
if sale_price >= fmv_at_exercise:
    ordinary_income = spread * shares
else:
    ordinary_income = max(sale_price - strike_price, 0) * shares  # limited to actual gain
regular_basis = (strike_price * shares) + ordinary_income
capital_gain = proceeds - regular_basis                  # may be STCG or LTCG
amt_adjustment = 0                                       # no AMT impact
```

#### 5.3 Wash Sale Detection
Add to `BasisCorrectionEngine`:
- `detect_wash_sales(sales: list[SaleResult], lots: list[Lot]) -> list[SaleResult]`
- For each sale at a loss, check if a substantially identical security was purchased 30 days before or after the sale date.
- "Substantially identical" = same CUSIP or ticker.
- If wash sale detected:
  - Disallow the loss: `wash_sale_disallowed = abs(loss)`
  - Adjust basis of replacement lot: add disallowed loss to replacement lot's cost basis.
  - Set `adjustment_code = AdjustmentCode.E` (wash sale) in addition to any basis adjustment.
- Per Pub. 550 ("Wash Sales"): check across ALL accounts (Shareworks + Robinhood).

#### 5.4 Test Cases

**ESPP Tests:**
- Qualifying disposition with stock appreciation → ordinary income = lesser of discount and gain.
- Qualifying disposition with stock decline → ordinary income = $0 (sold below purchase price).
- Disqualifying disposition → ordinary income = spread at purchase.
- Basis adjustment prevents double taxation: verify `adjusted_basis = purchase_price + ordinary_income`.
- Same-day sale (ESPP purchase and immediate sell) → disqualifying, ordinary income = spread.

**ISO Tests:**
- Qualifying ISO disposition → no ordinary income, all LTCG, AMT adjustment reported.
- Disqualifying ISO disposition (sold within 1 year of exercise) → ordinary income = spread, basis adjusted.
- Disqualifying ISO sold at a loss → ordinary income limited to actual gain.
- ISO with $0 broker-reported basis → full correction with code B.
- AMT basis ≠ regular basis: verify both tracked correctly.

**Wash Sale Tests:**
- Loss sale with repurchase within 30 days → loss disallowed, replacement basis increased.
- Loss sale with no repurchase → loss allowed normally.
- Cross-account wash sale (sell in Robinhood, buy in Shareworks within 30 days) → detected.
- Gain sale → wash sale rules do not apply.

### IRS Citations
- ESPP qualifying disposition: Pub. 525 "Employee Stock Purchase Plans," Form 3922 Instructions
- ESPP basis adjustment: Pub. 525 "Adjusted Basis"
- ISO qualifying/disqualifying: Pub. 525 "Incentive Stock Options," Form 3921 Instructions
- ISO AMT: Form 6251 Line 2i, Form 6251 Instructions
- ISO disqualifying income limitation: Pub. 525 "Dispositions of Incentive Stock Options"
- Wash sales: Pub. 550 "Wash Sales," IRC Section 1091

---

## Phase 6: Complete AMT Engine

**Priority:** HIGH — Required for any taxpayer who exercises ISOs.
**Estimated Scope:** ~250 lines of code + tests

### Tax Context
The Alternative Minimum Tax (AMT) is a parallel tax system that primarily affects taxpayers who exercise ISOs. The ISO spread at exercise is an AMT preference item that increases AMT income. If the tentative minimum tax exceeds regular tax, the taxpayer owes AMT. However, AMT paid due to ISO exercises generates a credit (Minimum Tax Credit) that carries forward indefinitely and offsets regular tax in future years.

### Requirements

#### 6.1 AMT Liability Computation (`iso_amt.py` → `compute_amt_liability()`)

**Step-by-step per Form 6251:**

1. **Start with regular taxable income** (from TaxEstimator).
2. **Add back preference items:**
   - ISO spread at exercise: `sum(form3921.spread * form3921.shares)` (Line 2i).
   - State and local tax deduction (if itemizing): add back SALT deduction (Line 2a).
3. **Compute AMTI** (Alternative Minimum Taxable Income):
   - `AMTI = taxable_income + preferences`
4. **Apply AMT exemption:**
   - 2025 exemption: $85,700 (Single) / $133,300 (MFJ).
   - Phase-out: exemption reduced by 25% of AMTI above $609,350 (Single) / $1,218,700 (MFJ).
   - `exemption = max(base_exemption - 0.25 * max(AMTI - phaseout_start, 0), 0)`
5. **Compute AMTI after exemption:**
   - `amt_base = max(AMTI - exemption, 0)`
6. **Apply AMT rates:**
   - 26% on first $232,600 (2025, MFJ) / $116,300 (Single).
   - 28% on amount above that threshold.
   - For LTCG/qualified dividends within AMT: use preferential rates (0/15/20%) instead.
7. **Tentative minimum tax** = AMT computed above.
8. **AMT owed** = `max(tentative_minimum_tax - regular_tax, 0)`.

**Formulas:**
```
amti = regular_taxable_income + iso_preference + salt_addback
exemption_raw = AMT_EXEMPTION[filing_status]
phaseout = max(amti - AMT_PHASEOUT_START[filing_status], Decimal("0"))
exemption = max(exemption_raw - phaseout * Decimal("0.25"), Decimal("0"))
amt_base = max(amti - exemption, Decimal("0"))

# Separate ordinary AMT income from LTCG AMT income
amt_ordinary = amt_base - ltcg_in_amt
if amt_ordinary <= amt_26_threshold:
    amt_on_ordinary = amt_ordinary * Decimal("0.26")
else:
    amt_on_ordinary = amt_26_threshold * Decimal("0.26") + (amt_ordinary - amt_26_threshold) * Decimal("0.28")
amt_on_ltcg = <apply preferential LTCG rates to LTCG portion>
tentative_minimum_tax = amt_on_ordinary + amt_on_ltcg

amt_owed = max(tentative_minimum_tax - regular_tax_before_credits, Decimal("0"))
```

#### 6.2 AMT Credit Carryforward (`iso_amt.py` → `compute_amt_credit()`)

**Per Form 8801 (Prior Year Minimum Tax Credit):**
- AMT paid due to "deferral items" (like ISO exercises) generates a credit.
- ISO AMT is a deferral item (the income is just deferred, not permanently excluded).
- Credit = AMT attributable to deferral items (in most ISO cases, this is the full AMT amount).
- Credit carries forward indefinitely.
- In each future year: `credit_used = min(credit_available, regular_tax - tentative_minimum_tax)`
- The credit can only reduce regular tax down to the tentative minimum tax.

**Model addition — add to `app/models/reports.py`:**
```python
class AMTCreditTracker(BaseModel):
    tax_year: int
    prior_year_credit: Decimal        # Carried from previous year
    current_year_amt: Decimal          # AMT generated this year
    credit_used: Decimal               # Credit applied this year
    remaining_credit: Decimal          # Carried to next year
```

#### 6.3 Add AMT Bracket Configuration to `brackets.py`
```python
AMT_RATE_THRESHOLD: dict[int, dict[FilingStatus, Decimal]] = {
    2025: {
        FilingStatus.SINGLE: Decimal("116300"),
        FilingStatus.MFJ: Decimal("232600"),
    },
}
```

#### 6.4 Test Cases
- No ISO exercises → AMT = $0.
- Small ISO exercise below exemption → AMT = $0.
- ISO exercise that pushes AMTI above exemption → compute correct AMT.
- AMT exemption phase-out: verify exemption reduces correctly.
- AMT credit from prior year: verify credit offsets regular tax.
- AMT credit cannot reduce regular tax below tentative minimum tax.
- High-income taxpayer: verify 28% rate kicks in above threshold.
- LTCG within AMT: verify preferential rates apply.

### IRS Citations
- Form 6251 Instructions (all lines)
- Form 6251 Line 2i: ISO preference item
- Form 8801 Instructions: Prior Year Minimum Tax Credit
- IRC Section 55-59: Alternative Minimum Tax
- IRC Section 53: Minimum Tax Credit

---

## Phase 7: Complete Tax Estimator

**Priority:** HIGH — Core output for the user.
**Estimated Scope:** ~200 lines of code + tests

### Tax Context
The current estimator uses a simplified flat 15% LTCG rate. This phase implements proper LTCG bracket computation and integrates AMT from Phase 6.

### Requirements

#### 7.1 Proper LTCG/Qualified Dividend Tax (`estimator.py` → `compute_ltcg_tax()`)

**Per Schedule D Instructions and IRS Qualified Dividends and Capital Gain Tax Worksheet:**
LTCG and qualified dividends are taxed at preferential rates, but the rate depends on where the income falls in the taxpayer's overall income stack.

The computation (simplified):
1. Compute regular taxable income.
2. Subtract LTCG + qualified dividends = ordinary taxable income.
3. Tax ordinary income at regular brackets.
4. Stack LTCG + qualified dividends on top of ordinary income:
   - Portion falling in 0% LTCG bracket → 0% tax.
   - Portion falling in 15% LTCG bracket → 15% tax.
   - Portion falling in 20% LTCG bracket → 20% tax.
5. Total tax = ordinary tax + LTCG tax.

**LTCG Rate Brackets (2025):**
| Taxable Income (Single) | Taxable Income (MFJ) | LTCG Rate |
|---|---|---|
| Up to $48,475 | Up to $96,950 | 0% |
| $48,476 - $533,400 | $96,951 - $600,050 | 15% |
| Above $533,400 | Above $600,050 | 20% |

#### 7.2 AMT Integration
- After computing regular tax, call `ISOAMTEngine.compute_amt_liability()`.
- Add AMT to total federal tax.
- Apply AMT credit from prior years.
- Update `TaxEstimate` model fields: `federal_amt`, `federal_total_tax`.

#### 7.3 California AMT
California has its own AMT with different exemption amounts. For simplicity in Phase 7:
- If federal AMT applies, flag it for California review.
- California AMT exemption is lower than federal.
- California taxes all income at ordinary rates (no LTCG preference) — so CA AMT is less common.

#### 7.4 Estimated Tax Penalty Check
Per Form 2210 safe harbor rules:
- No penalty if total tax withholding >= 90% of current year tax, OR
- Total withholding >= 100% of prior year tax (110% if prior year AGI > $150K).
- Add a method: `check_estimated_tax_penalty(current_tax, prior_year_tax, total_withheld, agi) -> bool`
- Flag if the taxpayer may owe an underpayment penalty.

#### 7.5 Test Cases
- LTCG stacking: $100K ordinary + $50K LTCG → verify 0% and 15% portions correct.
- High income: $500K ordinary + $200K LTCG → verify 15% and 20% portions.
- AMT integration: ISO exercise → AMT adds to federal total.
- Safe harbor: withholding >= 90% → no penalty flag.
- Under-withholding: withholding < 90% → penalty flag.

### IRS Citations
- Schedule D Instructions: "Qualified Dividends and Capital Gain Tax Worksheet"
- Form 6251: AMT integration
- Form 2210: Underpayment of Estimated Tax
- IRC Section 1(h): Capital gains rates

---

## Phase 8: Strategy Engine

**Priority:** MEDIUM — Value-add for tax planning.
**Estimated Scope:** ~400 lines of code + tests

### Tax Context
This is where the system goes beyond reporting and into advising. The strategy engine analyzes the taxpayer's current position and recommends actions to reduce taxes.

### Requirements

#### 8.1 Tax-Loss Harvesting Analyzer
**Module:** `app/engines/strategy.py` → `_analyze_tax_loss_harvesting()`

Inputs: list of all Lots with current market prices.
Logic:
- Identify lots with unrealized losses.
- Compute potential tax savings: `loss * marginal_rate`.
- Check for wash sale risk: would selling trigger a wash sale with recent purchases?
- Prioritize: short-term losses offset short-term gains first (taxed at ordinary rates).
- California impact: all gains taxed at ordinary rates, so loss harvesting is equally valuable for LTCG in CA.

Output per lot:
```
Strategy: Tax-Loss Harvesting
Security: ACME
Unrealized Loss: ($5,000)
Federal Savings: $1,200 (at 24% marginal rate)
CA Savings: $665 (at 13.3%)
Total Savings: $1,865
Wash Sale Risk: None (no purchases in 30-day window)
Risk Level: Low
Deadline: December 31, 2025
```

#### 8.2 ESPP Holding Period Optimizer
**Module:** `app/engines/strategy.py` → `_analyze_espp_holding()`

Inputs: ESPP lots still held, Form 3922 data.
Logic:
- For each unsold ESPP lot, compute:
  - Days until qualifying disposition.
  - Tax difference between selling now (disqualifying) vs. waiting (qualifying).
  - Ordinary income difference: `spread_at_purchase - min(actual_gain, offering_discount)`.
  - Risk: stock price decline during holding period could exceed tax savings.

Output:
```
Strategy: ESPP Hold for Qualifying Disposition
Security: ACME (50 shares, purchased 2024-06-30)
Qualifying Date: 2026-01-02 (175 days away)
Tax Savings if Qualifying: $500 (ordinary income reduced from $1,125 to $625)
Risk: Stock must not decline >3.5% to break even on holding
Risk Level: Moderate
```

#### 8.3 ISO Exercise Optimizer
**Module:** `app/engines/strategy.py` → `_analyze_iso_exercise()`

Inputs: unexercised ISO grants, current stock price, current income.
Logic:
- Compute AMT exemption remaining: `exemption - (current_AMTI - phaseout_start)`.
- Compute max shares exercisable before triggering AMT: `exemption_remaining / spread_per_share`.
- Model scenarios:
  - Exercise 0 shares: no AMT, but options may expire.
  - Exercise up to AMT exemption: no AMT, builds AMT basis.
  - Exercise beyond exemption: triggers AMT, but may be worthwhile if stock is appreciating.

Output:
```
Strategy: ISO Exercise Optimization
Max Shares Before AMT: 1,221 shares ($85,700 exemption / $70.00 spread)
Scenario A — Exercise 0: No AMT, options continue to vest
Scenario B — Exercise 1,000: No AMT, builds $70K of AMT basis
Scenario C — Exercise 2,000: $18,600 AMT, generates $18,600 MTC credit
Recommendation: Exercise up to 1,221 shares if stock outlook is positive
Risk Level: Moderate (stock price risk)
```

#### 8.4 Income Smoothing Analyzer
**Module:** `app/engines/strategy.py` → `_analyze_income_smoothing()`

Inputs: current year income, projected next year income, unrealized gains.
Logic:
- If current year marginal rate is significantly higher than projected next year:
  - Defer income (delay RSU sales) to next year.
- If current year rate is lower:
  - Accelerate income (sell appreciated shares this year).
- California threshold: $1M income triggers additional 1% Mental Health Services Tax.
  - If total income is near $1M, recommend deferring sales to stay below.

#### 8.5 AMT Credit Recovery Planner
**Module:** `app/engines/strategy.py` → `_analyze_amt_credit_recovery()`

Inputs: accumulated AMT credit, current year regular tax, tentative minimum tax.
Logic:
- Compute credit usable this year: `min(credit, regular_tax - tentative_min_tax)`.
- Project years to full recovery at current income levels.
- Recommend actions to accelerate recovery (e.g., avoid ISO exercises to keep AMTI low).

#### 8.6 StrategyRecommendation Model Update
Add `tier` field to the existing model:
```python
tier: str  # "Tier 1 — Low Risk" / "Tier 2 — Moderate" / "Tier 3 — Complex"
```

#### 8.7 Test Cases
- Tax-loss harvesting: lot with $5K unrealized loss → correct savings computed.
- ESPP hold: 90 days from qualifying → savings vs. risk quantified.
- ISO exercise: $70 spread, $85K exemption → max shares = 1,214.
- Income smoothing: income at $980K → recommend deferring $50K sale.
- AMT credit: $20K credit, $50K regular tax, $35K TMT → $15K credit usable.
- No losses, no ISOs, no ESPP → strategy engine returns empty list gracefully.

### IRS Citations
- Tax-loss harvesting: Pub. 550 "Wash Sales," Schedule D Instructions
- ESPP holding: Pub. 525 "Employee Stock Purchase Plans"
- ISO exercise: Pub. 525 "Incentive Stock Options," Form 6251 Instructions
- AMT credit: Form 8801 Instructions
- California $1M threshold: CA Revenue and Taxation Code Section 17043

---

## Phase 9: CLI Wiring and End-to-End Workflow

**Priority:** CRITICAL — Makes the system usable.
**Estimated Scope:** ~350 lines of code + tests

### Requirements

#### 9.1 Database Initialization
- On first CLI invocation, create SQLite database at `~/.equitytax/data.db`.
- Run `create_schema()` and `migrate()`.
- Store database path in a config constant.

#### 9.2 `import-data` Command
Wire to ingestion adapters:
```python
@app.command()
def import_data(source, file, year):
    adapter = get_adapter(source)        # ShareworksAdapter / RobinhoodAdapter / ManualAdapter
    data = adapter.parse(file)
    errors = adapter.validate(data)
    if errors:
        print_errors(errors)
        raise typer.Exit(1)
    # Persist to database
    for item in data:
        if isinstance(item, EquityEvent):
            repo.save_event(item)
        elif isinstance(item, Form1099B):
            # Convert to Sale and persist
            ...
    print(f"Imported {len(data)} records from {source}")
```

#### 9.3 `reconcile` Command
Wire to basis correction pipeline:
```python
@app.command()
def reconcile(year):
    # 1. Load events and sales from database for the year
    # 2. Normalize events
    # 3. Build lots from acquisition events
    # 4. Match sales to lots
    # 5. For each (lot, sale), run basis correction
    # 6. Detect wash sales across all results
    # 7. Save SaleResults to database
    # 8. Print reconciliation summary
```

#### 9.4 `estimate` Command
Wire to tax estimator:
```python
@app.command()
def estimate(year):
    # 1. Load W-2s, sale_results, 1099-DIV, 1099-INT from database
    # 2. Sum income categories
    # 3. Call TaxEstimator.estimate()
    # 4. If ISOs present, call ISOAMTEngine
    # 5. Print tax estimate summary
```

#### 9.5 `strategy` Command
Wire to strategy engine:
```python
@app.command()
def strategy(year):
    # 1. Load tax estimate and lot data
    # 2. Call StrategyEngine.analyze()
    # 3. Print recommendations
```

#### 9.6 `report` Command
Wire to report generators:
```python
@app.command()
def report(year, output):
    # 1. Load all data for the year
    # 2. Generate: Form 8949, ESPP Report, AMT Worksheet, Reconciliation, Strategy
    # 3. Write each report to output directory
    # 4. Print summary of files generated
```

#### 9.7 Additional Repository Methods
Add to `TaxRepository`:
- `get_events_by_year(year: int) -> list[dict]`
- `get_sales_by_year(year: int) -> list[dict]`
- `get_sale_results_by_year(year: int) -> list[dict]`
- `get_w2s_by_year(year: int) -> list[dict]`
- `save_w2(w2: W2) -> None`
- `save_form1099b(form: Form1099B) -> None`
- `save_form1099div(form: Form1099DIV) -> None`
- `save_form1099int(form: Form1099INT) -> None`

Also add tables for W-2, 1099-DIV, 1099-INT to the schema:
```sql
CREATE TABLE IF NOT EXISTS w2s (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_year INTEGER NOT NULL,
    employer_name TEXT NOT NULL,
    employer_ein TEXT,
    box1_wages TEXT NOT NULL,
    box2_federal_withheld TEXT NOT NULL,
    box12_codes TEXT,  -- JSON
    box14_other TEXT,  -- JSON
    box16_state_wages TEXT,
    box17_state_withheld TEXT,
    state TEXT DEFAULT 'CA',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS form_1099_div (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_year INTEGER NOT NULL,
    broker_name TEXT NOT NULL,
    ordinary_dividends TEXT NOT NULL,
    qualified_dividends TEXT NOT NULL,
    total_capital_gain_distributions TEXT DEFAULT '0',
    federal_tax_withheld TEXT DEFAULT '0',
    state_tax_withheld TEXT DEFAULT '0',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS form_1099_int (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_year INTEGER NOT NULL,
    payer_name TEXT NOT NULL,
    interest_income TEXT NOT NULL,
    early_withdrawal_penalty TEXT DEFAULT '0',
    federal_tax_withheld TEXT DEFAULT '0',
    state_tax_withheld TEXT DEFAULT '0',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

#### 9.8 Test Cases
- End-to-end: import W-2 + 1099-B + 3922 → reconcile → estimate → report.
- Import validation errors are surfaced to user.
- Reconcile with no data → helpful error message.
- Report generation creates files in output directory.
- Database is created on first run.

### IRS Citations
- All citations from previous phases apply to the CLI orchestration.

---

## Phase 10: Integration Testing and Hardening

**Priority:** HIGH — Production readiness.
**Estimated Scope:** ~400 lines of test code + fixes

### Requirements

#### 10.1 End-to-End Test Scenarios

**Scenario A: RSU-Only Taxpayer**
- W-2 with $200K wages including $50K RSU income.
- 1099-B showing RSU sale with $0 reported basis.
- Expected: Form 8949 with code B adjustment, correct LTCG, accurate federal + CA estimate.

**Scenario B: ESPP Taxpayer with Qualifying Disposition**
- Form 3922 with 15% discount ESPP purchase.
- Sale > 2 years from offering, > 1 year from purchase.
- Expected: Lesser-of-rule ordinary income, basis adjustment, LTCG for remainder.

**Scenario C: ESPP Taxpayer with Disqualifying Disposition**
- Same Form 3922.
- Sale < 1 year from purchase.
- Expected: Spread-at-purchase ordinary income, basis adjustment, STCG for remainder.

**Scenario D: ISO Taxpayer with AMT**
- Form 3921 with large ISO exercise.
- No sale in the same year.
- Expected: AMT preference item computed, AMT liability if above exemption, MTC credit generated.

**Scenario E: ISO Disqualifying Disposition**
- Form 3921 + sale within 1 year of exercise.
- Expected: Ordinary income = spread (limited to actual gain if sold at loss), basis adjusted.

**Scenario F: Mixed Portfolio**
- W-2 with RSU + NSO income.
- Shareworks 1099-B with RSU and NSO sales.
- Robinhood 1099-B with stock trades.
- 1099-DIV with qualified dividends.
- 1099-INT with interest.
- Form 3922 with ESPP sale.
- Expected: All income types aggregated, correct federal + CA estimate, Form 8949 with all categories.

**Scenario G: Wash Sale Cross-Account**
- Sell ACME at loss in Robinhood on Dec 15.
- Buy ACME in Shareworks on Dec 30 (within 30 days).
- Expected: Loss disallowed, replacement lot basis increased, code e on Form 8949.

**Scenario H: California $1M Threshold**
- Total income: $1,050,000.
- Expected: 1% Mental Health Services Tax on $50K = $500 additional CA tax.

#### 10.2 Audit Trail Verification
- Every engine operation must produce an `AuditEntry`.
- Verify audit log records all basis corrections, income computations, and estimation steps.
- Audit trail should be human-readable and sufficient for a CPA review.

#### 10.3 Error Handling
- Graceful handling of missing Form 3921 when ISO sale is detected → flag to user.
- Graceful handling of missing Form 3922 when ESPP sale is detected → flag to user.
- Database corruption recovery: verify schema version on startup.
- File not found: clear error message with path.

#### 10.4 Edge Cases
- $0 proceeds sale (worthless securities).
- Fractional shares (common with ESPP).
- Same-day acquisition and sale.
- Feb 29 leap year holding period calculation.
- Multiple W-2s (multiple employers).
- Shares acquired in prior tax year, sold in current year.

---

## Accountant Verification Checklist

**[ACCOUNTANT] — Please verify the following across all phases:**

- [ ] **Lot Integrity:** Every share acquired can be traced to a specific lot. No lot is double-counted. Lots consumed by sales have `shares_remaining` decremented correctly.
- [ ] **Basis Computation:** RSU basis = FMV at vest. NSO basis = strike + ordinary income. ESPP basis = purchase price + ordinary income. ISO regular basis = strike. ISO AMT basis = FMV at exercise.
- [ ] **Double-Entry Consistency:** Every equity event can be represented as a journal entry where debits = credits.
- [ ] **Reconciliation Completeness:** The reconciliation report accounts for every sale on the 1099-B with a matching lot, correct basis, and appropriate Form 8949 category and adjustment code.
- [ ] **Rounding:** Intermediate calculations retain full Decimal precision. IRS form values round to whole dollars using banker's rounding. California form values round per FTB rules.
- [ ] **Holding Period:** Starts day AFTER acquisition per IRS rules. > 1 year = long-term.
- [ ] **Wash Sale Basis Transfer:** When a wash sale is detected, the disallowed loss is added to the replacement lot's cost basis and the replacement lot's holding period includes the original lot's holding period.
- [ ] **Income Classification:** Ordinary income from equity events is correctly segregated from capital gains. W-2 cross-reference validates totals.

---

## Tax Planner Verification Checklist

**[TAX PLANNER] — Please verify the following in Phase 8:**

- [ ] **Tax-Loss Harvesting:** Correctly identifies unrealized losses, computes savings at marginal rate, checks wash sale risk window (30 days before AND after), considers California impact (no LTCG preference).
- [ ] **ESPP Optimization:** Correctly computes qualifying disposition date, quantifies tax savings vs. stock-price risk, considers concentration risk.
- [ ] **ISO Exercise Modeling:** Correctly computes AMT exemption remaining, models exercise scenarios at different share counts, accounts for AMT phase-out.
- [ ] **Income Smoothing:** Identifies California $1M Mental Health Services Tax cliff, considers marginal rate differences across years.
- [ ] **AMT Credit Recovery:** Correctly projects recovery timeline based on current income, recommends actions to accelerate recovery.
- [ ] **Strategy Prioritization:** Tier 1 (low risk, high impact) recommendations come first. All recommendations include quantified dollar savings. Risk levels accurately reflect the strategy's complexity and assumptions.
- [ ] **California-Specific:** Every strategy notes whether it works differently for California (especially: no LTCG preference, Mental Health Services Tax, CA AMT differences).
- [ ] **Forward-Looking:** Strategies consider multi-year impact, not just current year.

---

## Phase Dependencies

```
Phase 1 (Manual Adapter) ──────────────────────┐
Phase 2 (Shareworks Adapter) ──────────────────┤
Phase 3 (Robinhood Adapter) ───────────────────┤
                                                ▼
Phase 4 (Validation & Normalization) ──────────┤
                                                ▼
Phase 5 (Complete Basis Engine) ───────────────┤
Phase 6 (Complete AMT Engine) ─────────────────┤
                                                ▼
Phase 7 (Complete Tax Estimator) ──────────────┤
                                                ▼
Phase 8 (Strategy Engine) ─────────────────────┤
                                                ▼
Phase 9 (CLI Wiring) ─────────────────────────┤
                                                ▼
Phase 10 (Integration Testing) ────────────────┘
```

**Parallelizable:**
- Phases 1, 2, 3 can be built in parallel.
- Phases 5 and 6 can be built in parallel (after Phase 4).
- Phase 8 can start after Phase 7 but can be developed in parallel with Phase 9.

---

## Addendum A: Accountant Verification Findings (Must-Fix)

**Status:** CONDITIONAL APPROVAL — The following items must be addressed before implementation.

### A.1 Lot State Management (MUST FIX — Phases 5, 9)
The plan references `shares_remaining` in the Accountant Verification Checklist but no phase specifies the decrement logic. Add to Phase 5:
- When a sale is matched to a lot via `LotMatcher`, decrement `lot.shares_remaining` by the number of shares sold.
- If `shares_remaining` reaches 0, mark the lot as fully consumed.
- If a sale requires more shares than available in a lot, split across multiple lots (FIFO order).
- Add `shares_remaining: Decimal` field to `Lot` model if not already present.
- Test: Lot with 100 shares, sell 60 → `shares_remaining = 40`. Sell 40 more → `shares_remaining = 0`.

### A.2 Multi-Lot Sale Handling (MUST FIX — Phase 5)
A single 1099-B row may cover a sale of shares from multiple lots (e.g., "100 sh ACME" where 60 came from a 2023 vest and 40 from a 2024 vest). Add:
- `BasisCorrectionEngine` must handle multi-lot sales by producing one `SaleResult` per lot consumed.
- Each partial `SaleResult` gets its own Form 8949 line with the correct basis and holding period.
- The sum of all partial proceeds must equal the 1099-B reported proceeds.
- Test: 100-share sale spanning 2 lots (60 + 40) → 2 Form 8949 lines, proceeds split proportionally.

### A.3 Capital Loss Limitation (MUST FIX — Phase 7)
The estimator must enforce the $3,000 annual capital loss deduction limit (IRC Section 1211(b)):
- Net capital losses exceeding $3,000 are carried forward to the next tax year.
- Add `capital_loss_carryforward: Decimal` to `TaxEstimate` model.
- California follows the same $3,000 limit.
- Test: $20K STCG + ($30K) LTCL = ($10K) net loss → deduct $3K, carry forward $7K.

### A.4 Double-Entry Validation (MUST FIX — Phase 4)
Add a validation check to `EventNormalizer`:
- For every VEST event that creates ordinary income, verify there's a corresponding W-2 income entry.
- For every SALE event, verify the proceeds balance against the basis + gain/loss.
- Log all validation results as `AuditEntry` records.
- This doesn't require full journal-entry accounting, but should flag obvious imbalances.

### A.5 Additional Recommendations (SHOULD FIX)

**Fractional Share Handling (Phase 5):**
- ESPP purchases commonly result in fractional shares (e.g., 49.73 shares).
- All share quantities must use `Decimal`, not `int`.
- Verify the `Lot.shares` and `Sale.shares` fields accept fractional values.

**Wash Sale Replacement Lot Identification (Phase 5):**
- When a wash sale is detected, the plan says to adjust the replacement lot's basis but doesn't specify HOW to identify which lot is the replacement.
- Rule: The replacement lot is the FIRST substantially identical purchase within the 61-day window (30 before + 30 after sale date).
- If multiple purchases exist, allocate the disallowed loss proportionally.

**ISO AMT Preference Reversal (Phase 6):**
- If ISO shares are exercised and sold in the SAME tax year (disqualifying disposition), the AMT preference item from that exercise must be reversed (set to $0).
- The disqualifying disposition eliminates the AMT timing difference.
- Add this reversal logic to `ISOAMTEngine.compute_amt_preference()`.

**Worthless Securities (Phase 10):**
- A sale with $0 proceeds represents worthless securities.
- Holding period rule: deemed sold on the LAST day of the tax year (Rev. Rul. 2003-18).
- Always long-term if held > 1 year as of December 31.
- Basis = full acquisition cost → results in a capital loss.

---

## Addendum B: Tax Planner Verification Findings (Must-Fix)

**Status:** CONDITIONAL APPROVAL — Phase 8 needs significant expansion.

### B.1 Wash Sale 61-Day Window Logic (MUST FIX — Phase 5)
The plan mentions "30 days before or after" but the implementation spec in Phase 5.3 must clarify:
- The wash sale window is 61 days total: 30 days BEFORE the sale, the sale date itself, and 30 days AFTER.
- Both purchases and sales of call options on the same security can trigger wash sales.
- Compute: `wash_window_start = sale_date - timedelta(days=30)`, `wash_window_end = sale_date + timedelta(days=30)`.
- Test edge case: purchase exactly 30 days after sale → wash sale. Purchase 31 days after → no wash sale.

### B.2 ESPP Analysis Gaps (MUST FIX — Phase 8)
Add to `_analyze_espp_holding()`:
- **Concentration risk warning:** If ESPP holdings exceed 10% of portfolio, recommend diversification regardless of tax benefit.
- **California impact:** CA taxes all gains at ordinary rates, so the qualifying/disqualifying distinction has LESS impact in CA than federally (the ordinary income portion is taxed the same either way in CA).
- Quantify the CA-specific savings separately from federal.

### B.3 ISO AMT Phase-Out Formula (MUST FIX — Phase 8)
The ISO exercise optimizer in 8.3 computes `max shares before AMT` using the raw exemption. It must account for the exemption phase-out:
- If AMTI > phaseout threshold, the effective exemption is reduced.
- `effective_exemption = max(base_exemption - 0.25 * max(amti - phaseout_start, 0), 0)`
- The `max_shares` calculation must use the effective exemption AFTER phase-out.
- Add iterative solver: exercising more shares increases AMTI, which reduces exemption, which changes the max shares. Converge to the correct answer.

### B.4 Missing Strategies (SHOULD ADD — Phase 8)

**Charitable Stock Donation:**
- Donating appreciated stock held > 1 year avoids capital gains AND provides a fair-market-value deduction.
- Tax savings = `fmv * marginal_rate + avoided_gain * ltcg_rate`.
- Only recommend for lots with significant unrealized LTCG.
- Add as `_analyze_charitable_donation()`.

**Specific Lot Identification:**
- When selling partial positions, choosing specific lots (vs. FIFO) can optimize the tax outcome.
- Sell high-basis lots first to minimize gain (or maximize loss).
- Compare FIFO vs. highest-basis vs. tax-optimal lot selection.
- Add as `_analyze_lot_selection()`.

**Roth Conversion Coordination:**
- In years where income is lower (e.g., between jobs), converting traditional IRA to Roth at a lower bracket can be beneficial.
- ISO exercise years may push taxpayer into AMT — Roth conversion in those years "wastes" the low-bracket space.
- Add as a note/flag in the income smoothing analyzer rather than a standalone strategy.

### B.5 Multi-Year Projection (SHOULD ADD — Phase 8)
- The strategy engine should model 3-5 year scenarios for ISO exercise timing and AMT credit recovery.
- Input: projected annual income, remaining unvested grants, stock price assumptions (flat, +10%, -10%).
- Output: year-by-year table showing AMT impact, credit recovery, and optimal exercise schedule.
- Add as `_project_multi_year()` method.

### B.6 Strategy Prioritization Algorithm (SHOULD ADD — Phase 8)
- Score each recommendation: `score = dollar_savings * probability_of_success / complexity`.
- Sort by score descending.
- Group into tiers: Tier 1 (score > threshold_high), Tier 2, Tier 3.
- Present Tier 1 recommendations first with clear dollar amounts.

### B.7 California-Specific Strategy Notes
Every strategy must include a "California Impact" section noting:
- CA taxes all capital gains as ordinary income (no LTCG preference).
- CA Mental Health Services Tax: additional 1% on income > $1M.
- CA AMT uses different exemption amounts and rates.
- CA does not conform to all federal wash sale rules for certain securities.

---

## Log

### [CPA] 2026-02-10T23:45
- Comprehensive 10-phase build plan created.
- All phases include tax analysis, IRS citations, exact formulas, test cases.
- Accountant verification checklist included for lot tracking and reconciliation.
- Tax Planner verification checklist included for strategy engine.
- Phase dependencies documented with parallelization opportunities.
- Awaiting Accountant and Tax Planner sign-off.

### [ACCOUNTANT] 2026-02-10T23:50
- Verified all 10 phases. Plan is 75% accounting-complete.
- Filed 4 MUST FIX items (lot state management, multi-lot sales, capital loss limitation, double-entry validation).
- Filed 4 SHOULD FIX recommendations (fractional shares, wash sale replacement ID, ISO AMT reversal, worthless securities).
- CONDITIONAL APPROVAL: All must-fix items incorporated into Addendum A.

### [TAX PLANNER] 2026-02-10T23:50
- Verified Phase 8 and cross-cutting strategy concerns.
- Filed 3 MUST FIX items (wash sale 61-day window, ESPP CA impact, ISO phase-out formula).
- Filed 4 SHOULD ADD recommendations (charitable donation, specific lot ID, Roth conversion, multi-year projection).
- Filed strategy prioritization and California-specific requirements.
- CONDITIONAL APPROVAL: All findings incorporated into Addendum B.

### [CPA] 2026-02-11T00:00
- Incorporated all Accountant findings into Addendum A.
- Incorporated all Tax Planner findings into Addendum B.
- Addendum items are cross-referenced to the original phases they affect.
- Plan status updated: APPROVED WITH ADDENDA.
- Ready for Python Engineer implementation.
