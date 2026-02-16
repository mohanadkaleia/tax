"""Reconciliation engine — the core of TaxBot 9000.

Matches 1099-B sales to acquisition lots, corrects cost basis per equity type
(RSU, NSO, ESPP, ISO), and generates Form 8949 adjustments.

Per IRS Instructions for Form 8949 and Pub. 525.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from app.db.repository import TaxRepository
from app.engines.basis import BasisCorrectionEngine
from app.engines.espp import ESPPEngine
from app.engines.iso_amt import ISOAMTEngine
from app.engines.lot_matcher import LotMatcher
from app.exceptions import MissingEventDataError
from app.models.enums import AdjustmentCode, BrokerSource, EquityType, HoldingPeriod, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult, Security
from app.models.reports import AuditEntry
from app.models.tax_forms import Form3921, Form3922


class ReconciliationEngine:
    """Orchestrates sale-to-lot matching, basis correction, and Form 8949 generation."""

    def __init__(self, repo: TaxRepository):
        self.repo = repo
        self.basis_engine = BasisCorrectionEngine()
        self.espp_engine = ESPPEngine()
        self.iso_engine = ISOAMTEngine()
        self.lot_matcher = LotMatcher()
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def reconcile(self, tax_year: int) -> dict:
        """Run full reconciliation for a tax year.

        Steps:
        1. Clear previous results for idempotent re-runs
        2. Load all sales, lots, and events from the database
        3. Match each sale to acquisition lots (FIFO or specific)
        4. Correct basis per equity type (RSU, NSO, ESPP, ISO)
        5. Save SaleResults and update lot shares_remaining
        6. Record reconciliation run summary

        Returns:
            dict with run summary (totals, warnings, errors).
        """
        self.warnings = []
        self.errors = []

        # 1. Clear previous results for idempotent re-runs
        self.repo.clear_sale_results(tax_year)
        self.repo.delete_auto_created_lots()
        self.repo.reset_lot_shares()

        # 2. Load data
        sales = self._load_sales(tax_year)
        lots = self._load_lots()
        events = self._load_events()

        if not sales:
            self.warnings.append(f"No sales found for tax year {tax_year}")
            run = self._build_run_summary(tax_year, [], 0, 0)
            self.repo.save_reconciliation_run(run)
            return run

        # 3-5. Process each sale
        results: list[SaleResult] = []
        matched = 0
        passthrough = 0
        unmatched = 0

        for sale in sales:
            sale_results = self._process_sale(sale, lots, events)
            if sale_results:
                # Distinguish pass-through (lot_id=None) from real matches
                if any(r.lot_id is None for r in sale_results):
                    passthrough += 1
                else:
                    matched += 1
                results.extend(sale_results)
            else:
                unmatched += 1

        # 6. Build and save summary
        run = self._build_run_summary(
            tax_year, results, matched, unmatched, passthrough
        )
        self.repo.save_reconciliation_run(run)

        # Audit log
        self.repo.save_audit_entry(AuditEntry(
            timestamp=datetime.now(),
            engine="ReconciliationEngine",
            operation="reconcile",
            inputs={"tax_year": tax_year},
            output={
                "total_sales": len(sales),
                "matched": matched,
                "passthrough": passthrough,
                "unmatched": unmatched,
                "total_gain_loss": run.get("total_gain_loss"),
            },
        ))

        return run

    def _process_sale(
        self, sale: Sale, lots: list[Lot], events: list[dict]
    ) -> list[SaleResult]:
        """Process a single sale: match to lots and correct basis."""
        # Find matching lots by ticker
        matching_lots = [
            lot for lot in lots
            if lot.security.ticker == sale.security.ticker
            and lot.shares_remaining > 0
        ]

        # Fuzzy match if exact ticker didn't work (common for UNKNOWN tickers)
        if not matching_lots:
            matching_lots = self.lot_matcher.match_fuzzy(lots, sale)

        # ESPP/ISO priority matching: before the generic RSU flow, check if
        # this sale's date_acquired matches an ESPP or ISO lot.  ESPP and ISO
        # sales require special basis correction (ordinary income, AMT
        # preference) that the generic auto-create/RSU path cannot provide.
        if isinstance(sale.date_acquired, date) and matching_lots:
            espp_iso_result = self._try_espp_iso_match(
                sale, matching_lots, events
            )
            if espp_iso_result is not None:
                return espp_iso_result

        if not matching_lots:
            # No lots found — try auto-creating a lot from 1099-B data.
            # This handles RSU sales where no lot was imported (e.g. pre-IPO vests,
            # or dates outside the Shareworks PDF range).
            auto_lot = self._auto_create_lot(sale, existing_lots=lots)
            if auto_lot:
                lots.append(auto_lot)
                matching_lots = [auto_lot]
            else:
                # Fall back to pass-through for "Various" dates, missing basis, etc.
                return self._passthrough_sale(sale)

        # Determine shares to allocate
        sale_for_match = sale
        if sale.shares == 0:
            # 1099-B import: shares unknown, proceeds_per_share holds total proceeds
            # Try to infer shares from available lots
            sale_for_match = self._infer_sale_shares(sale, matching_lots)
            if sale_for_match.shares == 0:
                # Could not infer shares from existing lots (no lot matches
                # the sale's date_acquired). Try auto-creating a lot from
                # 1099-B data — this handles pre-IPO vests, dates outside
                # the Shareworks PDF range, etc.
                auto_lot = self._auto_create_lot(sale, existing_lots=matching_lots)
                if auto_lot:
                    lots.append(auto_lot)
                    matching_lots = [auto_lot]
                    sale_for_match = self._infer_sale_shares(sale, [auto_lot])
                    if sale_for_match.shares == 0:
                        return self._passthrough_sale(sale)
                else:
                    return self._passthrough_sale(sale)

        # Match sale to lots
        if sale_for_match.lot_id:
            allocations = self.lot_matcher.match(
                matching_lots, sale_for_match, method="SPECIFIC"
            )
        else:
            # If sale has a specific date_acquired, prefer lots from that date
            fifo_lots = matching_lots
            if isinstance(sale_for_match.date_acquired, date):
                date_lots = [
                    lot for lot in matching_lots
                    if lot.acquisition_date == sale_for_match.date_acquired
                    and lot.shares_remaining > 0
                ]
                if date_lots:
                    fifo_lots = date_lots
            allocations = self.lot_matcher.match(
                fifo_lots, sale_for_match, method="FIFO"
            )

        if not allocations:
            self.warnings.append(
                f"Could not allocate sale {sale.id} to any lot"
            )
            return []

        # Process each allocation
        # Use sale_for_match which has corrected per-share proceeds when
        # shares were inferred from 1099-B total proceeds
        effective_sale = sale_for_match if sale_for_match is not sale else sale
        results: list[SaleResult] = []
        total_allocated = sum(shares for _, shares in allocations)

        for lot, shares_allocated in allocations:
            # Build a sub-sale for this allocation
            if total_allocated > 0 and len(allocations) > 1:
                # Split broker-reported basis proportionally
                ratio = shares_allocated / total_allocated
                sub_broker_basis = (
                    (effective_sale.broker_reported_basis * ratio)
                    if effective_sale.broker_reported_basis
                    else None
                )
            else:
                sub_broker_basis = effective_sale.broker_reported_basis

            sub_sale = Sale(
                id=effective_sale.id,
                lot_id=lot.id,
                security=effective_sale.security,
                sale_date=effective_sale.sale_date,
                shares=shares_allocated,
                proceeds_per_share=effective_sale.proceeds_per_share,
                broker_reported_basis=sub_broker_basis,
                basis_reported_to_irs=effective_sale.basis_reported_to_irs,
                broker_source=effective_sale.broker_source,
            )

            try:
                result = self._correct_basis(lot, sub_sale, events)
                results.append(result)
                self.repo.save_sale_result(result)
            except Exception as exc:
                self.errors.append(
                    f"Basis correction failed for sale {sale.id} "
                    f"(lot {lot.id}): {exc}"
                )
                continue

            # Update lot shares remaining
            lot.shares_remaining -= shares_allocated
            self.repo.update_lot_shares_remaining(lot.id, lot.shares_remaining)

        return results

    def _try_espp_iso_match(
        self, sale: Sale, matching_lots: list[Lot], events: list[dict]
    ) -> list[SaleResult] | None:
        """Try to match a sale to an ESPP or ISO lot before the generic RSU flow.

        ESPP and ISO sales require special basis correction (ordinary income,
        AMT preference).  If we can identify the sale as ESPP/ISO by matching
        its date_acquired to an ESPP/ISO lot, we should prioritize that lot
        over any RSU lots for the same date.

        Returns list of SaleResult if matched, None to fall through to generic flow.
        """
        if not isinstance(sale.date_acquired, date):
            return None

        # Find ESPP/ISO lots matching this sale's date_acquired
        priority_lots = [
            lot for lot in matching_lots
            if lot.equity_type in (EquityType.ESPP, EquityType.ISO)
            and lot.acquisition_date == sale.date_acquired
            and lot.shares_remaining > 0
        ]

        if not priority_lots:
            return None

        # For each candidate lot, try to infer shares using event data
        for lot in priority_lots:
            event = self._find_source_event(lot, events)
            if not event:
                continue

            if lot.equity_type == EquityType.ESPP:
                result = self._try_espp_lot_match(sale, lot, event, events)
                if result is not None:
                    return result
            elif lot.equity_type == EquityType.ISO:
                result = self._try_iso_lot_match(sale, lot, event, events)
                if result is not None:
                    return result

        return None  # Fall through to generic flow

    def _try_espp_lot_match(
        self, sale: Sale, lot: Lot, event: dict, events: list[dict]
    ) -> list[SaleResult] | None:
        """Try to match a sale to a specific ESPP lot using event data.

        For ESPP sales, the broker typically reports FMV at purchase date as
        the cost basis on 1099-B.  We use FMV (event.price_per_share) to infer
        shares when shares=0 (manual 1099-B import).

        Returns list of SaleResult on success, None on failure.
        """
        fmv_at_purchase = Decimal(event["price_per_share"])
        if fmv_at_purchase <= 0:
            return None

        inferred_shares = self._infer_equity_shares(
            sale, fmv_at_purchase, lot.shares_remaining
        )
        if inferred_shares is None:
            return None

        return self._build_priority_match_result(
            sale, lot, inferred_shares, "ESPP", events
        )

    def _try_iso_lot_match(
        self, sale: Sale, lot: Lot, event: dict, events: list[dict]
    ) -> list[SaleResult] | None:
        """Try to match a sale to a specific ISO lot using event data.

        For ISO sales, the broker typically reports the strike (exercise) price
        as the cost basis on 1099-B.  We use lot.cost_per_share (strike price)
        to infer shares when shares=0 (manual 1099-B import).

        Returns list of SaleResult on success, None on failure.
        """
        strike = lot.cost_per_share
        if strike <= 0:
            return None

        inferred_shares = self._infer_equity_shares(
            sale, strike, lot.shares_remaining
        )
        if inferred_shares is None:
            return None

        return self._build_priority_match_result(
            sale, lot, inferred_shares, "ISO", events
        )

    def _infer_equity_shares(
        self,
        sale: Sale,
        reference_price: Decimal,
        max_shares: Decimal,
    ) -> int | None:
        """Infer share count for a sale using a reference price.

        When a 1099-B import has shares=0, we infer the share count by
        dividing broker_reported_basis by the reference price (FMV for ESPP,
        strike price for ISO).

        Returns inferred share count, or None if inference fails.
        """
        if sale.shares > 0:
            inferred = int(sale.shares)
        elif sale.broker_reported_basis and sale.broker_reported_basis > 0:
            inferred = int(round(sale.broker_reported_basis / reference_price))
        else:
            # Can't infer shares -- use lot's total remaining
            inferred = int(max_shares)

        if inferred <= 0:
            return None
        if inferred > int(max_shares):
            # More shares than lot has -- could be partial lot match;
            # cap at lot's remaining shares.
            inferred = int(max_shares)

        return inferred

    def _build_priority_match_result(
        self,
        sale: Sale,
        lot: Lot,
        inferred_shares: int,
        equity_label: str,
        events: list[dict],
    ) -> list[SaleResult] | None:
        """Build a SaleResult by matching a sale to a priority ESPP/ISO lot.

        Creates a sub-sale with the inferred share count and correct per-share
        proceeds, then runs basis correction via _correct_basis().

        Returns list of SaleResult on success, None on failure.
        """
        # For 1099-B imports, proceeds_per_share holds total proceeds
        total_proceeds = (
            sale.proceeds_per_share if sale.shares == 0 else sale.total_proceeds
        )
        per_share_proceeds = total_proceeds / Decimal(str(inferred_shares))

        sub_sale = Sale(
            id=sale.id,
            lot_id=lot.id,
            security=sale.security,
            date_acquired=sale.date_acquired,
            sale_date=sale.sale_date,
            shares=Decimal(str(inferred_shares)),
            proceeds_per_share=per_share_proceeds,
            broker_reported_basis=sale.broker_reported_basis,
            basis_reported_to_irs=sale.basis_reported_to_irs,
            broker_source=sale.broker_source,
        )

        try:
            result = self._correct_basis(lot, sub_sale, events)
            self.repo.save_sale_result(result)
            lot.shares_remaining -= Decimal(str(inferred_shares))
            self.repo.update_lot_shares_remaining(lot.id, lot.shares_remaining)
            return [result]
        except Exception as exc:
            self.errors.append(
                f"{equity_label} basis correction failed for sale {sale.id} "
                f"(lot {lot.id}): {exc}"
            )
            return None

    def _correct_basis(
        self, lot: Lot, sale: Sale, events: list[dict]
    ) -> SaleResult:
        """Dispatch to appropriate basis correction based on equity type."""
        match lot.equity_type:
            case EquityType.RSU:
                return self.basis_engine.correct_rsu_basis(lot, sale)
            case EquityType.NSO:
                return self.basis_engine.correct_nso_basis(lot, sale)
            case EquityType.ESPP:
                event = self._find_source_event(lot, events)
                if not event:
                    raise MissingEventDataError(lot.id, "ESPP")
                form3922 = self._event_to_form3922(event, lot)
                return self.basis_engine.correct_espp_basis(lot, sale, form3922)
            case EquityType.ISO:
                event = self._find_source_event(lot, events)
                if not event:
                    raise MissingEventDataError(lot.id, "ISO")
                form3921 = self._event_to_form3921(event, lot)
                return self.basis_engine.correct_iso_basis(lot, sale, form3921)
            case _:
                raise ValueError(f"Unknown equity type: {lot.equity_type}")

    def _passthrough_sale(self, sale: Sale) -> list[SaleResult]:
        """Create a SaleResult directly from 1099-B data without lot matching.

        Used when no acquisition lots are found but the 1099-B provides both
        proceeds and broker-reported basis. Acceptable for RSU sales where the
        broker already reported the correct cost basis to the IRS.

        ESPP and ISO sales are blocked — they require lot data for correct
        ordinary income and AMT computation.
        """
        # Fix 1 (CRITICAL): Block ESPP/ISO from pass-through
        sale_desc = (sale.security.name or "").upper()
        if any(kw in sale_desc for kw in ("ESPP", "EMPLOYEE STOCK PURCHASE")):
            self.errors.append(
                f"Sale {sale.id} ({sale.security.name} on {sale.sale_date}): "
                f"Appears to be ESPP — cannot use pass-through. "
                f"Import Form 3922 data first to create ESPP lots."
            )
            return []
        if any(kw in sale_desc for kw in ("ISO", "INCENTIVE STOCK OPTION")):
            self.errors.append(
                f"Sale {sale.id} ({sale.security.name} on {sale.sale_date}): "
                f"Appears to be ISO — cannot use pass-through. "
                f"Import Form 3921 data first to create ISO lots."
            )
            return []

        # Must have broker-reported basis to pass through
        if sale.broker_reported_basis is None:
            self.warnings.append(
                f"No lots found and no broker basis for sale {sale.id} "
                f"({sale.security.name} on {sale.sale_date})"
            )
            return []

        # proceeds_per_share actually stores total proceeds for 1099-B imports
        proceeds = sale.proceeds_per_share if sale.shares == 0 else sale.total_proceeds
        broker_basis = sale.broker_reported_basis
        gain_loss = proceeds - broker_basis

        # Fix 5: Use self.basis_engine instead of throwaway instances
        # Determine holding period from date_acquired
        if isinstance(sale.date_acquired, date):
            holding = self.basis_engine._holding_period(
                sale.date_acquired, sale.sale_date
            )
        else:
            # "Various" or None — default to short-term (conservative)
            holding = HoldingPeriod.SHORT_TERM
            if sale.date_acquired and str(sale.date_acquired).lower() == "various":
                self.warnings.append(
                    f"Sale {sale.id}: date_acquired is 'Various', "
                    f"defaulting to short-term. Review manually."
                )

        category = self.basis_engine._form_8949_category(
            holding, sale.basis_reported_to_irs, sale.form_1099b_received
        )

        # Fix 6: Determine adjustment code and amount consistently
        adj_amount = Decimal("0")
        if sale.wash_sale_disallowed > 0:
            adj_amount = sale.wash_sale_disallowed
            adj_code = AdjustmentCode.W
        elif not sale.basis_reported_to_irs:
            adj_code = AdjustmentCode.B
            self.warnings.append(
                f"Sale {sale.id}: basis not reported to IRS and no lot data "
                f"available for correction. Using broker basis ${broker_basis:,.2f}. "
                f"Verify this is correct."
            )
        else:
            adj_code = AdjustmentCode.NONE

        # Fix 4: Use sentinel date instead of sale_date for unknown acquisition
        if isinstance(sale.date_acquired, date):
            acq_date = sale.date_acquired
            acq_note = ""
        else:
            acq_date = date(1, 1, 1)  # Sentinel: unknown acquisition date
            acq_note = " (acquisition date unknown)"

        result = SaleResult(
            sale_id=sale.id,
            lot_id=None,  # No lot matched — pass-through
            security=sale.security,
            acquisition_date=acq_date,
            sale_date=sale.sale_date,
            shares=sale.shares,  # Fix 2: Keep actual shares (0 = unknown)
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=broker_basis,  # Accept broker basis for pass-through
            adjustment_amount=adj_amount,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=gain_loss,
            wash_sale_disallowed=sale.wash_sale_disallowed,
            notes=f"Pass-through: basis from 1099-B (no lot matching){acq_note}",
        )

        try:
            self.repo.save_sale_result(result)
        except Exception as exc:
            self.errors.append(f"Failed to save pass-through result for sale {sale.id}: {exc}")
            return []

        return [result]

    def _auto_create_lot(self, sale: Sale, existing_lots: list[Lot] | None = None) -> Lot | None:
        """Auto-create an RSU lot from 1099-B sale data when no lot exists.

        When the 1099-B provides date_acquired and broker_reported_basis but
        no acquisition lot exists in the database (e.g. pre-IPO RSU vests,
        or dates outside the Shareworks PDF range), create a synthetic lot
        so the sale flows through proper basis correction.

        Returns the created Lot, or None if preconditions aren't met.
        """
        # Must have a valid date_acquired and broker_reported_basis
        if not isinstance(sale.date_acquired, date):
            return None
        if sale.broker_reported_basis is None:
            return None

        # Block ESPP/ISO — they need proper event data (Form 3922/3921)
        sale_desc = (sale.security.name or "").upper()
        if any(kw in sale_desc for kw in ("ESPP", "EMPLOYEE STOCK PURCHASE")):
            return None
        if any(kw in sale_desc for kw in ("ISO", "INCENTIVE STOCK OPTION")):
            return None

        # Don't auto-create RSU lots when ESPP/ISO lots exist for the same
        # ticker + date.  The sale likely belongs to the ESPP/ISO lot but
        # _try_espp_iso_match() could not infer shares (bad data, etc.).
        # Auto-creating an RSU lot would mask the ESPP/ISO sale and suppress
        # ordinary income computation.
        if existing_lots and isinstance(sale.date_acquired, date):
            has_espp_iso = any(
                lot for lot in existing_lots
                if lot.equity_type in (EquityType.ESPP, EquityType.ISO)
                and lot.acquisition_date == sale.date_acquired
            )
            if has_espp_iso:
                self.warnings.append(
                    f"Sale {sale.id}: date {sale.date_acquired.isoformat()} "
                    f"matches an ESPP/ISO lot but shares could not be "
                    f"determined. Review manually — not auto-creating RSU lot."
                )
                return None

        # Determine shares and cost_per_share
        if sale.shares > 0:
            shares = sale.shares
            cost_per_share = sale.broker_reported_basis / shares
        else:
            # 1099-B with unknown shares: treat entire basis as 1 unit
            shares = Decimal("1")
            cost_per_share = sale.broker_reported_basis

        event_id = str(uuid4())
        event = EquityEvent(
            id=event_id,
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=sale.security,
            event_date=sale.date_acquired,
            shares=shares,
            price_per_share=cost_per_share,
            broker_source=sale.broker_source,
        )

        lot = Lot(
            id=str(uuid4()),
            equity_type=EquityType.RSU,
            security=sale.security,
            acquisition_date=sale.date_acquired,
            shares=shares,
            cost_per_share=cost_per_share,
            shares_remaining=shares,
            source_event_id=event_id,
            broker_source=sale.broker_source,
            notes=f"Auto-created from 1099-B data (sale {sale.id})",
        )

        # Persist to database
        self.repo.save_event(event)
        self.repo.save_lot(lot)

        self.warnings.append(
            f"Auto-created RSU lot for {sale.security.ticker} "
            f"(acquired {sale.date_acquired.isoformat()}, "
            f"basis ${sale.broker_reported_basis:,.2f}) from 1099-B data."
        )

        return lot

    def _infer_sale_shares(self, sale: Sale, lots: list[Lot]) -> Sale:
        """Try to infer share count for a 1099-B sale with shares=0.

        Strategy (in priority order):
        1. Filter lots by date_acquired matching lot.acquisition_date,
           then compute shares = broker_reported_basis / cost_per_share.
        2. If only one lot candidate total, use its remaining shares.
        3. Otherwise, return original (shares=0 → caller logs warning).
        """
        available_lots = [lot for lot in lots if lot.shares_remaining > 0]

        # For 1099-B imports, proceeds_per_share holds total proceeds.
        # We need to convert to actual per-share when inferring shares.
        total_proceeds = sale.proceeds_per_share  # This is total, not per-share

        # Strategy 1: Match by acquisition date and infer from cost basis
        if isinstance(sale.date_acquired, date) and sale.broker_reported_basis:
            date_lots = [
                lot for lot in available_lots
                if lot.acquisition_date == sale.date_acquired
            ]
            if date_lots and date_lots[0].cost_per_share > 0:
                cost_per_share = date_lots[0].cost_per_share
                inferred = int(round(sale.broker_reported_basis / cost_per_share))
                if inferred > 0:
                    per_share = total_proceeds / Decimal(str(inferred))
                    return Sale(
                        id=sale.id,
                        lot_id=sale.lot_id,
                        security=sale.security,
                        date_acquired=sale.date_acquired,
                        sale_date=sale.sale_date,
                        shares=Decimal(str(inferred)),
                        proceeds_per_share=per_share,
                        broker_reported_basis=sale.broker_reported_basis,
                        basis_reported_to_irs=sale.basis_reported_to_irs,
                        broker_source=sale.broker_source,
                    )

        # Strategy 2: Single lot candidate — use its remaining shares.
        # Only when dates are compatible (lot date matches sale date, or sale
        # has no specific date). Prevents matching a 2020 sale to a 2021 lot.
        if len(available_lots) == 1:
            candidate = available_lots[0]
            date_compatible = (
                not isinstance(sale.date_acquired, date)
                or candidate.acquisition_date == sale.date_acquired
            )
            if date_compatible:
                inferred_shares = candidate.shares_remaining
                per_share = (
                    total_proceeds / inferred_shares
                    if inferred_shares > 0
                    else total_proceeds
                )
                return Sale(
                    id=sale.id,
                    lot_id=sale.lot_id,
                    security=sale.security,
                    date_acquired=sale.date_acquired,
                    sale_date=sale.sale_date,
                    shares=inferred_shares,
                    proceeds_per_share=per_share,
                    broker_reported_basis=sale.broker_reported_basis,
                    basis_reported_to_irs=sale.basis_reported_to_irs,
                    broker_source=sale.broker_source,
                )

        # Can't infer — return original
        return sale

    # --- Data loading helpers ---

    def _load_sales(self, tax_year: int) -> list[Sale]:
        """Load sales from DB and convert to Sale models."""
        rows = self.repo.get_sales(tax_year)
        sales = []
        for row in rows:
            # Parse date_acquired: ISO date string, "Various", or None
            raw_date_acq = row.get("date_acquired")
            if raw_date_acq and raw_date_acq.lower() != "various":
                try:
                    parsed_date_acq: date | str | None = date.fromisoformat(raw_date_acq)
                except ValueError:
                    parsed_date_acq = raw_date_acq
            elif raw_date_acq:
                parsed_date_acq = raw_date_acq  # "Various"
            else:
                parsed_date_acq = None

            sales.append(Sale(
                id=row["id"],
                lot_id=row.get("lot_id") or "",
                security=Security(
                    ticker=row["ticker"],
                    name=row.get("security_name") or row["ticker"],
                ),
                date_acquired=parsed_date_acq,
                sale_date=date.fromisoformat(row["sale_date"]),
                shares=Decimal(row["shares"]),
                proceeds_per_share=Decimal(row["proceeds_per_share"]),
                broker_reported_basis=(
                    Decimal(row["broker_reported_basis"])
                    if row.get("broker_reported_basis")
                    else None
                ),
                broker_reported_basis_per_share=(
                    Decimal(row["broker_reported_basis_per_share"])
                    if row.get("broker_reported_basis_per_share")
                    else None
                ),
                wash_sale_disallowed=Decimal(row.get("wash_sale_disallowed", "0")),
                form_1099b_received=bool(row.get("form_1099b_received", 1)),
                basis_reported_to_irs=bool(row.get("basis_reported_to_irs", 1)),
                broker_source=BrokerSource(row["broker_source"]),
            ))
        return sales

    def _load_lots(self) -> list[Lot]:
        """Load all lots from DB and convert to Lot models."""
        rows = self.repo.get_lots()
        lots = []
        for row in rows:
            lots.append(Lot(
                id=row["id"],
                equity_type=EquityType(row["equity_type"]),
                security=Security(
                    ticker=row["ticker"], name=row["security_name"]
                ),
                acquisition_date=date.fromisoformat(row["acquisition_date"]),
                shares=Decimal(row["shares"]),
                cost_per_share=Decimal(row["cost_per_share"]),
                amt_cost_per_share=(
                    Decimal(row["amt_cost_per_share"])
                    if row.get("amt_cost_per_share")
                    else None
                ),
                shares_remaining=Decimal(row["shares_remaining"]),
                source_event_id=row["source_event_id"],
                broker_source=BrokerSource(row["broker_source"]),
                notes=row.get("notes"),
            ))
        return lots

    def _load_events(self) -> list[dict]:
        """Load all equity events from DB."""
        return self.repo.get_events()

    def _find_source_event(self, lot: Lot, events: list[dict]) -> dict | None:
        """Find the equity event that created a given lot."""
        for event in events:
            if event["id"] == lot.source_event_id:
                return event
        return None

    @staticmethod
    def _event_to_form3922(event: dict, lot: Lot) -> Form3922:
        """Reconstruct Form3922 from an equity event record."""
        return Form3922(
            tax_year=int(event.get("event_date", "0000")[:4]),
            offering_date=date.fromisoformat(event["offering_date"]),
            purchase_date=date.fromisoformat(event["event_date"]),
            fmv_on_offering_date=Decimal(event["fmv_on_offering_date"]),
            fmv_on_purchase_date=Decimal(event["price_per_share"]),
            purchase_price_per_share=Decimal(event["purchase_price"]),
            shares_transferred=Decimal(event["shares"]),
            employer_name=event.get("security_name"),
        )

    @staticmethod
    def _event_to_form3921(event: dict, lot: Lot) -> Form3921:
        """Reconstruct Form3921 from an equity event record."""
        return Form3921(
            tax_year=int(event.get("event_date", "0000")[:4]),
            grant_date=date.fromisoformat(event["grant_date"]),
            exercise_date=date.fromisoformat(event["event_date"]),
            exercise_price_per_share=Decimal(event["strike_price"]),
            fmv_on_exercise_date=Decimal(event["price_per_share"]),
            shares_transferred=Decimal(event["shares"]),
            employer_name=event.get("security_name"),
        )

    def _build_run_summary(
        self,
        tax_year: int,
        results: list[SaleResult],
        matched: int,
        unmatched: int,
        passthrough: int = 0,
    ) -> dict:
        """Build reconciliation run summary from results."""
        total_proceeds = sum(r.proceeds for r in results) if results else Decimal("0")
        total_basis = sum(r.correct_basis for r in results) if results else Decimal("0")
        total_gain = sum(r.gain_loss for r in results) if results else Decimal("0")
        total_ordinary = sum(r.ordinary_income for r in results) if results else Decimal("0")
        total_amt = sum(r.amt_adjustment for r in results) if results else Decimal("0")

        return {
            "id": str(uuid4()),
            "tax_year": tax_year,
            "total_sales": matched + passthrough + unmatched,
            "matched_sales": matched,
            "passthrough_sales": passthrough,
            "unmatched_sales": unmatched,
            "total_proceeds": str(total_proceeds),
            "total_correct_basis": str(total_basis),
            "total_gain_loss": str(total_gain),
            "total_ordinary_income": str(total_ordinary),
            "total_amt_adjustment": str(total_amt),
            "warnings": self.warnings,
            "errors": self.errors,
            "status": "completed" if not self.errors else "completed_with_errors",
        }
