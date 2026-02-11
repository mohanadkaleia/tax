"""Data access layer for EquityTax Reconciler."""

import sqlite3

from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult


class TaxRepository:
    """CRUD operations for tax entities."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- Lots ---

    def save_lot(self, lot: Lot) -> None:
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

    def save_event(self, event: EquityEvent) -> None:
        """Insert an equity event."""
        self.conn.execute(
            """INSERT OR REPLACE INTO equity_events
               (id, event_type, equity_type, ticker, security_name, event_date,
                shares, price_per_share, strike_price, purchase_price,
                offering_date, grant_date, ordinary_income, broker_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
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
                event.grant_date.isoformat() if event.grant_date else None,
                str(event.ordinary_income) if event.ordinary_income else None,
                event.broker_source.value,
            ),
        )
        self.conn.commit()

    # --- Sales ---

    def save_sale(self, sale: Sale) -> None:
        """Insert a sale record."""
        self.conn.execute(
            """INSERT OR REPLACE INTO sales
               (id, lot_id, ticker, sale_date, shares, proceeds_per_share,
                broker_reported_basis, broker_reported_basis_per_share,
                wash_sale_disallowed, form_1099b_received, basis_reported_to_irs,
                broker_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sale.id,
                sale.lot_id,
                sale.security.ticker,
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
            """INSERT OR REPLACE INTO sale_results
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
