"""ESPP income computation engine.

Implements qualifying vs. disqualifying disposition logic per
IRS Pub. 525 ("Employee Stock Purchase Plans") and Form 3922 Instructions.
"""

from datetime import date
from decimal import Decimal

from app.models.enums import DispositionType, HoldingPeriod
from app.models.equity_event import Lot, Sale
from app.models.reports import ESPPIncomeLine
from app.models.tax_forms import Form3922


class ESPPEngine:
    """Computes ESPP ordinary income and adjusted basis."""

    def compute_disposition(self, sale: Sale, lot: Lot, form3922: Form3922) -> ESPPIncomeLine:
        """Compute ESPP income for a sale.

        Args:
            sale: The sale transaction.
            lot: The acquisition lot (from ESPP purchase).
            form3922: The Form 3922 for this ESPP purchase.

        Returns:
            ESPPIncomeLine with ordinary income, adjusted basis, and capital gain/loss.
        """
        disposition = self.determine_disposition_type(
            form3922.offering_date, form3922.purchase_date, sale.sale_date
        )
        ordinary_income = self.compute_ordinary_income(form3922, sale, disposition)
        adjusted_basis = (form3922.purchase_price_per_share * sale.shares) + ordinary_income
        proceeds = sale.total_proceeds
        capital_gain_loss = proceeds - adjusted_basis
        holding = self._holding_period(form3922.purchase_date, sale.sale_date)

        return ESPPIncomeLine(
            security=lot.security.ticker,
            offering_date=form3922.offering_date,
            purchase_date=form3922.purchase_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            purchase_price=form3922.purchase_price_per_share * sale.shares,
            fmv_at_purchase=form3922.fmv_on_purchase_date,
            fmv_at_offering=form3922.fmv_on_offering_date,
            sale_proceeds=proceeds,
            disposition_type=disposition,
            ordinary_income=ordinary_income,
            adjusted_basis=adjusted_basis,
            capital_gain_loss=capital_gain_loss,
            holding_period=holding,
        )

    def determine_disposition_type(
        self, offering_date: date, purchase_date: date, sale_date: date
    ) -> DispositionType:
        """Determine if this is a qualifying or disqualifying disposition.

        Qualifying: held > 2 years from offering date AND > 1 year from purchase date.
        """
        two_years_from_offering = self._add_years(offering_date, 2)
        one_year_from_purchase = self._add_years(purchase_date, 1)

        if sale_date > two_years_from_offering and sale_date > one_year_from_purchase:
            return DispositionType.QUALIFYING
        return DispositionType.DISQUALIFYING

    def compute_ordinary_income(
        self, form3922: Form3922, sale: Sale, disposition_type: DispositionType
    ) -> Decimal:
        """Compute ordinary income per Pub. 525.

        Qualifying: lesser of (a) actual gain, (b) discount at offering date.
        Disqualifying: spread at purchase date.
        """
        if disposition_type == DispositionType.QUALIFYING:
            actual_gain = sale.proceeds_per_share - form3922.purchase_price_per_share
            discount_at_offering = form3922.fmv_on_offering_date - form3922.purchase_price_per_share
            per_share_income = min(actual_gain, discount_at_offering)
            # Ordinary income cannot be negative for qualifying dispositions
            per_share_income = max(per_share_income, Decimal("0"))
            return per_share_income * sale.shares
        else:
            # Disqualifying: spread at purchase date
            spread = form3922.fmv_on_purchase_date - form3922.purchase_price_per_share
            return spread * sale.shares

    def _holding_period(self, purchase_date: date, sale_date: date) -> HoldingPeriod:
        one_year_later = self._add_years(purchase_date, 1)
        if sale_date > one_year_later:
            return HoldingPeriod.LONG_TERM
        return HoldingPeriod.SHORT_TERM

    @staticmethod
    def _add_years(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year + years)
        except ValueError:
            return d.replace(year=d.year + years, day=28)
