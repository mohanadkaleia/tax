"""Tests for ESPP income computation engine."""

from datetime import date
from decimal import Decimal

from app.engines.espp import ESPPEngine
from app.models.enums import BrokerSource, DispositionType, EquityType
from app.models.equity_event import Lot, Sale, Security
from app.models.tax_forms import Form3922


class TestESPPDisposition:
    def setup_method(self):
        self.engine = ESPPEngine()
        self.security = Security(ticker="ACME", name="Acme Corp")
        self.form3922 = Form3922(
            tax_year=2025,
            offering_date=date(2024, 1, 1),
            purchase_date=date(2024, 6, 30),
            fmv_on_offering_date=Decimal("140.00"),
            fmv_on_purchase_date=Decimal("150.00"),
            purchase_price_per_share=Decimal("127.50"),  # 15% discount from offering FMV
            shares_transferred=Decimal("50"),
        )
        self.lot = Lot(
            id="lot-espp-001",
            equity_type=EquityType.ESPP,
            security=self.security,
            acquisition_date=date(2024, 6, 30),
            shares=Decimal("50"),
            cost_per_share=Decimal("127.50"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-espp-001",
            broker_source=BrokerSource.SHAREWORKS,
        )

    def test_qualifying_disposition(self):
        """Qualifying: held > 2 years from offering AND > 1 year from purchase."""
        sale = Sale(
            id="sale-espp-q-001",
            lot_id="lot-espp-001",
            security=self.security,
            sale_date=date(2026, 7, 1),  # > 2y from offering, > 1y from purchase
            shares=Decimal("50"),
            proceeds_per_share=Decimal("180.00"),
            broker_source=BrokerSource.SHAREWORKS,
        )
        result = self.engine.compute_disposition(sale, self.lot, self.form3922)
        assert result.disposition_type == DispositionType.QUALIFYING
        # Ordinary income = lesser of (actual gain per share, discount at offering)
        # Actual gain = 180 - 127.50 = 52.50
        # Discount at offering = 140 - 127.50 = 12.50
        # Ordinary income = 12.50 * 50 = 625.00
        assert result.ordinary_income == Decimal("625.00")

    def test_disqualifying_disposition(self):
        """Disqualifying: sold < 2 years from offering date."""
        sale = Sale(
            id="sale-espp-d-001",
            lot_id="lot-espp-001",
            security=self.security,
            sale_date=date(2025, 3, 1),  # < 2y from offering, < 1y from purchase
            shares=Decimal("50"),
            proceeds_per_share=Decimal("160.00"),
            broker_source=BrokerSource.SHAREWORKS,
        )
        result = self.engine.compute_disposition(sale, self.lot, self.form3922)
        assert result.disposition_type == DispositionType.DISQUALIFYING
        # Ordinary income = spread at purchase = (150 - 127.50) * 50 = 1125.00
        assert result.ordinary_income == Decimal("1125.00")

    def test_is_qualifying_boundary(self):
        """Test exact boundary dates."""
        # Exactly 2 years from offering and exactly 1 year from purchase — NOT qualifying
        assert not self.engine.determine_disposition_type(
            date(2024, 1, 1), date(2024, 6, 30), date(2026, 1, 1)
        ) == DispositionType.QUALIFYING
