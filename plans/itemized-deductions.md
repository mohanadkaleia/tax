# Itemized Deductions (Schedule A) — CPA Tax Plan

**Session ID:** tax-2026-02-14-itemized-deductions-001
**Date:** 2026-02-14
**Status:** Planning
**Tax Year:** 2024 (with 2025 forward compatibility)

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation and reconciliation sign-off)
- Tax Planner (strategy implications — itemized vs. standard optimization)

**Scope:**
- Add structured itemized deduction support to the tax estimator, replacing the current opaque `itemized_deductions: Decimal | None` pass-through with a detailed Schedule A data model.
- The estimator will compute federal and California itemized deductions separately, apply all applicable limitations (SALT cap, medical floor, charitable AGI limits), compare against the standard deduction, and use the higher amount for each jurisdiction independently.
- The CLI will accept itemized deduction components as individual options or via a JSON file.
- Definition of "done": A user can run `taxbot estimate 2024 --deductions-file inputs/deductions_2024.json` (or use individual CLI flags) and see a fully itemized Schedule A breakdown, with correct SALT capping, medical floor computation, and charitable limit enforcement for both federal and California.

---

## Tax Analysis

### IRS Schedule A Categories (Form 1040, Schedule A — 2024)

IRS Schedule A organizes itemized deductions into the following categories. All references are to the 2024 form unless noted.

#### Category 1: Medical and Dental Expenses (Schedule A, Lines 1-4)

**Authority:** IRC Section 213; IRS Publication 502

- **Line 1:** Total medical and dental expenses paid during the tax year.
- **Line 2:** Enter AGI (from Form 1040, Line 11).
- **Line 3:** Multiply Line 2 by 7.5% (0.075).
- **Line 4:** Subtract Line 3 from Line 1. If zero or less, enter 0.

**Computation:**
```
medical_deduction = max(medical_expenses - (agi * 0.075), 0)
```

**Relevance to this taxpayer:** For a high-income W-2 employee, the 7.5% AGI floor is extremely high. On $400K+ AGI, the floor exceeds $30,000. Medical expenses would need to be extraordinary (major surgery, chronic illness costs) to exceed this floor. This is low priority for MVP but must be structurally supported.

**What qualifies (Pub. 502):**
- Insurance premiums NOT paid pre-tax through employer
- Out-of-pocket medical costs (copays, prescriptions, surgery)
- Dental and vision expenses
- Long-term care insurance premiums (age-limited)
- Does NOT include: amounts reimbursed by insurance, HSA/FSA-paid expenses, or employer-paid premiums (already excluded from W-2 wages)

#### Category 2: Taxes You Paid — SALT (Schedule A, Lines 5-7)

**Authority:** IRC Section 164; IRC Section 164(b)(6) (SALT cap); IRS Publication 17, Chapter 22

- **Line 5a:** State and local income taxes OR general sales taxes (taxpayer chooses higher).
  - For a CA W-2 employee, state income tax will always dominate.
  - The amount is the ACTUAL state income tax paid/withheld during the year (W-2 Box 17 + estimated payments + prior-year balance paid), NOT the liability computed on the CA return.
- **Line 5b:** State and local personal property taxes.
- **Line 5c:** State and local real estate taxes.
- **Line 5d:** Sum of Lines 5a through 5c.
- **Line 5e:** Other taxes (foreign taxes if not taken as credit, etc.).
- **Line 5f:** Total of 5d + 5e.
- **Line 6:** Reserved for future use.
- **Line 7:** SALT deduction = **min(Line 5f, $10,000)** for Single or MFJ.

**The SALT Cap (IRC Section 164(b)(6)):**
- Enacted by Tax Cuts and Jobs Act (TCJA), effective 2018-2025.
- $10,000 cap for Single, MFJ, HOH.
- $5,000 cap for MFS.
- Applies to the SUM of: state/local income taxes (or sales taxes) + real estate taxes + personal property taxes.
- For 2024: the cap is still in effect.
- For 2025: TCJA provisions scheduled to expire 12/31/2025 unless extended. The engineer should make the cap configurable by year so it can be removed or increased if legislation changes.

**SALT cap values by filing status (2024):**
```
SINGLE:  $10,000
MFJ:     $10,000
MFS:     $ 5,000
HOH:     $10,000
```

**Example from CPA-filed 2024 return:**
- CA state income tax withheld (W-2 Box 17): $50,039
- Property taxes: $0 (no real estate)
- The TOTAL uncapped SALT is $50,039.
- The DEDUCTIBLE SALT is capped at $10,000.
- The EXCESS of $40,039 is lost (not deductible, not carried forward).

**Computation:**
```
uncapped_salt = state_income_tax_paid + real_estate_taxes + personal_property_taxes
salt_cap = SALT_CAP[filing_status]  # $10,000 for SINGLE
salt_deduction = min(uncapped_salt, salt_cap)
```

