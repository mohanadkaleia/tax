"""Tests for TaxEstimator — federal + California tax computation.

Expected values are hand-computed by the CPA agent using the 2024 bracket tables.
See plans/tax-estimator.md Section 5.8 for full workings.
"""

from decimal import Decimal

import pytest

from app.engines.estimator import TaxEstimator
from app.models.enums import FilingStatus


@pytest.fixture
def engine():
    return TaxEstimator()


class TestBasicComputation:
    """Existing basic tests (updated for new TaxEstimator interface)."""

    def test_basic_federal_estimate(self, engine):
        result = engine.estimate(
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

    def test_bracket_computation(self, engine):
        tax = engine.compute_federal_tax(Decimal("11925"), FilingStatus.SINGLE, 2025)
        assert tax == Decimal("1192.50")

    def test_niit_below_threshold(self, engine):
        niit = engine.compute_niit(Decimal("10000"), Decimal("150000"), FilingStatus.SINGLE)
        assert niit == Decimal("0")

    def test_niit_above_threshold(self, engine):
        niit = engine.compute_niit(Decimal("50000"), Decimal("250000"), FilingStatus.SINGLE)
        assert niit == Decimal("1900.000")


class TestW2OnlySingle:
    """CPA Test 1: W-2 only, Single filer, $150k wages."""

    def test_income(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("150000"),
            federal_withheld=Decimal("25000"),
            state_withheld=Decimal("8000"),
        )
        assert r.total_income == Decimal("150000")
        assert r.agi == Decimal("150000")
        assert r.standard_deduction == Decimal("14600")
        assert r.taxable_income == Decimal("135400")

    def test_federal_tax(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("150000"),
            federal_withheld=Decimal("25000"),
            state_withheld=Decimal("8000"),
        )
        assert r.federal_regular_tax == Decimal("25538.50")
        assert r.federal_ltcg_tax == Decimal("0")
        assert r.federal_niit == Decimal("0")
        assert r.federal_amt == Decimal("0")
        assert r.federal_total_tax == Decimal("25538.50")
        assert r.federal_balance_due == Decimal("538.50")

    def test_california_tax(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("150000"),
            federal_withheld=Decimal("25000"),
            state_withheld=Decimal("8000"),
        )
        assert r.ca_taxable_income == Decimal("144460")
        assert r.ca_tax == Decimal("10087.63")
        assert r.ca_mental_health_tax == Decimal("0")
        assert r.ca_balance_due == Decimal("2087.63")

    def test_total(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("150000"),
            federal_withheld=Decimal("25000"),
            state_withheld=Decimal("8000"),
        )
        assert r.total_balance_due == Decimal("2626.13")


class TestW2CapitalGainsSingle:
    """CPA Test 2: W-2 + capital gains, Single filer."""

    def test_federal(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("30000"),
            federal_withheld=Decimal("40000"),
            state_withheld=Decimal("15000"),
        )
        assert r.taxable_income == Decimal("225400")
        assert r.federal_regular_tax == Decimal("40214.50")
        assert r.federal_ltcg_tax == Decimal("4500")
        assert r.federal_niit == Decimal("1520")
        assert r.federal_amt == Decimal("0")
        assert r.federal_total_tax == Decimal("46234.50")
        assert r.federal_balance_due == Decimal("6234.50")

    def test_california(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("30000"),
            federal_withheld=Decimal("40000"),
            state_withheld=Decimal("15000"),
        )
        assert r.ca_taxable_income == Decimal("234460")
        assert r.ca_tax == Decimal("18457.63")
        assert r.ca_balance_due == Decimal("3457.63")

    def test_total(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("30000"),
            federal_withheld=Decimal("40000"),
            state_withheld=Decimal("15000"),
        )
        assert r.total_balance_due == Decimal("9692.13")


class TestW2ESPPCapitalGains:
    """CPA Test 3: W-2 with ESPP income + capital gains."""

    def test_federal(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("180000"),
            short_term_gains=Decimal("5000"),
            long_term_gains=Decimal("15000"),
            federal_withheld=Decimal("32000"),
            state_withheld=Decimal("12000"),
        )
        assert r.taxable_income == Decimal("185400")
        assert r.federal_regular_tax == Decimal("33938.50")
        assert r.federal_ltcg_tax == Decimal("2250")
        assert r.federal_niit == Decimal("0")
        assert r.federal_total_tax == Decimal("36188.50")
        assert r.federal_balance_due == Decimal("4188.50")

    def test_california(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("180000"),
            short_term_gains=Decimal("5000"),
            long_term_gains=Decimal("15000"),
            federal_withheld=Decimal("32000"),
            state_withheld=Decimal("12000"),
        )
        assert r.ca_tax == Decimal("14737.63")
        assert r.ca_balance_due == Decimal("2737.63")

    def test_total(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("180000"),
            short_term_gains=Decimal("5000"),
            long_term_gains=Decimal("15000"),
            federal_withheld=Decimal("32000"),
            state_withheld=Decimal("12000"),
        )
        assert r.total_balance_due == Decimal("6926.13")


class TestQualifiedDividendsMFJ:
    """CPA Test 4: W-2 + qualified dividends + interest, MFJ."""

    def test_federal(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("250000"),
            interest_income=Decimal("5000"),
            dividend_income=Decimal("12000"),
            qualified_dividends=Decimal("10000"),
            federal_withheld=Decimal("45000"),
            state_withheld=Decimal("18000"),
        )
        assert r.total_income == Decimal("267000")
        assert r.taxable_income == Decimal("237800")
        assert r.federal_regular_tax == Decimal("40757")
        assert r.federal_ltcg_tax == Decimal("1500")
        assert r.federal_niit == Decimal("646")
        assert r.federal_total_tax == Decimal("42903")
        assert r.federal_balance_due == Decimal("-2097")

    def test_california(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("250000"),
            interest_income=Decimal("5000"),
            dividend_income=Decimal("12000"),
            qualified_dividends=Decimal("10000"),
            federal_withheld=Decimal("45000"),
            state_withheld=Decimal("18000"),
        )
        assert r.ca_taxable_income == Decimal("255920")
        assert r.ca_tax == Decimal("17106.26")
        assert r.ca_balance_due == Decimal("-893.74")

    def test_total_refund(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.MFJ,
            w2_wages=Decimal("250000"),
            interest_income=Decimal("5000"),
            dividend_income=Decimal("12000"),
            qualified_dividends=Decimal("10000"),
            federal_withheld=Decimal("45000"),
            state_withheld=Decimal("18000"),
        )
        assert r.total_balance_due == Decimal("-2990.74")


class TestHighIncomeNIITAMT:
    """CPA Test 5: High income with NIIT and AMT."""

    def test_federal(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            interest_income=Decimal("10000"),
            dividend_income=Decimal("8000"),
            qualified_dividends=Decimal("5000"),
            long_term_gains=Decimal("100000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
        )
        assert r.total_income == Decimal("518000")
        assert r.taxable_income == Decimal("503400")
        assert r.federal_regular_tax == Decimal("109814.75")
        assert r.federal_ltcg_tax == Decimal("15750")
        assert r.federal_niit == Decimal("4484")

    def test_amt(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            interest_income=Decimal("10000"),
            dividend_income=Decimal("8000"),
            qualified_dividends=Decimal("5000"),
            long_term_gains=Decimal("100000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
        )
        assert r.federal_amt == Decimal("40922.75")

    def test_total(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            interest_income=Decimal("10000"),
            dividend_income=Decimal("8000"),
            qualified_dividends=Decimal("5000"),
            long_term_gains=Decimal("100000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
        )
        assert r.federal_total_tax == Decimal("170971.50")
        assert r.federal_balance_due == Decimal("70971.50")

    def test_california(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            interest_income=Decimal("10000"),
            dividend_income=Decimal("8000"),
            qualified_dividends=Decimal("5000"),
            long_term_gains=Decimal("100000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
        )
        assert r.ca_taxable_income == Decimal("512460")
        assert r.ca_tax == Decimal("46879.85")
        assert r.ca_balance_due == Decimal("6879.85")

    def test_grand_total(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            interest_income=Decimal("10000"),
            dividend_income=Decimal("8000"),
            qualified_dividends=Decimal("5000"),
            long_term_gains=Decimal("100000"),
            amt_iso_preference=Decimal("200000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("40000"),
        )
        assert r.total_balance_due == Decimal("77851.35")


class TestCapitalLossLimitation:
    """Capital loss limited to $3,000 for Single filer."""

    def test_loss_netting(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            short_term_gains=Decimal("-3000"),
            long_term_gains=Decimal("0"),
            federal_withheld=Decimal("15000"),
            state_withheld=Decimal("5000"),
        )
        assert r.total_income == Decimal("97000")
        assert r.taxable_income == Decimal("82400")
        assert r.federal_regular_tax == Decimal("13181")
        assert r.federal_balance_due == Decimal("-1819")


class TestCapitalLossMFS:
    """MFS filing status: capital loss limit is $1,500."""

    def test_mfs_loss_limit(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.MFS,
            w2_wages=Decimal("120000"),
            short_term_gains=Decimal("-1500"),
            federal_withheld=Decimal("20000"),
            state_withheld=Decimal("7000"),
        )
        assert r.total_income == Decimal("118500")
        assert r.taxable_income == Decimal("103900")
        assert r.federal_regular_tax == Decimal("17978.50")
        assert r.federal_balance_due == Decimal("-2021.50")


class TestLTCGZeroBracket:
    """Low-income filer gets 0% LTCG rate."""

    def test_zero_rate(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("30000"),
            long_term_gains=Decimal("10000"),
        )
        assert r.federal_ltcg_tax == Decimal("0")


class TestLTCG20Bracket:
    """Very high income triggers 20% LTCG rate."""

    def test_twenty_percent(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("500000"),
            long_term_gains=Decimal("100000"),
        )
        # ordinary_income_top = 585400 - 100000 = 485400
        # 0% bracket: 0-47025, start 485400 >= 47025, skip
        # 15% bracket: 47025-518900, start 485400
        #   space = 518900 - 485400 = 33500, taxed = 33500 * 0.15 = 5025
        # 20% bracket: remaining 66500 * 0.20 = 13300
        # total = 18325
        assert r.federal_ltcg_tax == Decimal("18325")


class TestCAMentalHealthTax:
    """Income above $1M triggers 1% Mental Health Services Tax."""

    def test_mental_health_surcharge(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("1200000"),
        )
        assert r.ca_mental_health_tax == Decimal("1944.60")


class TestCANoLTCGPreference:
    """California taxes all income at ordinary rates."""

    def test_ca_taxes_ltcg_at_ordinary(self, engine):
        r1 = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
        )
        r2 = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("80000"),
            long_term_gains=Decimal("20000"),
        )
        assert r1.ca_tax == r2.ca_tax


class TestNoAMTWhenNoPreferences:
    """AMT should be $0 when there are no ISO exercises."""

    def test_no_amt(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("500000"),
            long_term_gains=Decimal("100000"),
        )
        assert r.federal_amt == Decimal("0")


class TestStandardVsItemized:
    """Itemized deductions used when greater than standard."""

    def test_uses_itemized(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            itemized_deductions=Decimal("25000"),
        )
        assert r.deduction_used == Decimal("25000")
        assert r.taxable_income == Decimal("175000")

    def test_uses_standard_when_higher(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            itemized_deductions=Decimal("10000"),
        )
        assert r.deduction_used == Decimal("14600")


class TestZeroIncome:
    """All zero income produces all zero tax."""

    def test_all_zeros(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("0"),
        )
        assert r.total_income == Decimal("0")
        assert r.taxable_income == Decimal("0")
        assert r.federal_regular_tax == Decimal("0")
        assert r.federal_ltcg_tax == Decimal("0")
        assert r.federal_niit == Decimal("0")
        assert r.federal_amt == Decimal("0")
        assert r.ca_tax == Decimal("0")
        assert r.total_tax == Decimal("0")


class TestArithmeticIdentities:
    """Verify cross-reference identities from the CPA plan."""

    def test_identities(self, engine):
        r = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            interest_income=Decimal("5000"),
            dividend_income=Decimal("3000"),
            qualified_dividends=Decimal("2000"),
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("20000"),
            federal_withheld=Decimal("35000"),
            state_withheld=Decimal("12000"),
        )
        assert r.federal_total_tax == (
            r.federal_regular_tax + r.federal_ltcg_tax + r.federal_niit + r.federal_amt
        )
        assert r.ca_total_tax == r.ca_tax + r.ca_mental_health_tax
        assert r.total_tax == r.federal_total_tax + r.ca_total_tax
        assert r.federal_balance_due == (
            r.federal_total_tax - r.federal_withheld - r.federal_estimated_payments
        )
        assert r.ca_balance_due == (
            r.ca_total_tax - r.ca_withheld - r.ca_estimated_payments
        )
        assert r.total_balance_due == r.federal_balance_due + r.ca_balance_due
        assert r.taxable_income >= Decimal("0")
        assert r.federal_regular_tax >= Decimal("0")
        assert r.ca_tax >= Decimal("0")
        assert r.federal_amt >= Decimal("0")
