"""Tests for ShareworksAdapter — Shareworks all-activities PDF parsing."""

from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.shareworks import ShareworksAdapter, _parse_date, _parse_amount
from app.models.enums import BrokerSource, EquityType, TransactionType

# Sample text fixture that matches the Shareworks PDF format (no real PII)
SAMPLE_RSU_RELEASE_BLOCK = """\
Share Units - Release (RBB693DDE8)
Grant Name: 2/10/2024 - RSU Delivery Method: Share Purchase and Holdings
Grant Date: 10-Feb-2024 Number of Restricted Awards Released:308
Market Price at Time of Grant: $141.99 USD Number of Restricted Awards Withheld:127
Release Price: $180.31 USD Gross Amount of Shares 181
Quantity Released: 308 Number of Restricted Awards Sold: 0
Release Date: 20-Feb-2024 Net Amount of Shares Issued 181
Settlement Date: 21-Feb-2024 Gross Release Value: $55,535.48 USD
Release Method: Withhold shares to cover taxes
Value of Shares Withheld
Total Value: $22,899.37 USD
"""

SAMPLE_MULTI_RELEASE = """\
Some header text
Company: TestCorp

Share Units - Release (RBAAA11111)
Grant Name: Test RSU Grant Delivery Method: Share Purchase and Holdings
Grant Date: 01-Jan-2024 Number of Restricted Awards Released:100
Market Price at Time of Grant: $50.00 USD Number of Restricted Awards Withheld:40
Release Price: $60.00 USD Gross Amount of Shares 60
Quantity Released: 100 Number of Restricted Awards Sold: 0
Release Date: 15-Mar-2024 Net Amount of Shares Issued 60
Settlement Date: 18-Mar-2024 Gross Release Value: $6,000.00 USD
Release Method: Withhold shares to cover taxes
Total Value: $2,400.00 USD

Share Units - Release (RBBBB22222)
Grant Name: P4P High Performance Award Delivery Method: Share Purchase and Holdings
Grant Date: 15-Jun-2023 Number of Restricted Awards Released:200
Market Price at Time of Grant: $45.00 USD Number of Restricted Awards Withheld:80
Release Price: $75.50 USD Gross Amount of Shares 120
Quantity Released: 200 Number of Restricted Awards Sold: 0
Release Date: 20-Aug-2024 Net Amount of Shares Issued 120
Settlement Date: 22-Aug-2024 Gross Release Value: $15,100.00 USD
Release Method: Withhold shares to cover taxes
Total Value: $6,040.00 USD

Share Units - Release (RBCCC33333)
Grant Name: ESPP Purchase Delivery Method: Share Purchase and Holdings
Grant Date: 01-May-2024 Number of Restricted Awards Released:50
Release Price: $55.00 USD Gross Amount of Shares 50
Quantity Released: 50 Number of Restricted Awards Sold: 0
Release Date: 14-Nov-2024 Net Amount of Shares Issued 50
Total Value: $2,750.00 USD
"""


class TestParseDateHelper:
    def test_standard_format(self):
        assert _parse_date("20-Feb-2024") == date(2024, 2, 20)

    def test_single_digit_day(self):
        assert _parse_date("5-Jan-2023") == date(2023, 1, 5)

    def test_all_months(self):
        months = [
            ("Jan", 1), ("Feb", 2), ("Mar", 3), ("Apr", 4),
            ("May", 5), ("Jun", 6), ("Jul", 7), ("Aug", 8),
            ("Sep", 9), ("Oct", 10), ("Nov", 11), ("Dec", 12),
        ]
        for abbr, num in months:
            d = _parse_date(f"15-{abbr}-2024")
            assert d.month == num

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_date("2024-02-20")  # Wrong format


class TestParseAmountHelper:
    def test_with_dollar_and_usd(self):
        assert _parse_amount("$180.31 USD") == Decimal("180.31")

    def test_with_commas(self):
        assert _parse_amount("$55,535.48 USD") == Decimal("55535.48")

    def test_plain_number(self):
        assert _parse_amount("180.31") == Decimal("180.31")


