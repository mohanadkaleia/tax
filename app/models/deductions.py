"""Itemized deduction data models (Schedule A).

Per IRS Schedule A (Form 1040) categories, IRC Sections 63(e), 164, 170, 213.
California non-conformity per CA R&TC and FTB Publication 1001.
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class ItemizedDeductions(BaseModel):
    """Input data for Schedule A itemized deductions.

    All amounts are annual totals for the tax year.
    The estimator applies floors, caps, and AGI limits.
    """

    # --- Medical and Dental (Schedule A, Lines 1-4; IRC Section 213) ---
    medical_expenses: Decimal = Field(
        default=Decimal("0"),
        description="Total unreimbursed medical/dental expenses (Pub. 502)",
    )

    # --- Taxes Paid (Schedule A, Lines 5-7; IRC Section 164) ---
    state_income_tax_paid: Decimal = Field(
        default=Decimal("0"),
        description=(
            "State/local income tax paid during the year "
            "(W-2 Box 17 + estimated payments + prior-year balance paid)"
        ),
    )
    real_estate_taxes: Decimal = Field(
        default=Decimal("0"),
        description="State/local real estate (property) taxes paid",
    )
    personal_property_taxes: Decimal = Field(
        default=Decimal("0"),
        description="State/local personal property taxes (e.g. vehicle registration)",
    )

    # --- Interest Paid (Schedule A, Lines 8-10; IRC Section 163(h)) ---
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
        description="Investment interest expense (limited to net investment income)",
    )

    # --- Charitable Contributions (Schedule A, Lines 11-14; IRC Section 170) ---
    charitable_cash: Decimal = Field(
        default=Decimal("0"),
        description="Cash/check contributions to qualifying organizations (Pub. 526)",
    )
    charitable_noncash: Decimal = Field(
        default=Decimal("0"),
        description="Non-cash contributions (FMV of donated property)",
    )
    charitable_carryover: Decimal = Field(
        default=Decimal("0"),
        description="Charitable contribution carryover from prior years",
    )

    # --- Casualty and Theft Losses (Schedule A, Line 15; IRC Section 165(h)) ---
    casualty_loss: Decimal = Field(
        default=Decimal("0"),
        description="Net casualty/theft loss from federally declared disaster",
    )

    # --- Other Itemized Deductions (Schedule A, Line 16) ---
    other_deductions: Decimal = Field(
        default=Decimal("0"),
        description="Other itemized deductions (gambling losses, etc.)",
    )


class ItemizedDeductionResult(BaseModel):
    """Computed itemized deduction breakdown after applying all limits."""

    # Federal Schedule A
    federal_medical_deduction: Decimal
    federal_salt_uncapped: Decimal
    federal_salt_deduction: Decimal
    federal_salt_cap_lost: Decimal
    federal_interest_deduction: Decimal
    federal_charitable_deduction: Decimal
    federal_charitable_limited: Decimal
    federal_casualty_loss: Decimal
    federal_other_deductions: Decimal
    federal_total_itemized: Decimal
    federal_standard_deduction: Decimal
    federal_deduction_used: Decimal
    federal_used_itemized: bool

    # California Schedule CA
    ca_medical_deduction: Decimal
    ca_salt_deduction: Decimal
    ca_interest_deduction: Decimal
    ca_charitable_deduction: Decimal
    ca_casualty_loss: Decimal
    ca_other_deductions: Decimal
    ca_total_itemized: Decimal
    ca_standard_deduction: Decimal
    ca_deduction_used: Decimal
    ca_used_itemized: bool
