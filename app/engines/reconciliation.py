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
from app.models.enums import BrokerSource, EquityType
from app.models.equity_event import Lot, Sale, SaleResult, Security
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
        unmatched = 0

        for sale in sales:
            sale_results = self._process_sale(sale, lots, events)
            if sale_results:
                results.extend(sale_results)
                matched += 1
            else:
                unmatched += 1

        # 6. Build and save summary
        run = self._build_run_summary(tax_year, results, matched, unmatched)
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

        if not matching_lots:
            self.warnings.append(
                f"No lots found for sale {sale.id} "
                f"({sale.security.ticker} on {sale.sale_date})"
            )
            return []

        # Determine shares to allocate
        sale_for_match = sale
        if sale.shares == 0:
            # 1099-B import: shares unknown, proceeds_per_share holds total proceeds
            # Try to infer shares from available lots
            sale_for_match = self._infer_sale_shares(sale, matching_lots)
            if sale_for_match.shares == 0:
                self.warnings.append(
                    f"Cannot infer share count for sale {sale.id} — "
                    "set shares manually or import lot data first"
                )
                return []

        # Match sale to lots
        if sale_for_match.lot_id:
            allocations = self.lot_matcher.match(
                matching_lots, sale_for_match, method="SPECIFIC"
            )
        else:
            allocations = self.lot_matcher.match(
                matching_lots, sale_for_match, method="FIFO"
            )

        if not allocations:
            self.warnings.append(
                f"Could not allocate sale {sale.id} to any lot"
            )
            return []

        # Process each allocation
        results: list[SaleResult] = []
        total_allocated = sum(shares for _, shares in allocations)

        for lot, shares_allocated in allocations:
            # Build a sub-sale for this allocation
            if total_allocated > 0 and len(allocations) > 1:
                # Split broker-reported basis proportionally
                ratio = shares_allocated / total_allocated
                sub_broker_basis = (
                    (sale.broker_reported_basis * ratio)
                    if sale.broker_reported_basis
                    else None
                )
            else:
                sub_broker_basis = sale.broker_reported_basis

            sub_sale = Sale(
                id=sale.id,
                lot_id=lot.id,
                security=sale.security,
                sale_date=sale.sale_date,
                shares=shares_allocated,
                proceeds_per_share=sale.proceeds_per_share,
                broker_reported_basis=sub_broker_basis,
                basis_reported_to_irs=sale.basis_reported_to_irs,
                broker_source=sale.broker_source,
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

    def _infer_sale_shares(self, sale: Sale, lots: list[Lot]) -> Sale:
        """Try to infer share count for a 1099-B sale with shares=0.

        If total proceeds and lot cost_per_share are known, we can infer shares.
        Otherwise, if there's exactly one lot with remaining shares, use those.
        """
        # If only one lot candidate and it has shares, use its remaining shares
        available_lots = [lot for lot in lots if lot.shares_remaining > 0]
        if len(available_lots) == 1:
            return Sale(
                id=sale.id,
                lot_id=sale.lot_id,
                security=sale.security,
                sale_date=sale.sale_date,
                shares=available_lots[0].shares_remaining,
                proceeds_per_share=sale.proceeds_per_share,
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
            sales.append(Sale(
                id=row["id"],
                lot_id=row.get("lot_id") or "",
                security=Security(
                    ticker=row["ticker"],
                    name=row.get("security_name") or row["ticker"],
                ),
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
            "total_sales": matched + unmatched,
            "matched_sales": matched,
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
