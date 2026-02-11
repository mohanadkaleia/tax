"""Tests for Form 8949 report generation."""

from datetime import date
from decimal import Decimal

from app.models.enums import (
    AdjustmentCode,
    Form8949Category,
    HoldingPeriod,
)
from app.models.equity_event import SaleResult, Security
from app.reports.form8949 import Form8949Generator


class TestForm8949Generator:
    def test_generate_lines(self):
        security = Security(ticker="ACME", name="Acme Corp")
        result = SaleResult(
            sale_id="sale-1",
            lot_id="lot-1",
            security=security,
            acquisition_date=date(2024, 3, 15),
            sale_date=date(2025, 6, 1),
            shares=Decimal("100"),
            proceeds=Decimal("17500.00"),
            broker_reported_basis=Decimal("0"),
            correct_basis=Decimal("15000.00"),
            adjustment_amount=Decimal("15000.00"),
            adjustment_code=AdjustmentCode.B,
            holding_period=HoldingPeriod.LONG_TERM,
            form_8949_category=Form8949Category.A,
            gain_loss=Decimal("2500.00"),
        )
        gen = Form8949Generator()
        lines = gen.generate_lines([result])
        assert len(lines) == 1
        assert lines[0].proceeds == Decimal("17500.00")
        assert lines[0].adjustment_code == AdjustmentCode.B
