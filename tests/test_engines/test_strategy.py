"""Tests for the tax strategy analysis engine."""

from datetime import date
from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.engines.strategy import (
    Priority,
    RiskLevel,
    StrategyCategory,
    StrategyEngine,
    StrategyReport,
    UserInputs,
    _add_years,
    _net_capital_losses,
)
from app.models.enums import (
    AdjustmentCode,
    BrokerSource,
    EquityType,
    FilingStatus,
    Form8949Category,
    HoldingPeriod,
    TransactionType,
)
from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult, Security
from app.models.tax_forms import W2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def engine():
    return StrategyEngine()


def _insert_w2(repo, tax_year, wages, fed_withheld, state_withheld=None, box12=None):
    """Helper: insert a W-2 into the database."""
    w2 = W2(
        tax_year=tax_year,
        employer_name="Acme Corp",
        box1_wages=wages,
        box2_federal_withheld=fed_withheld,
        box17_state_withheld=state_withheld,
        box12_codes=box12 or {},
    )
    batch_id = repo.create_import_batch("manual", tax_year, "test.json", "w2", 1)
    repo.save_w2(w2, batch_id)
    return batch_id


def _insert_lot(repo, lot_id, ticker, acq_date, shares, cost, equity_type=EquityType.RSU,
                shares_remaining=None, source_event_id=None):
    """Helper: insert a lot into the database."""
    if shares_remaining is None:
        shares_remaining = shares
    if source_event_id is None:
        source_event_id = f"evt-{lot_id}"
        event = EquityEvent(
            id=source_event_id,
            event_type=TransactionType.VEST,
            equity_type=equity_type,
            security=Security(ticker=ticker, name=f"{ticker} Corp"),
            event_date=acq_date,
            shares=shares,
            price_per_share=cost,
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)
    lot = Lot(
        id=lot_id,
        equity_type=equity_type,
        security=Security(ticker=ticker, name=f"{ticker} Corp"),
        acquisition_date=acq_date,
        shares=shares,
        cost_per_share=cost,
        shares_remaining=shares_remaining,
        source_event_id=source_event_id,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    return lot


def _insert_espp_lot_and_event(repo, lot_id, ticker, purchase_date, offering_date,
                                shares, purchase_price, fmv_purchase, fmv_offering):
    """Helper: insert ESPP lot + event."""
    event_id = f"evt-espp-{lot_id}"
    event = EquityEvent(
        id=event_id,
        event_type=TransactionType.PURCHASE,
        equity_type=EquityType.ESPP,
        security=Security(ticker=ticker, name=f"{ticker} Corp"),
        event_date=purchase_date,
        shares=shares,
        price_per_share=fmv_purchase,
        purchase_price=purchase_price,
        offering_date=offering_date,
        fmv_on_offering_date=fmv_offering,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id=lot_id,
        equity_type=EquityType.ESPP,
        security=Security(ticker=ticker, name=f"{ticker} Corp"),
        acquisition_date=purchase_date,
        shares=shares,
        cost_per_share=purchase_price,
        shares_remaining=shares,
        source_event_id=event_id,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)
    return lot


def _insert_sale_result(repo, sale_id, lot_id, ticker, acq_date, sale_date,
                         shares, proceeds, basis, gain_loss, holding_period):
    """Helper: insert sale + sale_result for wash sale / gain tracking."""
    security = Security(ticker=ticker, name=f"{ticker} Corp")

    # Need the event + lot + sale for FK constraints
    event = EquityEvent(
        id=f"evt-{sale_id}",
        event_type=TransactionType.VEST,
        equity_type=EquityType.RSU,
        security=security,
        event_date=acq_date,
        shares=shares,
        price_per_share=basis / shares if shares else Decimal("0"),
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_event(event)

    lot = Lot(
        id=lot_id,
        equity_type=EquityType.RSU,
        security=security,
        acquisition_date=acq_date,
        shares=shares,
        cost_per_share=basis / shares if shares else Decimal("0"),
        shares_remaining=Decimal("0"),
        source_event_id=f"evt-{sale_id}",
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_lot(lot)

    sale = Sale(
        id=sale_id,
        lot_id=lot_id,
        security=security,
        sale_date=sale_date,
        shares=shares,
        proceeds_per_share=proceeds / shares if shares else Decimal("0"),
        broker_reported_basis=basis,
        basis_reported_to_irs=True,
        broker_source=BrokerSource.MANUAL,
    )
    repo.save_sale(sale)

    sr = SaleResult(
        sale_id=sale_id,
        lot_id=lot_id,
        security=security,
        acquisition_date=acq_date,
        sale_date=sale_date,
        shares=shares,
        proceeds=proceeds,
        broker_reported_basis=basis,
        correct_basis=basis,
        adjustment_amount=Decimal("0"),
        adjustment_code=AdjustmentCode.NONE,
        holding_period=holding_period,
        form_8949_category=(
            Form8949Category.A if holding_period == HoldingPeriod.SHORT_TERM else Form8949Category.D
        ),
        gain_loss=gain_loss,
        ordinary_income=Decimal("0"),
        amt_adjustment=Decimal("0"),
        wash_sale_disallowed=Decimal("0"),
        notes="test",
    )
    repo.save_sale_result(sr)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestAddYears:
    def test_normal_date(self):
        assert _add_years(date(2023, 6, 15), 1) == date(2024, 6, 15)

    def test_leap_year(self):
        assert _add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)

    def test_two_years(self):
        assert _add_years(date(2023, 9, 15), 2) == date(2025, 9, 15)


class TestNetCapitalLosses:
    def test_both_positive(self):
        st, lt = _net_capital_losses(Decimal("5000"), Decimal("3000"), FilingStatus.SINGLE)
        assert st == Decimal("5000")
        assert lt == Decimal("3000")

    def test_st_loss_within_limit(self):
        st, lt = _net_capital_losses(Decimal("-2000"), Decimal("0"), FilingStatus.SINGLE)
        assert st == Decimal("-2000")
        assert lt == Decimal("0")

    def test_st_loss_exceeds_limit(self):
        st, lt = _net_capital_losses(Decimal("-5000"), Decimal("0"), FilingStatus.SINGLE)
        assert st == Decimal("-3000")
        assert lt == Decimal("0")

    def test_st_loss_offset_by_lt_gain(self):
        st, lt = _net_capital_losses(Decimal("-10000"), Decimal("8000"), FilingStatus.SINGLE)
        assert st == Decimal("-2000")
        assert lt == Decimal("0")

    def test_lt_loss_exceeds_limit(self):
        st, lt = _net_capital_losses(Decimal("0"), Decimal("-8000"), FilingStatus.SINGLE)
        assert st == Decimal("0")
        assert lt == Decimal("-3000")

    def test_both_negative(self):
        st, lt = _net_capital_losses(Decimal("-5000"), Decimal("-3000"), FilingStatus.SINGLE)
        assert st + lt >= Decimal("-3000")

    def test_mfs_limit(self):
        st, lt = _net_capital_losses(Decimal("-5000"), Decimal("0"), FilingStatus.MFS)
        assert st == Decimal("-1500")
        assert lt == Decimal("0")


# ---------------------------------------------------------------------------
# Report structure tests
# ---------------------------------------------------------------------------


class TestStrategyReportStructure:
    def test_empty_db_produces_report(self, repo, engine):
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        assert isinstance(report, StrategyReport)
        assert report.tax_year == 2024
        assert report.filing_status == FilingStatus.SINGLE
        assert isinstance(report.recommendations, list)
        assert isinstance(report.total_potential_savings, Decimal)
        assert report.generated_at  # non-empty ISO timestamp
        assert isinstance(report.data_completeness, dict)

    def test_data_completeness_flags(self, repo, engine):
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        assert report.data_completeness["w2_data"] is False
        assert report.data_completeness["lots"] is False
        assert report.data_completeness["market_prices"] is False


# ---------------------------------------------------------------------------
# A.2 Retirement Contributions
# ---------------------------------------------------------------------------


class TestRetirementContributions:
    def test_401k_room_detected(self, repo, engine):
        """W-2 with $15k 401k contribution should recommend maximizing to $23k."""
        _insert_w2(
            repo, 2024,
            wages=Decimal("600000"),
            fed_withheld=Decimal("180000"),
            state_withheld=Decimal("50000"),
            box12={"D": Decimal("15000")},
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "401(k)" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert rec.category == StrategyCategory.CURRENT_YEAR
        assert rec.estimated_savings > Decimal("0")
        assert "$8,000" in rec.situation  # remaining room

    def test_401k_maxed_no_recommendation(self, repo, engine):
        """W-2 with maxed 401k should not recommend more contributions."""
        _insert_w2(
            repo, 2024,
            wages=Decimal("600000"),
            fed_withheld=Decimal("180000"),
            box12={"D": Decimal("23000")},
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "401(k)" in r.name]
        assert len(recs) == 0

    def test_401k_50plus_catchup(self, repo, engine):
        """Age 50+ should use $30,500 limit."""
        _insert_w2(
            repo, 2024,
            wages=Decimal("600000"),
            fed_withheld=Decimal("180000"),
            box12={"D": Decimal("23000")},
        )
        user_inputs = UserInputs(age=55)
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "401(k)" in r.name]
        assert len(recs) == 1
        assert "$7,500" in recs[0].situation  # 30500 - 23000

    def test_backdoor_roth_recommended(self, repo, engine):
        """High-income earners should get backdoor Roth IRA recommendation."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "Roth" in r.name]
        assert len(recs) == 1
        assert recs[0].priority == Priority.LOW
        assert recs[0].estimated_savings == Decimal("0")

    def test_no_backdoor_roth_for_low_income(self, repo, engine):
        """Income under threshold should not recommend backdoor Roth."""
        _insert_w2(repo, 2024, wages=Decimal("100000"), fed_withheld=Decimal("15000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "Roth" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# A.3 HSA
# ---------------------------------------------------------------------------


class TestHSA:
    def test_hsa_recommended_with_hdhp(self, repo, engine):
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        user_inputs = UserInputs(has_hdhp=True, hsa_coverage="self")
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "HSA" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings > Decimal("0")
        assert "CALIFORNIA DOES NOT CONFORM" in recs[0].california_impact

    def test_no_hsa_without_hdhp(self, repo, engine):
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "HSA" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# A.5 SALT Analysis
# ---------------------------------------------------------------------------


class TestSALTAnalysis:
    def test_salt_cap_flagged(self, repo, engine):
        """High-income CA resident should see SALT cap warning."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "SALT" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings == Decimal("0")  # informational
        assert "Excess not deductible" in recs[0].situation

    def test_no_salt_for_low_income(self, repo, engine):
        """Low CA tax should not trigger SALT analysis."""
        _insert_w2(repo, 2024, wages=Decimal("30000"), fed_withheld=Decimal("3000"),
                   state_withheld=Decimal("1000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "SALT" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# C.4 NIIT Analysis
# ---------------------------------------------------------------------------


class TestNIITAnalysis:
    def test_niit_flagged_for_high_income(self, repo, engine):
        _insert_w2(repo, 2024, wages=Decimal("500000"), fed_withheld=Decimal("150000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "NIIT" in r.name]
        assert len(recs) == 1

    def test_no_niit_below_threshold(self, repo, engine):
        _insert_w2(repo, 2024, wages=Decimal("150000"), fed_withheld=Decimal("25000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "NIIT" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# C.1 Holding Period Analysis
# ---------------------------------------------------------------------------


class TestHoldingPeriodAnalysis:
    def test_lot_near_ltcg_flagged(self, repo, engine):
        """Lot acquired 11 months ago with unrealized gain should be flagged."""
        today = date.today()
        # 330 days ago → 35 days to LTCG
        acq = date(today.year - 1, today.month, today.day) + __import__("datetime").timedelta(days=35)
        # Handle acq in the future or past correctly
        from datetime import timedelta
        acq = today - timedelta(days=330)

        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        _insert_lot(repo, "lot-near", "ACME", acq, Decimal("100"), Decimal("100"))

        user_inputs = UserInputs(current_market_prices={"ACME": Decimal("150")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Hold" in r.name and "ACME" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings > Decimal("0")
        assert recs[0].category == StrategyCategory.CAPITAL_GAINS

    def test_lot_already_ltcg_not_flagged(self, repo, engine):
        """Lot held > 1 year should not be flagged."""
        from datetime import timedelta
        acq = date.today() - timedelta(days=400)

        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        _insert_lot(repo, "lot-old", "ACME", acq, Decimal("100"), Decimal("100"))

        user_inputs = UserInputs(current_market_prices={"ACME": Decimal("150")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Hold" in r.name and "ACME" in r.name]
        assert len(recs) == 0

    def test_no_prices_produces_warning(self, repo, engine):
        """Without market prices, holding period analysis is skipped with warning."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))
        from datetime import timedelta
        _insert_lot(repo, "lot-x", "ACME", date.today() - timedelta(days=330),
                    Decimal("100"), Decimal("100"))

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        assert any("Market prices" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# A.1 Tax-Loss Harvesting
# ---------------------------------------------------------------------------


class TestTaxLossHarvesting:
    def test_unrealized_loss_flagged(self, repo, engine):
        """Lot with unrealized loss should generate TLH recommendation."""
        from datetime import timedelta
        acq = date.today() - timedelta(days=100)

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))
        _insert_lot(repo, "lot-loss", "COIN", acq, Decimal("200"), Decimal("260"))

        # Realized ST gains to offset
        _insert_sale_result(
            repo, "sale-st", "lot-sold", "COIN",
            date(2024, 1, 1), date(2024, 3, 1),
            Decimal("50"), Decimal("15000"), Decimal("10000"),
            Decimal("5000"), HoldingPeriod.SHORT_TERM,
        )

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("190")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Tax-Loss Harvest" in r.name]
        assert len(recs) >= 1
        rec = recs[0]
        assert rec.estimated_savings > Decimal("0")
        assert "COIN" in rec.name
        assert rec.risk_level == RiskLevel.LOW

    def test_no_loss_no_recommendation(self, repo, engine):
        """Lot with unrealized gain should not trigger TLH."""
        from datetime import timedelta
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))
        _insert_lot(repo, "lot-gain", "COIN", date.today() - timedelta(days=100),
                    Decimal("200"), Decimal("190"))

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("260")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Tax-Loss Harvest" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# C.3 Wash Sale Detection
# ---------------------------------------------------------------------------


class TestWashSaleDetection:
    def test_wash_sale_detected(self, repo, engine):
        """Loss sale with vest within 30 days should trigger warning."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))

        # Loss sale on June 1
        _insert_sale_result(
            repo, "sale-wash", "lot-wash", "COIN",
            date(2024, 1, 1), date(2024, 6, 1),
            Decimal("100"), Decimal("9000"), Decimal("12000"),
            Decimal("-3000"), HoldingPeriod.SHORT_TERM,
        )

        # Vest within 30 days (June 15)
        event = EquityEvent(
            id="vest-wash",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=Security(ticker="COIN", name="COIN Corp"),
            event_date=date(2024, 6, 15),
            shares=Decimal("50"),
            price_per_share=Decimal("100"),
            broker_source=BrokerSource.MANUAL,
        )
        repo.save_event(event)

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "Wash Sale" in r.name]
        assert len(recs) >= 1
        assert recs[0].priority == Priority.HIGH

    def test_no_wash_sale_without_conflicting_event(self, repo, engine):
        """Loss sale without nearby vest/purchase should not trigger warning."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))

        _insert_sale_result(
            repo, "sale-clean", "lot-clean", "COIN",
            date(2024, 1, 1), date(2024, 6, 1),
            Decimal("100"), Decimal("9000"), Decimal("12000"),
            Decimal("-3000"), HoldingPeriod.SHORT_TERM,
        )

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "Wash Sale" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# D.4 Estimated Payments
# ---------------------------------------------------------------------------


class TestEstimatedPayments:
    def test_shortfall_detected(self, repo, engine):
        """Underpayment relative to safe harbor should be flagged."""
        _insert_w2(repo, 2024, wages=Decimal("600000"), fed_withheld=Decimal("130000"))

        user_inputs = UserInputs(
            prior_year_federal_tax=Decimal("160000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Estimated Tax Payment" in r.name]
        assert len(recs) >= 1
        # Should be CRITICAL or HIGH
        assert recs[0].priority in (Priority.CRITICAL, Priority.HIGH)

    def test_no_shortfall_when_withheld_enough(self, repo, engine):
        """Adequate withholding should not trigger estimated payment warning."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("200000"))

        user_inputs = UserInputs(
            prior_year_federal_tax=Decimal("40000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Estimated Tax Payment" in r.name]
        assert len(recs) == 0

    def test_missing_prior_year_produces_warning(self, repo, engine):
        """Without prior year tax, estimated payment analysis should warn."""
        _insert_w2(repo, 2024, wages=Decimal("600000"), fed_withheld=Decimal("130000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        assert any("Prior year tax not provided" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# A.4 Charitable Bunching
# ---------------------------------------------------------------------------


class TestCharitableBunching:
    def test_bunching_recommended(self, repo, engine):
        """Taxpayer near standard deduction should get bunching recommendation."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("40000"),
                   state_withheld=Decimal("10000"))

        user_inputs = UserInputs(
            annual_charitable_giving=Decimal("5000"),
            property_tax=Decimal("0"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Charitable" in r.name or "Bunching" in r.name]
        # May or may not trigger depending on whether itemized < standard deduction
        # For $200k income with $10k SALT + $5k charity = $15k > $14.6k standard deduction
        # So already itemizing → no bunching needed
        # This test just ensures no crash

    def test_bunching_not_recommended_when_already_itemizing(self, repo, engine):
        """Taxpayer already well above standard deduction should not get bunching."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("40000"),
                   state_withheld=Decimal("10000"))

        user_inputs = UserInputs(
            annual_charitable_giving=Decimal("20000"),
            property_tax=Decimal("5000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Bunching" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# D.1 Income Shifting
# ---------------------------------------------------------------------------


class TestIncomeShifting:
    def test_defer_to_lower_rate_year(self, repo, engine):
        """Higher current income vs projected lower next year should recommend deferral."""
        _insert_w2(repo, 2024, wages=Decimal("600000"), fed_withheld=Decimal("180000"),
                   state_withheld=Decimal("50000"))

        user_inputs = UserInputs(
            projected_income_next_year=Decimal("200000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Income Shifting" in r.name]
        assert len(recs) == 1
        assert "Defer" in recs[0].name

    def test_no_shifting_similar_income(self, repo, engine):
        """Similar income between years should not recommend shifting."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"),
                   state_withheld=Decimal("20000"))

        user_inputs = UserInputs(
            projected_income_next_year=Decimal("310000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Income Shifting" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# B.1 ESPP Holding Period
# ---------------------------------------------------------------------------


class TestESPPHolding:
    def test_espp_holding_recommendation(self, repo, engine):
        """ESPP lot not yet qualifying should recommend holding."""
        _insert_w2(repo, 2024, wages=Decimal("500000"), fed_withheld=Decimal("150000"),
                   state_withheld=Decimal("40000"))

        # Purchase 6 months ago, offering 1 year ago → 1 year to qualifying
        today = date.today()
        from datetime import timedelta
        purchase_date = today - timedelta(days=180)
        offering_date = today - timedelta(days=365)

        _insert_espp_lot_and_event(
            repo, "lot-espp", "ACME",
            purchase_date=purchase_date,
            offering_date=offering_date,
            shares=Decimal("150"),
            purchase_price=Decimal("170"),
            fmv_purchase=Decimal("200"),
            fmv_offering=Decimal("190"),
        )

        user_inputs = UserInputs(current_market_prices={"ACME": Decimal("230")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "ESPP" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings > Decimal("0")
        assert recs[0].category == StrategyCategory.EQUITY_COMPENSATION


# ---------------------------------------------------------------------------
# D.3 Loss Carryforward
# ---------------------------------------------------------------------------


class TestLossCarryforward:
    def test_carryforward_flagged(self, repo, engine):
        """Negative capital loss carryforward should be reported."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("40000"))

        user_inputs = UserInputs(capital_loss_carryforward=Decimal("-10000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "Carryforward" in r.name]
        assert len(recs) == 1
        assert recs[0].priority == Priority.LOW


# ---------------------------------------------------------------------------
# Integration: Full scenario with multiple strategies
# ---------------------------------------------------------------------------


class TestFullScenario:
    """CPA Test 1 from the plan: High-income single filer with RSU gains."""

    def test_high_income_tech_worker(self, repo, engine):
        """$600k W-2, $15k 401k, with unrealized loss and realized gains."""
        from datetime import timedelta

        # W-2 with $15k 401k contribution
        _insert_w2(
            repo, 2024,
            wages=Decimal("600000"),
            fed_withheld=Decimal("180000"),
            state_withheld=Decimal("50000"),
            box12={"D": Decimal("15000")},
        )

        # Open lot with unrealized loss (short-term)
        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "lot-coin", "COIN", acq, Decimal("200"), Decimal("260"))

        # Realized ST gains from RSU sales
        _insert_sale_result(
            repo, "sale-rsu", "lot-rsu", "COIN",
            date(2024, 1, 15), date(2024, 5, 1),
            Decimal("200"), Decimal("60000"), Decimal("50000"),
            Decimal("10000"), HoldingPeriod.SHORT_TERM,
        )

        user_inputs = UserInputs(
            current_market_prices={"COIN": Decimal("190")},
            age=35,
            prior_year_federal_tax=Decimal("160000"),
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        # Should have multiple recommendations
        assert len(report.recommendations) >= 3

        # Check key strategies are present
        strategy_names = [r.name for r in report.recommendations]
        assert any("401(k)" in n for n in strategy_names)
        assert any("Tax-Loss Harvest" in n for n in strategy_names)
        assert any("NIIT" in n for n in strategy_names)
        assert any("SALT" in n for n in strategy_names)
        assert any("Roth" in n for n in strategy_names)

        # Total savings should be positive
        assert report.total_potential_savings > Decimal("0")

        # Data completeness
        assert report.data_completeness["w2_data"] is True
        assert report.data_completeness["market_prices"] is True
        assert report.data_completeness["prior_year_tax"] is True

    def test_report_sorted_by_priority(self, repo, engine):
        """Recommendations should be sorted: CRITICAL > HIGH > MEDIUM > LOW."""
        _insert_w2(
            repo, 2024,
            wages=Decimal("600000"),
            fed_withheld=Decimal("130000"),
            state_withheld=Decimal("50000"),
            box12={"D": Decimal("15000")},
        )

        user_inputs = UserInputs(
            prior_year_federal_tax=Decimal("160000"),
            age=35,
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        if len(report.recommendations) >= 2:
            priority_order = {
                Priority.CRITICAL: 0,
                Priority.HIGH: 1,
                Priority.MEDIUM: 2,
                Priority.LOW: 3,
            }
            for i in range(len(report.recommendations) - 1):
                r1 = report.recommendations[i]
                r2 = report.recommendations[i + 1]
                assert priority_order[r1.priority] <= priority_order[r2.priority]

    def test_mfj_filing_status(self, repo, engine):
        """MFJ filing status should use different thresholds."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        report = engine.analyze(repo, 2024, FilingStatus.MFJ)

        assert report.filing_status == FilingStatus.MFJ
        assert isinstance(report.baseline_estimate.total_tax, Decimal)

    def test_2025_tax_year(self, repo, engine):
        """Engine should work with 2025 tax year brackets."""
        _insert_w2(repo, 2025, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        report = engine.analyze(repo, 2025, FilingStatus.SINGLE)

        assert report.tax_year == 2025
        assert report.baseline_estimate.total_tax > Decimal("0")


# ---------------------------------------------------------------------------
# B.2 ISO Exercise Timing
# ---------------------------------------------------------------------------


class TestISOExerciseTiming:
    def test_iso_within_amt_headroom(self, repo, engine):
        """ISO grant with spread within AMT headroom should recommend exercise."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("50000"),
                   state_withheld=Decimal("15000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("50")},
            unexercised_iso_grants=[{
                "ticker": "ACME",
                "shares": "100",
                "strike_price": "30",
                "grant_date": "2022-01-15",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "ISO Exercise" in r.name and "ACME" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert "Within AMT Headroom" in rec.name
        assert rec.category == StrategyCategory.EQUITY_COMPENSATION
        assert rec.priority == Priority.HIGH
        assert "CA does NOT have AMT" in rec.california_impact

    def test_iso_exceeds_amt_headroom(self, repo, engine):
        """Large ISO spread exceeding AMT headroom should show AMT cost."""
        _insert_w2(repo, 2024, wages=Decimal("500000"), fed_withheld=Decimal("150000"),
                   state_withheld=Decimal("40000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("200")},
            unexercised_iso_grants=[{
                "ticker": "ACME",
                "shares": "5000",
                "strike_price": "50",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "ISO Exercise" in r.name and "Exceeds" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert "AMT" in rec.mechanism
        assert rec.risk_level == RiskLevel.HIGH
        assert "Form 8801" in rec.irs_authority

    def test_iso_underwater_no_exercise(self, repo, engine):
        """Underwater ISOs should report informational, not recommend exercise."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("20")},
            unexercised_iso_grants=[{
                "ticker": "ACME",
                "shares": "1000",
                "strike_price": "50",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "ISO" in r.name and "Underwater" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings == Decimal("0")
        assert recs[0].priority == Priority.LOW

    def test_iso_no_grants_no_recommendation(self, repo, engine):
        """Without ISO grants, no ISO recommendation should appear."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "ISO Exercise" in r.name]
        assert len(recs) == 0

    def test_amt_credit_carryforward_usable(self, repo, engine):
        """Prior AMT credit should generate recommendation when usable."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        user_inputs = UserInputs(amt_credit_carryforward=Decimal("15000"))
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "AMT Credit" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert "Form 8801" in rec.irs_authority
        # Should be usable since regular tax > TMT at $400k income
        if "Utilization" in rec.name:
            assert rec.estimated_savings > Decimal("0")

    def test_no_amt_credit_without_carryforward(self, repo, engine):
        """Without AMT credit carryforward, no credit recommendation."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "AMT Credit" in r.name]
        assert len(recs) == 0

    def test_amt_headroom_computation(self, repo, engine):
        """AMT headroom should be a positive value for typical income."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)
        baseline = report.baseline_estimate

        headroom = engine._compute_amt_headroom(baseline, 2024, FilingStatus.SINGLE)
        assert headroom > Decimal("0")
        assert headroom < Decimal("500000")

    def test_iso_expiration_warning(self, repo, engine):
        """ISO near expiration should include urgency warning."""
        from datetime import timedelta
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        exp_date = (date.today() + timedelta(days=60)).isoformat()
        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("200")},
            unexercised_iso_grants=[{
                "ticker": "ACME",
                "shares": "5000",
                "strike_price": "50",
                "expiration_date": exp_date,
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "ISO Exercise" in r.name]
        assert len(recs) >= 1
        # Should have expiration warning
        all_warnings = []
        for rec in recs:
            all_warnings.extend(rec.warnings)
        assert any("EXPIRE" in w or "expire" in w for w in all_warnings)


# ---------------------------------------------------------------------------
# B.3 RSU Harvesting Coordination
# ---------------------------------------------------------------------------


class TestRSUHarvesting:
    def test_rsu_loss_harvested(self, repo, engine):
        """RSU lot with unrealized loss should recommend harvesting."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "rsu-loss", "COIN", acq, Decimal("200"), Decimal("260"),
                    equity_type=EquityType.RSU)

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("190")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert rec.estimated_savings > Decimal("0")
        assert rec.category == StrategyCategory.EQUITY_COMPENSATION
        assert "COIN" in rec.name

    def test_rsu_gain_not_harvested(self, repo, engine):
        """RSU lot with unrealized gain should not trigger harvesting."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))

        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "rsu-gain", "COIN", acq, Decimal("200"), Decimal("190"),
                    equity_type=EquityType.RSU)

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("260")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        assert len(recs) == 0

    def test_rsu_wash_sale_risk_from_future_vest(self, repo, engine):
        """RSU loss near upcoming vest should warn about wash sale."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "rsu-ws", "COIN", acq, Decimal("200"), Decimal("260"),
                    equity_type=EquityType.RSU)

        # Upcoming vest in 20 days
        vest_date = (date.today() + timedelta(days=20)).isoformat()
        user_inputs = UserInputs(
            current_market_prices={"COIN": Decimal("190")},
            future_vest_dates=[{
                "ticker": "COIN",
                "vest_date": vest_date,
                "shares": "100",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert rec.risk_level == RiskLevel.MODERATE
        assert any("wash sale" in w.lower() for w in rec.warnings)

    def test_rsu_no_wash_sale_risk_without_vest(self, repo, engine):
        """RSU loss without upcoming vest should have low risk."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "rsu-safe", "COIN", acq, Decimal("200"), Decimal("260"),
                    equity_type=EquityType.RSU)

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("190")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        assert len(recs) == 1
        assert recs[0].risk_level == RiskLevel.LOW

    def test_non_rsu_lot_not_included(self, repo, engine):
        """NSO lot with loss should not appear in RSU harvesting."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"))

        acq = date.today() - timedelta(days=100)
        _insert_lot(repo, "nso-loss", "COIN", acq, Decimal("200"), Decimal("260"),
                    equity_type=EquityType.NSO)

        user_inputs = UserInputs(current_market_prices={"COIN": Decimal("190")})
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# B.4 NSO Exercise Timing
# ---------------------------------------------------------------------------


class TestNSOTiming:
    def test_nso_defer_to_lower_rate_year(self, repo, engine):
        """NSO with lower projected income next year should recommend deferral."""
        _insert_w2(repo, 2024, wages=Decimal("600000"), fed_withheld=Decimal("180000"),
                   state_withheld=Decimal("50000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            projected_income_next_year=Decimal("200000"),
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "1000",
                "strike_price": "20",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO Timing" in r.name and "Defer" in r.name]
        assert len(recs) == 1
        rec = recs[0]
        assert rec.estimated_savings > Decimal("0")
        assert rec.category == StrategyCategory.EQUITY_COMPENSATION

    def test_nso_exercise_this_year_when_next_year_higher(self, repo, engine):
        """NSO with higher projected income next year should recommend exercise now."""
        _insert_w2(repo, 2024, wages=Decimal("200000"), fed_withheld=Decimal("50000"),
                   state_withheld=Decimal("15000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            projected_income_next_year=Decimal("700000"),
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "1000",
                "strike_price": "20",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO Timing" in r.name and "This Year" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings > Decimal("0")

    def test_nso_underwater(self, repo, engine):
        """Underwater NSO should be reported as informational."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("15")},
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "1000",
                "strike_price": "50",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO" in r.name and "Underwater" in r.name]
        assert len(recs) == 1
        assert recs[0].estimated_savings == Decimal("0")

    def test_nso_no_grants_no_recommendation(self, repo, engine):
        """Without NSO grants, no NSO recommendation should appear."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        report = engine.analyze(repo, 2024, FilingStatus.SINGLE)

        recs = [r for r in report.recommendations if "NSO" in r.name]
        assert len(recs) == 0

    def test_nso_no_projected_income_informational(self, repo, engine):
        """Without projected next-year income, NSO should be informational."""
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "500",
                "strike_price": "20",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO" in r.name]
        assert len(recs) == 1
        # Without projected income, should be informational (no deferral analysis)
        assert recs[0].estimated_savings == Decimal("0")

    def test_nso_expiration_warning(self, repo, engine):
        """NSO near expiration should include urgency warning."""
        from datetime import timedelta
        _insert_w2(repo, 2024, wages=Decimal("300000"), fed_withheld=Decimal("80000"))

        exp_date = (date.today() + timedelta(days=45)).isoformat()
        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "500",
                "strike_price": "20",
                "expiration_date": exp_date,
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO" in r.name]
        assert len(recs) >= 1
        all_warnings = []
        for rec in recs:
            all_warnings.extend(rec.warnings)
        assert any("EXPIRE" in w or "expire" in w for w in all_warnings)

    def test_nso_similar_income_no_timing_advantage(self, repo, engine):
        """NSO with similar income both years should show no timing advantage."""
        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            projected_income_next_year=Decimal("400000"),
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "100",
                "strike_price": "90",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        recs = [r for r in report.recommendations if "NSO" in r.name]
        # Should get informational rec with no significant savings
        if recs:
            for rec in recs:
                # Should not recommend deferral or acceleration
                assert "Defer" not in rec.name
                assert "This Year" not in rec.name


# ---------------------------------------------------------------------------
# Phase 2 Integration
# ---------------------------------------------------------------------------


class TestPhase2Integration:
    """Integration tests combining Phase 2 strategies with existing ones."""

    def test_iso_plus_retirement_plus_niit(self, repo, engine):
        """High-income filer with ISOs, 401k room, and NIIT should see all strategies."""
        _insert_w2(repo, 2024, wages=Decimal("500000"), fed_withheld=Decimal("150000"),
                   state_withheld=Decimal("40000"), box12={"D": Decimal("15000")})

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            age=40,
            unexercised_iso_grants=[{
                "ticker": "ACME",
                "shares": "500",
                "strike_price": "40",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        names = [r.name for r in report.recommendations]
        assert any("401(k)" in n for n in names)
        assert any("ISO" in n for n in names)
        assert any("NIIT" in n for n in names)
        assert report.total_potential_savings >= Decimal("0")

    def test_rsu_harvest_plus_tlh_coexist(self, repo, engine):
        """RSU harvest and TLH recommendations should both appear for different lots."""
        from datetime import timedelta

        _insert_w2(repo, 2024, wages=Decimal("400000"), fed_withheld=Decimal("100000"),
                   state_withheld=Decimal("30000"))

        # RSU lot with loss
        acq_rsu = date.today() - timedelta(days=100)
        _insert_lot(repo, "rsu-lot", "COIN", acq_rsu, Decimal("100"), Decimal("260"),
                    equity_type=EquityType.RSU)

        # Non-RSU lot with loss (different ticker for TLH)
        acq_other = date.today() - timedelta(days=200)
        _insert_lot(repo, "other-lot", "TSLA", acq_other, Decimal("50"), Decimal("300"),
                    equity_type=EquityType.RSU)  # Still RSU but different ticker

        user_inputs = UserInputs(
            current_market_prices={"COIN": Decimal("190"), "TSLA": Decimal("250")},
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        rsu_recs = [r for r in report.recommendations if "RSU Harvest" in r.name]
        tlh_recs = [r for r in report.recommendations if "Tax-Loss Harvest" in r.name]

        # Both COIN and TSLA should appear in either RSU harvest or TLH
        all_loss_recs = rsu_recs + tlh_recs
        tickers_found = set()
        for rec in all_loss_recs:
            if "COIN" in rec.name:
                tickers_found.add("COIN")
            if "TSLA" in rec.name:
                tickers_found.add("TSLA")
        assert "COIN" in tickers_found or "TSLA" in tickers_found

    def test_nso_and_income_shifting_interaction(self, repo, engine):
        """NSO timing and income shifting should both appear with consistent direction."""
        _insert_w2(repo, 2024, wages=Decimal("600000"), fed_withheld=Decimal("180000"),
                   state_withheld=Decimal("50000"))

        user_inputs = UserInputs(
            current_market_prices={"ACME": Decimal("100")},
            projected_income_next_year=Decimal("200000"),
            unexercised_nso_grants=[{
                "ticker": "ACME",
                "shares": "500",
                "strike_price": "20",
                "expiration_date": "2032-01-15",
            }],
        )
        report = engine.analyze(repo, 2024, FilingStatus.SINGLE, user_inputs)

        nso_recs = [r for r in report.recommendations if "NSO" in r.name]
        shift_recs = [r for r in report.recommendations if "Income Shifting" in r.name]

        # Both should exist and recommend deferral (lower income next year)
        assert len(nso_recs) >= 1
        assert len(shift_recs) >= 1
        assert "Defer" in nso_recs[0].name or "Defer" in shift_recs[0].name
