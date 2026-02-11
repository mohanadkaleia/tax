"""ISO AMT computation engine.

Implements AMT preference item computation per Form 6251 Instructions
and AMT credit carryforward per Form 8801 Instructions.
"""

from decimal import Decimal

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
        preferences: list[AMTWorksheetLine],
        other_income: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> Decimal:
        """Compute tentative AMT liability.

        Per Form 6251: AMTI = regular taxable income + preferences - exemption.
        AMT rates: 26% on first $232,600 (MFJ) / $116,300 (Single), 28% above.
        """
        # TODO: Implement full AMT computation with brackets and phase-outs
        _ = preferences, other_income, filing_status, tax_year
        raise NotImplementedError("AMT liability computation not yet implemented")

    def compute_amt_credit(self, prior_year_amt: Decimal) -> Decimal:
        """Compute minimum tax credit carryforward per Form 8801.

        Prior-year AMT attributable to deferral items (like ISO exercises)
        generates a credit that carries forward indefinitely.
        """
        # TODO: Implement AMT credit computation
        _ = prior_year_amt
        raise NotImplementedError("AMT credit computation not yet implemented")
