"""Tax form data models (W-2, 1099-B, 3921, 3922, etc.)."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import BrokerSource


class W2(BaseModel):
    employer_name: str
    employer_ein: str | None = None
    tax_year: int
    box1_wages: Decimal
    box2_federal_withheld: Decimal
    box3_ss_wages: Decimal | None = None
    box4_ss_withheld: Decimal | None = None
    box5_medicare_wages: Decimal | None = None
    box6_medicare_withheld: Decimal | None = None
    box12_codes: dict[str, Decimal] = Field(default_factory=dict)
    box14_other: dict[str, Decimal] = Field(default_factory=dict)
    box16_state_wages: Decimal | None = None
    box17_state_withheld: Decimal | None = None
    state: str = "CA"


class Form1099B(BaseModel):
    broker_name: str
    tax_year: int
    description: str
    date_acquired: date | None = None
    date_sold: date
    proceeds: Decimal
    cost_basis: Decimal | None = None
    wash_sale_loss_disallowed: Decimal | None = None
    basis_reported_to_irs: bool
    box_type: str | None = None
    broker_source: BrokerSource
    raw_data: dict | None = None


class Form3921(BaseModel):
    """ISO exercise record."""

    tax_year: int
    grant_date: date
    exercise_date: date
    exercise_price_per_share: Decimal  # Box 3
    fmv_on_exercise_date: Decimal  # Box 4
    shares_transferred: Decimal  # Box 5
    employer_name: str | None = None

    @property
    def spread_per_share(self) -> Decimal:
        return self.fmv_on_exercise_date - self.exercise_price_per_share

    @property
    def total_amt_preference(self) -> Decimal:
        return self.spread_per_share * self.shares_transferred


class Form3922(BaseModel):
    """ESPP transfer record."""

    tax_year: int
    offering_date: date  # Box 1
    purchase_date: date  # Box 2
    fmv_on_offering_date: Decimal  # Box 3
    fmv_on_purchase_date: Decimal  # Box 4
    purchase_price_per_share: Decimal  # Box 5
    shares_transferred: Decimal  # Box 6
    employer_name: str | None = None

    @property
    def discount_per_share(self) -> Decimal:
        return self.fmv_on_purchase_date - self.purchase_price_per_share


class Form1099DIV(BaseModel):
    broker_name: str
    tax_year: int
    ordinary_dividends: Decimal  # Box 1a
    qualified_dividends: Decimal  # Box 1b
    total_capital_gain_distributions: Decimal = Decimal("0")  # Box 2a
    nondividend_distributions: Decimal = Decimal("0")  # Box 3
    section_199a_dividends: Decimal = Decimal("0")  # Box 5
    foreign_tax_paid: Decimal = Decimal("0")  # Box 6 (Box 7 on pre-2020 revisions)
    foreign_country: str | None = None  # Box 7 (Box 8 on pre-2020 revisions)
    federal_tax_withheld: Decimal = Decimal("0")
    state_tax_withheld: Decimal = Decimal("0")


class Form1099INT(BaseModel):
    payer_name: str
    tax_year: int
    interest_income: Decimal  # Box 1
    early_withdrawal_penalty: Decimal = Decimal("0")  # Box 2
    us_savings_bond_interest: Decimal = Decimal("0")  # Box 3
    federal_tax_withheld: Decimal = Decimal("0")
    state_tax_withheld: Decimal = Decimal("0")
