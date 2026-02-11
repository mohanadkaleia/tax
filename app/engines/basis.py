"""Cost-basis correction engine.

Compares broker-reported basis against correct basis for each equity type
and generates Form 8949 adjustments per IRS Instructions for Form 8949.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.models.enums import AdjustmentCode, Form8949Category, HoldingPeriod
from app.models.equity_event import Lot, Sale, SaleResult
from app.models.tax_forms import Form3921, Form3922


class BasisCorrectionEngine:
    """Corrects cost basis for equity compensation sales."""

    def correct(self, lot: Lot, sale: Sale) -> SaleResult:
        """Dispatch to the appropriate basis correction method based on equity type."""
        match lot.equity_type.value:
            case "RSU":
                return self.correct_rsu_basis(lot, sale)
            case "NSO":
                return self.correct_nso_basis(lot, sale)
            case _:
                raise ValueError(f"Use type-specific methods for {lot.equity_type}")

    def correct_rsu_basis(self, lot: Lot, sale: Sale) -> SaleResult:
        """RSU basis correction.

        Per Pub. 525: RSU basis = FMV at vest (lot.cost_per_share).
        Brokers often report $0. Adjustment code B.
        """
        correct_basis = lot.cost_per_share * sale.shares
        proceeds = sale.total_proceeds
        broker_basis = sale.broker_reported_basis or Decimal("0")
        adjustment = correct_basis - broker_basis
        holding = self._holding_period(lot.acquisition_date, sale.sale_date)
        category = self._form_8949_category(holding, sale.basis_reported_to_irs, sale.form_1099b_received)
        adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

        return SaleResult(
            sale_id=sale.id,
            lot_id=lot.id,
            security=lot.security,
            acquisition_date=lot.acquisition_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=correct_basis,
            adjustment_amount=adjustment,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=proceeds - correct_basis,
        )

    def correct_nso_basis(self, lot: Lot, sale: Sale) -> SaleResult:
        """NSO basis correction.

        Per Pub. 525: NSO basis = strike price + ordinary income recognized at exercise.
        The lot.cost_per_share already includes both components.
        """
        correct_basis = lot.cost_per_share * sale.shares
        proceeds = sale.total_proceeds
        broker_basis = sale.broker_reported_basis or Decimal("0")
        adjustment = correct_basis - broker_basis
        holding = self._holding_period(lot.acquisition_date, sale.sale_date)
        category = self._form_8949_category(holding, sale.basis_reported_to_irs, sale.form_1099b_received)
        adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

        return SaleResult(
            sale_id=sale.id,
            lot_id=lot.id,
            security=lot.security,
            acquisition_date=lot.acquisition_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=correct_basis,
            adjustment_amount=adjustment,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=proceeds - correct_basis,
        )

    def correct_espp_basis(self, lot: Lot, sale: Sale, form3922: Form3922) -> SaleResult:
        """ESPP basis correction — delegates income computation to ESPPEngine."""
        # TODO: Integrate with ESPPEngine for ordinary income and basis adjustment
        raise NotImplementedError("ESPP basis correction requires ESPPEngine integration")

    def correct_iso_basis(self, lot: Lot, sale: Sale, form3921: Form3921) -> SaleResult:
        """ISO basis correction — handles regular and AMT basis."""
        # TODO: Implement ISO basis correction with dual-basis tracking
        raise NotImplementedError("ISO basis correction not yet implemented")

    def _holding_period(self, acquisition_date: date, sale_date: date) -> HoldingPeriod:
        """Determine holding period per IRS rules.

        Holding period starts the day AFTER acquisition.
        Long-term = held more than 1 year.
        """
        holding_start = acquisition_date + timedelta(days=1)
        try:
            one_year_later = holding_start.replace(year=holding_start.year + 1)
        except ValueError:
            # Handle Feb 29 -> Feb 28
            one_year_later = holding_start.replace(year=holding_start.year + 1, day=28)

        if sale_date > one_year_later or sale_date == one_year_later:
            return HoldingPeriod.LONG_TERM
        return HoldingPeriod.SHORT_TERM

    def _form_8949_category(
        self,
        holding: HoldingPeriod,
        basis_reported: bool,
        form_1099b_received: bool,
    ) -> Form8949Category:
        """Determine Form 8949 category (A-F)."""
        if holding == HoldingPeriod.SHORT_TERM:
            if not form_1099b_received:
                return Form8949Category.C
            return Form8949Category.A if basis_reported else Form8949Category.B
        else:
            if not form_1099b_received:
                return Form8949Category.F
            return Form8949Category.D if basis_reported else Form8949Category.E
