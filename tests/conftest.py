"""Shared test fixtures for EquityTax Reconciler."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, Security
from app.models.tax_forms import W2, Form3921, Form3922


@pytest.fixture
def sample_security() -> Security:
    return Security(ticker="ACME", name="Acme Corp", cusip="000000000")


@pytest.fixture
def sample_rsu_lot(sample_security: Security) -> Lot:
    return Lot(
        id="lot-rsu-001",
        equity_type=EquityType.RSU,
        security=sample_security,
        acquisition_date=date(2024, 3, 15),
        shares=Decimal("100"),
        cost_per_share=Decimal("150.00"),
        shares_remaining=Decimal("100"),
        source_event_id="evt-rsu-001",
        broker_source=BrokerSource.SHAREWORKS,
    )


@pytest.fixture
def sample_rsu_sale(sample_security: Security) -> Sale:
    return Sale(
        id="sale-rsu-001",
        lot_id="lot-rsu-001",
        security=sample_security,
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        proceeds_per_share=Decimal("175.00"),
        broker_reported_basis=Decimal("0"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.SHAREWORKS,
    )


@pytest.fixture
def sample_iso_lot(sample_security: Security) -> Lot:
    return Lot(
        id="lot-iso-001",
        equity_type=EquityType.ISO,
        security=sample_security,
        acquisition_date=date(2024, 1, 10),
        shares=Decimal("200"),
        cost_per_share=Decimal("50.00"),
        amt_cost_per_share=Decimal("120.00"),
        shares_remaining=Decimal("200"),
        source_event_id="evt-iso-001",
        broker_source=BrokerSource.SHAREWORKS,
    )


@pytest.fixture
def sample_espp_lot(sample_security: Security) -> Lot:
    return Lot(
        id="lot-espp-001",
        equity_type=EquityType.ESPP,
        security=sample_security,
        acquisition_date=date(2024, 6, 30),
        shares=Decimal("50"),
        cost_per_share=Decimal("127.50"),
        shares_remaining=Decimal("50"),
        source_event_id="evt-espp-001",
        broker_source=BrokerSource.SHAREWORKS,
    )


@pytest.fixture
def sample_vest_event(sample_security: Security) -> EquityEvent:
    return EquityEvent(
        id="evt-rsu-001",
        event_type=TransactionType.VEST,
        equity_type=EquityType.RSU,
        security=sample_security,
        event_date=date(2024, 3, 15),
        shares=Decimal("100"),
        price_per_share=Decimal("150.00"),
        broker_source=BrokerSource.SHAREWORKS,
    )


@pytest.fixture
def sample_w2() -> W2:
    return W2(
        employer_name="Acme Corp",
        employer_ein="12-3456789",
        tax_year=2025,
        box1_wages=Decimal("250000"),
        box2_federal_withheld=Decimal("55000"),
        box12_codes={"V": Decimal("5000")},
        box14_other={"RSU": Decimal("50000")},
        box16_state_wages=Decimal("250000"),
        box17_state_withheld=Decimal("22000"),
    )


@pytest.fixture
def sample_form3921() -> Form3921:
    return Form3921(
        tax_year=2025,
        grant_date=date(2022, 1, 15),
        exercise_date=date(2025, 3, 1),
        exercise_price_per_share=Decimal("50.00"),
        fmv_on_exercise_date=Decimal("120.00"),
        shares_transferred=Decimal("200"),
        employer_name="Acme Corp",
    )


@pytest.fixture
def sample_form3922() -> Form3922:
    return Form3922(
        tax_year=2025,
        offering_date=date(2024, 1, 1),
        purchase_date=date(2024, 6, 30),
        fmv_on_offering_date=Decimal("140.00"),
        fmv_on_purchase_date=Decimal("150.00"),
        purchase_price_per_share=Decimal("127.50"),
        shares_transferred=Decimal("50"),
        employer_name="Acme Corp",
    )
