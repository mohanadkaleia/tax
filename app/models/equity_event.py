"""Core equity event, lot, and sale models."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    EquityType,
    Form8949Category,
    HoldingPeriod,
    TransactionType,
)


class Security(BaseModel):
    ticker: str
    name: str
    cusip: str | None = None


class Lot(BaseModel):
    id: str
    equity_type: EquityType
    security: Security
    acquisition_date: date
    shares: Decimal = Field(ge=0)
    cost_per_share: Decimal
    amt_cost_per_share: Decimal | None = None
    shares_remaining: Decimal = Field(ge=0)
    source_event_id: str
    broker_source: BrokerSource
    notes: str | None = None

    @property
    def total_cost_basis(self) -> Decimal:
        return self.shares * self.cost_per_share

    @property
    def total_amt_basis(self) -> Decimal | None:
        if self.amt_cost_per_share is None:
            return None
        return self.shares * self.amt_cost_per_share


class EquityEvent(BaseModel):
    id: str
    event_type: TransactionType
    equity_type: EquityType
    security: Security
    event_date: date
    shares: Decimal = Field(ge=0)
    price_per_share: Decimal
    strike_price: Decimal | None = None
    purchase_price: Decimal | None = None
    offering_date: date | None = None
    fmv_on_offering_date: Decimal | None = None
    grant_date: date | None = None
    ordinary_income: Decimal | None = None
    broker_source: BrokerSource
    raw_data: dict | None = None


class Sale(BaseModel):
    id: str
    lot_id: str
    security: Security
    date_acquired: date | str | None = None  # "Various" allowed; None if unknown
    sale_date: date
    shares: Decimal = Field(ge=0)
    proceeds_per_share: Decimal
    broker_reported_basis: Decimal | None = None
    broker_reported_basis_per_share: Decimal | None = None
    wash_sale_disallowed: Decimal = Decimal("0")
    form_1099b_received: bool = True
    basis_reported_to_irs: bool = True
    broker_source: BrokerSource

    @property
    def total_proceeds(self) -> Decimal:
        return self.shares * self.proceeds_per_share


class SaleResult(BaseModel):
    """Output of basis correction engine for a single sale."""

    sale_id: str
    lot_id: str | None = None
    security: Security
    acquisition_date: date
    sale_date: date
    shares: Decimal
    proceeds: Decimal
    broker_reported_basis: Decimal | None
    correct_basis: Decimal
    adjustment_amount: Decimal
    adjustment_code: AdjustmentCode
    holding_period: HoldingPeriod
    form_8949_category: Form8949Category
    gain_loss: Decimal
    ordinary_income: Decimal = Decimal("0")
    amt_adjustment: Decimal = Decimal("0")
    wash_sale_disallowed: Decimal = Decimal("0")
    notes: str | None = None