class TestShareworksAdapterParsing:
    """Test RSU release block parsing from text fixtures."""

    def test_parse_single_release_block(self):
        """Parse a single RSU release block and verify event + lot creation."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_rsu_releases(SAMPLE_RSU_RELEASE_BLOCK, security)

        assert len(result["events"]) == 1
        assert len(result["lots"]) == 1

        event = result["events"][0]
        assert event.event_type == TransactionType.VEST
        assert event.equity_type == EquityType.RSU
        assert event.event_date == date(2024, 2, 20)
        assert event.shares == Decimal("308")
        assert event.price_per_share == Decimal("180.31")
        assert event.grant_date == date(2024, 2, 10)
        assert event.broker_source == BrokerSource.SHAREWORKS

        lot = result["lots"][0]
        assert lot.equity_type == EquityType.RSU
        assert lot.acquisition_date == date(2024, 2, 20)
        assert lot.shares == Decimal("181")  # Net shares
        assert lot.cost_per_share == Decimal("180.31")  # FMV at vest
        assert lot.amt_cost_per_share is None  # RSU has no AMT
        assert lot.shares_remaining == Decimal("181")
        assert lot.source_event_id == event.id

    def test_parse_multiple_releases(self):
        """Parse multiple RSU release blocks."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="TEST", name="TestCorp")
        result = adapter._parse_rsu_releases(SAMPLE_MULTI_RELEASE, security)

        # Should find 2 RSU releases (skipping the ESPP one)
        assert len(result["events"]) == 2
        assert len(result["lots"]) == 2

        # First release: Test RSU Grant
        assert result["events"][0].event_date == date(2024, 3, 15)
        assert result["events"][0].shares == Decimal("100")
        assert result["events"][0].price_per_share == Decimal("60.00")
        assert result["lots"][0].shares == Decimal("60")

        # Second release: P4P High Performance Award
        assert result["events"][1].event_date == date(2024, 8, 20)
        assert result["events"][1].shares == Decimal("200")
        assert result["events"][1].price_per_share == Decimal("75.50")
        assert result["lots"][1].shares == Decimal("120")

    def test_espp_releases_skipped(self):
        """ESPP release blocks should be filtered out (covered by Form 3922)."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        espp_block = """\
Share Units - Release (RBESPP0001)
Grant Name: Employee Stock Purchase Plan Delivery Method: Share Purchase
Grant Date: 01-May-2024
Release Price: $55.00 USD
Quantity Released: 50
Release Date: 14-Nov-2024 Net Amount of Shares Issued 50
"""
        security = Security(ticker="TEST", name="TestCorp")
        result = adapter._parse_rsu_releases(espp_block, security)

        assert len(result["events"]) == 0
        assert len(result["lots"]) == 0

    def test_lot_links_to_event(self):
        """Each lot's source_event_id should reference the corresponding event."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_rsu_releases(SAMPLE_RSU_RELEASE_BLOCK, security)

        event_ids = {e.id for e in result["events"]}
        for lot in result["lots"]:
            assert lot.source_event_id in event_ids


class TestShareworksAdapterValidation:
    def test_valid_data_passes(self):
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_rsu_releases(SAMPLE_RSU_RELEASE_BLOCK, security)
        from app.ingestion.base import ImportResult
        from app.parsing.detector import FormType

        import_result = ImportResult(
            form_type=FormType.SHAREWORKS_SUPPLEMENTAL,
            tax_year=2024,
            events=result["events"],
            lots=result["lots"],
        )
        errors = adapter.validate(import_result)
        assert errors == []

    def test_zero_price_fails_validation(self):
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security, EquityEvent, Lot

        security = Security(ticker="TEST", name="TestCorp")
        event = EquityEvent(
            id="evt-bad",
            event_type=TransactionType.VEST,
            equity_type=EquityType.RSU,
            security=security,
            event_date=date(2024, 1, 1),
            shares=Decimal("100"),
            price_per_share=Decimal("0"),  # Bad: zero FMV
            broker_source=BrokerSource.SHAREWORKS,
        )
        lot = Lot(
            id="lot-bad",
            equity_type=EquityType.RSU,
            security=security,
            acquisition_date=date(2024, 1, 1),
            shares=Decimal("50"),
            cost_per_share=Decimal("0"),
            shares_remaining=Decimal("50"),
            source_event_id="evt-bad",
            broker_source=BrokerSource.SHAREWORKS,
        )

        from app.ingestion.base import ImportResult
        from app.parsing.detector import FormType

        import_result = ImportResult(
            form_type=FormType.SHAREWORKS_SUPPLEMENTAL,
            tax_year=2024,
            events=[event],
            lots=[lot],
        )
        errors = adapter.validate(import_result)
        assert len(errors) >= 1
        assert any("cost_per_share" in e or "release price" in e for e in errors)


# --- ISO Exercise block fixtures ---

