"""Tests for ESPP cost-basis correction in BasisCorrectionEngine."""

from datetime import date
from decimal import Decimal

import pytest

from app.engines.basis import BasisCorrectionEngine
from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    EquityType,
    HoldingPeriod,
)
from app.models.equity_event import Lot, Sale, Security
from app.models.tax_forms import Form3922


@pytest.fixture
def engine():
    return BasisCorrectionEngine()


@pytest.fixture
def security():
    return Security(ticker="ACME", name="Acme Corp")


@pytest.fixture
def espp_form3922():
    """ESPP purchase: offering $140, purchase price $127.50, FMV at purchase $150."""
    return Form3922(
        tax_year=2024,
        offering_date=date(2024, 1, 1),
        purchase_date=date(2024, 6, 30),
        fmv_on_offering_date=Decimal("140.00"),
        fmv_on_purchase_date=Decimal("150.00"),
        purchase_price_per_share=Decimal("127.50"),
        shares_transferred=Decimal("50"),
        employer_name="Acme Corp",
    )


@pytest.fixture
def espp_lot(security):
    return Lot(
        id="lot-espp-001",
        equity_type=EquityType.ESPP,
        security=security,
        acquisition_date=date(2024, 6, 30),
        shares=Decimal("50"),
        cost_per_share=Decimal("127.50"),
        shares_remaining=Decimal("50"),
        source_event_id="evt-espp-001",
        broker_source=BrokerSource.SHAREWORKS,
    )


class TestESPPDisqualifyingDisposition:
    """Disqualifying: sold < 2yr from offering or < 1yr from purchase."""

    def test_disqualifying_basis_correction(self, engine, espp_lot, espp_form3922, security):
        """Sold 3 months after purchase — disqualifying.

        Ordinary income = spread at purchase = ($150 - $127.50) × 50 = $1,125.
        Correct basis = $127.50 × 50 + $1,125 = $7,500.
        """
        sale = Sale(
            id="sale-001",
            lot_id="lot-espp-001",
            security=security,
            sale_date=date(2024, 10, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("160.00"),
            broker_reported_basis=Decimal("6375.00"),  # $127.50 × 50
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_espp_basis(espp_lot, sale, espp_form3922)

        assert result.ordinary_income == Decimal("1125.00")
        assert result.correct_basis == Decimal("7500.00")
        assert result.proceeds == Decimal("8000.00")
        assert result.gain_loss == Decimal("500.00")
        assert result.holding_period == HoldingPeriod.SHORT_TERM
        assert "DISQUALIFYING" in result.notes

    def test_disqualifying_zero_broker_basis(self, engine, espp_lot, espp_form3922, security):
        """Broker reports $0 basis — adjustment code B."""
        sale = Sale(
            id="sale-002",
            lot_id="lot-espp-001",
            security=security,
            sale_date=date(2024, 10, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("160.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_espp_basis(espp_lot, sale, espp_form3922)

        assert result.adjustment_code == AdjustmentCode.B
        assert result.adjustment_amount == Decimal("7500.00")


class TestESPPQualifyingDisposition:
    """Qualifying: held > 2yr from offering AND > 1yr from purchase."""

    def test_qualifying_basis_correction(self, engine, espp_lot, espp_form3922, security):
        """Sold 2.5 years after offering, 2 years after purchase — qualifying.

        Ordinary income = lesser of:
          (a) actual gain = ($180 - $127.50) × 50 = $2,625
          (b) discount at offering = ($140 - $127.50) × 50 = $625
        → $625

        Correct basis = $127.50 × 50 + $625 = $7,000.
        """
        sale = Sale(
            id="sale-003",
            lot_id="lot-espp-001",
            security=security,
            sale_date=date(2026, 7, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("180.00"),
            broker_reported_basis=Decimal("6375.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_espp_basis(espp_lot, sale, espp_form3922)

        assert result.ordinary_income == Decimal("625.00")
        assert result.correct_basis == Decimal("7000.00")
        assert result.proceeds == Decimal("9000.00")
        assert result.gain_loss == Decimal("2000.00")
        assert result.holding_period == HoldingPeriod.LONG_TERM
        assert "QUALIFYING" in result.notes

    def test_qualifying_sold_at_loss(self, engine, espp_lot, espp_form3922, security):
        """Qualifying disposition sold at a loss — ordinary income capped at 0.

        Sale price below purchase price: actual gain negative.
        Ordinary income = max(0, lesser of actual gain, discount) = 0.
        """
        sale = Sale(
            id="sale-004",
            lot_id="lot-espp-001",
            security=security,
            sale_date=date(2026, 7, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("120.00"),
            broker_reported_basis=Decimal("6375.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_espp_basis(espp_lot, sale, espp_form3922)

        assert result.ordinary_income == Decimal("0")
        # Basis = purchase price only (no ordinary income added)
        assert result.correct_basis == Decimal("6375.00")
        assert result.gain_loss == Decimal("-375.00")  # $6000 - $6375

    def test_qualifying_partial_shares(self, engine, espp_form3922, security):
        """Sell only 20 of 50 shares — qualifying."""
        lot = Lot(
            id="lot-espp-partial",
            equity_type=EquityType.ESPP,
            security=security,
            acquisition_date=date(2024, 6, 30),
            shares=Decimal("50"),
            cost_per_share=Decimal("127.50"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-espp-001",
            broker_source=BrokerSource.SHAREWORKS,
        )
        sale = Sale(
            id="sale-005",
            lot_id="lot-espp-partial",
            security=security,
            sale_date=date(2026, 7, 1),
            shares=Decimal("20"),
            proceeds_per_share=Decimal("180.00"),
            broker_reported_basis=Decimal("2550.00"),  # $127.50 × 20
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_espp_basis(lot, sale, espp_form3922)

        # Ordinary income for 20 shares: min(($180-$127.50)*20, ($140-$127.50)*20)
        # = min($1050, $250) = $250
        assert result.ordinary_income == Decimal("250.00")
        assert result.shares == Decimal("20")
