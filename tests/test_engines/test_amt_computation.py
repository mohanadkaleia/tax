"""Tests for AMT computation (Form 6251) and AMT credit (Form 8801).

Tests cover:
- SALT add-back as an exclusion item
- MFS half-threshold for 28% rate
- LTCG preferential rates under AMT
- Exemption phase-out
- Deferral vs exclusion item split
- AMT credit carryforward logic
- Removal of the early return guard (zero ISO + SALT should still compute)
"""

from decimal import Decimal

import pytest

from app.engines.estimator import TaxEstimator
from app.engines.iso_amt import ISOAMTEngine
from app.models.enums import FilingStatus


@pytest.fixture
def engine():
    return TaxEstimator()


@pytest.fixture
def iso_engine():
    return ISOAMTEngine()


class TestAMTComputation:
    """Tests for AMT computation in TaxEstimator.compute_amt()."""

    def test_amt_with_salt_addback_only(self, engine):
        """High-income taxpayer who itemizes with $10K SALT, zero ISO exercises.

        This tests that removing the early return guard works.
        SALT add-back is an exclusion item that can trigger AMT even
        with zero ISO preference items.
        """
        # With $0 ISO preference but $10K SALT add-back, AMT should be
        # computed (no longer short-circuited).
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("0"),  # no ISO
            regular_tax=Decimal("90000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            salt_addback=Decimal("10000"),
        )
        # AMTI = 400000 + 10000 = 410000
        assert amti == Decimal("410000")
        # AMT may or may not be > 0 depending on whether TMT > regular_tax
        # The key assertion is that computation was not skipped
        assert amti > Decimal("400000")

    def test_amt_with_iso_preference(self, engine):
        """ISO exercise creates AMT preference, triggers AMT."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("50000"),
            regular_tax=Decimal("90000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        # AMTI = 400000 + 50000 = 450000
        assert amti == Decimal("450000")
        # Exemption: $85,700 (Single 2024), no phase-out since
        # AMTI $450K < phaseout start $609,350
        assert exemption == Decimal("85700")
        # AMT base = 450000 - 85700 = 364300
        # All ordinary, so: first $232,600 at 26%, rest at 28%
        # 232600 * 0.26 = 60476
        # (364300 - 232600) * 0.28 = 131700 * 0.28 = 36876
        # TMT = 60476 + 36876 = 97352
        assert tmt == Decimal("97352.00")
        # AMT = max(97352 - 90000, 0) = 7352
        assert amt == Decimal("7352.00")

    def test_amt_exemption_phase_out(self, engine):
        """AMTI above phase-out threshold reduces exemption."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("600000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("150000"),
            regular_tax=Decimal("170000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        # AMTI = 600000 + 150000 = 750000
        assert amti == Decimal("750000")
        # Phase-out: (750000 - 609350) * 0.25 = 140650 * 0.25 = 35162.50
        # Exemption: 85700 - 35162.50 = 50537.50
        assert exemption == Decimal("50537.50")

    def test_amt_exemption_fully_phased_out(self, engine):
        """Very high AMTI fully phases out the exemption."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("900000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("200000"),
            regular_tax=Decimal("260000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        # AMTI = 1100000
        assert amti == Decimal("1100000")
        # Phase-out: (1100000 - 609350) * 0.25 = 490650 * 0.25 = 122662.50
        # Exemption: 85700 - 122662.50 = -36962.50 -> floored at 0
        assert exemption == Decimal("0")

    def test_amt_26_28_brackets(self, engine):
        """AMT base above $232,600 threshold uses 28% rate."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("50000"),
            regular_tax=Decimal("0"),  # artificial: forces AMT > 0
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        # AMTI = 450000, exemption = 85700
        # AMT base = 450000 - 85700 = 364300
        # First $232,600 at 26% = 60,476.00
        # Remaining $131,700 at 28% = 36,876.00
        # TMT = 97,352.00
        assert tmt == Decimal("97352.00")

    def test_amt_mfs_half_threshold(self, engine):
        """MFS uses half the 28% threshold ($116,300 for 2024)."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("300000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("50000"),
            regular_tax=Decimal("0"),
            filing_status=FilingStatus.MFS,
            tax_year=2024,
        )
        # AMTI = 350000, exemption for MFS = 66650
        # Phase-out: AMTI $350K < phaseout $609,350 -> no reduction
        assert exemption == Decimal("66650")
        # AMT base = 350000 - 66650 = 283350
        # MFS threshold = 232600 / 2 = 116300
        # First $116,300 at 26% = 30238.00
        # Remaining $167,050 at 28% = 46774.00
        # TMT = 77012.00
        assert tmt == Decimal("77012.00")

    def test_amt_ltcg_preferential_rates(self, engine):
        """LTCG taxed at preferential rates under AMT, not 26%/28%."""
        # Scenario: $300K ordinary + $200K LTCG, single 2024
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("500000"),
            preferential_income=Decimal("200000"),
            amt_preference=Decimal("50000"),
            regular_tax=Decimal("100000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        # AMTI = 550000
        assert amti == Decimal("550000")
        # AMT base = 550000 - 85700 = 464300
        # Ordinary part = 464300 - 200000 = 264300
        # 232600 * 0.26 + (264300 - 232600) * 0.28 = 60476 + 8876 = 69352
        # LTCG part: 200000 taxed at preferential rates (stacked)
        # TMT should be lower than if all at 26%/28%
        assert tmt > Decimal("0")
        # TMT with LTCG should be less than all at 26% (200000 * 0.26 = 52000)
        # LTCG rates are 0%/15%/20%, so LTCG portion < 52000
        ltcg_tax = engine.compute_ltcg_tax(
            Decimal("200000"), Decimal("464300"), FilingStatus.SINGLE, 2024
        )
        expected_tmt = Decimal("69352") + ltcg_tax
        assert tmt == expected_tmt

    def test_amt_zero_when_regular_tax_higher(self, engine):
        """AMT = max(0, TMT - regular_tax). If regular > TMT, AMT = 0."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("1000"),  # tiny preference
            regular_tax=Decimal("999999"),  # artificially high
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        assert amt == Decimal("0")
        assert tmt > Decimal("0")

    def test_no_amt_preference_no_salt_no_amt(self, engine):
        """Zero ISO preference + standard deduction (no SALT) -> no AMT."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("200000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("0"),
            regular_tax=Decimal("40000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            salt_addback=Decimal("0"),
        )
        # No additions -> early return path (no AMT additions)
        assert amt == Decimal("0")

    def test_amt_returns_tuple(self, engine):
        """Verify compute_amt returns (amt, amti, exemption, tmt) tuple."""
        result = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("50000"),
            regular_tax=Decimal("90000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
        )
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_amt_combined_iso_and_salt(self, engine):
        """Both ISO preference and SALT add-back contribute to AMTI."""
        amt, amti, exemption, tmt = engine.compute_amt(
            taxable_income=Decimal("400000"),
            preferential_income=Decimal("0"),
            amt_preference=Decimal("30000"),
            regular_tax=Decimal("90000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            salt_addback=Decimal("10000"),
        )
        # AMTI = 400000 + 30000 + 10000 = 440000
        assert amti == Decimal("440000")


class TestAMTIntegration:
    """Integration tests: compute_amt wired into estimate()."""

    def test_estimate_includes_salt_addback(self, engine):
        """When itemizing with SALT, the SALT deduction is added back for AMT."""
        from app.models.deductions import ItemizedDeductions

        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("500000"),
            amt_iso_preference=Decimal("50000"),
            federal_withheld=Decimal("100000"),
            state_withheld=Decimal("30000"),
            itemized_detail=ItemizedDeductions(
                state_income_tax_paid=Decimal("40000"),
                charitable_cash=Decimal("11000"),
            ),
        )
        # SALT is capped at $10K; that $10K should be added back for AMT
        assert result.amti > result.taxable_income
        assert result.federal_amt >= Decimal("0")

    def test_estimate_no_salt_addback_when_standard(self, engine):
        """No SALT add-back when using standard deduction."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            federal_withheld=Decimal("20000"),
            state_withheld=Decimal("5000"),
            # No itemized_detail -> standard deduction
        )
        # AMT additions = $0 ISO + $0 SALT = $0 total
        assert result.federal_amt == Decimal("0")

    def test_estimate_amt_credit_reduces_total_tax(self, engine):
        """AMT credit from prior year reduces federal total tax."""
        # Base case: no credit
        base = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("15000"),
        )

        engine2 = TaxEstimator()
        # With credit
        with_credit = engine2.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("300000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("15000"),
            prior_year_amt_credit=Decimal("5000"),
        )

        # Credit should reduce federal total tax (or at least not increase it)
        assert with_credit.federal_total_tax <= base.federal_total_tax
        assert with_credit.amt_credit_used >= Decimal("0")

    def test_estimate_amt_detail_fields_populated(self, engine):
        """Verify AMT detail fields are populated in TaxEstimate."""
        from app.models.deductions import ItemizedDeductions

        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("400000"),
            amt_iso_preference=Decimal("50000"),
            federal_withheld=Decimal("80000"),
            state_withheld=Decimal("20000"),
            itemized_detail=ItemizedDeductions(
                state_income_tax_paid=Decimal("30000"),
                charitable_cash=Decimal("10000"),
            ),
        )
        # AMT detail fields should be populated
        assert result.amti > Decimal("0")
        assert result.amt_exemption_used >= Decimal("0")
        assert result.amt_tentative_minimum_tax >= Decimal("0")


class TestAMTCredit:
    """Tests for AMT credit computation (Form 8801)."""

    def test_credit_used_when_regular_exceeds_tmt(self, iso_engine):
        """Credit = min(prior_credit, regular_tax - TMT)."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("5000"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("90000"),
        )
        assert credit_used == Decimal("5000")
        assert credit_remaining == Decimal("0")

    def test_no_credit_when_amt_owed(self, iso_engine):
        """If TMT >= regular_tax (AMT owed), no credit usable."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("5000"),
            regular_tax=Decimal("80000"),
            tentative_minimum_tax=Decimal("100000"),
        )
        assert credit_used == Decimal("0")
        assert credit_remaining == Decimal("5000")

    def test_partial_credit_use(self, iso_engine):
        """Credit partially used, remainder carries forward."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("15000"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("90000"),
        )
        # Credit limit = 100000 - 90000 = 10000
        assert credit_used == Decimal("10000")
        assert credit_remaining == Decimal("5000")

    def test_zero_prior_credit(self, iso_engine):
        """No prior credit -> $0 used, $0 remaining."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("0"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("90000"),
        )
        assert credit_used == Decimal("0")
        assert credit_remaining == Decimal("0")

    def test_full_credit_use(self, iso_engine):
        """Credit fully used when regular_tax - TMT exceeds credit."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("3000"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("90000"),
        )
        # Credit limit = 10000 > credit = 3000
        assert credit_used == Decimal("3000")
        assert credit_remaining == Decimal("0")

    def test_credit_when_regular_equals_tmt(self, iso_engine):
        """If regular_tax == TMT exactly, no credit usable."""
        credit_used, credit_remaining = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("5000"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("100000"),
        )
        assert credit_used == Decimal("0")
        assert credit_remaining == Decimal("5000")


class TestISOAMTEngineStubs:
    """Verify the NotImplementedError stubs are gone."""

    def test_compute_amt_liability_no_longer_raises(self, iso_engine):
        """Verify the NotImplementedError is gone."""
        # Should return a tuple, not raise
        result = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("50000"),
            amt_adjustments=Decimal("10000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("90000"),
        )
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_compute_amt_credit_no_longer_raises(self, iso_engine):
        """Verify the NotImplementedError is gone."""
        result = iso_engine.compute_amt_credit(
            prior_year_amt_credit=Decimal("5000"),
            regular_tax=Decimal("100000"),
            tentative_minimum_tax=Decimal("90000"),
        )
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestISOAMTEngineLiability:
    """Tests for ISOAMTEngine.compute_amt_liability() deferral/exclusion split."""

    def test_deferral_only(self, iso_engine):
        """All AMT from deferral items -> full amount is deferral AMT."""
        total, deferral, exclusion = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("50000"),  # deferral (ISO)
            amt_adjustments=Decimal("0"),  # no exclusion
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("90000"),
        )
        assert total > Decimal("0")
        assert deferral == total
        assert exclusion == Decimal("0")

    def test_exclusion_only(self, iso_engine):
        """All AMT from exclusion items -> full amount is exclusion AMT."""
        total, deferral, exclusion = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("0"),  # no deferral
            amt_adjustments=Decimal("50000"),  # exclusion (SALT)
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("90000"),
        )
        if total > Decimal("0"):
            assert exclusion == total
            assert deferral == Decimal("0")

    def test_mixed_deferral_and_exclusion(self, iso_engine):
        """Both deferral and exclusion items -> split proportionally."""
        total, deferral, exclusion = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("30000"),
            amt_adjustments=Decimal("20000"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("90000"),
        )
        if total > Decimal("0"):
            assert deferral + exclusion == total
            assert deferral >= Decimal("0")
            assert exclusion >= Decimal("0")

    def test_no_amt_owed(self, iso_engine):
        """When TMT < regular tax, all components are zero."""
        total, deferral, exclusion = iso_engine.compute_amt_liability(
            taxable_income=Decimal("200000"),
            amt_preference_items=Decimal("1000"),
            amt_adjustments=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("999999"),
        )
        assert total == Decimal("0")
        assert deferral == Decimal("0")
        assert exclusion == Decimal("0")

    def test_mfs_half_threshold(self, iso_engine):
        """MFS uses half the 28% threshold in iso_amt engine too."""
        total_mfs, _, _ = iso_engine.compute_amt_liability(
            taxable_income=Decimal("300000"),
            amt_preference_items=Decimal("50000"),
            amt_adjustments=Decimal("0"),
            filing_status=FilingStatus.MFS,
            tax_year=2024,
            regular_tax=Decimal("0"),
        )
        # MFS threshold is $116,300 (half of $232,600)
        # This means more income hits the 28% rate
        total_single, _, _ = iso_engine.compute_amt_liability(
            taxable_income=Decimal("300000"),
            amt_preference_items=Decimal("50000"),
            amt_adjustments=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            regular_tax=Decimal("0"),
        )
        # MFS exemption ($66,650) is lower than Single ($85,700)
        # and 28% threshold is halved, so MFS AMT should be higher
        assert total_mfs > total_single

    def test_with_ltcg(self, iso_engine):
        """LTCG gets preferential rates under AMT."""
        total_with_ltcg, _, _ = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("50000"),
            amt_adjustments=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            long_term_gains=Decimal("100000"),
            regular_tax=Decimal("0"),
        )
        total_no_ltcg, _, _ = iso_engine.compute_amt_liability(
            taxable_income=Decimal("400000"),
            amt_preference_items=Decimal("50000"),
            amt_adjustments=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
            tax_year=2024,
            long_term_gains=Decimal("0"),
            regular_tax=Decimal("0"),
        )
        # With LTCG, TMT should be lower because LTCG taxed at 15%/20%
        # instead of 26%/28%
        assert total_with_ltcg < total_no_ltcg