#### Category 3: Interest You Paid (Schedule A, Lines 8-10)

**Authority:** IRC Section 163(h); IRS Publication 936

- **Line 8a:** Home mortgage interest from Form 1098.
- **Line 8b:** Home mortgage interest not reported on Form 1098.
- **Line 8c:** Points not reported on Form 1098.
- **Line 9:** Investment interest (Form 4952).
- **Line 10:** Total interest deduction = sum of Lines 8a + 8b + 8c + 9.

**Mortgage interest limits (post-TCJA, 2018-2025):**
- Deductible on acquisition debt up to $750,000 ($375,000 MFS).
- Pre-12/15/2017 debt grandfathered at $1,000,000 limit.
- Home equity loan interest NOT deductible unless proceeds used for home improvement.
- Investment interest limited to net investment income (Form 4952).

**Relevance to this taxpayer:** CPA-filed 2024 return shows $0 mortgage interest. This field must be supported but is currently zero. Investment interest is unlikely for a W-2 employee without margin accounts.

**Computation:**
```
mortgage_interest_deduction = min(mortgage_interest_paid, limit_based_on_debt)
# For MVP, accept the user-provided amount and trust it is within limits.
# Debt-limit validation is a future enhancement.
```

#### Category 4: Gifts to Charity (Schedule A, Lines 11-14)

**Authority:** IRC Section 170; IRS Publication 526

- **Line 11:** Gifts by cash or check.
- **Line 12:** Other than cash or check (Form 8283 required if >$500).
- **Line 13:** Carryover from prior year.
- **Line 14:** Total charitable deduction = Line 11 + 12 + 13 (subject to AGI limits).

**AGI Limits on Charitable Contributions (IRC Section 170(b)):**

| Contribution Type | AGI Limit | Reference |
|---|---|---|
| Cash to public charities (50%/60% organizations) | 60% of AGI | IRC 170(b)(1)(G) |
| Cash to private foundations | 30% of AGI | IRC 170(b)(1)(B) |
| Appreciated property to public charities (FMV election) | 30% of AGI | IRC 170(b)(1)(C) |
| Appreciated property to private foundations | 20% of AGI | IRC 170(b)(1)(D) |

**Excess carries forward 5 years** (IRC Section 170(d)(1)).

**Example from CPA-filed 2024 return:**
- Cash charitable contributions: $11,118
- AGI: approximately $430,000 (estimated)
- 60% AGI limit: approximately $258,000
- The $11,118 is well within the 60% limit. No limitation applies.

**Computation (MVP — cash to public charities only):**
```
charitable_cash_limit = agi * Decimal("0.60")
charitable_property_limit = agi * Decimal("0.30")
charitable_deduction = min(charitable_cash + charitable_property, charitable_cash_limit)
# Full multi-tier limit logic is a Phase 2 enhancement
```

**Simplified MVP computation:**
```
charitable_deduction = min(charitable_contributions, agi * Decimal("0.60"))
```

#### Category 5: Casualty and Theft Losses (Schedule A, Line 15)

**Authority:** IRC Section 165(h); IRS Publication 547

- Only deductible for losses attributable to a federally declared disaster (post-TCJA).
- Subject to $100 per-event floor AND 10% AGI floor.
- Extremely rare for this taxpayer profile.

**MVP decision:** Accept a single `casualty_loss` Decimal field. No computation engine needed. The user enters the net deductible amount already computed. Add a warning if nonzero that the user should verify it meets the federally-declared-disaster requirement.

#### Category 6: Other Itemized Deductions (Schedule A, Line 16)

**Authority:** IRC Section 67 (suspended through 2025 by TCJA)

- Miscellaneous itemized deductions subject to the 2% AGI floor are **SUSPENDED** through 2025 by TCJA.
- What remains on Line 16: gambling losses (to extent of winnings), impairment-related work expenses, federal estate tax on income in respect of a decedent, and a few others.
- Not relevant for this taxpayer.

**MVP decision:** Accept a single `other_deductions` Decimal field as a pass-through. No computation logic needed.

---

## Section 1: Federal Itemized Deduction Computation

### 1.1 Total Federal Itemized Deduction

```
federal_itemized = (
    medical_deduction           # max(medical - 0.075*AGI, 0)
    + salt_deduction            # min(uncapped_salt, SALT_CAP)
    + mortgage_interest         # user-provided (debt limit check future)
    + charitable_deduction      # min(charitable, AGI limit)
    + casualty_loss             # user-provided
    + other_deductions          # user-provided
)
```

### 1.2 Standard vs. Itemized Comparison

```
federal_deduction_used = max(federal_itemized, federal_standard_deduction)
```

The estimator already does this comparison on line 82 of `estimator.py`:
```python
deduction_used = max(itemized_deductions or Decimal("0"), std_ded)
```

