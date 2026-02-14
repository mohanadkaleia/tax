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


class TestAutoLotCreation:
    """Tests for auto-lot creation from 1099-B data when no lots exist."""

    def test_auto_lot_matched(self, repo, engine):
        """Sale with date_acquired + broker_basis and no lots → auto-lot matched."""
        sale = Sale(
            id="sale-pt-001",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="COINBASE GLOBAL INC CL A"),
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("51037.21"),  # Total proceeds
            broker_reported_basis=Decimal("8057.40"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        assert run["passthrough_sales"] == 0
        assert run["unmatched_sales"] == 0
        assert Decimal(run["total_proceeds"]) == Decimal("51037.21")
        assert Decimal(run["total_correct_basis"]) == Decimal("8057.40")
        assert Decimal(run["total_gain_loss"]) == Decimal("42979.81")

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        assert results[0]["lot_id"] is not None  # Matched to auto-created lot
        assert results[0]["adjustment_code"] == ""  # No adjustment (basis matches)
        assert results[0]["holding_period"] == "SHORT_TERM"  # May 14 → Nov 11 < 1 year
        assert any("Auto-created" in w for w in run["warnings"])

    def test_auto_lot_creates_db_records(self, repo, engine):
        """Auto-lot creation persists both lot and event to database."""
        sale = Sale(
            id="sale-autolot-db",
            lot_id="",
            security=Security(ticker="COIN", name="Coinbase"),
            date_acquired=date(2020, 2, 20),
            sale_date=date(2024, 11, 27),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("4649.87"),
            broker_reported_basis=Decimal("3365.85"),
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        # Verify lot created
        lots = repo.get_lots("COIN")
        assert len(lots) == 1
        assert lots[0]["equity_type"] == "RSU"
        assert lots[0]["acquisition_date"] == "2020-02-20"
        assert Decimal(lots[0]["cost_per_share"]) == Decimal("3365.85")
        assert "Auto-created" in (lots[0]["notes"] or "")

        # Verify event created
        events = repo.get_events(ticker="COIN")
        assert len(events) == 1
        assert events[0]["event_type"] == "VEST"
        assert events[0]["equity_type"] == "RSU"

    def test_auto_lot_holding_period_short(self, repo, engine):
        """Short-term holding period computed from auto-lot acquisition date."""
        sale = Sale(
            id="sale-pt-st",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Test Stock"),
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("8000"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        results = repo.get_sale_results(2024)
        assert results[0]["holding_period"] == "SHORT_TERM"
        assert results[0]["form_8949_category"] == "A"  # ST + basis reported

    def test_auto_lot_holding_period_long(self, repo, engine):
        """Long-term holding period computed from auto-lot acquisition date."""
        sale = Sale(
            id="sale-pt-lt",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Test Stock"),
            date_acquired=date(2023, 1, 1),
            sale_date=date(2024, 6, 1),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("8000"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        results = repo.get_sale_results(2024)
        assert results[0]["holding_period"] == "LONG_TERM"
        assert results[0]["form_8949_category"] == "D"  # LT + basis reported

    def test_auto_lot_basis_not_reported(self, repo, engine):
        """Sale with basis not reported → auto-lot matched, category B."""
        sale = Sale(
            id="sale-pt-notrep",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Some Stock"),
            date_acquired=date(2024, 5, 20),
            sale_date=date(2024, 5, 20),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("22108.18"),
            broker_reported_basis=Decimal("21590.40"),
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        assert run["passthrough_sales"] == 0
        results = repo.get_sale_results(2024)
        # Auto-lot basis = broker basis → no adjustment needed
        assert results[0]["adjustment_code"] == ""
        assert results[0]["form_8949_category"] == "B"  # ST + basis NOT reported
        assert any("Auto-created" in w for w in run["warnings"])

    def test_auto_lot_with_known_shares(self, repo, engine):
        """Auto-lot creation when sale has known share count."""
        sale = Sale(
            id="sale-autolot-shares",
            lot_id="",
            security=Security(ticker="COIN", name="Coinbase"),
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("175.00"),
            broker_reported_basis=Decimal("7500.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        lots = repo.get_lots("COIN")
        assert len(lots) == 1
        assert Decimal(lots[0]["shares"]) == Decimal("50")
        assert Decimal(lots[0]["cost_per_share"]) == Decimal("150.00")  # 7500/50

    def test_no_auto_lot_without_date(self, repo, engine):
        """Sale without date_acquired → no auto-lot, falls to pass-through."""
        sale = Sale(
            id="sale-nodate",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Test Stock"),
            sale_date=date(2024, 6, 1),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("8000"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        assert run["passthrough_sales"] == 1
        assert run["matched_sales"] == 0

    def test_no_auto_lot_without_basis(self, repo, engine):
        """Sale without broker_reported_basis → no auto-lot."""
        sale = Sale(
            id="sale-nobasis",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Test Stock"),
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("50"),
            broker_reported_basis=None,
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        assert run["unmatched_sales"] == 1
        assert run["matched_sales"] == 0

    def test_auto_lot_when_lots_exist_by_ticker_but_not_date(self, repo, engine):
        """Sale matches lots by ticker but no lot has the same date → auto-lot.

        This covers pre-IPO RSU vests: the 1099-B shows date_acquired=2020-02-20
        but the Shareworks PDF only has lots starting from 2021+.
        """
        security = Security(ticker="COIN", name="Coinbase")

        # Create a lot for a DIFFERENT date (simulating Shareworks import)
        event = EquityEvent(
            id="evt-later",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=security,
            event_date=date(2021, 5, 17),
            shares=Decimal("50"),
            price_per_share=Decimal("200.00"),
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)
        lot = Lot(
            id="lot-later",
            equity_type=EquityType.RSU,
            security=security,
            acquisition_date=date(2021, 5, 17),
            shares=Decimal("50"),
            cost_per_share=Decimal("200.00"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-later",
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_lot(lot)

        # Sale from an earlier date (pre-IPO) — no lot for 2020-02-20
        sale = Sale(
            id="sale-preipo",
            lot_id="",
            security=Security(ticker="COIN", name="COINBASE GLOBAL INC CL A"),
            date_acquired=date(2020, 2, 20),
            sale_date=date(2024, 11, 27),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("4649.87"),
            broker_reported_basis=Decimal("3365.85"),
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        assert run["passthrough_sales"] == 0
        assert Decimal(run["total_proceeds"]) == Decimal("4649.87")
        assert Decimal(run["total_correct_basis"]) == Decimal("3365.85")
        assert any("Auto-created" in w for w in run["warnings"])

        # Verify auto-lot created with correct date
        lots = repo.get_lots("COIN")
        auto_lots = [l for l in lots if l["acquisition_date"] == "2020-02-20"]
        assert len(auto_lots) == 1
        assert auto_lots[0]["equity_type"] == "RSU"

        # Original lot should be untouched
        original = [l for l in lots if l["acquisition_date"] == "2021-05-17"]
        assert len(original) == 1
        assert Decimal(original[0]["shares_remaining"]) == Decimal("50")


class TestPassthroughReconciliation:
    """Tests for pass-through reconciliation (Various dates, etc.)."""

    def test_passthrough_blocked_for_espp(self, repo, engine):
        """ESPP sale without lots should be blocked from pass-through."""
        sale = Sale(
            id="sale-pt-espp-blocked",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="ESPP Stock"),
            date_acquired=date(2024, 5, 20),
            sale_date=date(2024, 5, 20),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("22108.18"),
            broker_reported_basis=Decimal("21590.40"),
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["unmatched_sales"] == 1
        assert run["passthrough_sales"] == 0
        assert any("ESPP" in e for e in run["errors"])

    def test_passthrough_blocked_for_iso(self, repo, engine):
        """ISO sale without lots should be blocked from pass-through."""
        sale = Sale(
            id="sale-pt-iso-blocked",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="ISO Exercise Stock"),
            date_acquired=date(2024, 5, 20),
            sale_date=date(2024, 5, 20),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("5000"),
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["unmatched_sales"] == 1
        assert any("ISO" in e for e in run["errors"])

    def test_passthrough_various_date_acquired(self, repo, engine):
        """Sale with 'Various' date_acquired defaults to short-term."""
        sale = Sale(
            id="sale-pt-various",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Test Stock"),
            date_acquired="Various",
            sale_date=date(2024, 6, 1),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("8000"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        results = repo.get_sale_results(2024)
        assert results[0]["holding_period"] == "SHORT_TERM"
        assert any("Various" in w for w in run["warnings"])

    def test_passthrough_no_basis_still_fails(self, repo, engine):
        """Sale with no broker basis and no lots → unmatched."""
        sale = Sale(
            id="sale-pt-nobasis",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="Mystery Stock"),
            sale_date=date(2024, 6, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("50"),
            broker_reported_basis=None,
            basis_reported_to_irs=False,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)
        assert run["unmatched_sales"] == 1
        assert run["matched_sales"] == 0

    def test_espp_with_lots_not_passthrough(self, repo, engine):
        """ESPP sale WITH matching lots should NOT use pass-through."""
        _seed_espp_data(repo)
        run = engine.reconcile(2024)

        # Should use lot-based reconciliation, not pass-through
        assert run["matched_sales"] == 1
        results = repo.get_sale_results(2024)
        assert results[0]["lot_id"] == "lot-espp-001"
        assert "Pass-through" not in (results[0].get("notes") or "")


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
