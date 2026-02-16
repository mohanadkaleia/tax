"""Tests for Additional Medicare Tax (Form 8959) computation.

IRC Section 3101(b)(2) imposes an additional 0.9% Hospital Insurance
(Medicare) tax on wages exceeding the filing-status threshold.

Expected values are hand-computed per Form 8959 instructions and verified
against the user's IRS transcript for tax year 2024.
"""

from decimal import Decimal

import pytest

from app.engines.estimator import TaxEstimator
from app.models.enums import FilingStatus


@pytest.fixture
def engine():
    return TaxEstimator()


class TestAdditionalMedicareTax:
    """Tests for compute_additional_medicare_tax()."""

    def test_single_above_threshold(self, engine):
        """Medicare wages $538,489 -> 0.9% x ($538,489 - $200,000) = $3,046.40."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("538489"), Decimal("10854"), FilingStatus.SINGLE
        )
        # 0.9% x $338,489 = $3,046.401 -> rounds to $3,046.40
        assert tax == Decimal("3046.40")
        # Credit: $10,854 - (1.45% x $538,489) = $10,854 - $7,808.09 = $3,045.91
        regular_medicare = (Decimal("538489") * Decimal("0.0145")).quantize(
            Decimal("0.01")
        )
        assert regular_medicare == Decimal("7808.09")
        assert credit == Decimal("10854") - regular_medicare
        assert credit == Decimal("3045.91")

    def test_single_below_threshold(self, engine):
        """Medicare wages $150,000 -> $0 tax, $0 credit."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("150000"), Decimal("2175"), FilingStatus.SINGLE
        )
        assert tax == Decimal("0.00")
        # Credit: $2,175 - (1.45% x $150,000) = $2,175 - $2,175 = $0
        assert credit == Decimal("0")

    def test_single_at_threshold_exactly(self, engine):
        """Medicare wages exactly at $200,000 -> $0 tax."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("200000"), Decimal("2900"), FilingStatus.SINGLE
        )
        assert tax == Decimal("0.00")
        # Credit: $2,900 - (1.45% x $200,000) = $2,900 - $2,900 = $0
        assert credit == Decimal("0")

    def test_mfj_threshold(self, engine):
        """MFJ threshold is $250,000. $300,000 -> 0.9% x $50,000 = $450."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("300000"), Decimal("4350"), FilingStatus.MFJ
        )
        assert tax == Decimal("450.00")

    def test_mfs_threshold(self, engine):
        """MFS threshold is $125,000. $150,000 -> 0.9% x $25,000 = $225."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("150000"), Decimal("2175"), FilingStatus.MFS
        )
        assert tax == Decimal("225.00")

    def test_hoh_threshold(self, engine):
        """HOH threshold is $200,000. Same as Single."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("250000"), Decimal("3625"), FilingStatus.HOH
        )
        # 0.9% x $50,000 = $450
        assert tax == Decimal("450.00")

    def test_withholding_credit(self, engine):
        """Credit = Box 6 - 1.45% x Box 5."""
        # If employer withheld $5,000 but regular rate would be $2,900 (1.45% x $200K)
        # Credit = $5,000 - $2,900 = $2,100
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("200000"), Decimal("5000"), FilingStatus.SINGLE
        )
        assert credit == Decimal("2100.00")

    def test_withholding_credit_not_negative(self, engine):
        """Credit cannot be negative if employer underwithheld."""
        # If employer only withheld $1,000 on $200K wages
        # Regular rate = 1.45% x $200K = $2,900
        # Credit = $1,000 - $2,900 = -$1,900 -> capped at $0
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("200000"), Decimal("1000"), FilingStatus.SINGLE
        )
        assert credit == Decimal("0")

    def test_zero_wages_zero_withholding(self, engine):
        """No W-2 Medicare data -> $0 tax, $0 credit."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("0"), Decimal("0"), FilingStatus.SINGLE
        )
        assert tax == Decimal("0.00")
        assert credit == Decimal("0")

    def test_self_employment_param_accepted(self, engine):
        """Self-employment income parameter is accepted for future-proofing."""
        tax, credit = engine.compute_additional_medicare_tax(
            Decimal("250000"),
            Decimal("3625"),
            FilingStatus.SINGLE,
            self_employment_income=Decimal("50000"),
        )
        # Currently self_employment_income is not used in computation
        # but the parameter should be accepted without error
        assert tax == Decimal("450.00")


class TestMedicareTaxInFullEstimate:
    """Tests that Additional Medicare Tax integrates correctly into the full estimate."""

    def test_full_estimate_includes_medicare_tax(self, engine):
        """Full estimate with Medicare wages includes Additional Medicare Tax in total."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("538489"),
            federal_withheld=Decimal("109772"),
            state_withheld=Decimal("30000"),
            medicare_wages=Decimal("538489"),
            medicare_tax_withheld=Decimal("10854"),
        )
        assert result.additional_medicare_tax == Decimal("3046.40")
        assert result.medicare_wages == Decimal("538489")
        assert result.medicare_tax_withheld == Decimal("10854")
        assert result.additional_medicare_withholding_credit == Decimal("3045.91")

    def test_medicare_tax_in_federal_total(self, engine):
        """Additional Medicare Tax is included in federal_total_tax."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
        )
        with_medicare = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            medicare_wages=Decimal("300000"),
            medicare_tax_withheld=Decimal("4350"),
        )
        # Additional Medicare Tax = 0.9% x ($300K - $200K) = $900
        expected_additional = Decimal("900.00")
        assert with_medicare.additional_medicare_tax == expected_additional
        assert with_medicare.federal_total_tax == (
            without.federal_total_tax + expected_additional
        )

    def test_medicare_credit_in_total_withheld(self, engine):
        """Medicare withholding credit is included in total_withheld."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            medicare_wages=Decimal("300000"),
            medicare_tax_withheld=Decimal("4350"),
        )
        # Credit: $4,350 - (1.45% x $300,000) = $4,350 - $4,350 = $0
        assert result.additional_medicare_withholding_credit == Decimal("0")
        assert result.total_withheld == Decimal("60000") + Decimal("20000")

    def test_medicare_credit_increases_effective_withheld(self, engine):
        """When employer overwithhold Medicare, credit increases effective withheld."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("538489"),
            federal_withheld=Decimal("109772"),
            state_withheld=Decimal("30000"),
            medicare_wages=Decimal("538489"),
            medicare_tax_withheld=Decimal("10854"),
        )
        # Effective federal withheld = $109,772 + $3,045.91 = $112,817.91
        expected_effective = Decimal("109772") + Decimal("3045.91")
        assert result.total_withheld == expected_effective + Decimal("30000")

    def test_no_medicare_wages_no_effect(self, engine):
        """When medicare_wages=0, no Additional Medicare Tax is computed."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
        )
        assert result.additional_medicare_tax == Decimal("0")
        assert result.additional_medicare_withholding_credit == Decimal("0")
        assert result.total_withheld == Decimal("80000")

    def test_medicare_does_not_affect_california(self, engine):
        """Additional Medicare Tax is federal only; CA totals are unchanged."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
        )
        with_medicare = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            medicare_wages=Decimal("300000"),
            medicare_tax_withheld=Decimal("4350"),
        )
        assert with_medicare.ca_total_tax == without.ca_total_tax
        assert with_medicare.ca_balance_due == without.ca_balance_due

    def test_federal_balance_due_identity(self, engine):
        """Verify federal_balance_due = total_tax - effective_withheld - est_payments."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("538489"),
            federal_withheld=Decimal("109772"),
            state_withheld=Decimal("30000"),
            medicare_wages=Decimal("538489"),
            medicare_tax_withheld=Decimal("10854"),
        )
        effective_withheld = (
            result.federal_withheld + result.additional_medicare_withholding_credit
        )
        expected_balance = (
            result.federal_total_tax
            - effective_withheld
            - result.federal_estimated_payments
        )
        assert result.federal_balance_due == expected_balance


