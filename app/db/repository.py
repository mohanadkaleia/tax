"""Data access layer for TaxBot 9000."""

import json
import sqlite3
from decimal import Decimal
from uuid import uuid4

from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult
from app.models.reports import AuditEntry
from app.models.tax_forms import W2, Form1099DIV, Form1099INT


class TaxRepository:
    """CRUD operations for tax entities."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- Import batches ---

    def create_import_batch(
        self,
        source: str,
        tax_year: int,
        file_path: str,
        form_type: str,
        record_count: int = 0,
    ) -> str:
        """Create an import batch record. Returns the batch ID."""
        batch_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO import_batches
               (id, source, file_path, tax_year, form_type, record_count, status)
               VALUES (?, ?, ?, ?, ?, ?, 'completed')""",
            (batch_id, source, file_path, tax_year, form_type, record_count),
        )
        self.conn.commit()
        return batch_id

    def get_import_batches(self, tax_year: int | None = None) -> list[dict]:
        """Retrieve import batch records, optionally filtered by tax year."""
        if tax_year:
            cursor = self.conn.execute(
                "SELECT * FROM import_batches WHERE tax_year = ?", (tax_year,)
            )
        else:
            cursor = self.conn.execute("SELECT * FROM import_batches")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- W-2 forms ---

    def save_w2(self, w2: W2, batch_id: str) -> str:
        """Insert a W-2 form record. Returns the record ID."""
        record_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO w2_forms
               (id, import_batch_id, tax_year, employer_name,
                box1_wages, box2_federal_withheld, box3_ss_wages,
                box4_ss_withheld, box5_medicare_wages, box6_medicare_withheld,
                box12_codes, box14_other, box16_state_wages,
                box17_state_withheld, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                batch_id,
                w2.tax_year,
                w2.employer_name,
                str(w2.box1_wages),
                str(w2.box2_federal_withheld),
                str(w2.box3_ss_wages) if w2.box3_ss_wages is not None else None,
                str(w2.box4_ss_withheld) if w2.box4_ss_withheld is not None else None,
                str(w2.box5_medicare_wages) if w2.box5_medicare_wages is not None else None,
                str(w2.box6_medicare_withheld) if w2.box6_medicare_withheld is not None else None,
                json.dumps({k: str(v) for k, v in w2.box12_codes.items()}) if w2.box12_codes else None,
                json.dumps({k: str(v) for k, v in w2.box14_other.items()}) if w2.box14_other else None,
                str(w2.box16_state_wages) if w2.box16_state_wages is not None else None,
                str(w2.box17_state_withheld) if w2.box17_state_withheld is not None else None,
                w2.state,
            ),
        )
        self.conn.commit()
        return record_id

    def get_w2s(self, tax_year: int) -> list[dict]:
        """Retrieve W-2 records for a given tax year."""
        cursor = self.conn.execute(
            "SELECT * FROM w2_forms WHERE tax_year = ?", (tax_year,)
        )
        columns = [desc[0] for desc in cursor.description]
        rows = []
        for row in cursor.fetchall():
            record = dict(zip(columns, row))
            # Deserialize JSON fields
            if record.get("box12_codes"):
                record["box12_codes"] = json.loads(record["box12_codes"])
            if record.get("box14_other"):
                record["box14_other"] = json.loads(record["box14_other"])
            rows.append(record)
        return rows

    # --- 1099-DIV forms ---

    def save_1099div(self, form: Form1099DIV, batch_id: str) -> str:
        """Insert a 1099-DIV form record. Returns the record ID."""
        record_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO form_1099div
               (id, import_batch_id, tax_year, payer_name,
                ordinary_dividends, qualified_dividends,
                capital_gain_distributions, federal_tax_withheld)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                batch_id,
                form.tax_year,
                form.broker_name,
                str(form.ordinary_dividends),
                str(form.qualified_dividends),
                str(form.total_capital_gain_distributions),
                str(form.federal_tax_withheld),
            ),
        )
        self.conn.commit()
        return record_id

    # --- 1099-INT forms ---

    def save_1099int(self, form: Form1099INT, batch_id: str) -> str:
        """Insert a 1099-INT form record. Returns the record ID."""
        record_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO form_1099int
               (id, import_batch_id, tax_year, payer_name,
                interest_income, early_withdrawal_penalty,
                federal_tax_withheld)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                batch_id,
                form.tax_year,
                form.payer_name,
                str(form.interest_income),
                str(form.early_withdrawal_penalty),
                str(form.federal_tax_withheld),
            ),
        )
        self.conn.commit()
        return record_id

    # --- 1099-DIV queries ---

    def get_1099divs(self, tax_year: int) -> list[dict]:
        """Retrieve 1099-DIV records for a given tax year."""
        cursor = self.conn.execute(
            "SELECT * FROM form_1099div WHERE tax_year = ?", (tax_year,)
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- 1099-INT queries ---

    def get_1099ints(self, tax_year: int) -> list[dict]:
        """Retrieve 1099-INT records for a given tax year."""
        cursor = self.conn.execute(
            "SELECT * FROM form_1099int WHERE tax_year = ?", (tax_year,)
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- Lots ---

    def save_lot(self, lot: Lot, batch_id: str | None = None) -> None:
        """Insert or update a lot."""
        self.conn.execute(
            """INSERT OR REPLACE INTO lots
               (id, equity_type, ticker, security_name, acquisition_date,
                shares, cost_per_share, amt_cost_per_share, shares_remaining,
                source_event_id, broker_source, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lot.id,
                lot.equity_type.value,
                lot.security.ticker,
                lot.security.name,
                lot.acquisition_date.isoformat(),
                str(lot.shares),
                str(lot.cost_per_share),
                str(lot.amt_cost_per_share) if lot.amt_cost_per_share else None,
                str(lot.shares_remaining),
                lot.source_event_id,
                lot.broker_source.value,
                lot.notes,
            ),
        )
        self.conn.commit()

    def get_lots(self, ticker: str | None = None) -> list[dict]:
        """Retrieve lots, optionally filtered by ticker."""
        if ticker:
            cursor = self.conn.execute("SELECT * FROM lots WHERE ticker = ?", (ticker,))
        else:
            cursor = self.conn.execute("SELECT * FROM lots")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- Events ---

    def save_event(self, event: EquityEvent, batch_id: str | None = None) -> None:
        """Insert an equity event."""
        self.conn.execute(
            """INSERT OR REPLACE INTO equity_events
               (id, batch_id, event_type, equity_type, ticker, security_name, event_date,
                shares, price_per_share, strike_price, purchase_price,
                offering_date, fmv_on_offering_date, grant_date, ordinary_income,
                broker_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                batch_id,
                event.event_type.value,
                event.equity_type.value,
                event.security.ticker,
                event.security.name,
                event.event_date.isoformat(),
                str(event.shares),
                str(event.price_per_share),
                str(event.strike_price) if event.strike_price else None,
                str(event.purchase_price) if event.purchase_price else None,
                event.offering_date.isoformat() if event.offering_date else None,
                str(event.fmv_on_offering_date) if event.fmv_on_offering_date else None,
                event.grant_date.isoformat() if event.grant_date else None,
                str(event.ordinary_income) if event.ordinary_income else None,
                event.broker_source.value,
            ),
        )
        self.conn.commit()

    # --- Sales ---

    def save_sale(self, sale: Sale, batch_id: str | None = None) -> None:
        """Insert a sale record."""
        self.conn.execute(
            """INSERT OR REPLACE INTO sales
               (id, lot_id, ticker, security_name, sale_date, shares,
                proceeds_per_share, broker_reported_basis,
                broker_reported_basis_per_share,
                wash_sale_disallowed, form_1099b_received, basis_reported_to_irs,
                broker_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sale.id,
                sale.lot_id if sale.lot_id else None,
                sale.security.ticker,
                sale.security.name,
                sale.sale_date.isoformat(),
                str(sale.shares),
                str(sale.proceeds_per_share),
                str(sale.broker_reported_basis) if sale.broker_reported_basis else None,
                str(sale.broker_reported_basis_per_share) if sale.broker_reported_basis_per_share else None,
                str(sale.wash_sale_disallowed),
                int(sale.form_1099b_received),
                int(sale.basis_reported_to_irs),
                sale.broker_source.value,
            ),
        )
        self.conn.commit()

    # --- Sale Results ---

    def save_sale_result(self, result: SaleResult) -> None:
        """Insert a sale result (basis correction output)."""
        self.conn.execute(
            """INSERT INTO sale_results
               (sale_id, lot_id, acquisition_date, sale_date, shares, proceeds,
                broker_reported_basis, correct_basis, adjustment_amount,
                adjustment_code, holding_period, form_8949_category,
                gain_loss, ordinary_income, amt_adjustment, wash_sale_disallowed, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.sale_id,
                result.lot_id,
                result.acquisition_date.isoformat(),
                result.sale_date.isoformat(),
                str(result.shares),
                str(result.proceeds),
                str(result.broker_reported_basis) if result.broker_reported_basis else None,
                str(result.correct_basis),
                str(result.adjustment_amount),
                result.adjustment_code.value,
                result.holding_period.value,
                result.form_8949_category.value,
                str(result.gain_loss),
                str(result.ordinary_income),
                str(result.amt_adjustment),
                str(result.wash_sale_disallowed),
                result.notes,
            ),
        )
        self.conn.commit()

    def get_sale_results(self, tax_year: int | None = None) -> list[dict]:
        """Retrieve sale results, optionally filtered by tax year."""
        if tax_year:
            cursor = self.conn.execute(
                "SELECT * FROM sale_results WHERE sale_date LIKE ?",
                (f"{tax_year}-%",),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM sale_results")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def clear_sale_results(self, tax_year: int) -> int:
        """Delete all sale results for a tax year. Returns count deleted."""
        cursor = self.conn.execute(
            "DELETE FROM sale_results WHERE sale_date LIKE ?",
            (f"{tax_year}-%",),
        )
        self.conn.commit()
        return cursor.rowcount

    # --- Sales queries ---

    def get_sales(self, tax_year: int | None = None) -> list[dict]:
        """Retrieve sales, optionally filtered by tax year."""
        if tax_year:
            cursor = self.conn.execute(
                "SELECT * FROM sales WHERE sale_date LIKE ?",
                (f"{tax_year}-%",),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM sales")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- Events queries ---

    def get_events(
        self, ticker: str | None = None, equity_type: str | None = None
    ) -> list[dict]:
        """Retrieve equity events with optional filters."""
        query = "SELECT * FROM equity_events"
        params: list[str] = []
        conditions = []
        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        if equity_type:
            conditions.append("equity_type = ?")
            params.append(equity_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        cursor = self.conn.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # --- Lot updates ---

    def update_lot_shares_remaining(
        self, lot_id: str, shares_remaining: Decimal
    ) -> None:
        """Update the shares_remaining for a lot after allocation."""
        self.conn.execute(
            "UPDATE lots SET shares_remaining = ? WHERE id = ?",
            (str(shares_remaining), lot_id),
        )
        self.conn.commit()

    # --- Audit log ---

    def save_audit_entry(self, entry: AuditEntry) -> None:
        """Insert an audit log entry."""
        self.conn.execute(
            """INSERT INTO audit_log (engine, operation, inputs, output, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (
                entry.engine,
                entry.operation,
                json.dumps(entry.inputs, default=str),
                json.dumps(entry.output, default=str),
                entry.notes,
            ),
        )
        self.conn.commit()

    # --- Reconciliation runs ---

    def save_reconciliation_run(self, run: dict) -> str:
        """Insert a reconciliation run record. Returns the run ID."""
        run_id = run.get("id", str(uuid4()))
        self.conn.execute(
            """INSERT INTO reconciliation_runs
               (id, tax_year, total_sales, matched_sales, unmatched_sales,
                total_proceeds, total_correct_basis, total_gain_loss,
                total_ordinary_income, total_amt_adjustment,
                warnings, errors, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                run["tax_year"],
                run.get("total_sales", 0),
                run.get("matched_sales", 0),
                run.get("unmatched_sales", 0),
                run.get("total_proceeds"),
                run.get("total_correct_basis"),
                run.get("total_gain_loss"),
                run.get("total_ordinary_income"),
                run.get("total_amt_adjustment"),
                json.dumps(run.get("warnings", [])),
                json.dumps(run.get("errors", [])),
                run.get("status", "completed"),
            ),
        )
        self.conn.commit()
        return run_id

    def get_reconciliation_runs(self, tax_year: int | None = None) -> list[dict]:
        """Retrieve reconciliation runs, optionally filtered by tax year."""
        if tax_year:
            cursor = self.conn.execute(
                "SELECT * FROM reconciliation_runs WHERE tax_year = ?",
                (tax_year,),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM reconciliation_runs")
        columns = [desc[0] for desc in cursor.description]
        rows = []
        for row in cursor.fetchall():
            record = dict(zip(columns, row))
            if record.get("warnings"):
                record["warnings"] = json.loads(record["warnings"])
            if record.get("errors"):
                record["errors"] = json.loads(record["errors"])
            rows.append(record)
        return rows

    # --- Duplicate detection ---

    def check_w2_duplicate(self, employer_name: str, tax_year: int) -> bool:
        """Check if a W-2 from the same employer/year already exists."""
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM w2_forms WHERE employer_name = ? AND tax_year = ?",
            (employer_name, tax_year),
        )
        return cursor.fetchone()[0] > 0

    def check_event_duplicate(
        self, event_type: str, event_date: str, shares: str
    ) -> bool:
        """Check if an event with same type/date/shares already exists."""
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM equity_events WHERE event_type = ? AND event_date = ? AND shares = ?",
            (event_type, event_date, shares),
        )
        return cursor.fetchone()[0] > 0