This must be changed to compute `federal_itemized` from the components rather than accepting a pre-computed total.

### 1.3 Pease Limitation

The Pease limitation (reducing itemized deductions for high-income taxpayers) was **eliminated by TCJA** for 2018-2025. No phase-out logic needed for 2024. If TCJA expires, Pease may return for 2026+. The engineer should add a placeholder/flag for this.

---

## Section 2: California Itemized Deduction Computation

### 2.1 California Does NOT Conform to SALT Cap

**Authority:** CA Revenue and Taxation Code (R&TC); FTB Publication 1001; Schedule CA (540), Part II

California **does not conform** to the federal $10,000 SALT cap. On the California return:

- The taxpayer deducts the FULL amount of state income taxes, real estate taxes, and personal property taxes with NO cap.
- However, CA state income tax paid is NOT deductible on the CA return (you cannot deduct CA tax from CA taxable income). California only allows deduction of taxes paid to OTHER states.
- For a California-only resident, this means:
  - Real estate taxes: fully deductible on CA (no SALT cap)
  - Personal property taxes: fully deductible on CA (no SALT cap)
  - State income tax paid to CA: NOT deductible on CA return (R&TC Section 17220)
  - State income tax paid to OTHER states: deductible on CA return

**This is a critical difference from federal.** The federal SALT deduction may include $10,000 of CA income tax. The CA Schedule CA SALT deduction will typically be ONLY real estate + personal property taxes (no state income tax to CA).

### 2.2 California Charitable Deduction

CA generally conforms to federal charitable deduction rules (R&TC Section 17201). The same AGI limits apply. However, California has some specific non-conformity items:
- CA does not allow the deduction for contributions to a college athletic event seating rights program (repealed federally as well by TCJA).
- For MVP, treat CA charitable = federal charitable.

### 2.3 California Medical Deduction

CA conforms to the 7.5% AGI floor for 2024 (R&TC Section 17201). Same computation as federal.

### 2.4 California Mortgage Interest

CA generally conforms but with a key difference:
- CA uses the $1,000,000 mortgage debt limit (pre-TCJA amount) regardless of when the mortgage was originated.
- Federal uses $750,000 for post-12/15/2017 mortgages.
- For MVP (no mortgage for this taxpayer), this is informational only.

### 2.5 Total California Itemized Deduction

```
ca_salt = real_estate_taxes + personal_property_taxes + other_state_taxes_paid
# NOTE: CA income tax paid to CA is NOT included
# NOTE: No SALT cap applied

ca_itemized = (
    medical_deduction           # same as federal (7.5% AGI floor)
    + ca_salt                   # different from federal — no cap, no CA income tax
    + mortgage_interest         # same for MVP (CA uses $1M limit vs federal $750K)
    + charitable_deduction      # same as federal for MVP
    + casualty_loss             # same as federal
    + other_deductions          # same as federal
)

ca_deduction_used = max(ca_itemized, ca_standard_deduction)
```

### 2.6 Schedule CA (540) Adjustments

The California return uses Schedule CA to reconcile federal and CA differences. The itemized deduction section (Part II, Section B) requires:

- **Column A:** Federal amount (from Schedule A)
- **Column B:** California subtractions (amounts to SUBTRACT from federal)
- **Column C:** California additions (amounts to ADD to federal)

For the SALT line:
- Column A: $10,000 (federal capped SALT)
- Column B: $10,000 (subtract the entire federal SALT — CA does not allow CA income tax deduction)
- Column C: real_estate_taxes + personal_property_taxes (add back the CA-allowed taxes)

The TaxEstimate model should store both federal and CA itemized deduction totals separately.

---

## Section 3: Data Model Changes

### 3.1 New Pydantic Model: `ItemizedDeductions`

**File:** `app/models/deductions.py` (new file)

