"""Ledger builder: construct lots from equity events and match sales."""

from uuid import uuid4

from app.models.enums import TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale


class LedgerBuilder:
    """Builds acquisition lots from equity events and matches sales to lots."""

    def build_lots(self, events: list[EquityEvent]) -> list[Lot]:
        """Convert vest/exercise/purchase events into acquisition lots."""
        lots: list[Lot] = []
        acquisition_types = {TransactionType.VEST, TransactionType.EXERCISE, TransactionType.PURCHASE}

        for event in events:
            if event.event_type not in acquisition_types:
                continue

            lot = Lot(
                id=str(uuid4()),
                equity_type=event.equity_type,
                security=event.security,
                acquisition_date=event.event_date,
                shares=event.shares,
                cost_per_share=event.price_per_share,
                amt_cost_per_share=event.price_per_share if event.equity_type.value == "ISO" else None,
                shares_remaining=event.shares,
                source_event_id=event.id,
                broker_source=event.broker_source,
            )
            lots.append(lot)

        return lots

    def match_sales(self, lots: list[Lot], sales: list[Sale]) -> list[tuple[Lot, Sale]]:
        """Match each sale to its originating lot."""
        # TODO: Implement lot matching (delegate to LotMatcher for FIFO/specific ID)
        matched: list[tuple[Lot, Sale]] = []
        lot_map = {lot.id: lot for lot in lots}

        for sale in sales:
            if sale.lot_id in lot_map:
                matched.append((lot_map[sale.lot_id], sale))

        return matched
