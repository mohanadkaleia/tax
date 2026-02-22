"""Tests for import deduplication: sale-level and batch-level checks."""

from datetime import date
from decimal import Decimal

import pytest

from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.models.enums import BrokerSource, EquityType
from app.parsing.detector import FormType
from app.models.equity_event import Sale, Security


@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = create_schema(db_path)
    yield conn
    conn.close()


@pytest.fixture
def repo(db_conn):
    return TaxRepository(db_conn)


def _make_sale(sale_id, ticker, sale_date, shares, proceeds_per_share):
    return Sale(
        id=sale_id,
        lot_id="",
        security=Security(ticker=ticker, name=f"{ticker} Corp"),
        date_acquired=date(2023, 1, 1),
        sale_date=sale_date,
        shares=shares,
        proceeds_per_share=proceeds_per_share,
        broker_reported_basis=Decimal("100"),
        basis_reported_to_irs=True,
        broker_source=BrokerSource.ROBINHOOD,
    )


class TestCheckSaleDuplicate:
    def test_duplicate_found(self, repo):
        """A sale with matching ticker/date/shares/proceeds is detected as duplicate."""
        batch_id = repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/test.csv",
            form_type="1099B", record_count=1,
        )
        sale = _make_sale("s1", "SBUX", date(2024, 3, 15), Decimal("10"), Decimal("95.50"))
        repo.save_sale(sale, batch_id)

        assert repo.check_sale_duplicate("SBUX", "2024-03-15", "10", "95.50") is True

    def test_no_duplicate_different_ticker(self, repo):
        """A sale with a different ticker is not flagged."""
        batch_id = repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/test.csv",
            form_type="1099B", record_count=1,
        )
        sale = _make_sale("s1", "SBUX", date(2024, 3, 15), Decimal("10"), Decimal("95.50"))
        repo.save_sale(sale, batch_id)

        assert repo.check_sale_duplicate("AAPL", "2024-03-15", "10", "95.50") is False

    def test_no_duplicate_different_shares(self, repo):
        """A sale with different share count is not flagged."""
        batch_id = repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/test.csv",
            form_type="1099B", record_count=1,
        )
        sale = _make_sale("s1", "SBUX", date(2024, 3, 15), Decimal("10"), Decimal("95.50"))
        repo.save_sale(sale, batch_id)

        assert repo.check_sale_duplicate("SBUX", "2024-03-15", "20", "95.50") is False


class TestCheckBatchDuplicate:
    def test_duplicate_found(self, repo):
        """Same file + tax year is detected as duplicate batch."""
        repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/sales.csv",
            form_type="1099B", record_count=5,
        )

        assert repo.check_batch_duplicate("/tmp/sales.csv", 2024) is True

    def test_no_duplicate_different_file(self, repo):
        """Different file path is not flagged."""
        repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/sales.csv",
            form_type="1099B", record_count=5,
        )

        assert repo.check_batch_duplicate("/tmp/other.csv", 2024) is False

    def test_no_duplicate_different_year(self, repo):
        """Same file but different tax year is not flagged."""
        repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/sales.csv",
            form_type="1099B", record_count=5,
        )

        assert repo.check_batch_duplicate("/tmp/sales.csv", 2023) is False

    def test_duplicate_no_year(self, repo):
        """When tax_year is None, matches on file_path alone."""
        repo.create_import_batch(
            source="manual", tax_year=2024, file_path="/tmp/lots.json",
            form_type="EQUITY_LOT", record_count=1,
        )

        assert repo.check_batch_duplicate("/tmp/lots.json", None) is True


class TestSavImportSkipsDuplicateSales:
    def test_duplicate_sales_skipped_in_save(self, repo, tmp_path):
        """Integration: _save_import_result skips sales that already exist in the DB."""
        from unittest.mock import MagicMock

        from app.cli import _save_import_result

        # Pre-insert a sale so it becomes a duplicate
        batch_id = repo.create_import_batch(
            source="robinhood", tax_year=2024, file_path="/tmp/first.csv",
            form_type="1099B", record_count=1,
        )
        existing_sale = _make_sale(
            "existing-1", "SBUX", date(2024, 3, 15), Decimal("10"), Decimal("95.50"),
        )
        repo.save_sale(existing_sale, batch_id)

        # Build a fake ImportResult with 2 sales: 1 duplicate, 1 new
        result = MagicMock()
        result.tax_year = 2024
        result.form_type = FormType.FORM_1099B
        result.forms = []
        result.events = []
        result.lots = []
        result.sales = [
            _make_sale("dup-1", "SBUX", date(2024, 3, 15), Decimal("10"), Decimal("95.50")),
            _make_sale("new-1", "AAPL", date(2024, 6, 1), Decimal("5"), Decimal("180.00")),
        ]

        file_path = tmp_path / "second.csv"
        file_path.touch()

        summary = _save_import_result(result, "robinhood", file_path, repo)

        assert summary["sales"] == 1  # only the new sale saved
        assert summary["skipped_sales"] == 1

        # Verify DB has exactly 2 sales total (1 existing + 1 new)
        all_sales = repo.get_sales()
        assert len(all_sales) == 2
