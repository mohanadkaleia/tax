"""Lot matching engine: FIFO and specific identification."""

from decimal import Decimal

from app.models.equity_event import Lot, Sale


class LotMatcher:
    """Matches sales to acquisition lots using FIFO or specific identification."""

    def match(
        self,
        lots: list[Lot],
        sale: Sale,
        method: str = "FIFO",
    ) -> list[tuple[Lot, Decimal]]:
        """Match a sale to lots, returning (lot, shares_from_lot) pairs.

        Args:
            lots: Available acquisition lots for the same security.
            sale: The sale to match.
            method: Matching method — "FIFO" or "SPECIFIC".

        Returns:
            List of (lot, shares_allocated) tuples.
        """
        if method == "SPECIFIC":
            return self._match_specific(lots, sale)
        return self._match_fifo(lots, sale)

    def _match_fifo(self, lots: list[Lot], sale: Sale) -> list[tuple[Lot, Decimal]]:
        """FIFO: allocate shares from oldest lots first."""
        sorted_lots = sorted(
            [lot for lot in lots if lot.shares_remaining > 0],
            key=lambda lot_item: lot_item.acquisition_date,
        )
        remaining = sale.shares
        allocations: list[tuple[Lot, Decimal]] = []

        for lot in sorted_lots:
            if remaining <= 0:
                break
            allocated = min(lot.shares_remaining, remaining)
            allocations.append((lot, allocated))
            remaining -= allocated

        return allocations

    def _match_specific(self, lots: list[Lot], sale: Sale) -> list[tuple[Lot, Decimal]]:
        """Specific identification: match sale directly to its designated lot."""
        for lot in lots:
            if lot.id == sale.lot_id and lot.shares_remaining >= sale.shares:
                return [(lot, sale.shares)]
        return []