SAMPLE_ISO_EXERCISE_BLOCK = """\
Exercise (ERH-7BF1F6B1)
Exercise Method: Exercise options and hold the shares, pay from Fair Market Value: $342.00 USD
cash holdings
Delivery Method: Share Purchase and Holdings Held Quantity: 250
Transaction Date: 19-Apr-2021 Taxable Compensation: $0.00 USD
  Amount Subject to AMT: $80,822.50 USD
Grants
Reference Number Award Type Grant Name Grant Date Grant Price Quantity
ERH-7BF1F6B1-1 Options (ISO) 2020-2-05 - 18.71 - ISO_2020-02-05_ 05-Feb-2020 $18.71USD 250
Option Award (ISO) - Coinbase_18.71
Cost
Description Value
Cost of Options $4,677.50 USD
United States Withholding $0.00 USD
"""

SAMPLE_MULTI_ISO_EXERCISE = """\
Some activity text here

Exercise (ERH-7BF1F6B1)
Exercise Method: Exercise options and hold the shares, pay from Fair Market Value: $342.00 USD
cash holdings
Delivery Method: Share Purchase and Holdings Held Quantity: 250
Transaction Date: 19-Apr-2021 Taxable Compensation: $0.00 USD
  Amount Subject to AMT: $80,822.50 USD
Grants
Reference Number Award Type Grant Name Grant Date Grant Price Quantity
ERH-7BF1F6B1-1 Options (ISO) 2020-2-05 - 18.71 - ISO_2020-02-05_ 05-Feb-2020 $18.71USD 250
Option Award (ISO) - Coinbase_18.71
Cost
Description Value
Cost of Options $4,677.50 USD

Exercise (ERH-7CA8D250)
Exercise Method: Exercise options and hold the shares, pay from Fair Market Value: $294.21 USD
cash holdings
Delivery Method: Share Purchase and Holdings Held Quantity: 250
Transaction Date: 03-May-2021 Taxable Compensation: $0.00 USD
  Amount Subject to AMT: $68,875.00 USD
Grants
Reference Number Award Type Grant Name Grant Date Grant Price Quantity
ERH-7CA8D250-1 Options (ISO) 2020-2-05 - 18.71 - ISO_2020-02-05_ 05-Feb-2020 $18.71USD 250
Option Award (ISO) - Coinbase_18.71
Cost
Description Value
Cost of Options $4,677.50 USD

Exercise (ERH-7BF1F6B1) Employee  250  $4,677.50
04-May-2021
"""

# A summary-only block (should be skipped — no "Exercise Method:")
SAMPLE_ISO_SUMMARY_ONLY = """\
Exercise (ERH-7BF1F6B1) Employee  250  $4,677.50
04-May-2021
"""