```python
"""Itemized deduction data models (Schedule A)."""

from decimal import Decimal
from pydantic import BaseModel, Field


class ItemizedDeductions(BaseModel):
    """Input data for Schedule A itemized deductions.

    All amounts are annual totals for the tax year.
    The estimator will apply floors, caps, and AGI limits.
    """

    # --- Medical and Dental (Schedule A, Lines 1-4) ---
    medical_expenses: Decimal = Field(
        default=Decimal("0"),
        description="Total unreimbursed medical/dental expenses (Pub. 502)",
    )

    # --- Taxes Paid (Schedule A, Lines 5-7) ---
    state_income_tax_paid: Decimal = Field(
        default=Decimal("0"),
        description=(
            "State/local income tax actually paid during the year "
            "(W-2 Box 17 + estimated payments + prior-year balance paid). "
            "This is the CASH-BASIS amount paid, not the liability."
        ),
    )
    real_estate_taxes: Decimal = Field(
        default=Decimal("0"),
        description="State/local real estate (property) taxes paid",
    )
    personal_property_taxes: Decimal = Field(
        default=Decimal("0"),
        description="State/local personal property taxes paid (e.g. vehicle registration ad valorem)",
    )

    # --- Interest Paid (Schedule A, Lines 8-10) ---
    mortgage_interest: Decimal = Field(
        default=Decimal("0"),
        description="Home mortgage interest (Form 1098, Box 1)",
    )
    mortgage_points: Decimal = Field(
        default=Decimal("0"),
        description="Points paid on home mortgage (Form 1098, Box 6)",
    )
    investment_interest: Decimal = Field(
        default=Decimal("0"),
        description="Investment interest expense (Form 4952, limited to net investment income)",
    )

    # --- Charitable Contributions (Schedule A, Lines 11-14) ---
    charitable_cash: Decimal = Field(
        default=Decimal("0"),
        description="Cash/check contributions to qualifying organizations (Pub. 526)",
    )
    charitable_noncash: Decimal = Field(
        default=Decimal("0"),
        description="Non-cash contributions (FMV of donated property; Form 8283 if > $500)",
    )
    charitable_carryover: Decimal = Field(
        default=Decimal("0"),
        description="Charitable contribution carryover from prior years",
    )

    # --- Casualty and Theft Losses (Schedule A, Line 15) ---
    casualty_loss: Decimal = Field(
        default=Decimal("0"),
        description="Net casualty/theft loss from federally declared disaster (Form 4684)",
    )

    # --- Other Itemized Deductions (Schedule A, Line 16) ---
    other_deductions: Decimal = Field(
        default=Decimal("0"),
        description="Other itemized deductions (gambling losses, etc.)",
    )
```

### 3.2 New Pydantic Model: `ItemizedDeductionResult`

```python
class ItemizedDeductionResult(BaseModel):
    """Computed itemized deduction breakdown after applying all limits."""

    # Federal Schedule A
    federal_medical_deduction: Decimal      # After 7.5% AGI floor
    federal_salt_uncapped: Decimal           # Total SALT before cap
    federal_salt_deduction: Decimal          # After SALT cap
    federal_salt_cap_applied: Decimal        # Amount lost to SALT cap
    federal_interest_deduction: Decimal      # Mortgage + investment interest
    federal_charitable_deduction: Decimal    # After AGI limits
    federal_charitable_limited: Decimal      # Amount lost to AGI limits
    federal_casualty_loss: Decimal
    federal_other_deductions: Decimal
    federal_total_itemized: Decimal          # Sum of all federal itemized
    federal_standard_deduction: Decimal      # For comparison
    federal_deduction_used: Decimal          # max(itemized, standard)
    federal_used_itemized: bool              # True if itemized > standard

    # California Schedule CA
    ca_medical_deduction: Decimal
    ca_salt_deduction: Decimal               # No cap, no CA income tax to CA
    ca_interest_deduction: Decimal
    ca_charitable_deduction: Decimal
    ca_casualty_loss: Decimal
    ca_other_deductions: Decimal
    ca_total_itemized: Decimal
    ca_standard_deduction: Decimal
    ca_deduction_used: Decimal
    ca_used_itemized: bool
```

### 3.3 Updates to `TaxEstimate` Model

**File:** `app/models/reports.py`

Add the following fields to the `TaxEstimate` class:

```python
# Replace the existing deduction fields with richer data:
itemized_detail: ItemizedDeductionResult | None = None

# Add CA-specific deduction tracking:
ca_deduction: Decimal              # The CA deduction used (may differ from federal)
ca_itemized_deductions: Decimal | None = None  # CA itemized total
```

The existing `itemized_deductions: Decimal | None` field should be kept for backward compatibility but deprecated in favor of `itemized_detail`.

### 3.4 SALT Cap Configuration

**File:** `app/engines/brackets.py`

Add a new constant:

```python
# SALT cap per IRC Section 164(b)(6) — TCJA 2018-2025
FEDERAL_SALT_CAP: dict[int, dict[FilingStatus, Decimal]] = {
    2024: {
        FilingStatus.SINGLE: Decimal("10000"),
        FilingStatus.MFJ: Decimal("10000"),
        FilingStatus.MFS: Decimal("5000"),
        FilingStatus.HOH: Decimal("10000"),
    },
    2025: {
        FilingStatus.SINGLE: Decimal("10000"),
        FilingStatus.MFJ: Decimal("10000"),
        FilingStatus.MFS: Decimal("5000"),
        FilingStatus.HOH: Decimal("10000"),
    },
    # If TCJA expires, 2026 would have no cap — omit the year or set to None
}

# Charitable contribution AGI limits
CHARITABLE_CASH_AGI_LIMIT = Decimal("0.60")         # 60% of AGI for cash to public charities
CHARITABLE_PROPERTY_AGI_LIMIT = Decimal("0.30")      # 30% of AGI for appreciated property
CHARITABLE_PRIVATE_FOUNDATION_LIMIT = Decimal("0.30") # 30% of AGI for cash to private foundations

# Medical expense AGI floor
MEDICAL_EXPENSE_AGI_FLOOR = Decimal("0.075")  # 7.5% of AGI
```

