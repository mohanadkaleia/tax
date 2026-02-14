"""Manual entry adapter for importing JSON files produced by `taxbot parse`."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from app.ingestion.base import BaseAdapter, ImportResult
from app.models.enums import BrokerSource, EquityType, TransactionType
from app.models.equity_event import EquityEvent, Lot, Sale, Security
from app.models.tax_forms import (
    W2,
    Form1099B,
    Form1099DIV,
    Form1099INT,
    Form3921,
    Form3922,
)
from app.parsing.detector import FormType


class ManualAdapter(BaseAdapter):
    """Imports JSON files produced by `taxbot parse` into domain models."""

    def parse(self, file_path: Path) -> ImportResult:
        """Read a parse-output JSON file, detect form type, return typed models."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw = json.loads(file_path.read_text())
        form_type = self._detect_form_type(raw)

        dispatch = {
            FormType.W2: self._parse_w2,
            FormType.FORM_1099B: self._parse_1099b,
            FormType.FORM_1099DIV: self._parse_1099div,
            FormType.FORM_1099INT: self._parse_1099int,
            FormType.FORM_3921: self._parse_3921,
            FormType.FORM_3922: self._parse_3922,
        }
        return dispatch[form_type](raw)

    def validate(self, data: ImportResult) -> list[str]:
        """Validate imported data for completeness and consistency."""
        dispatch = {
            FormType.W2: self._validate_w2,
            FormType.FORM_1099B: self._validate_1099b,
            FormType.FORM_1099DIV: self._validate_1099div,
            FormType.FORM_1099INT: self._validate_1099int,
            FormType.FORM_3921: self._validate_3921,
            FormType.FORM_3922: self._validate_3922,
        }
        return dispatch[data.form_type](data)

    # --- Form type detection ---

    @staticmethod
    def _detect_form_type(raw: dict | list) -> FormType:
        """Detect form type from the JSON shape and field signatures."""
        if isinstance(raw, list) and len(raw) == 0:
            raise ValueError("JSON file contains an empty list — no records to import")

        # If it's a list, inspect the first record
        sample = raw[0] if isinstance(raw, list) else raw

        if "box1_wages" in sample or "box2_federal_withheld" in sample:
            return FormType.W2
        if "exercise_price_per_share" in sample and "fmv_on_exercise_date" in sample:
            return FormType.FORM_3921
        if "purchase_price_per_share" in sample and "fmv_on_purchase_date" in sample:
            return FormType.FORM_3922
        if "proceeds" in sample and "date_sold" in sample:
            return FormType.FORM_1099B
        if "ordinary_dividends" in sample or "qualified_dividends" in sample:
            return FormType.FORM_1099DIV
        if "interest_income" in sample:
            return FormType.FORM_1099INT

        raise ValueError(f"Cannot detect form type from JSON keys: {list(sample.keys())}")

    # --- Parsers ---

    def _parse_w2(self, data: dict) -> ImportResult:
        box12 = {k: Decimal(str(v)) for k, v in (data.get("box12_codes") or {}).items()}
        box14 = {k: Decimal(str(v)) for k, v in (data.get("box14_other") or {}).items()}

        w2 = W2(
            employer_name=data.get("employer_name", "Unknown"),
            employer_ein=data.get("employer_ein"),
            tax_year=int(data.get("tax_year", 0)),
            box1_wages=Decimal(str(data["box1_wages"])),
            box2_federal_withheld=Decimal(str(data["box2_federal_withheld"])),
            box3_ss_wages=_decimal_or_none(data.get("box3_ss_wages")),
            box4_ss_withheld=_decimal_or_none(data.get("box4_ss_withheld")),
            box5_medicare_wages=_decimal_or_none(data.get("box5_medicare_wages")),
            box6_medicare_withheld=_decimal_or_none(data.get("box6_medicare_withheld")),
            box12_codes=box12,
            box14_other=box14,
            box16_state_wages=_decimal_or_none(data.get("box16_state_wages")),
            box17_state_withheld=_decimal_or_none(data.get("box17_state_withheld")),
            state=data.get("state", "CA"),
        )
        return ImportResult(
            form_type=FormType.W2,
            tax_year=w2.tax_year,
            forms=[w2],
        )

    def _parse_1099b(self, raw: list | dict) -> ImportResult:
        records = raw if isinstance(raw, list) else [raw]
        forms = []
        sales = []
        tax_year = 0

        for record in records:
            tax_year = int(record.get("tax_year", tax_year or 0))

            date_acquired = None
            raw_date_acq = record.get("date_acquired")
            if raw_date_acq and str(raw_date_acq).lower() != "various":
                date_acquired = date.fromisoformat(str(raw_date_acq))

            form = Form1099B(
                broker_name=record.get("broker_name", "Unknown"),
                tax_year=tax_year,
                description=record.get("description", ""),
                date_acquired=date_acquired,
                date_sold=date.fromisoformat(record["date_sold"]),
                proceeds=Decimal(str(record["proceeds"])),
                cost_basis=_decimal_or_none(record.get("cost_basis")),
                wash_sale_loss_disallowed=_decimal_or_none(record.get("wash_sale_loss_disallowed")),
                basis_reported_to_irs=record.get("basis_reported_to_irs", True),
                broker_source=BrokerSource(record.get("broker_source", "MANUAL")),
            )
            forms.append(form)

            sale = Sale(
                id=str(uuid4()),
                lot_id="",  # Not matched yet — happens at reconcile
                security=Security(ticker="UNKNOWN", name=form.description),
                sale_date=form.date_sold,
                shares=Decimal("0"),  # Often not in 1099-B; inferred at reconcile
                proceeds_per_share=form.proceeds,  # Store total proceeds; per-share computed later
                broker_reported_basis=form.cost_basis,
                basis_reported_to_irs=form.basis_reported_to_irs,
                broker_source=BrokerSource.MANUAL,
            )
            sales.append(sale)

        return ImportResult(
            form_type=FormType.FORM_1099B,
            tax_year=tax_year,
            forms=forms,
            sales=sales,
        )

    def _parse_1099div(self, data: dict) -> ImportResult:
        form = Form1099DIV(
            broker_name=data.get("payer_name", data.get("broker_name", "Unknown")),
            tax_year=int(data.get("tax_year", 0)),
            ordinary_dividends=Decimal(str(data["ordinary_dividends"])),
            qualified_dividends=Decimal(str(data["qualified_dividends"])),
            total_capital_gain_distributions=Decimal(
                str(data.get("capital_gain_distributions", "0"))
            ),
            nondividend_distributions=Decimal(
                str(data.get("nondividend_distributions", "0"))
            ),
            section_199a_dividends=Decimal(
                str(data.get("section_199a_dividends", "0"))
            ),
            foreign_tax_paid=Decimal(str(data.get("foreign_tax_paid", "0"))),
            foreign_country=data.get("foreign_country"),
            federal_tax_withheld=Decimal(str(data.get("federal_tax_withheld", "0"))),
            state_tax_withheld=Decimal(str(data.get("state_tax_withheld", "0"))),
        )
        return ImportResult(
            form_type=FormType.FORM_1099DIV,
            tax_year=form.tax_year,
            forms=[form],
        )

    def _parse_1099int(self, data: dict) -> ImportResult:
        form = Form1099INT(
            payer_name=data.get("payer_name", "Unknown"),
            tax_year=int(data.get("tax_year", 0)),
            interest_income=Decimal(str(data["interest_income"])),
            early_withdrawal_penalty=Decimal(str(data.get("early_withdrawal_penalty", "0"))),
            us_savings_bond_interest=Decimal(str(data.get("us_savings_bond_interest", "0"))),
            federal_tax_withheld=Decimal(str(data.get("federal_tax_withheld", "0"))),
            state_tax_withheld=Decimal(str(data.get("state_tax_withheld", "0"))),
        )
        return ImportResult(
            form_type=FormType.FORM_1099INT,
            tax_year=form.tax_year,
            forms=[form],
        )

    def _parse_3921(self, raw: list | dict) -> ImportResult:
        records = raw if isinstance(raw, list) else [raw]
        forms = []
        events = []
        lots = []
        tax_year = 0

        for record in records:
            tax_year = int(record.get("tax_year", tax_year or 0))
            corporation = record.get("corporation_name", record.get("employer_name", "Unknown"))

            form = Form3921(
                tax_year=tax_year,
                grant_date=date.fromisoformat(record["grant_date"]),
                exercise_date=date.fromisoformat(record["exercise_date"]),
                exercise_price_per_share=Decimal(str(record["exercise_price_per_share"])),
                fmv_on_exercise_date=Decimal(str(record["fmv_on_exercise_date"])),
                shares_transferred=Decimal(str(record["shares_transferred"])),
                employer_name=corporation,
            )
            forms.append(form)

            event_id = str(uuid4())
            event = EquityEvent(
                id=event_id,
                event_type=TransactionType.EXERCISE,
                equity_type=EquityType.ISO,
                security=Security(ticker="UNKNOWN", name=f"ISO Exercise ({corporation})"),
                event_date=form.exercise_date,
                shares=form.shares_transferred,
                price_per_share=form.fmv_on_exercise_date,
                strike_price=form.exercise_price_per_share,
                grant_date=form.grant_date,
                broker_source=BrokerSource.MANUAL,
            )
            events.append(event)

            lot = Lot(
                id=str(uuid4()),
                equity_type=EquityType.ISO,
                security=event.security,
                acquisition_date=form.exercise_date,
                shares=form.shares_transferred,
                cost_per_share=form.exercise_price_per_share,  # Regular basis = strike
                amt_cost_per_share=form.fmv_on_exercise_date,  # AMT basis = FMV
                shares_remaining=form.shares_transferred,
                source_event_id=event_id,
                broker_source=BrokerSource.MANUAL,
            )
            lots.append(lot)

        return ImportResult(
            form_type=FormType.FORM_3921,
            tax_year=tax_year,
            forms=forms,
            events=events,
            lots=lots,
        )

    def _parse_3922(self, raw: list | dict) -> ImportResult:
        records = raw if isinstance(raw, list) else [raw]
        forms = []
        events = []
        lots = []
        tax_year = 0

        for record in records:
            tax_year = int(record.get("tax_year", tax_year or 0))
            corporation = record.get("corporation_name", record.get("employer_name", "Unknown"))

            form = Form3922(
                tax_year=tax_year,
                offering_date=date.fromisoformat(record["offering_date"]),
                purchase_date=date.fromisoformat(record["purchase_date"]),
                fmv_on_offering_date=Decimal(str(record["fmv_on_offering_date"])),
                fmv_on_purchase_date=Decimal(str(record["fmv_on_purchase_date"])),
                purchase_price_per_share=Decimal(str(record["purchase_price_per_share"])),
                shares_transferred=Decimal(str(record["shares_transferred"])),
                employer_name=corporation,
            )
            forms.append(form)

            event_id = str(uuid4())
            event = EquityEvent(
                id=event_id,
                event_type=TransactionType.PURCHASE,
                equity_type=EquityType.ESPP,
                security=Security(ticker="UNKNOWN", name=f"ESPP Purchase ({corporation})"),
                event_date=form.purchase_date,
                shares=form.shares_transferred,
                price_per_share=form.fmv_on_purchase_date,
                purchase_price=form.purchase_price_per_share,
                offering_date=form.offering_date,
                fmv_on_offering_date=form.fmv_on_offering_date,
                broker_source=BrokerSource.MANUAL,
            )
            events.append(event)

            lot = Lot(
                id=str(uuid4()),
                equity_type=EquityType.ESPP,
                security=event.security,
                acquisition_date=form.purchase_date,
                shares=form.shares_transferred,
                cost_per_share=form.purchase_price_per_share,  # Basis = purchase price
                amt_cost_per_share=None,  # ESPP has no AMT implications at purchase
                shares_remaining=form.shares_transferred,
                source_event_id=event_id,
                broker_source=BrokerSource.MANUAL,
            )
            lots.append(lot)

        return ImportResult(
            form_type=FormType.FORM_3922,
            tax_year=tax_year,
            forms=forms,
            events=events,
            lots=lots,
        )

    # --- Validators ---

    def _validate_w2(self, data: ImportResult) -> list[str]:
        errors = []
        for w2 in data.forms:
            if not isinstance(w2, W2):
                continue
            if w2.box1_wages <= 0:
                errors.append("W-2: box1_wages must be greater than 0")
            if w2.box2_federal_withheld < 0:
                errors.append("W-2: box2_federal_withheld must be >= 0")
            if w2.box2_federal_withheld > w2.box1_wages:
                errors.append("W-2: box2_federal_withheld exceeds box1_wages")
            if not w2.employer_name or w2.employer_name == "Unknown":
                errors.append("W-2: employer_name is missing")
            if w2.tax_year == 0:
                errors.append("W-2: tax_year is missing")
        return errors

    def _validate_1099b(self, data: ImportResult) -> list[str]:
        errors = []
        for i, form in enumerate(data.forms):
            if not isinstance(form, Form1099B):
                continue
            if not form.description:
                errors.append(f"1099-B record {i + 1}: description is missing")
            if form.proceeds <= 0:
                errors.append(f"1099-B record {i + 1}: proceeds must be > 0")
        return errors

    def _validate_1099div(self, data: ImportResult) -> list[str]:
        errors = []
        for form in data.forms:
            if not isinstance(form, Form1099DIV):
                continue
            if form.ordinary_dividends < form.qualified_dividends:
                errors.append("1099-DIV: ordinary_dividends must be >= qualified_dividends")
            if form.section_199a_dividends > form.ordinary_dividends:
                errors.append("1099-DIV: section_199a_dividends must be <= ordinary_dividends")
            if form.nondividend_distributions < 0:
                errors.append("1099-DIV: nondividend_distributions must be >= 0")
            if form.foreign_tax_paid < 0:
                errors.append("1099-DIV: foreign_tax_paid must be >= 0")
        return errors

    def _validate_1099int(self, data: ImportResult) -> list[str]:
        errors = []
        for form in data.forms:
            if not isinstance(form, Form1099INT):
                continue
            if form.interest_income < 0:
                errors.append("1099-INT: interest_income must be >= 0")
            if form.us_savings_bond_interest > form.interest_income:
                errors.append("1099-INT: us_savings_bond_interest must be <= interest_income")
            if form.us_savings_bond_interest < 0:
                errors.append("1099-INT: us_savings_bond_interest must be >= 0")
        return errors

    def _validate_3921(self, data: ImportResult) -> list[str]:
        errors = []
        for form in data.forms:
            if not isinstance(form, Form3921):
                continue
            if form.exercise_date <= form.grant_date:
                errors.append("3921: exercise_date must be after grant_date")
            if form.fmv_on_exercise_date <= 0:
                errors.append("3921: fmv_on_exercise_date must be > 0")
            if form.exercise_price_per_share <= 0:
                errors.append("3921: exercise_price_per_share must be > 0")
            if form.shares_transferred <= 0:
                errors.append("3921: shares_transferred must be > 0")
        return errors

    def _validate_3922(self, data: ImportResult) -> list[str]:
        errors = []
        for form in data.forms:
            if not isinstance(form, Form3922):
                continue
            if form.purchase_date <= form.offering_date:
                errors.append("3922: purchase_date must be after offering_date")
            if form.fmv_on_purchase_date <= 0:
                errors.append("3922: fmv_on_purchase_date must be > 0")
            if form.purchase_price_per_share <= 0:
                errors.append("3922: purchase_price_per_share must be > 0")
            if form.purchase_price_per_share > form.fmv_on_purchase_date:
                errors.append(
                    "3922: purchase_price_per_share exceeds fmv_on_purchase_date"
                )
            if form.shares_transferred <= 0:
                errors.append("3922: shares_transferred must be > 0")
        return errors


def _decimal_or_none(value: str | int | float | None) -> Decimal | None:
    """Convert a value to Decimal, returning None if null/empty."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
