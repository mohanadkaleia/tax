"""Tests for ledger builder."""

from app.models.equity_event import EquityEvent
from app.normalization.ledger import LedgerBuilder


class TestLedgerBuilder:
    def test_build_lots_from_vest(self, sample_vest_event: EquityEvent):
        builder = LedgerBuilder()
        lots = builder.build_lots([sample_vest_event])
        assert len(lots) == 1
        assert lots[0].equity_type == sample_vest_event.equity_type
        assert lots[0].shares == sample_vest_event.shares
