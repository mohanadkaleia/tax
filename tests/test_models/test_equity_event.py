"""Tests for core equity event models."""

from decimal import Decimal

from app.models.enums import EquityType
from app.models.equity_event import Lot, Security


class TestSecurity:
    def test_create_security(self):
        sec = Security(ticker="ACME", name="Acme Corp")
        assert sec.ticker == "ACME"
        assert sec.cusip is None


class TestLot:
    def test_create_rsu_lot(self, sample_rsu_lot: Lot):
        assert sample_rsu_lot.equity_type == EquityType.RSU
        assert sample_rsu_lot.shares == Decimal("100")

    def test_total_cost_basis(self, sample_rsu_lot: Lot):
        assert sample_rsu_lot.total_cost_basis == Decimal("15000.00")

    def test_amt_basis_none_for_rsu(self, sample_rsu_lot: Lot):
        assert sample_rsu_lot.total_amt_basis is None

    def test_amt_basis_for_iso(self, sample_iso_lot: Lot):
        assert sample_iso_lot.total_amt_basis == Decimal("24000.00")
