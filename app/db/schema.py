"""SQLite database schema definition."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    file_path TEXT NOT NULL,
    tax_year INTEGER,
    form_type TEXT,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    record_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS equity_events (
    id TEXT PRIMARY KEY,
    batch_id TEXT REFERENCES import_batches(id),
    event_type TEXT NOT NULL,
    equity_type TEXT NOT NULL,
    ticker TEXT NOT NULL,
    security_name TEXT NOT NULL,
    event_date TEXT NOT NULL,
    shares TEXT NOT NULL,
    price_per_share TEXT NOT NULL,
    strike_price TEXT,
    purchase_price TEXT,
    offering_date TEXT,
    fmv_on_offering_date TEXT,
    grant_date TEXT,
    ordinary_income TEXT,
    broker_source TEXT NOT NULL,
    raw_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lots (
    id TEXT PRIMARY KEY,
    equity_type TEXT NOT NULL,
    ticker TEXT NOT NULL,
    security_name TEXT NOT NULL,
    acquisition_date TEXT NOT NULL,
    shares TEXT NOT NULL,
    cost_per_share TEXT NOT NULL,
    amt_cost_per_share TEXT,
    shares_remaining TEXT NOT NULL,
    source_event_id TEXT REFERENCES equity_events(id),
    broker_source TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sales (
    id TEXT PRIMARY KEY,
    lot_id TEXT REFERENCES lots(id),
    ticker TEXT NOT NULL,
    security_name TEXT,
    sale_date TEXT NOT NULL,
    shares TEXT NOT NULL,
    proceeds_per_share TEXT NOT NULL,
    broker_reported_basis TEXT,
    broker_reported_basis_per_share TEXT,
    wash_sale_disallowed TEXT NOT NULL DEFAULT '0',
    form_1099b_received INTEGER NOT NULL DEFAULT 1,
    basis_reported_to_irs INTEGER NOT NULL DEFAULT 1,
    broker_source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sale_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id TEXT NOT NULL REFERENCES sales(id),
    lot_id TEXT REFERENCES lots(id),
    acquisition_date TEXT NOT NULL,
    sale_date TEXT NOT NULL,
    shares TEXT NOT NULL,
    proceeds TEXT NOT NULL,
    broker_reported_basis TEXT,
    correct_basis TEXT NOT NULL,
    adjustment_amount TEXT NOT NULL,
    adjustment_code TEXT NOT NULL,
    holding_period TEXT NOT NULL,
    form_8949_category TEXT NOT NULL,
    gain_loss TEXT NOT NULL,
    ordinary_income TEXT NOT NULL DEFAULT '0',
    amt_adjustment TEXT NOT NULL DEFAULT '0',
    wash_sale_disallowed TEXT NOT NULL DEFAULT '0',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS w2_forms (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    employer_name TEXT NOT NULL,
    box1_wages TEXT NOT NULL,
    box2_federal_withheld TEXT NOT NULL,
    box3_ss_wages TEXT,
    box4_ss_withheld TEXT,
    box5_medicare_wages TEXT,
    box6_medicare_withheld TEXT,
    box12_codes TEXT,
    box14_other TEXT,
    box16_state_wages TEXT,
    box17_state_withheld TEXT,
    state TEXT DEFAULT 'CA',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS form_1099div (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    payer_name TEXT,
    ordinary_dividends TEXT NOT NULL,
    qualified_dividends TEXT NOT NULL,
    capital_gain_distributions TEXT,
    nondividend_distributions TEXT DEFAULT '0',
    section_199a_dividends TEXT DEFAULT '0',
    foreign_tax_paid TEXT DEFAULT '0',
    foreign_country TEXT,
    federal_tax_withheld TEXT,
    state_tax_withheld TEXT DEFAULT '0',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS form_1099int (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    payer_name TEXT,
    interest_income TEXT NOT NULL,
    us_savings_bond_interest TEXT DEFAULT '0',
    early_withdrawal_penalty TEXT,
    federal_tax_withheld TEXT,
    state_tax_withheld TEXT DEFAULT '0',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id TEXT PRIMARY KEY,
    tax_year INTEGER NOT NULL,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    total_sales INTEGER NOT NULL DEFAULT 0,
    matched_sales INTEGER NOT NULL DEFAULT 0,
    unmatched_sales INTEGER NOT NULL DEFAULT 0,
    total_proceeds TEXT,
    total_correct_basis TEXT,
    total_gain_loss TEXT,
    total_ordinary_income TEXT,
    total_amt_adjustment TEXT,
    warnings TEXT,
    errors TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    engine TEXT NOT NULL,
    operation TEXT NOT NULL,
    inputs TEXT NOT NULL,
    output TEXT NOT NULL,
    notes TEXT
);
"""


def create_schema(db_path: Path) -> sqlite3.Connection:
    """Create the database schema. Returns the connection."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn
