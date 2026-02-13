"""Tests for ReconciliationEngine — the core orchestrator."""

from datetime import date
from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.engines.reconciliation import ReconciliationEngine
from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, Security


@pytest.fixture
def db_conn(tmp_path):
    """Create an in-memory database with full schema."""
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


def _seed_rsu_data(repo):
    """Seed DB with RSU lot + sale for reconciliation."""
    security = Security(ticker="ACME", name="Acme Corp")

    event = EquityEvent(
        id="evt-rsu-001",
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
        id="lot-rsu-001",
        equity_type=EquityType.RSU,
        security=security,
        acquisition_date=date(2024, 3, 15),
        shares=Decimal("100"),
        cost_per_share=Decimal("150.00"),
        shares_remaining=Decimal("100"),
        source_event_id="evt-rsu-001",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)

    sale = Sale(
        id="sale-rsu-001",
        lot_id="",
        security=security,
        sale_date=date(2025, 6, 1),
        shares=Decimal("100"),
        proceeds_per_share=Decimal("175.00"),
        broker_reported_basis=Decimal("0"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale)


def _seed_espp_data(repo):
    """Seed DB with ESPP event + lot + sale for reconciliation."""
    security = Security(ticker="ACME", name="Acme Corp")

    event = EquityEvent(
        id="evt-espp-001",
        event_type=TransactionType.PURCHASE,
        equity_type=EquityType.ESPP,
        security=security,
        event_date=date(2024, 6, 30),
        shares=Decimal("50"),
        price_per_share=Decimal("150.00"),
        purchase_price=Decimal("127.50"),
        offering_date=date(2024, 1, 1),
        fmv_on_offering_date=Decimal("140.00"),
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id="lot-espp-001",
        equity_type=EquityType.ESPP,
        security=security,
        acquisition_date=date(2024, 6, 30),
        shares=Decimal("50"),
        cost_per_share=Decimal("127.50"),
        shares_remaining=Decimal("50"),
        source_event_id="evt-espp-001",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)

    sale = Sale(
        id="sale-espp-001",
        lot_id="",
        security=security,
        sale_date=date(2024, 10, 1),
        shares=Decimal("50"),
        proceeds_per_share=Decimal("160.00"),
        broker_reported_basis=Decimal("6375.00"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale)


def _seed_iso_data(repo):
    """Seed DB with ISO event + lot + sale for reconciliation."""
    security = Security(ticker="ACME", name="Acme Corp")

    event = EquityEvent(
        id="evt-iso-001",
        event_type=TransactionType.EXERCISE,
        equity_type=EquityType.ISO,
        security=security,
        event_date=date(2024, 1, 10),
        shares=Decimal("200"),
        price_per_share=Decimal("120.00"),
        strike_price=Decimal("50.00"),
        grant_date=date(2022, 1, 15),
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id="lot-iso-001",
        equity_type=EquityType.ISO,
        security=security,
        acquisition_date=date(2024, 1, 10),
        shares=Decimal("200"),
        cost_per_share=Decimal("50.00"),
        amt_cost_per_share=Decimal("120.00"),
        shares_remaining=Decimal("200"),
        source_event_id="evt-iso-001",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)

    sale = Sale(
        id="sale-iso-001",
        lot_id="",
        security=security,
        sale_date=date(2025, 7, 1),
        shares=Decimal("200"),
        proceeds_per_share=Decimal("150.00"),
        broker_reported_basis=Decimal("0"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale)


class TestReconcileRSU:
    def test_rsu_reconciliation(self, repo, engine):
        """RSU sale: broker reported $0 basis, correct to FMV at vest."""
        _seed_rsu_data(repo)
        run = engine.reconcile(2025)

        assert run["total_sales"] == 1
        assert run["matched_sales"] == 1
        assert run["unmatched_sales"] == 0
        assert Decimal(run["total_proceeds"]) == Decimal("17500.00")
        assert Decimal(run["total_correct_basis"]) == Decimal("15000.00")
        assert Decimal(run["total_gain_loss"]) == Decimal("2500.00")
        assert run["status"] == "completed"

    def test_rsu_lot_shares_decremented(self, repo, engine):
        """After reconciliation, lot shares_remaining should be 0."""
        _seed_rsu_data(repo)
        engine.reconcile(2025)

        lots = repo.get_lots("ACME")
        assert Decimal(lots[0]["shares_remaining"]) == Decimal("0")

    def test_sale_results_persisted(self, repo, engine):
        """Sale results should be written to the database."""
        _seed_rsu_data(repo)
        engine.reconcile(2025)

        results = repo.get_sale_results(2025)
        assert len(results) == 1
        assert results[0]["sale_id"] == "sale-rsu-001"
        assert results[0]["adjustment_code"] == "B"


class TestReconcileESPP:
    def test_espp_disqualifying_reconciliation(self, repo, engine):
        """ESPP disqualifying disposition via reconciliation engine."""
        _seed_espp_data(repo)
        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        # Ordinary income = spread at purchase = ($150-$127.50) × 50 = $1,125
        assert Decimal(run["total_ordinary_income"]) == Decimal("1125.00")
        assert run["status"] == "completed"


class TestReconcileISO:
    def test_iso_qualifying_reconciliation(self, repo, engine):
        """ISO qualifying disposition via reconciliation engine."""
        _seed_iso_data(repo)
        run = engine.reconcile(2025)

        assert run["matched_sales"] == 1
        # Qualifying ISO: no ordinary income
        assert Decimal(run["total_ordinary_income"]) == Decimal("0")
        # AMT adjustment = regular gain - AMT gain
        # Regular: ($150-$50)*200 = $20,000
        # AMT: ($150-$120)*200 = $6,000
        # Adjustment: $20,000 - $6,000 = $14,000
        assert Decimal(run["total_amt_adjustment"]) == Decimal("14000.00")


class TestReconcileEdgeCases:
    def test_no_sales(self, repo, engine):
        """Reconcile with no sales produces warning."""
        run = engine.reconcile(2025)

        assert run["total_sales"] == 0
        assert run["matched_sales"] == 0
        assert len(run["warnings"]) >= 1
        assert "No sales found" in run["warnings"][0]

    def test_no_matching_lots(self, repo, engine):
        """Sale with no matching lots produces warning."""
        security = Security(ticker="UNKNOWN", name="Unknown Stock")
        sale = Sale(
            id="sale-orphan",
            lot_id="",
            security=security,
            sale_date=date(2025, 6, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("50.00"),
            broker_reported_basis=None,
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2025)

        assert run["unmatched_sales"] == 1
        assert any("No lots found" in w for w in run["warnings"])

    def test_idempotent_rerun(self, repo, engine):
        """Running reconcile twice produces the same results."""
        _seed_rsu_data(repo)

        run1 = engine.reconcile(2025)
        # Re-seed the lot shares (would have been decremented)
        lot = Lot(
            id="lot-rsu-001",
            equity_type=EquityType.RSU,
            security=Security(ticker="ACME", name="Acme Corp"),
            acquisition_date=date(2024, 3, 15),
            shares=Decimal("100"),
            cost_per_share=Decimal("150.00"),
            shares_remaining=Decimal("100"),
            source_event_id="evt-rsu-001",
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_lot(lot)

        run2 = engine.reconcile(2025)

        assert run1["total_gain_loss"] == run2["total_gain_loss"]
        assert run1["matched_sales"] == run2["matched_sales"]

        # Should only have 1 set of results (cleared before re-run)
        results = repo.get_sale_results(2025)
        assert len(results) == 1

    def test_multiple_sales_same_year(self, repo, engine):
        """Multiple sales matched to the same lot (FIFO)."""
        security = Security(ticker="ACME", name="Acme Corp")

        event = EquityEvent(
            id="evt-multi",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=security,
            event_date=date(2024, 1, 1),
            shares=Decimal("200"),
            price_per_share=Decimal("100.00"),
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)

        lot = Lot(
            id="lot-multi",
            equity_type=EquityType.RSU,
            security=security,
            acquisition_date=date(2024, 1, 1),
            shares=Decimal("200"),
            cost_per_share=Decimal("100.00"),
            shares_remaining=Decimal("200"),
            source_event_id="evt-multi",
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_lot(lot)

        for i in range(3):
            sale = Sale(
                id=f"sale-multi-{i}",
                lot_id="",
                security=security,
                sale_date=date(2025, 3 + i, 1),
                shares=Decimal("50"),
                proceeds_per_share=Decimal("120.00"),
                broker_reported_basis=Decimal("0"),
                basis_reported_to_irs=True,
                broker_source=BrokerSource.MANUAL,
            )
            repo.save_sale(sale)

        run = engine.reconcile(2025)

        assert run["matched_sales"] == 3
        assert run["unmatched_sales"] == 0

        # 50 shares remain in the lot (200 - 3*50 = 50)
        lots = repo.get_lots("ACME")
        assert Decimal(lots[0]["shares_remaining"]) == Decimal("50")

    def test_reconciliation_run_saved(self, repo, engine):
        """Reconciliation run summary is persisted to DB."""
        _seed_rsu_data(repo)
        engine.reconcile(2025)

        runs = repo.get_reconciliation_runs(2025)
        assert len(runs) == 1
        assert runs[0]["tax_year"] == 2025
        assert runs[0]["matched_sales"] == 1


class TestReconcileFuzzyMatch:
    def test_fuzzy_match_by_name(self, repo, engine):
        """Sale with UNKNOWN ticker matches lot by security name."""
        security_lot = Security(ticker="ACME", name="Acme Corp ISO Exercise")
        security_sale = Security(ticker="UNKNOWN", name="Acme Corp")

        event = EquityEvent(
            id="evt-fuzzy",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=security_lot,
            event_date=date(2024, 1, 1),
            shares=Decimal("100"),
            price_per_share=Decimal("100.00"),
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)

        lot = Lot(
            id="lot-fuzzy",
            equity_type=EquityType.RSU,
            security=security_lot,
            acquisition_date=date(2024, 1, 1),
            shares=Decimal("100"),
            cost_per_share=Decimal("100.00"),
            shares_remaining=Decimal("100"),
            source_event_id="evt-fuzzy",
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_lot(lot)

        sale = Sale(
            id="sale-fuzzy",
            lot_id="",
            security=security_sale,
            sale_date=date(2025, 6, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("120.00"),
            broker_reported_basis=Decimal("0"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2025)

        assert run["matched_sales"] == 1
        assert run["unmatched_sales"] == 0
