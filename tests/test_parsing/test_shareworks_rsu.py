"""Tests for Shareworks RSU Releases Report parser."""

import json
import sqlite3
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.parsing.detector import FormType, detect_form_type
from app.parsing.extractors.shareworks_rsu import ShareworksRSUExtractor
from app.ingestion.manual import ManualAdapter
from app.models.enums import BrokerSource, EquityType, TransactionType


# --- Sample vest records (as Vision API would produce) ---

SAMPLE_VEST_RECORDS = [
    {
        "grant_name": "RSU - P4P 2020",
        "grant_date": "2020-02-03",
        "vest_date": "2021-05-20",
        "release_price": "224.80",
        "shares_vested": 142,
        "shares_withheld": 57,
        "shares_sold": 0,
        "shares_net": 85,
        "taxable_compensation": "31921.60",
        "corporation_name": "Coinbase",
    },
    {
        "grant_name": "RSU - New Hire 2021",
        "grant_date": "2021-01-15",
        "vest_date": "2022-01-15",
        "release_price": "171.04",
        "shares_vested": 50,
        "shares_withheld": 20,
        "shares_sold": 0,
        "shares_net": 30,
        "taxable_compensation": "8552.00",
        "corporation_name": "Coinbase",
    },
]


class TestShareworksRSUDetector:
    def test_detect_releases_report(self):
        text = (
            "Coinbase - Releases Report (Details)\n"
            "Release Price (Cost Basis)\n"
            "Vest Date\n"
            "Vested  Withheld  Net"
        )
        assert detect_form_type(text) == FormType.SHAREWORKS_RSU_RELEASE

    def test_detect_takes_priority_over_generic(self):
        """Releases Report signature should match before generic forms."""
        text = (
            "Releases Report (Details)\n"
            "Release Price (Cost Basis)\n"
            "Vest Date\n"
            "Form W-2"
        )
        assert detect_form_type(text) == FormType.SHAREWORKS_RSU_RELEASE

    def test_does_not_false_match(self):
        text = "This is a generic report about releases and prices."
        assert detect_form_type(text) is None


class TestShareworksRSUExtractor:
    def setup_method(self):
        self.extractor = ShareworksRSUExtractor()

    def test_extract_returns_empty(self):
        """Text extraction returns empty (complex PDF layout), triggering Vision fallback."""
        text = "Releases Report (Details) Release Price (Cost Basis) Vest Date"
        records = self.extractor.extract(text)
        assert records == []

    def test_validate_complete(self):
        errors = self.extractor.validate_extraction(SAMPLE_VEST_RECORDS)
        assert errors == []

    def test_validate_missing_vest_date(self):
        data = [{"release_price": "224.80", "shares_vested": 142, "shares_net": 85}]
        errors = self.extractor.validate_extraction(data)
        assert any("vest_date" in e for e in errors)

    def test_validate_missing_release_price(self):
        data = [{"vest_date": "2021-05-20", "shares_vested": 142, "shares_net": 85}]
        errors = self.extractor.validate_extraction(data)
        assert any("release_price" in e for e in errors)

    def test_validate_missing_shares_vested(self):
        data = [{"vest_date": "2021-05-20", "release_price": "224.80", "shares_net": 85}]
        errors = self.extractor.validate_extraction(data)
        assert any("shares_vested" in e for e in errors)

    def test_validate_missing_shares_net(self):
        data = [{"vest_date": "2021-05-20", "release_price": "224.80", "shares_vested": 142}]
        errors = self.extractor.validate_extraction(data)
        assert any("shares_net" in e for e in errors)

    def test_validate_multiple_missing(self):
        data = [{}]
        errors = self.extractor.validate_extraction(data)
        assert len(errors) == 4


