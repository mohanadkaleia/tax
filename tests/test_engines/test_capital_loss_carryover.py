"""Tests for Capital Loss Carryover per IRC Section 1212(b).

Short-term carryover retains short-term character, long-term retains
long-term character. Netting order follows the Schedule D Capital Loss
Carryover Worksheet:
  1. ST carryover offsets ST gains first, then remaining offsets LT gains.
  2. LT carryover offsets LT gains first, then remaining offsets ST gains.
  3. Net capital loss limited to $3,000/year ($1,500 MFS).
  4. Excess carries forward retaining character.

California conforms to federal capital loss rules (R&TC 17024.5).
"""

from decimal import Decimal

import pytest

from app.engines.estimator import TaxEstimator
from app.models.enums import FilingStatus


@pytest.fixture
def engine():
    return TaxEstimator()


class TestCapitalLossCarryover:
    """Tests for apply_capital_loss_carryover() method."""

    def test_st_carryover_offsets_st_gains(self, engine):
        """$10K ST carryover against $15K ST gains -> ST = $5K, no carryforward."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("15000"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("10000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("5000")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_lt_carryover_offsets_lt_gains(self, engine):
        """$10K LT carryover against $15K LT gains -> LT = $5K, no carryforward."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("15000"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("10000"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("0")
        assert adj_lt == Decimal("5000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_st_carryover_exceeds_st_gains_offsets_lt(self, engine):
        """$20K ST carryover against $5K ST + $10K LT.

        ST carryover first offsets $5K ST -> ST=0, remaining $15K.
        Then offsets $10K LT -> LT=0, remaining $5K.
        Remaining $5K becomes additional ST loss.
        Net = -$5K, limited to -$3K, $2K ST carryforward.
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("5000"),
            long_term_gains=Decimal("10000"),
            st_loss_carryover=Decimal("20000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
        )
        # After applying carryover: ST = -$5K, LT = $0
        # Net = -$5K, limited to -$3K
        assert adj_st == Decimal("-3000")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("2000")
        assert cf_lt == Decimal("0")

    def test_lt_carryover_exceeds_lt_gains_offsets_st(self, engine):
        """$20K LT carryover against $10K ST + $5K LT.

        LT carryover first offsets $5K LT -> LT=0, remaining $15K.
        Then offsets $10K ST -> ST=0, remaining $5K.
        Remaining $5K becomes additional LT loss.
        Net = -$5K, limited to -$3K, $2K LT carryforward.
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("5000"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("20000"),
            filing_status=FilingStatus.SINGLE,
        )
        # After applying carryover: ST = $0, LT = -$5K
        # Net = -$5K, limited to -$3K
        assert adj_st == Decimal("0")
        assert adj_lt == Decimal("-3000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("2000")

    def test_both_carryovers(self, engine):
        """$5K ST + $5K LT carryover against $10K ST + $10K LT.

        ST carryover offsets $5K of ST gains -> ST = $5K.
        LT carryover offsets $5K of LT gains -> LT = $5K.
        Net = $10K, no loss limit applies.
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("10000"),
            st_loss_carryover=Decimal("5000"),
            lt_loss_carryover=Decimal("5000"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("5000")
        assert adj_lt == Decimal("5000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_carryover_with_no_gains(self, engine):
        """$10K ST carryover, $0 gains -> $3K loss deduction, $7K carryforward."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("10000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
        )
        # Carryover makes ST = -$10K
        # Net = -$10K, limited to -$3K, $7K ST carryforward
        assert adj_st == Decimal("-3000")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("7000")
        assert cf_lt == Decimal("0")

    def test_annual_loss_limit_single(self, engine):
        """Net loss after carryover > $3,000 -> capped at $3,000, rest carries forward."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("2000"),
            long_term_gains=Decimal("1000"),
            st_loss_carryover=Decimal("10000"),
            lt_loss_carryover=Decimal("5000"),
            filing_status=FilingStatus.SINGLE,
        )
        # ST carryover: offsets $2K ST -> remaining $8K -> offsets $1K LT -> remaining $7K
        # After ST co: ST = 0, LT = 0, st_co_remaining = 7K
        # LT carryover: LT already 0, ST already 0 -> lt_co_remaining = 5K
        # Apply remaining: ST -= 7K -> ST = -7K, LT -= 5K -> LT = -5K
        # Net = -12K, limited to -3K
        # ST loss = 7K, LT loss = 5K, total = 12K
        # ST deduction = min(7K, 3K) = 3K, LT deduction = 0
        # ST carryforward = 7K - 3K = 4K, LT carryforward = 5K - 0 = 5K
        assert adj_st == Decimal("-3000")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("4000")
        assert cf_lt == Decimal("5000")

    def test_annual_loss_limit_mfs(self, engine):
        """MFS uses $1,500 limit."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("5000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.MFS,
        )
        # Net = -$5K, limited to -$1.5K
        assert adj_st == Decimal("-1500")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("3500")
        assert cf_lt == Decimal("0")

    def test_carryforward_retains_character(self, engine):
        """$15K LT carryover, $0 gains -> carryforward is LT, not ST."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("15000"),
            filing_status=FilingStatus.SINGLE,
        )
        # LT carryover becomes LT loss: LT = -$15K
        # Net = -$15K, limited to -$3K
        # LT carryforward = $15K - $3K = $12K
        assert adj_st == Decimal("0")
        assert adj_lt == Decimal("-3000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("12000")

    def test_zero_carryover_no_effect(self, engine):
        """No carryover -> gains unchanged."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("10000"),
            long_term_gains=Decimal("5000"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("10000")
        assert adj_lt == Decimal("5000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_carryover_with_existing_losses(self, engine):
        """Current year already has net losses + carryover -> combined loss limited.

        Current: ST = -$2K, LT = -$1K (net = -$3K, exactly at limit)
        Carryover: ST = $5K
        After carryover: ST = -$7K (carryover doesn't offset negative gains)
        Net = -$8K, limited to -$3K, $5K ST carryforward
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("-2000"),
            long_term_gains=Decimal("-1000"),
            st_loss_carryover=Decimal("5000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.SINGLE,
        )
        # ST is already negative, so ST carryover has no positive gains to offset
        # ST carryover remaining = $5K, applied to ST: ST = -2K - 5K = -7K
        # LT remains -$1K
        # Net = -$8K, limited to -$3K
        # ST loss = $7K, LT loss = $1K
        # ST deduction = min($7K, $3K) = $3K, LT deduction = $0
        # ST CF = $7K - $3K = $4K, LT CF = $1K - $0 = $1K
        assert adj_st == Decimal("-3000")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("4000")
        assert cf_lt == Decimal("1000")

    def test_user_scenario_lt_carryover(self, engine):
        """User's actual data: ST $75K, LT $470K, LT carryover $25,292.

        Per CPA's Schedule D, the carryover was -$25,292 (all LT).
        LT carryover offsets LT gains first: $470K - $25,292 = $444,708.
        Net remains positive -> no loss limit applies.
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("75000"),
            long_term_gains=Decimal("470000"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("25292"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("75000")
        assert adj_lt == Decimal("444708")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_carryover_exactly_equals_gains(self, engine):
        """$15K ST + $10,292 LT carryover exactly equals gains."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("15000"),
            long_term_gains=Decimal("10292"),
            st_loss_carryover=Decimal("15000"),
            lt_loss_carryover=Decimal("10292"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("0")
        assert adj_lt == Decimal("0")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("0")

    def test_mixed_carryover_with_cross_offset(self, engine):
        """$8K ST carryover, $3K LT carryover against $5K ST, $2K LT.

        ST carryover: offsets $5K ST -> remaining $3K -> offsets $2K LT -> remaining $1K
        After ST co: ST=0, LT=0, st_remaining=$1K
        LT carryover: LT=0 nothing to offset, ST=0 nothing to offset -> lt_remaining=$3K
        Apply remaining: ST -= $1K -> ST = -$1K, LT -= $3K -> LT = -$3K
        Net = -$4K, limited to -$3K, $1K carryforward
        ST loss = $1K, LT loss = $3K
        ST deduction = min($1K, $3K) = $1K, remaining limit = $2K
        LT deduction = min($3K, $2K) = $2K
        ST CF = 0, LT CF = $1K
        """
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("5000"),
            long_term_gains=Decimal("2000"),
            st_loss_carryover=Decimal("8000"),
            lt_loss_carryover=Decimal("3000"),
            filing_status=FilingStatus.SINGLE,
        )
        assert adj_st == Decimal("-1000")
        assert adj_lt == Decimal("-2000")
        assert cf_st == Decimal("0")
        assert cf_lt == Decimal("1000")

    def test_hoh_uses_3000_limit(self, engine):
        """HOH uses $3,000 limit, same as single."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("5000"),
            lt_loss_carryover=Decimal("0"),
            filing_status=FilingStatus.HOH,
        )
        assert adj_st == Decimal("-3000")
        assert cf_st == Decimal("2000")

    def test_mfj_uses_3000_limit(self, engine):
        """MFJ uses $3,000 limit."""
        adj_st, adj_lt, cf_st, cf_lt = engine.apply_capital_loss_carryover(
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            st_loss_carryover=Decimal("0"),
            lt_loss_carryover=Decimal("8000"),
            filing_status=FilingStatus.MFJ,
        )
        assert adj_lt == Decimal("-3000")
        assert cf_lt == Decimal("5000")


class TestCarryoverInFullEstimate:
    """Tests that carryover integrates correctly into the full tax estimate."""

    def test_carryover_reduces_taxable_income(self, engine):
        """Full estimate with carryover shows lower taxable income."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("50000"),
            federal_withheld=Decimal("50000"),
            state_withheld=Decimal("15000"),
        )
        with_co = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("50000"),
            federal_withheld=Decimal("50000"),
            state_withheld=Decimal("15000"),
            lt_loss_carryover=Decimal("20000"),
        )
        # LT carryover reduces LT gains by $20K
        assert with_co.long_term_gains == Decimal("30000")
        assert with_co.taxable_income < without.taxable_income
        assert with_co.total_income < without.total_income

    def test_carryover_reduces_niit(self, engine):
        """Carryover reduces investment income for NIIT computation."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("50000"),
            state_withheld=Decimal("15000"),
        )
        with_co = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("50000"),
            state_withheld=Decimal("15000"),
            lt_loss_carryover=Decimal("50000"),
        )
        # NIIT should be lower because LT gains are reduced
        assert with_co.federal_niit < without.federal_niit

    def test_carryover_reduces_ltcg_tax(self, engine):
        """LT carryover specifically reduces LTCG tax."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            long_term_gains=Decimal("50000"),
            federal_withheld=Decimal("30000"),
            state_withheld=Decimal("10000"),
        )
        with_co = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            long_term_gains=Decimal("50000"),
            federal_withheld=Decimal("30000"),
            state_withheld=Decimal("10000"),
            lt_loss_carryover=Decimal("25000"),
        )
        assert with_co.federal_ltcg_tax < without.federal_ltcg_tax

    def test_carryover_fields_populated(self, engine):
        """TaxEstimate has carryover fields populated."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("50000"),
            federal_withheld=Decimal("50000"),
            state_withheld=Decimal("15000"),
            lt_loss_carryover=Decimal("20000"),
        )
        # The LT carryover was applied to reduce LT gains
        assert result.lt_loss_carryover_applied > Decimal("0")
        assert result.lt_loss_carryforward == Decimal("0")

    def test_carryover_with_resulting_carryforward(self, engine):
        """Large carryover that produces a new carryforward."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            short_term_gains=Decimal("0"),
            long_term_gains=Decimal("0"),
            federal_withheld=Decimal("20000"),
            state_withheld=Decimal("5000"),
            lt_loss_carryover=Decimal("10000"),
        )
        # No gains to offset, so $10K LT carryover becomes a $10K LT loss
        # Limited to $3K deduction, $7K LT carryforward
        assert result.lt_loss_carryforward == Decimal("7000")
        assert result.long_term_gains == Decimal("-3000")

    def test_zero_carryover_no_fields(self, engine):
        """No carryover -> carryover fields all zero."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            federal_withheld=Decimal("20000"),
            state_withheld=Decimal("5000"),
        )
        assert result.st_loss_carryover_applied == Decimal("0")
        assert result.lt_loss_carryover_applied == Decimal("0")
        assert result.st_loss_carryforward == Decimal("0")
        assert result.lt_loss_carryforward == Decimal("0")

    def test_carryover_reduces_federal_total_tax(self, engine):
        """Carryover should reduce federal total tax."""
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
        )
        with_co = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            lt_loss_carryover=Decimal("25292"),
        )
        assert with_co.federal_total_tax < without.federal_total_tax

    def test_carryover_also_reduces_ca_tax(self, engine):
        """CA conforms to federal capital loss rules (R&TC 17024.5).

        Since carryover is applied before estimate(), the adjusted gains
        flow into the CA computation as well.
        """
        without = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
        )
        with_co = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            long_term_gains=Decimal("100000"),
            federal_withheld=Decimal("60000"),
            state_withheld=Decimal("20000"),
            lt_loss_carryover=Decimal("25292"),
        )
        assert with_co.ca_total_tax < without.ca_total_tax

    def test_st_carryover_in_full_estimate(self, engine):
        """ST carryover reduces ST gains in full estimate."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("100000"),
            short_term_gains=Decimal("20000"),
            long_term_gains=Decimal("10000"),
            federal_withheld=Decimal("25000"),
            state_withheld=Decimal("8000"),
            st_loss_carryover=Decimal("5000"),
        )
        assert result.short_term_gains == Decimal("15000")
        assert result.st_loss_carryover_applied > Decimal("0")