---

## Section 4: Estimator Engine Changes

### 4.1 New Method: `compute_itemized_deductions()`

**File:** `app/engines/estimator.py`

Add a new method to the `TaxEstimator` class:

```python
def compute_itemized_deductions(
    self,
    deductions: ItemizedDeductions,
    agi: Decimal,
    filing_status: FilingStatus,
    tax_year: int,
) -> ItemizedDeductionResult:
    """Compute federal and CA itemized deductions with all limitations.

    Returns a result object with both federal and CA amounts,
    applying SALT cap (federal only), medical floor, and
    charitable AGI limits.
    """
```

**Step-by-step logic:**

```
# 1. Medical deduction (same for federal and CA)
medical_floor = agi * MEDICAL_EXPENSE_AGI_FLOOR  # 7.5%
medical_deduction = max(deductions.medical_expenses - medical_floor, Decimal("0"))

# 2. Federal SALT
uncapped_salt = (
    deductions.state_income_tax_paid
    + deductions.real_estate_taxes
    + deductions.personal_property_taxes
)
salt_cap = FEDERAL_SALT_CAP[tax_year][filing_status]  # $10,000 for SINGLE 2024
federal_salt = min(uncapped_salt, salt_cap)
salt_cap_lost = uncapped_salt - federal_salt

# 3. California SALT (NO cap, NO CA income tax to CA)
ca_salt = deductions.real_estate_taxes + deductions.personal_property_taxes
# Note: state_income_tax_paid is excluded because it is CA tax paid to CA.
# If the taxpayer paid taxes to OTHER states, those would be a separate field (future).

# 4. Interest
federal_interest = (
    deductions.mortgage_interest
    + deductions.mortgage_points
    + deductions.investment_interest
)
ca_interest = federal_interest  # Same for MVP (CA $1M limit vs federal $750K ignored for now)

# 5. Charitable
total_charitable = (
    deductions.charitable_cash
    + deductions.charitable_noncash
    + deductions.charitable_carryover
)
# Simplified: apply 60% AGI limit to total (conservative; mixed donations need tiered limits)
charitable_limit = agi * CHARITABLE_CASH_AGI_LIMIT  # 60%
federal_charitable = min(total_charitable, charitable_limit)
charitable_excess = total_charitable - federal_charitable
ca_charitable = federal_charitable  # CA conforms

# 6. Casualty + Other
federal_casualty = deductions.casualty_loss
federal_other = deductions.other_deductions

# 7. Totals
federal_itemized = (
    medical_deduction + federal_salt + federal_interest
    + federal_charitable + federal_casualty + federal_other
)
ca_itemized = (
    medical_deduction + ca_salt + ca_interest
    + ca_charitable + federal_casualty + federal_other
)

# 8. Standard deduction comparison
federal_std = FEDERAL_STANDARD_DEDUCTION[tax_year][filing_status]
ca_std = CALIFORNIA_STANDARD_DEDUCTION[tax_year][filing_status]

federal_used = max(federal_itemized, federal_std)
ca_used = max(ca_itemized, ca_std)

# 9. Warnings
if salt_cap_lost > Decimal("0"):
    self.warnings.append(
        f"SALT cap: ${uncapped_salt:,.2f} in state/local taxes exceeds the "
        f"${salt_cap:,.2f} federal limit. ${salt_cap_lost:,.2f} is not deductible."
    )
if charitable_excess > Decimal("0"):
    self.warnings.append(
        f"Charitable contributions of ${total_charitable:,.2f} exceed the "
        f"60% AGI limit of ${charitable_limit:,.2f}. "
        f"${charitable_excess:,.2f} carries forward 5 years."
    )
```

### 4.2 Modify `estimate()` Method Signature

The `estimate()` method currently accepts `itemized_deductions: Decimal | None`. Change to:

```python
def estimate(
    self,
    ...,
    itemized_deductions: Decimal | None = None,          # KEEP for backward compat
    itemized_detail: ItemizedDeductions | None = None,    # NEW structured input
    ...
) -> TaxEstimate:
```

**Logic change in `estimate()` (lines 79-83 of current code):**