class TestISOExerciseParsing:
    """Test ISO exercise block parsing from text fixtures."""

    def test_parse_single_iso_exercise(self):
        """Parse a single ISO exercise block and verify event + lot."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_EXERCISE_BLOCK, security)

        assert len(result["events"]) == 1
        assert len(result["lots"]) == 1

        event = result["events"][0]
        assert event.event_type == TransactionType.EXERCISE
        assert event.equity_type == EquityType.ISO
        assert event.event_date == date(2021, 4, 19)
        assert event.shares == Decimal("250")
        assert event.price_per_share == Decimal("342.00")  # FMV at exercise
        assert event.strike_price == Decimal("18.71")
        assert event.grant_date == date(2020, 2, 5)
        assert event.ordinary_income == Decimal("0")  # ISO: no OI at exercise
        assert event.broker_source == BrokerSource.SHAREWORKS

        lot = result["lots"][0]
        assert lot.equity_type == EquityType.ISO
        assert lot.acquisition_date == date(2021, 4, 19)
        assert lot.shares == Decimal("250")
        assert lot.cost_per_share == Decimal("18.71")  # Strike price = regular basis
        assert lot.amt_cost_per_share == Decimal("342.00")  # FMV = AMT basis
        assert lot.shares_remaining == Decimal("250")
        assert lot.source_event_id == event.id

    def test_parse_multiple_iso_exercises(self):
        """Parse multiple ISO exercises, skipping summary blocks."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_MULTI_ISO_EXERCISE, security)

        # Should find 2 detailed blocks (skip the summary block at the end)
        assert len(result["events"]) == 2
        assert len(result["lots"]) == 2

        # First exercise: FMV $342.00, 19-Apr-2021
        assert result["events"][0].price_per_share == Decimal("342.00")
        assert result["events"][0].event_date == date(2021, 4, 19)

        # Second exercise: FMV $294.21, 03-May-2021
        assert result["events"][1].price_per_share == Decimal("294.21")
        assert result["events"][1].event_date == date(2021, 5, 3)

    def test_summary_block_skipped(self):
        """Summary-only exercise blocks (no Exercise Method:) are skipped."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_SUMMARY_ONLY, security)

        assert len(result["events"]) == 0
        assert len(result["lots"]) == 0

    def test_iso_lot_has_amt_basis(self):
        """ISO lots must have amt_cost_per_share set to FMV."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_EXERCISE_BLOCK, security)

        lot = result["lots"][0]
        # Regular basis = strike price
        assert lot.cost_per_share == Decimal("18.71")
        # AMT basis = FMV at exercise
        assert lot.amt_cost_per_share == Decimal("342.00")
        # AMT preference = (FMV - strike) * shares = (342.00 - 18.71) * 250 = $80,822.50
        expected_amt = (lot.amt_cost_per_share - lot.cost_per_share) * lot.shares
        assert expected_amt == Decimal("80822.50")

    def test_iso_lot_links_to_event(self):
        """Each ISO lot's source_event_id should reference its event."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_EXERCISE_BLOCK, security)

        event_ids = {e.id for e in result["events"]}
        for lot in result["lots"]:
            assert lot.source_event_id in event_ids

    def test_iso_exercise_raw_data(self):
        """Exercise events should store exercise_id and AMT amount in raw_data."""
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_EXERCISE_BLOCK, security)

        event = result["events"][0]
        assert event.raw_data["exercise_id"] == "ERH-7BF1F6B1"
        assert event.raw_data["amt_amount"] == "80822.50"


class TestISOValidation:
    """Test validation logic for ISO exercise data."""

    def test_valid_iso_passes(self):
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security
        from app.ingestion.base import ImportResult
        from app.parsing.detector import FormType

        security = Security(ticker="COIN", name="Coinbase")
        result = adapter._parse_iso_exercises(SAMPLE_ISO_EXERCISE_BLOCK, security)

        import_result = ImportResult(
            form_type=FormType.SHAREWORKS_SUPPLEMENTAL,
            tax_year=2021,
            events=result["events"],
            lots=result["lots"],
        )
        errors = adapter.validate(import_result)
        assert errors == []

    def test_zero_strike_fails_validation(self):
        adapter = ShareworksAdapter()
        from app.models.equity_event import Security, EquityEvent, Lot

        security = Security(ticker="TEST", name="TestCorp")
        event = EquityEvent(
            id="evt-iso-bad",
            event_type=TransactionType.EXERCISE,
            equity_type=EquityType.ISO,
            security=security,
            event_date=date(2021, 4, 19),
            shares=Decimal("250"),
            price_per_share=Decimal("342.00"),
            strike_price=Decimal("0"),  # Bad: zero strike
            broker_source=BrokerSource.SHAREWORKS,
        )
        lot = Lot(
            id="lot-iso-bad",
            equity_type=EquityType.ISO,
            security=security,
            acquisition_date=date(2021, 4, 19),
            shares=Decimal("250"),
            cost_per_share=Decimal("0"),  # Strike = 0
            amt_cost_per_share=Decimal("342.00"),
            shares_remaining=Decimal("250"),
            source_event_id="evt-iso-bad",
            broker_source=BrokerSource.SHAREWORKS,
        )
        from app.ingestion.base import ImportResult
        from app.parsing.detector import FormType

        import_result = ImportResult(
            form_type=FormType.SHAREWORKS_SUPPLEMENTAL,
            tax_year=2021,
            events=[event],
            lots=[lot],
        )
        errors = adapter.validate(import_result)
        assert len(errors) >= 1
        assert any("strike price" in e for e in errors)


class TestDetectCompany:
    def test_detect_coinbase(self):
        text = "Company: Coinbase\nSome other text"
        assert ShareworksAdapter._detect_company(text) == "Coinbase"

    def test_detect_unknown(self):
        text = "Some text without company"
        assert ShareworksAdapter._detect_company(text) == "Unknown"


class TestDetectTicker:
    def test_coinbase(self):
        assert ShareworksAdapter._detect_ticker("Coinbase") == "COIN"

    def test_unknown_company(self):
        assert ShareworksAdapter._detect_ticker("Acme Corp") == "ACME"
