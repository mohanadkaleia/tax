"""Report output models."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import (
    AdjustmentCode,
    DispositionType,
    FilingStatus,
    Form8949Category,
    HoldingPeriod,
)


class Form8949Line(BaseModel):
    description: str
    date_acquired: date | str  # "Various" allowed
    date_sold: date
    proceeds: Decimal
    cost_basis: Decimal
    adjustment_code: AdjustmentCode
    adjustment_amount: Decimal
    gain_loss: Decimal
    category: Form8949Category


class ReconciliationLine(BaseModel):
    sale_id: str
    security: str
    sale_date: date
    shares: Decimal
    broker_proceeds: Decimal
    broker_basis: Decimal | None
    correct_basis: Decimal
    adjustment: Decimal
    adjustment_code: AdjustmentCode
    gain_loss_broker: Decimal | None
    gain_loss_correct: Decimal
    difference: Decimal
    notes: str | None = None


class ESPPIncomeLine(BaseModel):
    security: str
    offering_date: date
    purchase_date: date
    sale_date: date
    shares: Decimal
    purchase_price: Decimal
    fmv_at_purchase: Decimal
    fmv_at_offering: Decimal
    sale_proceeds: Decimal
    disposition_type: DispositionType
    ordinary_income: Decimal
    adjusted_basis: Decimal
    capital_gain_loss: Decimal
    holding_period: HoldingPeriod


class AMTWorksheetLine(BaseModel):
    security: str
    grant_date: date
    exercise_date: date
    shares: Decimal
    strike_price: Decimal
    fmv_at_exercise: Decimal
    spread_per_share: Decimal
    total_amt_preference: Decimal
    regular_basis: Decimal
    amt_basis: Decimal


class TaxEstimate(BaseModel):
    tax_year: int
    filing_status: FilingStatus
    # Income
    w2_wages: Decimal
    interest_income: Decimal
    dividend_income: Decimal
    qualified_dividends: Decimal
    short_term_gains: Decimal
    long_term_gains: Decimal
    total_income: Decimal
    agi: Decimal
    # Deductions
    standard_deduction: Decimal
    itemized_deductions: Decimal | None = None
    deduction_used: Decimal
    taxable_income: Decimal
    # Federal
    federal_regular_tax: Decimal
    federal_ltcg_tax: Decimal
    federal_niit: Decimal
    federal_amt: Decimal
    federal_total_tax: Decimal
    federal_withheld: Decimal
    federal_estimated_payments: Decimal = Decimal("0")
    federal_balance_due: Decimal
    # California
    ca_taxable_income: Decimal
    ca_tax: Decimal
    ca_mental_health_tax: Decimal
    ca_total_tax: Decimal
    ca_withheld: Decimal
    ca_estimated_payments: Decimal = Decimal("0")
    ca_balance_due: Decimal
    # Total
    total_tax: Decimal
    total_withheld: Decimal
    total_balance_due: Decimal


class AuditEntry(BaseModel):
    timestamp: datetime
    engine: str
    operation: str
    inputs: dict
    output: dict
    notes: str | None = None
