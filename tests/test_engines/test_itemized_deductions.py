"""Tests for itemized deduction computation (Schedule A).

Covers SALT cap, medical 7.5% AGI floor, charitable 60% AGI limit,
standard vs. itemized comparison, and California non-conformity
(no SALT cap, no CA income tax deduction on CA return).
"""

from decimal import Decimal

import pytest

from app.engines.estimator import TaxEstimator
from app.models.deductions import ItemizedDeductions
from app.models.enums import FilingStatus


@pytest.fixture
def engine():
    return TaxEstimator()


AGI = Decimal("1200000")  # Typical high-income AGI for these tests


class TestSALTCap:
    def test_salt_cap_single(self, engine):
        """$50,039 state income tax, SINGLE → capped at $10,000."""
        ded = ItemizedDeductions(state_income_tax_paid=Decimal("50039"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_salt_deduction == Decimal("10000")
        assert result.federal_salt_uncapped == Decimal("50039")
        assert result.federal_salt_cap_lost == Decimal("40039")

    def test_salt_cap_mfs(self, engine):
        """MFS cap is $5,000."""
        ded = ItemizedDeductions(state_income_tax_paid=Decimal("50039"))
        result = engine.compute_itemized_deductions(
            ded, AGI, FilingStatus.MFS, 2024
        )
        assert result.federal_salt_deduction == Decimal("5000")
        assert result.federal_salt_cap_lost == Decimal("45039")

    def test_salt_under_cap(self, engine):
        """$8,000 state tax — no cap applied."""
        ded = ItemizedDeductions(state_income_tax_paid=Decimal("8000"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_salt_deduction == Decimal("8000")
        assert result.federal_salt_cap_lost == Decimal("0")

    def test_salt_with_property_tax(self, engine):
        """SALT cap applies to the SUM of income + property tax."""
        ded = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            real_estate_taxes=Decimal("5000"),
        )
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_salt_uncapped == Decimal("55039")
        assert result.federal_salt_deduction == Decimal("10000")
        assert result.federal_salt_cap_lost == Decimal("45039")


class TestMedicalFloor:
    def test_medical_below_floor(self, engine):
        """$30K medical, $400K AGI → floor = $30K, deduction = $0."""
        agi = Decimal("400000")
        ded = ItemizedDeductions(medical_expenses=Decimal("30000"))
        result = engine.compute_itemized_deductions(ded, agi, FilingStatus.SINGLE, 2024)
        assert result.federal_medical_deduction == Decimal("0")

    def test_medical_above_floor(self, engine):
        """$40K medical, $400K AGI → floor = $30K, deduction = $10K."""
        agi = Decimal("400000")
        ded = ItemizedDeductions(medical_expenses=Decimal("40000"))
        result = engine.compute_itemized_deductions(ded, agi, FilingStatus.SINGLE, 2024)
        assert result.federal_medical_deduction == Decimal("10000.000")

    def test_medical_high_income_no_deduction(self, engine):
        """$50K medical on $1.2M AGI → floor = $90K, deduction = $0."""
        ded = ItemizedDeductions(medical_expenses=Decimal("50000"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_medical_deduction == Decimal("0")


class TestCharitableLimit:
    def test_charitable_within_limit(self, engine):
        """$11,118 cash on $1.2M AGI → 60% limit = $720K, fully deductible."""
        ded = ItemizedDeductions(charitable_cash=Decimal("11118"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_charitable_deduction == Decimal("11118")
        assert result.federal_charitable_limited == Decimal("0")

    def test_charitable_exceeds_limit(self, engine):
        """$800K cash on $1.2M AGI → 60% limit = $720K."""
        ded = ItemizedDeductions(charitable_cash=Decimal("800000"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_charitable_deduction == Decimal("720000.00")
        assert result.federal_charitable_limited == Decimal("80000.00")


class TestStandardVsItemized:
    def test_itemized_wins(self, engine):
        """$21,118 itemized > $14,600 standard → use itemized."""
        ded = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            charitable_cash=Decimal("11118"),
        )
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_total_itemized == Decimal("21118")
        assert result.federal_used_itemized is True
        assert result.federal_deduction_used == Decimal("21118")

    def test_standard_wins(self, engine):
        """$5,000 itemized < $14,600 standard → use standard."""
        ded = ItemizedDeductions(state_income_tax_paid=Decimal("5000"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.federal_total_itemized == Decimal("5000")
        assert result.federal_used_itemized is False
        assert result.federal_deduction_used == Decimal("14600")


class TestCaliforniaNonConformity:
    def test_ca_no_salt_cap(self, engine):
        """CA has no SALT cap — but CA income tax is NOT deductible on CA."""
        ded = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            real_estate_taxes=Decimal("15000"),
        )
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        # CA SALT = only property tax (CA income tax excluded per R&TC 17220)
        assert result.ca_salt_deduction == Decimal("15000")
        # Federal SALT = capped at $10K
        assert result.federal_salt_deduction == Decimal("10000")

    def test_ca_no_income_tax_deduction(self, engine):
        """CA resident with only state income tax → CA SALT = $0."""
        ded = ItemizedDeductions(state_income_tax_paid=Decimal("50039"))
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.ca_salt_deduction == Decimal("0")

    def test_ca_different_totals(self, engine):
        """Federal and CA use different itemized totals."""
        ded = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            charitable_cash=Decimal("11118"),
        )
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        # Federal: $10K SALT + $11,118 charitable = $21,118
        assert result.federal_total_itemized == Decimal("21118")
        # CA: $0 SALT + $11,118 charitable = $11,118
        assert result.ca_total_itemized == Decimal("11118")
        # Both use itemized (both exceed their respective standard deductions)
        assert result.federal_used_itemized is True
        assert result.ca_used_itemized is True  # $11,118 > CA standard $5,540

    def test_ca_standard_wins_when_low_charitable(self, engine):
        """CA itemized $3K < CA standard $5,540 → CA uses standard."""
        ded = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            charitable_cash=Decimal("3000"),
        )
        result = engine.compute_itemized_deductions(ded, AGI, FilingStatus.SINGLE, 2024)
        assert result.ca_total_itemized == Decimal("3000")
        assert result.ca_used_itemized is False
        assert result.ca_deduction_used == Decimal("5540")
        # But federal still uses itemized ($10K + $3K = $13K... wait that's < $14,600)
        assert result.federal_total_itemized == Decimal("13000")
        assert result.federal_used_itemized is False  # $13K < $14,600


class TestBackwardCompatibility:
    def test_legacy_decimal_still_works(self, engine):
        """Legacy itemized_deductions=Decimal still works."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            itemized_deductions=Decimal("21118"),
        )
        assert result.deduction_used == Decimal("21118")
        assert result.itemized_detail is None

    def test_no_deductions_uses_standard(self, engine):
        """No deduction input → standard deduction."""
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
        )
        assert result.deduction_used == Decimal("14600")
        assert result.itemized_detail is None


class TestFullEstimateWithItemized:
    def test_2024_cpa_return_scenario(self, engine):
        """Full 2024 scenario matching the CPA-filed return deductions.

        Federal: $10,000 SALT + $11,118 charitable = $21,118 itemized
        CA: $0 SALT + $11,118 charitable = $11,118 itemized
        """
        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            charitable_cash=Decimal("11118"),
        )
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("600000"),
            short_term_gains=Decimal("75000"),
            long_term_gains=Decimal("470000"),
            federal_withheld=Decimal("110000"),
            state_withheld=Decimal("50039"),
            itemized_detail=itemized,
        )
        # Federal deduction should be $21,118 (itemized)
        assert result.itemized_detail is not None
        assert result.itemized_detail.federal_deduction_used == Decimal("21118")
        assert result.itemized_detail.federal_used_itemized is True
        # CA deduction should be $11,118 (itemized, no SALT)
        assert result.itemized_detail.ca_deduction_used == Decimal("11118")
        assert result.itemized_detail.ca_used_itemized is True
        # Federal taxable = AGI - $21,118
        expected_agi = Decimal("1145000")  # 600K + 75K + 470K
        assert result.agi == expected_agi
        assert result.taxable_income == expected_agi - Decimal("21118")

    def test_structured_overrides_legacy(self, engine):
        """When both itemized_detail and itemized_deductions provided,
        structured takes precedence."""
        itemized = ItemizedDeductions(
            state_income_tax_paid=Decimal("50039"),
            charitable_cash=Decimal("11118"),
        )
        result = engine.estimate(
            tax_year=2024,
            filing_status=FilingStatus.SINGLE,
            w2_wages=Decimal("200000"),
            itemized_deductions=Decimal("99999"),  # legacy — should be ignored
            itemized_detail=itemized,
        )
        assert result.itemized_detail is not None
        assert result.deduction_used == Decimal("21118")  # not 99999
