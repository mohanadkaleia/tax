"""Morgan Stanley Shareworks adapter for all-activities PDF."""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.ingestion.base import BaseAdapter, ImportResult
from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, Security
from app.parsing.detector import FormType

# Month abbreviation to number
_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_date(text: str) -> date:
    """Parse Shareworks date format: '20-Feb-2024' or '10-Feb-2024'."""
    text = text.strip()
    m = re.match(r"(\d{1,2})-(\w{3})-(\d{4})", text)
    if not m:
        raise ValueError(f"Cannot parse date: {text!r}")
    day, mon, year = int(m.group(1)), _MONTH_MAP[m.group(2)], int(m.group(3))
    return date(year, mon, day)


def _parse_amount(text: str) -> Decimal:
    """Parse dollar amount: '$180.31 USD' or '$180.31' or '180.31'."""
    cleaned = text.replace("$", "").replace(",", "").replace("USD", "").strip()
    return Decimal(cleaned)


def _parse_int(text: str) -> int:
    """Parse integer with optional commas: '1,500' -> 1500."""
    return int(text.replace(",", "").strip())


class ShareworksAdapter(BaseAdapter):
    """Adapter for parsing Morgan Stanley Shareworks all-activities PDF."""

    def parse(self, file_path: Path) -> ImportResult:
        """Parse Shareworks all_activities.pdf into events, lots, and sales."""
        text = self._extract_text(file_path)
        company = self._detect_company(text)
        ticker = self._detect_ticker(company)
        security = Security(ticker=ticker, name=company)
        tax_year = self._detect_tax_year(file_path, text)

        events: list[EquityEvent] = []
        lots: list[Lot] = []

        # Parse RSU release blocks
        releases = self._parse_rsu_releases(text, security)
        events.extend(releases["events"])
        lots.extend(releases["lots"])

        # Parse ISO/NSO exercise blocks
        exercises = self._parse_iso_exercises(text, security)
        events.extend(exercises["events"])
        lots.extend(exercises["lots"])

        return ImportResult(
            form_type=FormType.SHAREWORKS_SUPPLEMENTAL,
            tax_year=tax_year,
            events=events,
            lots=lots,
        )

    def validate(self, data: ImportResult) -> list[str]:
        """Validate Shareworks data."""
        errors: list[str] = []
        for lot in data.lots:
            if lot.equity_type == EquityType.ISO:
                # ISO lots: cost_per_share is strike price (can be low but must be > 0)
                if lot.cost_per_share <= 0:
                    errors.append(
                        f"ISO lot {lot.id}: strike price must be > 0, "
                        f"got {lot.cost_per_share}"
                    )
                if lot.amt_cost_per_share is not None and lot.amt_cost_per_share <= 0:
                    errors.append(
                        f"ISO lot {lot.id}: AMT basis (FMV) must be > 0, "
                        f"got {lot.amt_cost_per_share}"
                    )
            else:
                # RSU lots: cost_per_share is FMV at vest
                if lot.cost_per_share <= 0:
                    errors.append(
                        f"RSU lot {lot.id}: cost_per_share (FMV) must be > 0, "
                        f"got {lot.cost_per_share}"
                    )
            if lot.shares <= 0:
                errors.append(f"Lot {lot.id}: shares must be > 0")
        for event in data.events:
            if event.equity_type == EquityType.RSU and event.price_per_share <= 0:
                errors.append(
                    f"RSU vest event {event.id}: release price must be > 0"
                )
            if event.equity_type == EquityType.ISO:
                if event.price_per_share <= 0:
                    errors.append(
                        f"ISO exercise event {event.id}: FMV must be > 0"
                    )
                if event.strike_price is not None and event.strike_price <= 0:
                    errors.append(
                        f"ISO exercise event {event.id}: strike price must be > 0"
                    )
        # Cross-validate: every lot should have a matching event
        event_ids = {e.id for e in data.events}
        for lot in data.lots:
            if lot.source_event_id not in event_ids:
                errors.append(
                    f"Lot {lot.id}: source_event_id {lot.source_event_id} "
                    f"not found in events"
                )
        return errors

    # --- Internal methods ---

    @staticmethod
    def _extract_text(file_path: Path) -> str:
        """Extract all text from PDF using pdfplumber."""
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
            # Strip zero-width spaces that Shareworks PDFs embed
            return "\n".join(pages).replace("\u200b", "")

    @staticmethod
    def _detect_company(text: str) -> str:
        """Detect company name from PDF header."""
        m = re.search(r"Company:\s*(\w[\w\s]*?)(?:\s*\n|​)", text)
        if m:
            return m.group(1).strip()
        return "Unknown"

    @staticmethod
    def _detect_ticker(company: str) -> str:
        """Map company name to stock ticker."""
        ticker_map = {
            "coinbase": "COIN",
        }
        return ticker_map.get(company.lower(), company.upper()[:4])

    @staticmethod
    def _detect_tax_year(file_path: Path, text: str) -> int:
        """Detect tax year from the PDF content or filename."""
        # Look for a year in the summary period
        m = re.search(r"Summary Period:.*?(\d{4})\s*$", text, re.MULTILINE)
        if m:
            # Use the end year of the summary period
            pass
        # Use the most recent release year (multi-year PDFs span many years)
        release_years = re.findall(r"Release Date:\s+\d{2}-\w{3}-(\d{4})", text)
        if release_years:
            return max(int(y) for y in release_years)
        # Fallback: current year
        return datetime.now().year

    def _parse_rsu_releases(
        self, text: str, security: Security
    ) -> dict[str, list]:
        """Parse all RSU release blocks from the PDF text.

        Each block starts with 'Share Units - Release (RBxxxxxx)'
        and contains release details including price, date, and shares.
        """
        events: list[EquityEvent] = []
        lots: list[Lot] = []

        # Split text into release blocks
        # Pattern: "Share Units - Release (RBxxxxxxxx)"
        blocks = re.split(r"(?=Share Units - Release \(RB[0-9A-Fa-f]+\))", text)

        for block in blocks:
            if not block.strip().startswith("Share Units - Release"):
                continue

            # Extract release ID
            release_id_match = re.search(
                r"Share Units - Release \((RB[0-9A-Fa-f]+)\)", block
            )
            if not release_id_match:
                continue
            release_id = release_id_match.group(1)

            # Extract grant name to determine equity type
            grant_match = re.search(r"Grant Name:\s*(.+?)(?:\s*(?:Delivery|Settlement))", block)
            grant_name = grant_match.group(1).strip() if grant_match else ""

            # Only process RSU releases (skip ESPP — covered by Form 3922)
            grant_upper = grant_name.upper()
            if "ESPP" in grant_upper or "EMPLOYEE STOCK PURCHASE" in grant_upper:
                continue

            # Must be RSU (contains "RSU" or "Restricted Stock Unit")
            if "RSU" not in grant_upper and "RESTRICTED STOCK" not in grant_upper:
                # Check if it's a transition grant or other RSU variant
                if "TRANSITION" not in grant_upper and "P4P" not in grant_upper:
                    continue

            # Extract Release Price (FMV at vest = cost basis)
            price_match = re.search(
                r"Release Price:\s*\$([\d,]+\.?\d*)", block
            )
            if not price_match:
                continue
            release_price = Decimal(price_match.group(1).replace(",", ""))

            # Extract Release Date
            date_match = re.search(
                r"Release Date:\s*(\d{1,2}-\w{3}-\d{4})", block
            )
            if not date_match:
                continue
            release_date = _parse_date(date_match.group(1))

            # Extract Quantity Released (gross shares)
            qty_match = re.search(r"Quantity Released:\s*([\d,]+)", block)
            if not qty_match:
                continue
            quantity_released = _parse_int(qty_match.group(1))

            # Extract Net Amount of Shares Issued
            net_match = re.search(
                r"Net Amount of Shares Issued\s*([\d,]+)", block
            )
            if not net_match:
                # Try alternative: "Gross Amount of Shares" minus withheld
                gross_match = re.search(
                    r"Gross Amount of Shares\s*([\d,]+)", block
                )
                if gross_match:
                    net_shares = _parse_int(gross_match.group(1))
                else:
                    continue
            else:
                net_shares = _parse_int(net_match.group(1))

            # Extract Grant Date
            grant_date_match = re.search(
                r"Grant Date:\s*(\d{1,2}-\w{3}-\d{4})", block
            )
            grant_date = (
                _parse_date(grant_date_match.group(1))
                if grant_date_match
                else None
            )

            # Create EquityEvent for the vest
            event_id = str(uuid4())
            event = EquityEvent(
                id=event_id,
                event_type=TransactionType.VEST,
                equity_type=EquityType.RSU,
                security=security,
                event_date=release_date,
                shares=Decimal(quantity_released),
                price_per_share=release_price,
                grant_date=grant_date,
                ordinary_income=Decimal(quantity_released) * release_price,
                broker_source=BrokerSource.SHAREWORKS,
                raw_data={
                    "release_id": release_id,
                    "grant_name": grant_name,
                    "net_shares": net_shares,
                },
            )
            events.append(event)

            # Create Lot for net shares (after withholding)
            lot = Lot(
                id=str(uuid4()),
                equity_type=EquityType.RSU,
                security=security,
                acquisition_date=release_date,
                shares=Decimal(net_shares),
                cost_per_share=release_price,
                amt_cost_per_share=None,  # RSU has no AMT implications
                shares_remaining=Decimal(net_shares),
                source_event_id=event_id,
                broker_source=BrokerSource.SHAREWORKS,
                notes=f"RSU release {release_id} from {grant_name}",
            )
            lots.append(lot)

        return {"events": events, "lots": lots}

    def _parse_iso_exercises(
        self, text: str, security: Security
    ) -> dict[str, list]:
        """Parse ISO/NSO exercise blocks from the PDF text.

        Detailed exercise blocks follow this pattern:
            Exercise (ERH-7BF1F6B1)
            Exercise Method: ... Fair Market Value: $342.00 USD
            ...Held Quantity: 250
            Transaction Date: 19-Apr-2021 Taxable Compensation: $0.00 USD
              Amount Subject to AMT: $80,822.50 USD
            Grants
            Reference Number Award Type Grant Name Grant Date Grant Price Quantity
            ERH-...-1 Options (ISO) ... 05-Feb-2020 $18.71USD 250

        For ISOs:
          - Regular cost basis = strike price (what was paid)
          - AMT cost basis = FMV at exercise
          - AMT preference = (FMV - strike) * shares
        """
        events: list[EquityEvent] = []
        lots: list[Lot] = []

        # Split into blocks starting with "Exercise (ERH-..."
        # Only match detailed blocks that contain "Exercise Method:"
        blocks = re.split(r"(?=Exercise \(ERH-[0-9A-Fa-f]+\)\n)", text)

        for block in blocks:
            if not block.strip().startswith("Exercise (ERH-"):
                continue
            # Skip summary blocks (no "Exercise Method:" line)
            if "Exercise Method:" not in block:
                continue

            # Extract exercise reference ID
            ref_match = re.search(
                r"Exercise \((ERH-[0-9A-Fa-f]+)\)", block
            )
            if not ref_match:
                continue
            exercise_id = ref_match.group(1)

            # Extract Fair Market Value at exercise
            fmv_match = re.search(
                r"Fair Market Value:\s*\$([\d,]+\.?\d*)", block
            )
            if not fmv_match:
                continue
            fmv = Decimal(fmv_match.group(1).replace(",", ""))

            # Extract Held Quantity (shares exercised)
            qty_match = re.search(r"Held Quantity:\s*([\d,]+)", block)
            if not qty_match:
                continue
            quantity = _parse_int(qty_match.group(1))

            # Extract Transaction Date (exercise date)
            date_match = re.search(
                r"Transaction Date:\s*(\d{1,2}-\w{3}-\d{4})", block
            )
            if not date_match:
                continue
            exercise_date = _parse_date(date_match.group(1))

            # Extract AMT amount
            amt_match = re.search(
                r"Amount Subject to AMT:\s*\$([\d,]+\.?\d*)", block
            )
            amt_amount = (
                Decimal(amt_match.group(1).replace(",", ""))
                if amt_match
                else Decimal("0")
            )

            # Determine award type (ISO vs NSO) from the grants section
            award_type_match = re.search(
                r"Options \((ISO|NSO)\)", block
            )
            if not award_type_match:
                continue
            award_type_str = award_type_match.group(1)
            equity_type = (
                EquityType.ISO if award_type_str == "ISO" else EquityType.NSO
            )

            # Extract Grant Date and Grant Price (strike price)
            # Pattern: "05-Feb-2020 $18.71USD 250"
            grant_match = re.search(
                r"(\d{1,2}-\w{3}-\d{4})\s+\$([\d,.]+?)USD\s+(\d+)",
                block,
            )
            if grant_match:
                grant_date = _parse_date(grant_match.group(1))
                strike_price = Decimal(
                    grant_match.group(2).replace(",", "")
                )
            else:
                grant_date = None
                strike_price = Decimal("0")

            # Create EquityEvent for the exercise
            event_id = str(uuid4())
            event = EquityEvent(
                id=event_id,
                event_type=TransactionType.EXERCISE,
                equity_type=equity_type,
                security=security,
                event_date=exercise_date,
                shares=Decimal(quantity),
                price_per_share=fmv,
                strike_price=strike_price,
                grant_date=grant_date,
                ordinary_income=Decimal("0"),  # ISO: no ordinary income at exercise
                broker_source=BrokerSource.SHAREWORKS,
                raw_data={
                    "exercise_id": exercise_id,
                    "amt_amount": str(amt_amount),
                    "cost_of_options": str(strike_price * quantity),
                },
            )
            events.append(event)

            # Create Lot for exercised shares
            # Regular basis = strike price; AMT basis = FMV at exercise
            lot = Lot(
                id=str(uuid4()),
                equity_type=equity_type,
                security=security,
                acquisition_date=exercise_date,
                shares=Decimal(quantity),
                cost_per_share=strike_price,
                amt_cost_per_share=fmv,
                shares_remaining=Decimal(quantity),
                source_event_id=event_id,
                broker_source=BrokerSource.SHAREWORKS,
                notes=f"ISO exercise {exercise_id}, strike ${strike_price}, FMV ${fmv}",
            )
            lots.append(lot)

        return {"events": events, "lots": lots}
