"""Tax-due estimation engine.

Computes federal and California state tax liability using configurable brackets.
"""

from decimal import Decimal

from app.engines.brackets import (
    CA_MENTAL_HEALTH_RATE,
    CA_MENTAL_HEALTH_THRESHOLD,
    CALIFORNIA_BRACKETS,
    CALIFORNIA_STANDARD_DEDUCTION,
    FEDERAL_BRACKETS,
    FEDERAL_STANDARD_DEDUCTION,
    NIIT_RATE,
    NIIT_THRESHOLD,
)
from app.models.enums import FilingStatus
from app.models.reports import TaxEstimate


class TaxEstimator:
    """Estimates federal and California tax liability."""

    def estimate(
        self,
        tax_year: int,
        filing_status: FilingStatus,
        w2_wages: Decimal,
        interest_income: Decimal = Decimal("0"),
        dividend_income: Decimal = Decimal("0"),
        qualified_dividends: Decimal = Decimal("0"),
        short_term_gains: Decimal = Decimal("0"),
        long_term_gains: Decimal = Decimal("0"),
        federal_withheld: Decimal = Decimal("0"),
        state_withheld: Decimal = Decimal("0"),
        federal_estimated_payments: Decimal = Decimal("0"),
        state_estimated_payments: Decimal = Decimal("0"),
        itemized_deductions: Decimal | None = None,
    ) -> TaxEstimate:
        """Compute full tax estimate."""
        total_income = w2_wages + interest_income + dividend_income + short_term_gains + long_term_gains
        agi = total_income

        std_ded = FEDERAL_STANDARD_DEDUCTION.get(tax_year, {}).get(filing_status, Decimal("15000"))
        deduction_used = max(itemized_deductions or Decimal("0"), std_ded)
        taxable_income = max(agi - deduction_used, Decimal("0"))

        # Federal ordinary income tax (simplified — taxes all income at ordinary rates)
        ordinary_taxable = max(taxable_income - long_term_gains - qualified_dividends, Decimal("0"))
        federal_regular = self.compute_federal_tax(ordinary_taxable, filing_status, tax_year)
        ltcg_plus_qdiv = long_term_gains + qualified_dividends
        federal_ltcg = self.compute_ltcg_tax(ltcg_plus_qdiv, taxable_income, filing_status, tax_year)
        investment_income = interest_income + dividend_income + short_term_gains + long_term_gains
        federal_niit = self.compute_niit(investment_income, agi, filing_status)
        federal_amt = Decimal("0")  # TODO: integrate with ISOAMTEngine
        federal_total = federal_regular + federal_ltcg + federal_niit + federal_amt
        federal_balance = federal_total - federal_withheld - federal_estimated_payments

        # California
        ca_std_ded = CALIFORNIA_STANDARD_DEDUCTION.get(tax_year, {}).get(filing_status, Decimal("5540"))
        ca_deduction = max(itemized_deductions or Decimal("0"), ca_std_ded)
        ca_taxable = max(agi - ca_deduction, Decimal("0"))
        ca_tax = self.compute_california_tax(ca_taxable, filing_status, tax_year)
        ca_mh = max(ca_taxable - CA_MENTAL_HEALTH_THRESHOLD, Decimal("0")) * CA_MENTAL_HEALTH_RATE
        ca_total = ca_tax + ca_mh
        ca_balance = ca_total - state_withheld - state_estimated_payments

        return TaxEstimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=w2_wages,
            interest_income=interest_income,
            dividend_income=dividend_income,
            qualified_dividends=qualified_dividends,
            short_term_gains=short_term_gains,
            long_term_gains=long_term_gains,
            total_income=total_income,
            agi=agi,
            standard_deduction=std_ded,
            itemized_deductions=itemized_deductions,
            deduction_used=deduction_used,
            taxable_income=taxable_income,
            federal_regular_tax=federal_regular,
            federal_ltcg_tax=federal_ltcg,
            federal_niit=federal_niit,
            federal_amt=federal_amt,
            federal_total_tax=federal_total,
            federal_withheld=federal_withheld,
            federal_estimated_payments=federal_estimated_payments,
            federal_balance_due=federal_balance,
            ca_taxable_income=ca_taxable,
            ca_tax=ca_tax,
            ca_mental_health_tax=ca_mh,
            ca_total_tax=ca_total,
            ca_withheld=state_withheld,
            ca_estimated_payments=state_estimated_payments,
            ca_balance_due=ca_balance,
            total_tax=federal_total + ca_total,
            total_withheld=federal_withheld + state_withheld,
            total_balance_due=federal_balance + ca_balance,
        )

    def compute_federal_tax(
        self, taxable_income: Decimal, filing_status: FilingStatus, tax_year: int
    ) -> Decimal:
        """Compute federal ordinary income tax using progressive brackets."""
        brackets = FEDERAL_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            raise ValueError(f"No federal brackets for {tax_year}/{filing_status}")
        return self._apply_brackets(taxable_income, brackets)

    def compute_ltcg_tax(
        self,
        ltcg_and_qualified_divs: Decimal,
        taxable_income: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> Decimal:
        """Compute federal LTCG/qualified dividend tax."""
        # TODO: Implement proper LTCG bracket computation
        # Simplified: 15% flat rate on LTCG for now
        _ = taxable_income, filing_status, tax_year
        return ltcg_and_qualified_divs * Decimal("0.15")

    def compute_niit(
        self, investment_income: Decimal, agi: Decimal, filing_status: FilingStatus
    ) -> Decimal:
        """Compute Net Investment Income Tax (3.8%)."""
        threshold = NIIT_THRESHOLD.get(filing_status, Decimal("200000"))
        excess_agi = max(agi - threshold, Decimal("0"))
        niit_base = min(investment_income, excess_agi)
        return niit_base * NIIT_RATE

    def compute_california_tax(
        self, taxable_income: Decimal, filing_status: FilingStatus, tax_year: int
    ) -> Decimal:
        """Compute California state income tax. All income taxed at ordinary rates."""
        brackets = CALIFORNIA_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            raise ValueError(f"No CA brackets for {tax_year}/{filing_status}")
        return self._apply_brackets(taxable_income, brackets)

    @staticmethod
    def _apply_brackets(
        income: Decimal, brackets: list[tuple[Decimal | None, Decimal]]
    ) -> Decimal:
        """Apply progressive tax brackets to income."""
        tax = Decimal("0")
        prev_bound = Decimal("0")

        for upper_bound, rate in brackets:
            if upper_bound is None:
                taxable_in_bracket = max(income - prev_bound, Decimal("0"))
            else:
                taxable_in_bracket = max(min(income, upper_bound) - prev_bound, Decimal("0"))
            tax += taxable_in_bracket * rate
            prev_bound = upper_bound if upper_bound is not None else income
            if upper_bound is not None and income <= upper_bound:
                break

        return tax
