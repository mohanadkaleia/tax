"""Tests for cost-basis correction engine."""

from datetime import date
from decimal import Decimal

from app.engines.basis import BasisCorrectionEngine
from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    EquityType,
    Form8949Category,
    HoldingPeriod,
)
from app.models.equity_event import Lot, Sale, Security
from app.models.tax_forms import Form3921


class TestRSUBasisCorrection:
    def test_rsu_zero_basis_correction(self, sample_rsu_lot: Lot, sample_rsu_sale: Sale):
        """Broker reports $0 basis for RSU; correct to FMV at vest.

        Per Pub. 525: RSU basis = FMV at vest = $150.00/sh x 100 sh = $15,000.
        Broker reported $0. Adjustment = $15,000. Code B.
        """
        engine = BasisCorrectionEngine()
        result = engine.correct(sample_rsu_lot, sample_rsu_sale)

        assert result.correct_basis == Decimal("15000.00")
        assert result.adjustment_amount == Decimal("15000.00")
        assert result.adjustment_code == AdjustmentCode.B
        assert result.proceeds == Decimal("17500.00")
        assert result.gain_loss == Decimal("2500.00")
        assert result.holding_period == HoldingPeriod.LONG_TERM
        assert result.form_8949_category == Form8949Category.D  # Long-term, basis reported


class TestISOBasisCorrection:
    """Tests for ISO basis correction including AMT adjustment logic.

    Per IRS Form 6251 Line 2i:
    - Same-year exercise + sale: no AMT adjustment required.
    - Prior-year exercise, current-year sale: reverse the preference (negative adjustment).
    """

    def _make_iso_lot(self, acq_date: date, shares: Decimal = Decimal("200")) -> Lot:
        sec = Security(ticker="ACME", name="Acme Corp")
        return Lot(
            id="lot-iso-001",
            equity_type=EquityType.ISO,
            security=sec,
            acquisition_date=acq_date,
            shares=shares,
            cost_per_share=Decimal("50.00"),       # strike price
            amt_cost_per_share=Decimal("120.00"),   # FMV at exercise
            shares_remaining=shares,
            source_event_id="evt-iso-001",
            broker_source=BrokerSource.SHAREWORKS,
        )

    def _make_iso_sale(self, sale_date: date, shares: Decimal = Decimal("200")) -> Sale:
        sec = Security(ticker="ACME", name="Acme Corp")
        return Sale(
            id="sale-iso-001",
            lot_id="lot-iso-001",
            security=sec,
            sale_date=sale_date,
            shares=shares,
            proceeds_per_share=Decimal("160.00"),
            broker_reported_basis=Decimal("10000.00"),  # strike * shares
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )

    def _make_form3921(self, exercise_date: date) -> Form3921:
        return Form3921(
            tax_year=exercise_date.year,
            grant_date=date(2020, 1, 15),
            exercise_date=exercise_date,
            exercise_price_per_share=Decimal("50.00"),
            fmv_on_exercise_date=Decimal("120.00"),
            shares_transferred=Decimal("200"),
            employer_name="Acme Corp",
        )

    def test_same_year_exercise_and_sale_no_amt_adjustment(self):
        """Exercise and sale in same year → amt_adjustment = $0.

        Per Form 6251 instructions: 'If you disposed of the stock in the
        same year you exercised the ISO, no adjustment is required.'
        """
        engine = BasisCorrectionEngine()
        lot = self._make_iso_lot(date(2024, 3, 1))
        sale = self._make_iso_sale(date(2024, 9, 15))
        form = self._make_form3921(date(2024, 3, 1))

        result = engine.correct_iso_basis(lot, sale, form)

        assert result.amt_adjustment == Decimal("0")
        # Still a disqualifying disposition (< 1yr from exercise, < 2yr from grant)
        assert "DISQUALIFYING" in result.notes
        # Ordinary income recognized
        spread = (Decimal("120") - Decimal("50")) * Decimal("200")  # $14,000
        actual_gain = result.proceeds - (Decimal("50") * Decimal("200"))  # $22,000
        assert result.ordinary_income == min(spread, actual_gain)  # $14,000

    def test_prior_year_exercise_disqualifying_sale_negative_amt(self):
        """Exercise in 2023, sale in 2024 (< 1yr) → disqualifying, negative AMT adjustment.

        The prior-year exercise created a +$14,000 AMT preference.
        The sale-year adjustment reverses it: AMT gain − regular gain = negative.
        """
        engine = BasisCorrectionEngine()
        lot = self._make_iso_lot(date(2023, 6, 1))
        sale = self._make_iso_sale(date(2024, 3, 15))
        form = self._make_form3921(date(2023, 6, 1))

        result = engine.correct_iso_basis(lot, sale, form)

        # AMT adjustment = AMT gain - regular gain
        # regular_gain = 32000 - 10000 = 22000
        # amt_gain = 32000 - 24000 = 8000
        # amt_adjustment = 8000 - 22000 = -14000
        assert result.amt_adjustment == Decimal("-14000.00")
        assert "DISQUALIFYING" in result.notes

    def test_prior_year_exercise_qualifying_sale_negative_amt(self):
        """Exercise in 2022, sale in 2025 (> 2yr from grant, > 1yr from exercise) → qualifying.

        Qualifying disposition, still has negative AMT adjustment to reverse preference.
        """
        engine = BasisCorrectionEngine()
        lot = self._make_iso_lot(date(2022, 6, 1))
        sale = self._make_iso_sale(date(2025, 7, 1))
        form = self._make_form3921(date(2022, 6, 1))

        result = engine.correct_iso_basis(lot, sale, form)

        # Same math: amt_adjustment = amt_gain - regular_gain = -14000
        assert result.amt_adjustment == Decimal("-14000.00")
        assert result.ordinary_income == Decimal("0")
        assert "QUALIFYING" in result.notes

    def test_same_year_sale_at_loss(self):
        """Same-year exercise + sale at a loss → amt_adjustment = $0, ordinary income = $0."""
        engine = BasisCorrectionEngine()
        sec = Security(ticker="ACME", name="Acme Corp")
        lot = Lot(
            id="lot-iso-002",
            equity_type=EquityType.ISO,
            security=sec,
            acquisition_date=date(2024, 3, 1),
            shares=Decimal("100"),
            cost_per_share=Decimal("50.00"),
            amt_cost_per_share=Decimal("120.00"),
            shares_remaining=Decimal("100"),
            source_event_id="evt-iso-002",
            broker_source=BrokerSource.SHAREWORKS,
        )
        sale = Sale(
            id="sale-iso-002",
            lot_id="lot-iso-002",
            security=sec,
            sale_date=date(2024, 8, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("40.00"),  # sold below strike
            broker_reported_basis=Decimal("5000.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.SHAREWORKS,
        )
        form = Form3921(
            tax_year=2024,
            grant_date=date(2020, 1, 15),
            exercise_date=date(2024, 3, 1),
            exercise_price_per_share=Decimal("50.00"),
            fmv_on_exercise_date=Decimal("120.00"),
            shares_transferred=Decimal("100"),
            employer_name="Acme Corp",
        )

        result = engine.correct_iso_basis(lot, sale, form)

        assert result.amt_adjustment == Decimal("0")
        assert result.ordinary_income == Decimal("0")  # No gain → no ordinary income
        assert result.gain_loss == Decimal("-1000.00")  # 4000 - 5000