class TestMedicareTaxWithOtherFeatures:
    """Tests that Medicare Tax works correctly with other tax features."""

    def test_with_foreign_tax_credit(self, engine):
        """Medicare Tax + Foreign Tax Credit both applied correctly."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            dividend_income=Decimal("1000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            foreign_tax_paid=Decimal("50"),
            medicare_wages=Decimal("300000"),
            medicare_tax_withheld=Decimal("4350"),
        )
        assert result.additional_medicare_tax == Decimal("900.00")
        assert result.federal_foreign_tax_credit == Decimal("50")
        # Total tax includes medicare but subtracts FTC
        assert result.federal_total_tax > Decimal("0")

    def test_with_amt_and_medicare(self, engine):
        """Medicare Tax + AMT both included in federal total."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
            medicare_wages=Decimal("400000"),
            medicare_tax_withheld=Decimal("7800"),
        )
        assert result.additional_medicare_tax == Decimal("1800.00")
        assert result.federal_amt > Decimal("0")
        # Total should include both AMT and Medicare
        expected_total = (
            result.federal_regular_tax
            + result.federal_ltcg_tax
            + result.federal_niit
            + result.federal_amt
            + result.additional_medicare_tax
            - result.federal_foreign_tax_credit
        )
        assert result.federal_total_tax == expected_total
