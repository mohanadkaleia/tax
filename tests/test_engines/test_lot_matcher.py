"""Tests for lot matching engine."""

from datetime import date
from decimal import Decimal

from app.engines.lot_matcher import LotMatcher
from app.models.enums import BrokerSource, EquityType
from app.models.equity_event import Lot, Sale, Security


class TestFIFOMatching:
    def setup_method(self):
        self.matcher = LotMatcher()
        self.security = Security(ticker="ACME", name="Acme Corp")

    def test_single_lot_full_match(self):
        lot = Lot(
            id="lot-1",
            equity_type=EquityType.RSU,
            security=self.security,
            acquisition_date=date(2024, 1, 1),
            shares=Decimal("100"),
            cost_per_share=Decimal("150.00"),
            shares_remaining=Decimal("100"),
            source_event_id="evt-1",
            broker_source=BrokerSource.SHAREWORKS,
        )
        sale = Sale(
            id="sale-1",
            lot_id="lot-1",
            security=self.security,
            sale_date=date(2025, 6, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("175.00"),
            broker_source=BrokerSource.SHAREWORKS,
        )
        result = self.matcher.match([lot], sale)
        assert len(result) == 1
        assert result[0][1] == Decimal("100")

    def test_fifo_multiple_lots(self):
        lot1 = Lot(
            id="lot-1",
            equity_type=EquityType.RSU,
            security=self.security,
            acquisition_date=date(2024, 1, 1),
            shares=Decimal("50"),
            cost_per_share=Decimal("100.00"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-1",
            broker_source=BrokerSource.SHAREWORKS,
        )
        lot2 = Lot(
            id="lot-2",
            equity_type=EquityType.RSU,
            security=self.security,
            acquisition_date=date(2024, 6, 1),
            shares=Decimal("50"),
            cost_per_share=Decimal("120.00"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-2",
            broker_source=BrokerSource.SHAREWORKS,
        )
        sale = Sale(
            id="sale-1",
            lot_id="",
            security=self.security,
            sale_date=date(2025, 6, 1),
            shares=Decimal("75"),
            proceeds_per_share=Decimal("175.00"),
            broker_source=BrokerSource.SHAREWORKS,
        )
        result = self.matcher.match([lot2, lot1], sale)  # Pass in wrong order to test sorting
        assert len(result) == 2
        # FIFO: lot1 (older) should be matched first
        assert result[0][0].id == "lot-1"
        assert result[0][1] == Decimal("50")
        assert result[1][0].id == "lot-2"
        assert result[1][1] == Decimal("25")
