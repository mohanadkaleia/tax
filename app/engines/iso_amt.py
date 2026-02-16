"""ISO AMT computation engine.

Implements AMT preference item computation per Form 6251 Instructions
and AMT credit carryforward per Form 8801 Instructions.
"""

from decimal import Decimal

from app.engines.brackets import (
    AMT_28_PERCENT_THRESHOLD,
    AMT_EXEMPTION,
    AMT_PHASEOUT_START,
    FEDERAL_LTCG_BRACKETS,
)
from app.models.enums import FilingStatus
from app.models.reports import AMTWorksheetLine
from app.models.tax_forms import Form3921


class ISOAMTEngine:
    """Computes ISO AMT preference items and credit carryforwards."""

    def compute_amt_preference(self, form3921: Form3921) -> AMTWorksheetLine:
        """Compute AMT preference item for an ISO exercise.

        Per Form 6251 Line 2i: AMT preference = (FMV at exercise - exercise price) x shares.
        """
        spread = form3921.fmv_on_exercise_date - form3921.exercise_price_per_share
        total_preference = spread * form3921.shares_transferred
        regular_basis = form3921.exercise_price_per_share * form3921.shares_transferred
        amt_basis = form3921.fmv_on_exercise_date * form3921.shares_transferred

        return AMTWorksheetLine(
            security="",  # To be filled by caller
            grant_date=form3921.grant_date,
            exercise_date=form3921.exercise_date,
            shares=form3921.shares_transferred,
            strike_price=form3921.exercise_price_per_share,
            fmv_at_exercise=form3921.fmv_on_exercise_date,
            spread_per_share=spread,
            total_amt_preference=total_preference,
            regular_basis=regular_basis,
            amt_basis=amt_basis,
        )

    def compute_amt_liability(
        self,
        taxable_income: Decimal,
        amt_preference_items: Decimal,
        amt_adjustments: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
        long_term_gains: Decimal = Decimal("0"),
        qualified_dividends: Decimal = Decimal("0"),
        regular_tax: Decimal = Decimal("0"),
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Compute AMT per Form 6251.

        Args:
            taxable_income: Regular taxable income (Form 1040, Line 15).
            amt_preference_items: ISO exercise spreads (deferral items).
            amt_adjustments: SALT add-back, etc. (exclusion items).
            filing_status: Filing status.
            tax_year: Tax year.
            long_term_gains: Net long-term capital gains.
            qualified_dividends: Qualified dividend income.
            regular_tax: Federal tax before AMT (ordinary + LTCG rates).

        Returns:
            (amt_owed, deferral_amt, exclusion_amt)
            amt_owed: total AMT = max(0, TMT - regular_tax)
            deferral_amt: AMT attributable to deferral items (for credit carryforward)
            exclusion_amt: AMT attributable to exclusion items (no credit)
        """
        preferential_income = max(
            long_term_gains + qualified_dividends, Decimal("0")
        )

        # Full AMT with all items
        total_amt, _tmt = self._compute_amt_internal(
            taxable_income=taxable_income,
            amt_additions=amt_preference_items + amt_adjustments,
            preferential_income=preferential_income,
            regular_tax=regular_tax,
            filing_status=filing_status,
            tax_year=tax_year,
        )

        if total_amt <= Decimal("0"):
            return Decimal("0"), Decimal("0"), Decimal("0")

        # Compute AMT without deferral items (only exclusion items)
        # to determine how much AMT is from exclusions vs deferrals
        exclusion_only_amt, _tmt_excl = self._compute_amt_internal(
            taxable_income=taxable_income,
            amt_additions=amt_adjustments,
            preferential_income=preferential_income,
            regular_tax=regular_tax,
            filing_status=filing_status,
            tax_year=tax_year,
        )

        exclusion_amt = max(exclusion_only_amt, Decimal("0"))
        deferral_amt = total_amt - exclusion_amt

        return total_amt, deferral_amt, exclusion_amt

    def _compute_amt_internal(
        self,
        taxable_income: Decimal,
        amt_additions: Decimal,
        preferential_income: Decimal,
        regular_tax: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> tuple[Decimal, Decimal]:
        """Internal AMT computation per Form 6251.

        Returns:
            (amt, tentative_minimum_tax)
        """
        # Step 1: AMTI
        amti = taxable_income + amt_additions

        # Step 2: Exemption with phase-out
        exemption_data = AMT_EXEMPTION.get(tax_year, {})
        phaseout_data = AMT_PHASEOUT_START.get(tax_year, {})
        if not exemption_data or filing_status not in exemption_data:
            return Decimal("0"), Decimal("0")

        exemption_amount = exemption_data[filing_status]
        phaseout_start = phaseout_data[filing_status]
        exemption_reduction = (
            max(amti - phaseout_start, Decimal("0")) * Decimal("0.25")
        )
        amt_exemption = max(exemption_amount - exemption_reduction, Decimal("0"))

        # Step 3: AMT base
        amt_base = max(amti - amt_exemption, Decimal("0"))

        if amt_base == Decimal("0"):
            return Decimal("0"), Decimal("0")

        # Step 4: Compute tentative minimum tax
        # Preferential income gets LTCG rates under AMT (Form 6251 Part III)
        amt_ordinary_base = max(amt_base - preferential_income, Decimal("0"))
        breakpoint = AMT_28_PERCENT_THRESHOLD.get(tax_year, Decimal("232600"))

        # MFS filers use half the 28% threshold per IRC Section 55(b)(1)(A)(i)
        if filing_status == FilingStatus.MFS:
            breakpoint = breakpoint / 2

        if amt_ordinary_base <= breakpoint:
            amt_on_ordinary = amt_ordinary_base * Decimal("0.26")
        else:
            amt_on_ordinary = (
                breakpoint * Decimal("0.26")
                + (amt_ordinary_base - breakpoint) * Decimal("0.28")
            )

        # Preferential income taxed at LTCG rates under AMT
        amt_on_preferential = self._compute_ltcg_tax(
            preferential_income, amt_base, filing_status, tax_year
        )

        tentative_minimum_tax = amt_on_ordinary + amt_on_preferential

        # Step 5: AMT = excess over regular tax
        amt = max(tentative_minimum_tax - regular_tax, Decimal("0"))
        return amt, tentative_minimum_tax

    def _compute_ltcg_tax(
        self,
        ltcg_and_qualified_divs: Decimal,
        taxable_income: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> Decimal:
        """Compute tax on preferential income using LTCG brackets.

        Same stacking method as TaxEstimator.compute_ltcg_tax().
        """
        if ltcg_and_qualified_divs <= Decimal("0"):
            return Decimal("0")

        brackets = FEDERAL_LTCG_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            return ltcg_and_qualified_divs * Decimal("0.15")

        ordinary_income_top = max(
            taxable_income - ltcg_and_qualified_divs, Decimal("0")
        )

        tax = Decimal("0")
        remaining_pref = ltcg_and_qualified_divs
        prev_bound = Decimal("0")

        for upper_bound, rate in brackets:
            if remaining_pref <= Decimal("0"):
                break

            if upper_bound is None:
                tax += remaining_pref * rate
                remaining_pref = Decimal("0")
            else:
                bracket_start = max(prev_bound, ordinary_income_top)
                if bracket_start >= upper_bound:
                    prev_bound = upper_bound
                    continue
                bracket_space = upper_bound - bracket_start
                taxed_here = min(remaining_pref, bracket_space)
                tax += taxed_here * rate
                remaining_pref -= taxed_here
                prev_bound = upper_bound

        return tax

    def compute_amt_credit(
        self,
        prior_year_amt_credit: Decimal,
        regular_tax: Decimal,
        tentative_minimum_tax: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Compute AMT credit per Form 8801.

        The credit is usable when regular tax exceeds TMT (i.e., no current-year AMT).

        Args:
            prior_year_amt_credit: Credit carried forward from prior years
                (AMT attributable to deferral items only).
            regular_tax: Current year's regular tax (ordinary + LTCG).
            tentative_minimum_tax: Current year's tentative minimum tax.

        Returns:
            (credit_used, credit_remaining)
        """
        if prior_year_amt_credit <= Decimal("0"):
            return Decimal("0"), Decimal("0")

        # Credit is limited to the amount by which regular tax exceeds TMT
        credit_limit = max(regular_tax - tentative_minimum_tax, Decimal("0"))
        credit_used = min(prior_year_amt_credit, credit_limit)
        credit_remaining = prior_year_amt_credit - credit_used

        return credit_used, credit_remaining
