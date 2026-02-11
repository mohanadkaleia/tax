"""Tests for cost-basis correction engine."""

from decimal import Decimal

from app.engines.basis import BasisCorrectionEngine
from app.models.enums import AdjustmentCode, Form8949Category, HoldingPeriod
from app.models.equity_event import Lot, Sale


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
