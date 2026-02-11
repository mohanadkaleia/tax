"""Tests for ISO AMT computation engine."""

from decimal import Decimal

from app.engines.iso_amt import ISOAMTEngine
from app.models.tax_forms import Form3921


class TestISOAMTPreference:
    def test_compute_amt_preference(self, sample_form3921: Form3921):
        """AMT preference = (FMV at exercise - strike) x shares.

        Per Form 6251 Line 2i:
        ($120.00 - $50.00) x 200 = $14,000.00
        """
        engine = ISOAMTEngine()
        result = engine.compute_amt_preference(sample_form3921)

        assert result.spread_per_share == Decimal("70.00")
        assert result.total_amt_preference == Decimal("14000.00")
        assert result.regular_basis == Decimal("10000.00")  # 50 * 200
        assert result.amt_basis == Decimal("24000.00")  # 120 * 200
