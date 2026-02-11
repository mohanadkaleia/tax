"""Tests for tax estimation engine."""

from decimal import Decimal

from app.engines.estimator import TaxEstimator
from app.models.enums import FilingStatus


class TestTaxEstimator:
    def test_basic_federal_estimate(self):
        """Test basic federal tax computation for a single filer."""
        estimator = TaxEstimator()
        result = estimator.estimate(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            federal_withheld=Decimal("40000"),
            state_withheld=Decimal("15000"),
        )
        assert result.tax_year == 2025
        assert result.w2_wages == Decimal("200000")
        assert result.federal_regular_tax > Decimal("0")
        assert result.ca_tax > Decimal("0")

    def test_bracket_computation(self):
        """Verify progressive bracket computation."""
        estimator = TaxEstimator()
        # 2025 Single: 10% on first $11,925
        tax = estimator.compute_federal_tax(Decimal("11925"), FilingStatus.SINGLE, 2025)
        assert tax == Decimal("1192.50")

    def test_niit_below_threshold(self):
        """NIIT should be $0 when AGI is below threshold."""
        estimator = TaxEstimator()
        niit = estimator.compute_niit(Decimal("10000"), Decimal("150000"), FilingStatus.SINGLE)
        assert niit == Decimal("0")

    def test_niit_above_threshold(self):
        """NIIT should apply on lesser of investment income or AGI excess."""
        estimator = TaxEstimator()
        niit = estimator.compute_niit(Decimal("50000"), Decimal("250000"), FilingStatus.SINGLE)
        # Excess AGI = 250000 - 200000 = 50000
        # NIIT = min(50000, 50000) * 0.038 = 1900
        assert niit == Decimal("1900.000")