```python
# --- Federal deductions ---
std_ded = FEDERAL_STANDARD_DEDUCTION[tax_year][filing_status]

if itemized_detail is not None:
    # Structured itemized deductions — compute with limits
    deduction_result = self.compute_itemized_deductions(
        itemized_detail, agi, filing_status, tax_year
    )
    deduction_used = deduction_result.federal_deduction_used
    ca_deduction = deduction_result.ca_deduction_used
elif itemized_deductions is not None:
    # Legacy pass-through — use as-is (backward compat)
    deduction_used = max(itemized_deductions, std_ded)
    ca_deduction = deduction_used  # Legacy: same for CA (inaccurate but backward compat)
    deduction_result = None
else:
    # Standard deduction only
    deduction_used = std_ded
    ca_deduction = CALIFORNIA_STANDARD_DEDUCTION[tax_year][filing_status]
    deduction_result = None

taxable_income = max(agi - deduction_used - section_199a_deduction, Decimal("0"))
```

**CA section (lines 133-137 of current code) changes:**

```python
# --- California ---
ca_std_ded = CALIFORNIA_STANDARD_DEDUCTION[tax_year][filing_status]
if deduction_result is not None:
    ca_ded = deduction_result.ca_deduction_used
elif itemized_deductions is not None:
    ca_ded = max(itemized_deductions, ca_std_ded)
else:
    ca_ded = ca_std_ded
ca_taxable = max(agi - ca_ded - ca_treasury_exemption, Decimal("0"))
```

### 4.3 Modify `estimate_from_db()` Method

Update `estimate_from_db()` to accept the new structured input:

```python
def estimate_from_db(
    self,
    repo: "TaxRepository",
    tax_year: int,
    filing_status: FilingStatus,
    ...,
    itemized_deductions: Decimal | None = None,
    itemized_detail: ItemizedDeductions | None = None,  # NEW
) -> TaxEstimate:
```

Pass `itemized_detail` through to `self.estimate()`.

**Future enhancement:** If the `state_income_tax_paid` field in `ItemizedDeductions` is not provided, the estimator could auto-populate it from W-2 Box 17 + state estimated payments. This requires the estimator to sum state withholdings BEFORE calling `compute_itemized_deductions()` and optionally inject it. This is Phase 2.

---

## Section 5: CLI Changes

### 5.1 Option A: JSON File Input (Recommended for Full Data)

Add a new CLI option to the `estimate` command:

```python
@app.command()
def estimate(
    ...
    itemized: float | None = typer.Option(
        None,
        "--itemized",
        help="[LEGACY] Total itemized deductions as a single number",
    ),
    deductions_file: Path | None = typer.Option(
        None,
        "--deductions-file",
        help="JSON file with itemized deduction details (Schedule A)",
    ),
    salt: float | None = typer.Option(
        None,
        "--salt",
        help="State/local income tax paid (for quick SALT + standard input)",
    ),
    charitable: float | None = typer.Option(
        None,
        "--charitable",
        help="Charitable contributions (cash)",
    ),
    mortgage: float | None = typer.Option(
        None,
        "--mortgage-interest",
        help="Mortgage interest paid",
    ),
    medical: float | None = typer.Option(
        None,
        "--medical",
        help="Unreimbursed medical/dental expenses",
    ),
    property_tax: float | None = typer.Option(
        None,
        "--property-tax",
        help="Real estate property taxes paid",
    ),
) -> None:
```

**Logic:**

```python
# Build ItemizedDeductions from CLI inputs
if deductions_file is not None:
    # Load full Schedule A data from JSON
    data = json.loads(deductions_file.read_text())
    itemized_detail = ItemizedDeductions(**data)
elif any(x is not None for x in [salt, charitable, mortgage, medical, property_tax]):
    # Build from individual CLI flags
    itemized_detail = ItemizedDeductions(
        state_income_tax_paid=Decimal(str(salt or 0)),
        charitable_cash=Decimal(str(charitable or 0)),
        mortgage_interest=Decimal(str(mortgage or 0)),
        medical_expenses=Decimal(str(medical or 0)),
        real_estate_taxes=Decimal(str(property_tax or 0)),
    )
else:
    itemized_detail = None
```

### 5.2 JSON File Format

Create a sample file `inputs/deductions_sample.json`:

```json
{
    "medical_expenses": "0",
    "state_income_tax_paid": "50039",
    "real_estate_taxes": "0",
    "personal_property_taxes": "0",
    "mortgage_interest": "0",
    "mortgage_points": "0",
    "investment_interest": "0",
    "charitable_cash": "11118",
    "charitable_noncash": "0",
    "charitable_carryover": "0",
    "casualty_loss": "0",
    "other_deductions": "0"
}
```

### 5.3 Enhanced CLI Output

The estimate command output should show the itemized deduction breakdown when structured deductions are provided:

```
DEDUCTIONS
  Medical Expenses:        $         0.00  (7.5% AGI floor: $32,250)
  SALT (uncapped):         $    50,039.00
  SALT (capped at $10K):   $    10,000.00  *** $40,039 lost to SALT cap
  Mortgage Interest:       $         0.00
  Charitable:              $    11,118.00
  Casualty/Other:          $         0.00
  ──────────────────────────────────────
  Federal Itemized:        $    21,118.00
  Federal Standard:        $    14,600.00
  >>> Using ITEMIZED:      $    21,118.00  (saves $6,518 vs. standard)

  CA Itemized:             $    11,118.00  (no SALT — CA tax not deductible on CA)
  CA Standard:             $     5,540.00
  >>> CA Using ITEMIZED:   $    11,118.00  (saves $5,578 vs. standard)
```

---

## Section 6: AMT Interaction

**Important:** When the taxpayer itemizes, state/local tax deduction is an AMT preference item.

Per Form 6251 (Line 2a): If taxpayer claims itemized deductions, ADD BACK the state/local tax deduction (Schedule A, Line 7) to compute AMTI.

The current AMT computation in `compute_amt()` (line 460) uses:
```python
amti = taxable_income + amt_preference
```

When itemizing, the SALT deduction must be added back:
```python
if deduction_result and deduction_result.federal_used_itemized:
    salt_addback = deduction_result.federal_salt_deduction
    amti = taxable_income + amt_preference + salt_addback
```

**However**, for a high-income W-2 employee with the SALT cap, this addback is only $10,000 (the capped amount, not the uncapped amount). The AMT addback is limited to the amount actually deducted.

**The engineer must verify:** Does the current taxpayer trigger AMT? With ISO exercises plus SALT addback, AMT is possible. The AMT computation should accept an optional `salt_addback` parameter.

**Decision for MVP:** Defer the SALT AMT addback to Phase 2 unless the engineer determines it is straightforward to include. The impact is small ($10,000 addback * 26% AMT rate = $2,600 max AMT increase) and may not push the taxpayer into AMT territory. Document this limitation as a warning.

---

## Section 7: Implementation Phases

### Phase 1 — MVP (Implement First)

Priority: get the 2024 return to match the CPA-filed result.

| Task | File(s) | Description |
|---|---|---|
| 1. Create `ItemizedDeductions` model | `app/models/deductions.py` | Pydantic model with all Schedule A fields |
| 2. Create `ItemizedDeductionResult` model | `app/models/deductions.py` | Computed result with fed + CA breakdowns |
| 3. Add SALT cap constants | `app/engines/brackets.py` | `FEDERAL_SALT_CAP`, charitable limits, medical floor |
| 4. Implement `compute_itemized_deductions()` | `app/engines/estimator.py` | Core computation with SALT cap, medical floor, charitable 60% limit |
| 5. Modify `estimate()` to accept structured input | `app/engines/estimator.py` | Dual-path: structured `ItemizedDeductions` vs. legacy `Decimal` |
| 6. Separate federal and CA deduction paths | `app/engines/estimator.py` | CA uses different SALT rules (no cap, no CA income tax) |
| 7. Update `TaxEstimate` model | `app/models/reports.py` | Add `itemized_detail` field, CA-specific deduction fields |
| 8. Add CLI flags | `app/cli.py` | `--deductions-file`, `--salt`, `--charitable`, `--mortgage-interest`, `--medical`, `--property-tax` |
| 9. Enhanced CLI output | `app/cli.py` | Show itemized breakdown when structured deductions provided |
| 10. Tests | `tests/test_itemized_deductions.py` | Unit tests for all computation paths |
| 11. Create sample deductions JSON | `inputs/deductions_sample.json` | Reference file for users |

**Acceptance criteria for Phase 1:**
- `taxbot estimate 2024 --salt 50039 --charitable 11118` produces:
  - Federal itemized: $21,118 ($10,000 SALT + $11,118 charitable)
  - CA itemized: $11,118 ($0 SALT on CA return + $11,118 charitable)
  - Federal uses itemized ($21,118 > $14,600 standard)
  - CA uses itemized ($11,118 > $5,540 standard)
- Backward compatibility: `--itemized 21118` still works (legacy path)
- All existing tests pass unchanged.

### Phase 2 — Enhancements (Can Wait)

| Task | Description |
|---|---|
| AMT SALT addback | Add state/local tax addback to AMT computation when itemizing |
| Tiered charitable limits | Separate 60%/30%/20% AGI limits for cash vs. property vs. private foundations |
| Charitable carryforward tracking | Store excess charitable in DB for 5-year carryforward |
| Auto-populate SALT from W-2 | Use W-2 Box 17 + estimated payments to pre-fill state_income_tax_paid |
| Mortgage debt limit validation | Warn if mortgage balance exceeds $750K (federal) or $1M (CA) |
| Investment interest limitation | Implement Form 4952 logic (limit to net investment income) |
| Pease limitation placeholder | Add flag for potential 2026+ phase-out of itemized deductions |
| Schedule CA generation | Produce the actual Schedule CA Part II adjustments for CA filing |
| Medical expense substantiation | Warn about documentation requirements for large medical deductions |
| Non-cash charitable valuation | Prompt for FMV method and Form 8283 requirements |

