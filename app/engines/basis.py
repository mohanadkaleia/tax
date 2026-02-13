"""Cost-basis correction engine.

Compares broker-reported basis against correct basis for each equity type
and generates Form 8949 adjustments per IRS Instructions for Form 8949.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.engines.espp import ESPPEngine
from app.models.enums import AdjustmentCode, DispositionType, Form8949Category, HoldingPeriod
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
        """ESPP basis correction per Pub. 525 and Form 3922 Instructions.

        Ordinary income depends on qualifying vs. disqualifying disposition.
        Adjusted basis = purchase price + ordinary income (to avoid double taxation).
        Adjustment code B applies when broker basis is incorrect.
        """
        espp = ESPPEngine()
        disposition = espp.determine_disposition_type(
            form3922.offering_date, form3922.purchase_date, sale.sale_date
        )
        ordinary_income = espp.compute_ordinary_income(form3922, sale, disposition)

        # Correct basis = purchase price + ordinary income recognized
        correct_basis = (form3922.purchase_price_per_share * sale.shares) + ordinary_income
        proceeds = sale.shares * sale.proceeds_per_share
        broker_basis = sale.broker_reported_basis or Decimal("0")
        adjustment = correct_basis - broker_basis
        holding = self._holding_period(lot.acquisition_date, sale.sale_date)
        category = self._form_8949_category(
            holding, sale.basis_reported_to_irs, sale.form_1099b_received
        )
        adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

        # For disqualifying dispositions, capital gain uses adjusted holding period
        # from purchase date (not offering date)
        if disposition == DispositionType.DISQUALIFYING:
            holding = self._holding_period(form3922.purchase_date, sale.sale_date)

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
            ordinary_income=ordinary_income,
            notes=f"ESPP {disposition.value} disposition",
        )

    def correct_iso_basis(self, lot: Lot, sale: Sale, form3921: Form3921) -> SaleResult:
        """ISO basis correction per Pub. 525 and Form 3921 Instructions.

        Regular basis = exercise (strike) price.
        AMT basis = FMV at exercise date.
        AMT adjustment at sale = regular gain - AMT gain.
        """
        # Regular basis = strike price
        regular_basis = lot.cost_per_share * sale.shares
        # AMT basis = FMV at exercise
        amt_basis = (lot.amt_cost_per_share or lot.cost_per_share) * sale.shares

        proceeds = sale.shares * sale.proceeds_per_share
        broker_basis = sale.broker_reported_basis or Decimal("0")
        adjustment = regular_basis - broker_basis
        holding = self._holding_period(lot.acquisition_date, sale.sale_date)
        category = self._form_8949_category(
            holding, sale.basis_reported_to_irs, sale.form_1099b_received
        )
        adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

        # Check for disqualifying disposition (< 2yr from grant or < 1yr from exercise)
        two_years_from_grant = self._add_years(form3921.grant_date, 2)
        one_year_from_exercise = self._add_years(form3921.exercise_date, 1)
        is_disqualifying = (
            sale.sale_date <= two_years_from_grant
            or sale.sale_date <= one_year_from_exercise
        )

        ordinary_income = Decimal("0")
        if is_disqualifying:
            # Ordinary income = lesser of (spread at exercise, actual gain)
            spread = (form3921.fmv_on_exercise_date - form3921.exercise_price_per_share) * sale.shares
            actual_gain = proceeds - regular_basis
            ordinary_income = min(spread, max(actual_gain, Decimal("0")))

        # AMT adjustment: difference between regular and AMT gain
        regular_gain = proceeds - regular_basis
        amt_gain = proceeds - amt_basis
        amt_adjustment = regular_gain - amt_gain  # Negative = reversal of prior preference

        disp_label = "DISQUALIFYING" if is_disqualifying else "QUALIFYING"
        return SaleResult(
            sale_id=sale.id,
            lot_id=lot.id,
            security=lot.security,
            acquisition_date=lot.acquisition_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=regular_basis,
            adjustment_amount=adjustment,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=proceeds - regular_basis - ordinary_income,
            ordinary_income=ordinary_income,
            amt_adjustment=amt_adjustment,
            notes=f"ISO {disp_label} disposition",
        )

    @staticmethod
    def _add_years(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year + years)
        except ValueError:
            return d.replace(year=d.year + years, day=28)

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
