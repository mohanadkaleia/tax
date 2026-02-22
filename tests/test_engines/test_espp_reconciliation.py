"""Tests for ESPP priority matching in ReconciliationEngine.

Validates the fix for the critical bug where ESPP sales were matched
to auto-created RSU lots instead of real ESPP lots, causing ESPP
ordinary income to never be computed.
"""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COIN = Security(ticker="COIN", name="COINBASE GLOBAL INC CL A")


def _create_espp_lot(
    repo,
    *,
    lot_id: str,
    event_id: str,
    purchase_date: date,
    shares: Decimal,
    purchase_price: Decimal,
    fmv_at_purchase: Decimal,
    offering_date: date,
    fmv_on_offering_date: Decimal,
):
    """Seed an ESPP event + lot in the database."""
    event = EquityEvent(
        id=event_id,
        event_type=TransactionType.PURCHASE,
        equity_type=EquityType.ESPP,
        security=COIN,
        event_date=purchase_date,
        shares=shares,
        price_per_share=fmv_at_purchase,
        purchase_price=purchase_price,
        offering_date=offering_date,
        fmv_on_offering_date=fmv_on_offering_date,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id=lot_id,
        equity_type=EquityType.ESPP,
        security=COIN,
        acquisition_date=purchase_date,
        shares=shares,
        cost_per_share=purchase_price,
        shares_remaining=shares,
        source_event_id=event_id,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    return lot


def _create_rsu_lot(
    repo,
    *,
    lot_id: str,
    event_id: str,
    vest_date: date,
    shares: Decimal,
    fmv: Decimal,
):
    """Seed an RSU event + lot in the database."""
    event = EquityEvent(
        id=event_id,
        event_type=TransactionType.VEST,
        equity_type=EquityType.RSU,
        security=COIN,
        event_date=vest_date,
        shares=shares,
        price_per_share=fmv,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id=lot_id,
        equity_type=EquityType.RSU,
        security=COIN,
        acquisition_date=vest_date,
        shares=shares,
        cost_per_share=fmv,
        shares_remaining=shares,
        source_event_id=event_id,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    return lot


def _create_iso_lot(
    repo,
    *,
    lot_id: str,
    event_id: str,
    exercise_date: date,
    shares: Decimal,
    strike_price: Decimal,
    fmv_at_exercise: Decimal,
    grant_date: date,
):
    """Seed an ISO event + lot in the database."""
    event = EquityEvent(
        id=event_id,
        event_type=TransactionType.EXERCISE,
        equity_type=EquityType.ISO,
        security=COIN,
        event_date=exercise_date,
        shares=shares,
        price_per_share=fmv_at_exercise,
        strike_price=strike_price,
        grant_date=grant_date,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id=lot_id,
        equity_type=EquityType.ISO,
        security=COIN,
        acquisition_date=exercise_date,
        shares=shares,
        cost_per_share=strike_price,
        amt_cost_per_share=fmv_at_exercise,
        shares_remaining=shares,
        source_event_id=event_id,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    return lot


# ---------------------------------------------------------------------------
# ESPP priority matching tests
# ---------------------------------------------------------------------------


class TestESPPPriorityMatch:
    """ESPP lots should be preferred over RSU lots when dates match."""

    def test_espp_lot_preferred_over_rsu_same_date(self, repo, engine):
        """When both ESPP and RSU lots exist for the same date, ESPP wins.

        This is the core bug scenario: COIN has both an ESPP lot
        (cost=$51.65, FMV=$203.05) and could have an RSU lot for
        2024-05-14.  The sale should match to ESPP.
        """
        _create_espp_lot(
            repo,
            lot_id="lot-espp-0514",
            event_id="evt-espp-0514",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        _create_rsu_lot(
            repo,
            lot_id="lot-rsu-0514",
            event_id="evt-rsu-0514",
            vest_date=date(2024, 5, 14),
            shares=Decimal("50"),
            fmv=Decimal("240.00"),
        )

        # Sale with shares=0 (manual 1099-B import)
        # broker_reported_basis = FMV_at_purchase * shares = $203.05 * 40 = $8122
        sale = Sale(
            id="sale-espp-0514",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("51037.21"),  # total proceeds
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run = engine.reconcile(2024)

        assert run["matched_sales"] == 1
        assert run["passthrough_sales"] == 0
        assert run["unmatched_sales"] == 0

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        # Must be matched to ESPP lot, not RSU
        assert results[0]["lot_id"] == "lot-espp-0514"
        # Ordinary income should be computed (disqualifying disposition:
        # sold < 2yr from offering, < 1yr from purchase)
        ordinary_income = Decimal(results[0]["ordinary_income"])
        assert ordinary_income > 0, "ESPP ordinary income must be computed"

    def test_espp_shares_inferred_from_purchase_price(self, repo, engine):
        """Shares should be inferred using purchase price (lot cost_per_share).

        Brokers report ESPP cost basis as purchase_price × shares on 1099-B.
        broker_basis=$8,057.40, purchase_price=$51.65 -> round(8057.40/51.65) = 156 shares
        """
        _create_espp_lot(
            repo,
            lot_id="lot-espp-fmv",
            event_id="evt-espp-fmv",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-fmv",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("51037.21"),  # total
            broker_reported_basis=Decimal("8057.40"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        # round(8057.40 / 51.65) = round(156.0) = 156
        assert Decimal(results[0]["shares"]) == Decimal("156")

    def test_espp_known_shares(self, repo, engine):
        """When sale.shares > 0 (e.g. Robinhood), use that directly."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-known",
            event_id="evt-espp-known",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-known",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("40"),
            proceeds_per_share=Decimal("1275.93"),  # per-share
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        assert results[0]["lot_id"] == "lot-espp-known"
        assert Decimal(results[0]["shares"]) == Decimal("40")

    def test_espp_disqualifying_disposition_income(self, repo, engine):
        """ESPP disqualifying disposition: ordinary income = spread at purchase.

        purchase_price=$51.65, FMV_at_purchase=$203.05
        spread = $203.05 - $51.65 = $151.40 per share
        40 shares -> ordinary income = $6,056.00
        """
        _create_espp_lot(
            repo,
            lot_id="lot-espp-dq",
            event_id="evt-espp-dq",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-dq",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),  # < 1yr from purchase, < 2yr from offering
            shares=Decimal("40"),
            proceeds_per_share=Decimal("1275.93"),
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        oi = Decimal(results[0]["ordinary_income"])
        # spread = (203.05 - 51.65) * 40 = 151.40 * 40 = 6056.00
        assert oi == Decimal("6056.00")
        assert "DISQUALIFYING" in (results[0]["notes"] or "")

    def test_espp_correct_basis_includes_ordinary_income(self, repo, engine):
        """Correct basis = purchase_price * shares + ordinary_income.

        purchase_price=$51.65, shares=40 -> $2,066.00
        ordinary_income = $6,056.00
        correct_basis = $2,066.00 + $6,056.00 = $8,122.00
        """
        _create_espp_lot(
            repo,
            lot_id="lot-espp-basis",
            event_id="evt-espp-basis",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-basis",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("40"),
            proceeds_per_share=Decimal("1275.93"),
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        results = repo.get_sale_results(2024)
        correct_basis = Decimal(results[0]["correct_basis"])
        # purchase_price * shares + OI = 51.65*40 + 6056.00 = 2066 + 6056 = 8122
        assert correct_basis == Decimal("8122.00")

    def test_espp_lot_shares_decremented(self, repo, engine):
        """After matching, ESPP lot shares_remaining should decrease."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-dec",
            event_id="evt-espp-dec",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-dec",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("40"),
            proceeds_per_share=Decimal("1275.93"),
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        lots = repo.get_lots("COIN")
        espp_lots = [row for row in lots if row["equity_type"] == "ESPP"]
        assert len(espp_lots) == 1
        assert Decimal(espp_lots[0]["shares_remaining"]) == Decimal("163")


class TestAutoCreateBlockedForESPP:
    """Auto-create should NOT create RSU lots when ESPP lots exist for same date."""

    def test_auto_create_blocked_when_espp_lot_exists(self, repo, engine):
        """Sale matching ESPP lot date should NOT auto-create RSU lot."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-block",
            event_id="evt-espp-block",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        # Sale for a DIFFERENT ticker but same date -- should still use
        # fuzzy match to find the ESPP lot (COIN ticker in lot)
        sale = Sale(
            id="sale-block-test",
            lot_id="",
            security=Security(ticker="UNKNOWN", name="COINBASE GLOBAL INC CL A"),
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("51037.21"),
            broker_reported_basis=Decimal("8057.40"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        # Should match to ESPP lot via fuzzy match + priority match
        lots = repo.get_lots()
        auto_lots = [row for row in lots if "Auto-created" in (row.get("notes") or "")]
        assert len(auto_lots) == 0, "No auto-created RSU lots should exist"

    def test_auto_create_allowed_for_different_date(self, repo, engine):
        """Auto-create should still work when no ESPP lot matches the date."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-other",
            event_id="evt-espp-other",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        # Sale for a DIFFERENT date (pre-IPO RSU vest) -- should auto-create
        sale = Sale(
            id="sale-preipo",
            lot_id="",
            security=COIN,
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
        lots = repo.get_lots("COIN")
        auto_lots = [row for row in lots if "Auto-created" in (row.get("notes") or "")]
        assert len(auto_lots) == 1
        assert auto_lots[0]["acquisition_date"] == "2020-02-20"


class TestISOPriorityMatch:
    """ISO lots should be preferred over RSU lots when dates match."""

    def test_iso_lot_preferred_over_rsu_same_date(self, repo, engine):
        """ISO lot should be matched before RSU lot for same date."""
        _create_iso_lot(
            repo,
            lot_id="lot-iso-match",
            event_id="evt-iso-match",
            exercise_date=date(2024, 1, 10),
            shares=Decimal("200"),
            strike_price=Decimal("50.00"),
            fmv_at_exercise=Decimal("120.00"),
            grant_date=date(2022, 1, 15),
        )

        _create_rsu_lot(
            repo,
            lot_id="lot-rsu-match",
            event_id="evt-rsu-match",
            vest_date=date(2024, 1, 10),
            shares=Decimal("100"),
            fmv=Decimal("120.00"),
        )

        sale = Sale(
            id="sale-iso-match",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 1, 10),
            sale_date=date(2025, 7, 1),
            shares=Decimal("100"),
            proceeds_per_share=Decimal("150.00"),
            broker_reported_basis=Decimal("5000"),  # strike * shares
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2025)

        results = repo.get_sale_results(2025)
        assert len(results) == 1
        assert results[0]["lot_id"] == "lot-iso-match"
        # AMT adjustment should be present (prior-year exercise)
        amt = Decimal(results[0]["amt_adjustment"])
        assert amt != 0, "ISO AMT adjustment must be computed"


class TestReReconciliation:
    """Re-running reconcile should produce consistent results."""

    def test_idempotent_with_espp(self, repo, engine):
        """Running reconcile twice produces the same ESPP results."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-idem",
            event_id="evt-espp-idem",
            purchase_date=date(2024, 5, 14),
            shares=Decimal("203"),
            purchase_price=Decimal("51.65"),
            fmv_at_purchase=Decimal("203.05"),
            offering_date=date(2023, 11, 15),
            fmv_on_offering_date=Decimal("78.00"),
        )

        sale = Sale(
            id="sale-espp-idem",
            lot_id="",
            security=COIN,
            date_acquired=date(2024, 5, 14),
            sale_date=date(2024, 11, 11),
            shares=Decimal("40"),
            proceeds_per_share=Decimal("1275.93"),
            broker_reported_basis=Decimal("8122.00"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        run1 = engine.reconcile(2024)
        run2 = engine.reconcile(2024)

        assert run1["total_gain_loss"] == run2["total_gain_loss"]
        assert run1["total_ordinary_income"] == run2["total_ordinary_income"]
        assert run1["matched_sales"] == run2["matched_sales"]

        results = repo.get_sale_results(2024)
        assert len(results) == 1

    def test_auto_created_lots_cleaned_on_rerun(self, repo, engine):
        """Auto-created lots from previous run are deleted on re-run."""
        sale = Sale(
            id="sale-cleanup",
            lot_id="",
            security=Security(ticker="TEST", name="Test Stock"),
            date_acquired=date(2024, 3, 1),
            sale_date=date(2024, 6, 1),
            shares=Decimal("0"),
            proceeds_per_share=Decimal("10000"),
            broker_reported_basis=Decimal("8000"),
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        # Verify auto-lot was created
        lots_after_1 = repo.get_lots("TEST")
        auto_1 = [row for row in lots_after_1 if "Auto-created" in (row.get("notes") or "")]
        assert len(auto_1) == 1

        # Re-run: auto-lot should be cleaned up and re-created
        engine.reconcile(2024)

        lots_after_2 = repo.get_lots("TEST")
        auto_2 = [row for row in lots_after_2 if "Auto-created" in (row.get("notes") or "")]
        assert len(auto_2) == 1  # Still 1 (re-created, not accumulated)

        results = repo.get_sale_results(2024)
        assert len(results) == 1


class TestESPPQualifyingDisposition:
    """ESPP qualifying disposition: held > 2yr from offering AND > 1yr from purchase."""

    def test_qualifying_disposition(self, repo, engine):
        """ESPP qualifying: ordinary income = lesser of (actual gain, offering discount)."""
        _create_espp_lot(
            repo,
            lot_id="lot-espp-qual",
            event_id="evt-espp-qual",
            purchase_date=date(2022, 10, 31),
            shares=Decimal("133"),
            purchase_price=Decimal("56.31"),
            fmv_at_purchase=Decimal("66.25"),
            offering_date=date(2022, 5, 1),
            fmv_on_offering_date=Decimal("72.00"),
        )

        # Sell > 2 years from offering (2022-05-01 + 2yr = 2024-05-01)
        # and > 1 year from purchase (2022-10-31 + 1yr = 2023-10-31)
        sale = Sale(
            id="sale-espp-qual",
            lot_id="",
            security=COIN,
            date_acquired=date(2022, 10, 31),
            sale_date=date(2024, 11, 11),
            shares=Decimal("50"),
            proceeds_per_share=Decimal("280.00"),
            broker_reported_basis=Decimal("3312.50"),  # FMV * shares = 66.25*50
            basis_reported_to_irs=True,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_sale(sale)

        engine.reconcile(2024)

        results = repo.get_sale_results(2024)
        assert len(results) == 1
        assert "QUALIFYING" in (results[0]["notes"] or "")

        oi = Decimal(results[0]["ordinary_income"])
        # Qualifying: lesser of (actual gain per share, discount at offering)
        # actual gain = 280 - 56.31 = 223.69 per share
        # discount at offering = 72.00 - 56.31 = 15.69 per share
        # lesser = 15.69 per share * 50 = 784.50
        assert oi == Decimal("784.50")