### Phase 3 — Future Tax Years

| Task | Description |
|---|---|
| TCJA expiration monitoring | If TCJA expires after 2025, SALT cap removed, Pease returns, misc deductions return |
| 2026 bracket/limit updates | Add new SALT cap values (or removal) and updated standard deductions |
| State-specific non-conformity | Track which CA adjustments change year-over-year |

---

## Section 8: Test Plan

### Unit Tests: `tests/test_itemized_deductions.py`

| Test | Description | Expected |
|---|---|---|
| `test_salt_cap_single` | $50,039 state tax, SINGLE | SALT deduction = $10,000 |
| `test_salt_cap_mfs` | $50,039 state tax, MFS | SALT deduction = $5,000 |
| `test_salt_no_cap_needed` | $8,000 state tax | SALT deduction = $8,000 (under cap) |
| `test_salt_with_property_tax` | $50,039 income + $5,000 property tax | SALT = $10,000 (cap applies to sum) |
| `test_medical_floor` | $30K medical, $400K AGI | Deduction = $30K - $30K = $0 |
| `test_medical_above_floor` | $40K medical, $400K AGI | Deduction = $40K - $30K = $10K |
| `test_charitable_within_limit` | $11,118 cash, $430K AGI | Full $11,118 deductible |
| `test_charitable_exceeds_limit` | $300K cash, $430K AGI | Deduction = $258K (60% AGI) |
| `test_standard_vs_itemized_standard_wins` | $5K itemized total | Uses standard $14,600 |
| `test_standard_vs_itemized_itemized_wins` | $21,118 itemized | Uses itemized $21,118 |
| `test_ca_no_salt_cap` | $50K state tax | CA SALT = $0 (CA tax not deductible on CA) |
| `test_ca_property_tax_no_cap` | $15K property tax | CA SALT = $15,000 (no cap) |
| `test_ca_vs_federal_different_deductions` | Full scenario | Federal and CA use different itemized totals |
| `test_backward_compat_decimal` | Legacy `itemized_deductions=Decimal("21118")` | Works as before |
| `test_2024_cpa_return_match` | Full 2024 scenario matching CPA return | Federal itemized = $21,118, SALT capped at $10K |

### Integration Test

| Test | Description |
|---|---|
| `test_estimate_from_db_with_deductions` | Load W-2s from DB + structured deductions, verify full tax estimate |
| `test_cli_deductions_file` | Run CLI with `--deductions-file` and verify output |
| `test_cli_individual_flags` | Run CLI with `--salt 50039 --charitable 11118` |

---

## Section 9: IRS Authority Index

| Rule | Authority | Form/Line |
|---|---|---|
| Itemized deduction election | IRC Section 63(e) | Form 1040, Line 12 |
| Medical expense deduction | IRC Section 213; Pub. 502 | Schedule A, Lines 1-4 |
| 7.5% AGI floor for medical | IRC Section 213(a) | Schedule A, Line 3 |
| SALT deduction | IRC Section 164 | Schedule A, Lines 5-7 |
| SALT cap ($10K/$5K) | IRC Section 164(b)(6) | Schedule A, Line 7 |
| Home mortgage interest | IRC Section 163(h); Pub. 936 | Schedule A, Lines 8-10 |
| $750K mortgage debt limit | IRC Section 163(h)(3)(F)(i)(II) | — |
| Charitable contributions | IRC Section 170; Pub. 526 | Schedule A, Lines 11-14 |
| 60% AGI limit (cash) | IRC Section 170(b)(1)(G) | — |
| 30% AGI limit (property) | IRC Section 170(b)(1)(C) | — |
| 5-year charitable carryforward | IRC Section 170(d)(1) | — |
| Casualty losses | IRC Section 165(h); Pub. 547 | Schedule A, Line 15 |
| Pease limitation (suspended) | IRC Section 68 (suspended by TCJA) | — |
| AMT SALT addback | IRC Section 56(b)(1)(A) | Form 6251, Line 2a |
| CA no SALT cap | CA R&TC; FTB Pub. 1001 | Schedule CA (540), Part II |
| CA no deduction for CA tax | CA R&TC Section 17220 | Schedule CA (540) |
| CA mortgage limit ($1M) | CA R&TC Section 17220.5 | — |
| CA standard deduction | CA R&TC Section 17073.5 | FTB Pub. 1001 |

---

## Log

| Date | Agent | Entry |
|---|---|---|
| 2026-02-14 | CPA | Initial plan written. Covers all Schedule A categories, SALT cap, CA non-conformity, data models, estimator changes, CLI changes, and phased implementation. Ready for Python Engineer review and implementation. |
