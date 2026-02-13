"""Tests for ISO cost-basis correction in BasisCorrectionEngine."""

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
from app.models.tax_forms import Form3921


@pytest.fixture
def engine():
    return BasisCorrectionEngine()


@pytest.fixture
def security():
    return Security(ticker="ACME", name="Acme Corp")


@pytest.fixture
def iso_form3921():
    """ISO exercise: grant 2022-01-15, exercise 2024-01-10, strike $50, FMV $120."""
    return Form3921(
        tax_year=2024,
        grant_date=date(2022, 1, 15),
        exercise_date=date(2024, 1, 10),
        exercise_price_per_share=Decimal("50.00"),
        fmv_on_exercise_date=Decimal("120.00"),
        shares_transferred=Decimal("200"),
        employer_name="Acme Corp",
    )


@pytest.fixture
def iso_lot(security):
    """ISO lot: regular basis = $50 (strike), AMT basis = $120 (FMV at exercise)."""
    return Lot(
        id="lot-iso-001",
        equity_type=EquityType.ISO,
        security=security,
        acquisition_date=date(2024, 1, 10),
        shares=Decimal("200"),
        cost_per_share=Decimal("50.00"),
        amt_cost_per_share=Decimal("120.00"),
        shares_remaining=Decimal("200"),
        source_event_id="evt-iso-001",
        broker_source=BrokerSource.SHAREWORKS,
    )


class TestISOQualifyingDisposition:
    """Qualifying: held > 2yr from grant AND > 1yr from exercise."""

    def test_qualifying_basis_correction(self, engine, iso_lot, iso_form3921, security):
        """Sold 2.5yr after grant, 1.5yr after exercise — qualifying.

        Regular basis = $50 × 200 = $10,000.
        AMT basis = $120 × 200 = $24,000.
        Proceeds = $150 × 200 = $30,000.
        Regular gain = $20,000, AMT gain = $6,000.
        AMT adjustment = $20,000 - $6,000 = $14,000 (reversal of prior preference).
        No ordinary income for qualifying ISO disposition.
        """
        sale = Sale(
            id="sale-iso-001",
            lot_id="lot-iso-001",
            security=security,
            sale_date=date(2025, 7, 1),
            shares=Decimal("200"),
            proceeds_per_share=Decimal("150.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_iso_basis(iso_lot, sale, iso_form3921)

        assert result.correct_basis == Decimal("10000.00")
        assert result.proceeds == Decimal("30000.00")
        assert result.ordinary_income == Decimal("0")
        assert result.amt_adjustment == Decimal("14000.00")
        assert result.adjustment_code == AdjustmentCode.B
        assert result.adjustment_amount == Decimal("10000.00")
        assert result.holding_period == HoldingPeriod.LONG_TERM
        assert "QUALIFYING" in result.notes

    def test_qualifying_no_amt_adjustment_when_equal(self, engine, security, iso_form3921):
        """If regular basis == AMT basis, AMT adjustment is 0."""
        lot = Lot(
            id="lot-iso-eq",
            equity_type=EquityType.ISO,
            security=security,
            acquisition_date=date(2024, 1, 10),
            shares=Decimal("100"),
            cost_per_share=Decimal("50.00"),
            amt_cost_per_share=Decimal("50.00"),  # Same as regular
            shares_remaining=Decimal("100"),
            source_event_id="evt-iso-eq",
            broker_source=BrokerSource.SHAREWORKS,
        )
        sale = Sale(
            id="sale-iso-eq",
            lot_id="lot-iso-eq",
            security=security,
            sale_date=date(2025, 7, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("80.00"),
            broker_reported_basis=Decimal("5000.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_iso_basis(lot, sale, iso_form3921)

        assert result.amt_adjustment == Decimal("0")


class TestISODisqualifyingDisposition:
    """Disqualifying: sold < 2yr from grant or < 1yr from exercise."""

    def test_disqualifying_less_than_one_year(self, engine, iso_lot, iso_form3921, security):
        """Sold 6 months after exercise — disqualifying.

        Ordinary income = lesser of (spread at exercise, actual gain).
        Spread = ($120 - $50) × 200 = $14,000.
        Actual gain = ($130 - $50) × 200 = $16,000.
        Ordinary income = min($14,000, $16,000) = $14,000.
        Capital gain = total gain - ordinary income = $16,000 - $14,000 = $2,000.
        """
        sale = Sale(
            id="sale-iso-dq",
            lot_id="lot-iso-001",
            security=security,
            sale_date=date(2024, 7, 1),
            shares=Decimal("200"),
            proceeds_per_share=Decimal("130.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_iso_basis(iso_lot, sale, iso_form3921)

        assert result.ordinary_income == Decimal("14000.00")
        assert result.correct_basis == Decimal("10000.00")
        assert result.gain_loss == Decimal("2000.00")  # $26,000 - $14,000 - $10,000
        assert result.holding_period == HoldingPeriod.SHORT_TERM
        assert "DISQUALIFYING" in result.notes

    def test_disqualifying_sold_at_loss(self, engine, iso_lot, iso_form3921, security):
        """Disqualifying disposition sold below strike — ordinary income capped at actual gain.

        Sale price $40 < strike $50 → actual gain is negative.
        Ordinary income = min(spread, max(actual_gain, 0)) = min($14000, $0) = $0.
        """
        sale = Sale(
            id="sale-iso-loss",
            lot_id="lot-iso-001",
            security=security,
            sale_date=date(2024, 7, 1),
            shares=Decimal("200"),
            proceeds_per_share=Decimal("40.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_iso_basis(iso_lot, sale, iso_form3921)

        assert result.ordinary_income == Decimal("0")

    def test_disqualifying_gain_less_than_spread(self, engine, iso_lot, iso_form3921, security):
        """Disqualifying when actual gain < spread at exercise.

        Sale price $60 → actual gain = ($60-$50)*200 = $2,000.
        Spread = ($120-$50)*200 = $14,000.
        Ordinary income = min($14,000, $2,000) = $2,000.
        """
        sale = Sale(
            id="sale-iso-partial",
            lot_id="lot-iso-001",
            security=security,
            sale_date=date(2024, 7, 1),
            shares=Decimal("200"),
            proceeds_per_share=Decimal("60.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

        result = engine.correct_iso_basis(iso_lot, sale, iso_form3921)

        assert result.ordinary_income == Decimal("2000.00")
