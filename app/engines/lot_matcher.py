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

    def match_fuzzy(
        self, lots: list[Lot], sale: Sale
    ) -> list[Lot]:
        """Find lots that might match a sale by security name similarity.

        Used when ticker is UNKNOWN (common for 1099-B imports) and exact
        ticker matching fails. Matches on security name substring overlap.

        Returns:
            List of candidate lots (caller still needs to run FIFO/SPECIFIC).
        """
        sale_name = sale.security.name.upper()
        sale_ticker = sale.security.ticker.upper()
        candidates = []

        for lot in lots:
            if lot.shares_remaining <= 0:
                continue
            lot_name = lot.security.name.upper()
            lot_ticker = lot.security.ticker.upper()

            # Direct ticker match (case-insensitive)
            if lot_ticker == sale_ticker and sale_ticker != "UNKNOWN":
                candidates.append(lot)
                continue

            # Security name substring match
            if sale_name != "UNKNOWN" and sale_name in lot_name:
                candidates.append(lot)
                continue
            if lot_name != "UNKNOWN" and lot_name in sale_name:
                candidates.append(lot)
                continue

            # Word overlap (at least 2 meaningful words in common)
            sale_words = {w for w in sale_name.split() if len(w) > 2}
            lot_words = {w for w in lot_name.split() if len(w) > 2}
            if len(sale_words & lot_words) >= 2:
                candidates.append(lot)

        return candidates
