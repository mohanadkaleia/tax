"""Tests for the data gap analyzer in ReconciliationEngine."""

from datetime import date
from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.engines.reconciliation import ReconciliationEngine
from app.models.data_gaps import DataGapReport, GapCategory, GapSeverity
from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, Security


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = create_schema(db_path)
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn):
    return TaxRepository(db_conn)


@pytest.fixture
def engine(repo):
    return ReconciliationEngine(repo)


def _make_sale(sale_id, ticker, sale_date, proceeds, broker_basis, date_acquired=None):
    """Helper to create a sale for auto-lot-creation tests."""
    return Sale(
        id=sale_id,
        lot_id="",
        security=Security(ticker=ticker, name=f"{ticker} Corp"),
        date_acquired=date_acquired,
        sale_date=sale_date,
        shares=Decimal("10"),
        proceeds_per_share=proceeds,
        broker_reported_basis=broker_basis,
        basis_reported_to_irs=True,
        broker_source=BrokerSource.SHAREWORKS,
    )


class TestAutoCreatedLotGrouping:
    """Test that auto-created lots are grouped by ticker in gap report."""

    def test_auto_created_lots_grouped_by_ticker(self, repo, engine):
        """Multiple auto-created lots for one ticker → one DataGap."""
        # Seed 3 sales for COIN with no lots → engine will auto-create lots
        for i in range(3):
            sale = _make_sale(
                f"sale-{i}",
                "COIN",
                date(2024, 12, 1),
                Decimal("200.00"),
                Decimal("100.00"),
                date_acquired=date(2023, 1 + i, 15),
            )
            repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        assert len(auto_gaps) == 1
        assert auto_gaps[0].ticker == "COIN"
        assert auto_gaps[0].lot_count == 3
        assert gap_report.total_auto_created_lots == 3

    def test_multiple_tickers_separate_gaps(self, repo, engine):
        """Auto-created lots for different tickers → separate DataGap entries."""
        sale1 = _make_sale(
            "sale-coin", "COIN", date(2024, 12, 1),
            Decimal("200.00"), Decimal("100.00"),
            date_acquired=date(2023, 5, 10),
        )
        sale2 = _make_sale(
            "sale-aapl", "AAPL", date(2024, 12, 1),
            Decimal("300.00"), Decimal("150.00"),
            date_acquired=date(2023, 6, 10),
        )
        repo.save_sale(sale1)
        repo.save_sale(sale2)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        tickers = {g.ticker for g in auto_gaps}
        assert tickers == {"COIN", "AAPL"}

    def test_no_auto_created_lots_no_gap(self, repo, engine):
        """When lots exist and match → no AUTO_CREATED_LOT gap."""
        security = Security(ticker="ACME", name="Acme Corp")
        event = EquityEvent(
            id="evt-1",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=security,
            event_date=date(2024, 3, 15),
            shares=Decimal("100"),
            price_per_share=Decimal("150.00"),
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)
        lot = Lot(
            id="lot-1",
            equity_type=EquityType.RSU,
            security=security,
            acquisition_date=date(2024, 3, 15),
            shares=Decimal("100"),
            cost_per_share=Decimal("150.00"),
            shares_remaining=Decimal("100"),
            source_event_id="evt-1",
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_lot(lot)
        sale = Sale(
            id="sale-1",
            lot_id="",
            security=security,
            sale_date=date(2024, 12, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("175.00"),
            broker_reported_basis=Decimal("7500.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        assert len(auto_gaps) == 0


class TestDateRangeComputation:
    """Test that date ranges are correctly computed from auto-created lots."""

    def test_date_range_spans_multiple_years(self, repo, engine):
        """Date range should span from earliest to latest acquisition date."""
        dates = [date(2021, 4, 19), date(2023, 8, 1), date(2024, 11, 20)]
        for i, d in enumerate(dates):
            sale = _make_sale(
                f"sale-{i}", "COIN", date(2024, 12, 15),
                Decimal("200.00"), Decimal("50.00"),
                date_acquired=d,
            )
            repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        assert len(auto_gaps) == 1
        gap = auto_gaps[0]
        assert gap.date_range_start == date(2021, 4, 19)
        assert gap.date_range_end == date(2024, 11, 20)


class TestBasisAggregation:
    """Test that total basis is summed across auto-created lots."""

    def test_total_basis_summed(self, repo, engine):
        for i in range(2):
            sale = _make_sale(
                f"sale-{i}", "COIN", date(2024, 12, 1),
                Decimal("200.00"), Decimal("500.00"),
                date_acquired=date(2023, 1 + i, 15),
            )
            repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        assert len(auto_gaps) == 1
        assert auto_gaps[0].total_basis == Decimal("1000.00")


class TestMissingFormDetection:
    """Test detection of missing Form 3922/3921 from engine errors."""

    def test_missing_form_3922_detected(self, repo, engine):
        """ESPP sale blocked at pass-through → ERROR gap for Form 3922."""
        security = Security(ticker="ACME", name="Acme Corp ESPP")
        sale = Sale(
            id="sale-espp-1",
            lot_id="",
            security=security,
            sale_date=date(2024, 12, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("200.00"),
            broker_reported_basis=Decimal("5000.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        form_gaps = [g for g in gap_report.gaps if g.category == GapCategory.MISSING_FORM_3922]
        assert len(form_gaps) >= 1
        assert form_gaps[0].severity == GapSeverity.ERROR
        assert gap_report.has_blocking_gaps is True

    def test_missing_form_3921_detected(self, repo, engine):
        """ISO sale blocked at pass-through → ERROR gap for Form 3921."""
        security = Security(ticker="ACME", name="Acme Corp ISO")
        sale = Sale(
            id="sale-iso-1",
            lot_id="",
            security=security,
            sale_date=date(2024, 12, 1),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("200.00"),
            broker_reported_basis=Decimal("5000.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        form_gaps = [g for g in gap_report.gaps if g.category == GapCategory.MISSING_FORM_3921]
        assert len(form_gaps) >= 1
        assert form_gaps[0].severity == GapSeverity.ERROR


class TestSeverityFlags:
    """Test that severity levels are set correctly."""

    def test_auto_created_lots_are_warning(self, repo, engine):
        sale = _make_sale(
            "sale-1", "COIN", date(2024, 12, 1),
            Decimal("200.00"), Decimal("100.00"),
            date_acquired=date(2023, 5, 10),
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        auto_gaps = [g for g in gap_report.gaps if g.category == GapCategory.AUTO_CREATED_LOT]
        assert all(g.severity == GapSeverity.WARNING for g in auto_gaps)

    def test_passthrough_sales_are_info(self, repo, engine):
        """Pass-through sales should have INFO severity."""
        sale = Sale(
            id="sale-pt-1",
            lot_id="",
            security=Security(ticker="XYZ", name="XYZ Corp"),
            date_acquired="Various",
            sale_date=date(2024, 12, 1),
            shares=Decimal("10"),
            proceeds_per_share=Decimal("100.00"),
            broker_reported_basis=Decimal("80.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        pt_gaps = [g for g in gap_report.gaps if g.category == GapCategory.PASSTHROUGH_SALE]
        assert len(pt_gaps) == 1
        assert pt_gaps[0].severity == GapSeverity.INFO

    def test_has_blocking_gaps_false_when_no_errors(self, repo, engine):
        """No ERROR-severity gaps → has_blocking_gaps is False."""
        sale = _make_sale(
            "sale-1", "COIN", date(2024, 12, 1),
            Decimal("200.00"), Decimal("100.00"),
            date_acquired=date(2023, 5, 10),
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        assert gap_report.has_blocking_gaps is False


class TestEmptyRun:
    """Test gap analysis with no sales."""

    def test_no_sales_empty_gaps(self, repo, engine):
        """No sales → empty gap report."""
        run = engine.reconcile(2024)
        gap_report: DataGapReport = run["data_gap_report"]

        assert gap_report.gaps == []
        assert gap_report.total_auto_created_lots == 0
        assert gap_report.total_zero_basis_sales == 0
        assert gap_report.total_missing_forms == 0
        assert gap_report.has_blocking_gaps is False