class TestManualAdapterShareworksRSU:
    def setup_method(self):
        self.adapter = ManualAdapter()

    def _write_json(self, data) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        json.dump(data, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_detect_form_type(self):
        ft = ManualAdapter._detect_form_type(SAMPLE_VEST_RECORDS)
        assert ft == FormType.SHAREWORKS_RSU_RELEASE

    def test_parse_creates_events_and_lots(self):
        path = self._write_json(SAMPLE_VEST_RECORDS)
        try:
            result = self.adapter.parse(path)

            assert result.form_type == FormType.SHAREWORKS_RSU_RELEASE
            assert len(result.events) == 2
            assert len(result.lots) == 2

            # Check first event
            ev = result.events[0]
            assert ev.event_type == TransactionType.VEST
            assert ev.equity_type == EquityType.RSU
            assert ev.event_date == date(2021, 5, 20)
            assert ev.shares == Decimal("142")
            assert ev.price_per_share == Decimal("224.80")
            assert ev.ordinary_income == Decimal("31921.60")
            assert ev.broker_source == BrokerSource.SHAREWORKS
            assert ev.security.ticker == "COIN"

            # Check first lot
            lot = result.lots[0]
            assert lot.equity_type == EquityType.RSU
            assert lot.acquisition_date == date(2021, 5, 20)
            assert lot.shares == Decimal("85")
            assert lot.cost_per_share == Decimal("224.80")
            assert lot.shares_remaining == Decimal("85")
            assert lot.amt_cost_per_share is None
            assert lot.broker_source == BrokerSource.SHAREWORKS
            assert lot.source_event_id == ev.id
            assert "RSU - P4P 2020" in lot.notes

            # Check second lot
            lot2 = result.lots[1]
            assert lot2.shares == Decimal("30")
            assert lot2.cost_per_share == Decimal("171.04")
        finally:
            path.unlink(missing_ok=True)

    def test_parse_single_record(self):
        path = self._write_json(SAMPLE_VEST_RECORDS[0])
        try:
            result = self.adapter.parse(path)
            assert len(result.events) == 1
            assert len(result.lots) == 1
        finally:
            path.unlink(missing_ok=True)

    def test_parse_grant_date(self):
        path = self._write_json(SAMPLE_VEST_RECORDS)
        try:
            result = self.adapter.parse(path)
            assert result.events[0].grant_date == date(2020, 2, 3)
            assert result.events[1].grant_date == date(2021, 1, 15)
        finally:
            path.unlink(missing_ok=True)

    def test_validate_valid(self):
        path = self._write_json(SAMPLE_VEST_RECORDS)
        try:
            result = self.adapter.parse(path)
            errors = self.adapter.validate(result)
            assert errors == []
        finally:
            path.unlink(missing_ok=True)

    def test_tax_year_from_vest_date(self):
        path = self._write_json(SAMPLE_VEST_RECORDS)
        try:
            result = self.adapter.parse(path)
            assert result.tax_year == 2021  # First vest date year
        finally:
            path.unlink(missing_ok=True)


class TestDeleteAutoLotsForTicker:
    def setup_method(self):
        from app.db.schema import create_schema
        from app.db.repository import TaxRepository

        self.conn = create_schema(":memory:")
        self.repo = TaxRepository(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _insert_lot(self, ticker, notes, shares="10"):
        from app.models.equity_event import Lot, Security
        from app.models.enums import BrokerSource, EquityType

        event_id = str(uuid4())
        # Insert a dummy event first
        self.conn.execute(
            """INSERT INTO equity_events
               (id, event_type, equity_type, ticker, security_name, event_date,
                shares, price_per_share, broker_source)
               VALUES (?, 'VEST', 'RSU', ?, ?, '2024-01-15', ?, '100.00', 'MANUAL')""",
            (event_id, ticker, ticker, shares),
        )

        lot = Lot(
            id=str(uuid4()),
            equity_type=EquityType.RSU,
            security=Security(ticker=ticker, name=ticker),
            acquisition_date=date(2024, 1, 15),
            shares=Decimal(shares),
            cost_per_share=Decimal("100.00"),
            shares_remaining=Decimal(shares),
            source_event_id=event_id,
            broker_source=BrokerSource.MANUAL,
            notes=notes,
        )
        self.repo.save_lot(lot)
        return lot.id

    def test_delete_auto_lots_for_ticker(self):
        self._insert_lot("COIN", "Auto-created from 1099-B sale")
        self._insert_lot("COIN", "Auto-created RSU lot")
        self._insert_lot("COIN", "Manual lot")  # Should not be deleted
        self._insert_lot("AAPL", "Auto-created from 1099-B sale")  # Different ticker

        deleted = self.repo.delete_auto_lots_for_ticker("COIN")
        assert deleted == 2

        remaining = self.repo.get_lots("COIN")
        assert len(remaining) == 1
        assert remaining[0]["notes"] == "Manual lot"

        # AAPL auto-created lot should still exist
        aapl_lots = self.repo.get_lots("AAPL")
        assert len(aapl_lots) == 1

    def test_delete_auto_lots_none_found(self):
        self._insert_lot("COIN", "Manual lot")
        deleted = self.repo.delete_auto_lots_for_ticker("COIN")
        assert deleted == 0
