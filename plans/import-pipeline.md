# Import Pipeline — CPA Tax Plan

**Session ID:** tax-2026-02-11-import-pipeline-001
**Date:** 2026-02-11
**Status:** Planning
**Tax Year:** 2024

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation)

**Scope:**
- Implement the `import` CLI command and the Manual Adapter so that JSON output from `parse` can be loaded into TaxBot's data models and persisted to the SQLite database.
- This is the critical bridge between PDF extraction and tax computation — without it, no engine can run on real data.
- Definition of "done": A user can run `taxbot parse "W2.pdf" --vision` followed by `taxbot import-data manual inputs/w2_2024.json --year 2024` and see the data stored in the database, validated, and retrievable for downstream engines.

---

## Tax Analysis

### Forms & Documents Involved

| Form | JSON Source | Target Model | Notes |
|---|---|---|---|
| W-2 | `parse` output (w2_*.json) | `W2` (tax_forms.py) | Wages, withholdings, Box 12/14 equity items |
| 1099-B | `parse` output (1099b_*.json) | `Form1099B` (tax_forms.py) | Array of transaction records |
| 1099-DIV | `parse` output (1099div_*.json) | `Form1099DIV` (tax_forms.py) | Dividend income |
| 1099-INT | `parse` output (1099int_*.json) | `Form1099INT` (tax_forms.py) | Interest income |
| Form 3921 | `parse` output (3921_*.json) | `Form3921` (tax_forms.py) → `EquityEvent` | ISO exercise → creates Lot |
| Form 3922 | `parse` output (3922_*.json) | `Form3922` (tax_forms.py) → `EquityEvent` | ESPP purchase → creates Lot |

### Applicable Tax Rules

The import pipeline itself does not compute taxes, but it must correctly transform raw form data into domain models that the engines depend on. Critical transformations:

1. **W-2 Box 14 → EquityEvent mapping** (Pub. 525):
   - RSU amount in Box 14 = ordinary income recognized at vest. This is informational — it confirms the W-2 includes the equity income. It does NOT create lots (lots come from brokerage data or 3921/3922 forms).
   - ESPP amount = ordinary income from disqualifying dispositions already recognized.
   - NSO/NQSO amount = spread income from non-qualified option exercises (also in Box 12 Code V).

2. **Form 3921 → EquityEvent (EXERCISE) → Lot** (Form 3921 Instructions):
   - Each 3921 record creates an EXERCISE event and a corresponding Lot.
   - Lot.cost_per_share = exercise_price_per_share (Box 3) — this is the regular tax basis.
   - Lot.amt_cost_per_share = fmv_on_exercise_date (Box 4) — this is the AMT basis.
   - Lot.acquisition_date = exercise_date (Box 2).
   - The spread (Box 4 - Box 3) is the AMT preference item but is NOT ordinary income at exercise for ISOs.

3. **Form 3922 → EquityEvent (PURCHASE) → Lot** (Form 3922 Instructions):
   - Each 3922 record creates a PURCHASE event and a corresponding Lot.
   - Lot.cost_per_share = purchase_price_per_share (Box 5) — initial basis before any disposition adjustment.
   - Lot.acquisition_date = purchase_date (Box 2).
   - The offering_date (Box 1) must be preserved on the event for qualifying disposition determination.
   - No ordinary income is recognized at purchase time (Section 423 plans).

4. **1099-B → Sale records** (Form 8949 Instructions):
   - Each 1099-B transaction creates a Sale record.
   - Sale.broker_reported_basis = cost_basis from 1099-B (may be $0 or incorrect).
   - Sale.basis_reported_to_irs = basis_reported_to_irs flag.
   - Lot matching happens later in the reconciliation step, NOT at import time.

5. **1099-DIV / 1099-INT** (Pub. 550):
   - These are informational. Stored for tax estimation. No lot or event creation.

### Key Design Decision: What Happens at Import vs. Later

| Action | When | Why |
|---|---|---|
| Parse JSON → Pydantic model | Import | Validates data structure and types |
| Create EquityEvents from 3921/3922 | Import | Events are the source of truth for lot creation |
| Create Lots from 3921/3922 events | Import | Lots must exist before sales can be matched |
| Store W-2 data | Import | Needed for withholding totals and income verification |
| Store 1099-B as Sale records | Import | Raw sales stored; lot matching is a separate step |
| Store 1099-DIV/INT | Import | Needed for tax estimation |
| Match Sales to Lots | Reconcile (later) | Requires all lots to be imported first |
| Correct cost basis | Reconcile (later) | Requires matched lot-sale pairs |
| Compute tax estimate | Estimate (later) | Requires all income data |

---

## Implementation Instructions

### For Python Engineer

#### Overview

Three files to implement, one to modify:

| File | Action | Description |
|---|---|---|
| `app/ingestion/manual.py` | Implement | ManualAdapter — JSON → Pydantic models + events/lots |
| `app/cli.py` | Modify | Wire `import-data` command to ManualAdapter + database |
| `app/db/repository.py` | Modify | Add methods for W-2, 1099-DIV, 1099-INT, import batches |
| `app/db/schema.py` | Modify | Add tables for W-2, 1099-DIV, 1099-INT |
| `tests/test_ingestion/test_manual_adapter.py` | Create | Unit tests for all 6 form types |
| `tests/test_cli_import.py` | Create | Integration tests for `import-data` command |

---

#### 1. Manual Adapter (`app/ingestion/manual.py`)

The ManualAdapter reads JSON files produced by `taxbot parse` and returns typed Pydantic models.

```python
class ManualAdapter(BaseAdapter):
    """Imports JSON files produced by `taxbot parse` into domain models."""

    def parse(self, file_path: Path) -> ImportResult:
        """Read a parse-output JSON file, detect form type, return typed models.

        Returns an ImportResult containing:
        - form_type: FormType enum
        - forms: list of Pydantic tax form models (W2, Form1099B, etc.)
        - events: list of EquityEvent (created from 3921/3922)
        - lots: list of Lot (created from 3921/3922 events)
        - sales: list of Sale (created from 1099-B records)
        """

    def validate(self, data: ImportResult) -> list[str]:
        """Validate imported data for completeness and consistency."""
```

**ImportResult** — new dataclass to bundle the adapter output:

```python
@dataclass
class ImportResult:
    form_type: FormType
    tax_year: int
    forms: list           # W2 | Form1099B | Form1099DIV | Form1099INT | Form3921 | Form3922
    events: list          # EquityEvent instances (from 3921/3922)
    lots: list            # Lot instances (from 3921/3922)
    sales: list           # Sale instances (from 1099-B)
```

**Form type detection from JSON:**

The parse output JSON has predictable field signatures:
- W-2: has `box1_wages`, `box2_federal_withheld`
- 1099-B: is a JSON array with `proceeds`, `date_sold`
- 1099-DIV: has `ordinary_dividends`, `qualified_dividends`
- 1099-INT: has `interest_income`
- 3921: is a JSON array with `exercise_price_per_share`, `fmv_on_exercise_date`
- 3922: is a JSON array with `purchase_price_per_share`, `fmv_on_purchase_date`

**JSON → Model mapping (per form type):**

**W-2:**
```python
def _parse_w2(self, data: dict) -> ImportResult:
    # Direct field mapping — parse output matches W2 model fields
    # Convert string monetary values to Decimal
    # box12_codes: dict[str, str] → dict[str, Decimal]
    # box14_other: dict[str, str] → dict[str, Decimal]
    # No events or lots created from W-2
    return ImportResult(form_type=W2, forms=[w2], events=[], lots=[], sales=[])
```

**Form 3921 (ISO):**
```python
def _parse_3921(self, records: list[dict]) -> ImportResult:
    forms, events, lots = [], [], []
    for record in records:
        form = Form3921(...)  # Map JSON fields to model
        event = EquityEvent(
            id=str(uuid4()),
            event_type=TransactionType.EXERCISE,
            equity_type=EquityType.ISO,
            security=Security(ticker="UNKNOWN", name="ISO Exercise"),
            event_date=form.exercise_date,
            shares=form.shares_transferred,
            price_per_share=form.fmv_on_exercise_date,
            strike_price=form.exercise_price_per_share,
            grant_date=form.grant_date,
            broker_source=BrokerSource.MANUAL,
        )
        lot = Lot(
            id=str(uuid4()),
            equity_type=EquityType.ISO,
            security=event.security,
            acquisition_date=form.exercise_date,
            shares=form.shares_transferred,
            cost_per_share=form.exercise_price_per_share,      # Regular basis = strike
            amt_cost_per_share=form.fmv_on_exercise_date,      # AMT basis = FMV
            shares_remaining=form.shares_transferred,
            source_event_id=event.id,
            broker_source=BrokerSource.MANUAL,
        )
        forms.append(form)
        events.append(event)
        lots.append(lot)
    return ImportResult(form_type=FORM_3921, forms=forms, events=events, lots=lots, sales=[])
```

**Form 3922 (ESPP):**
```python
def _parse_3922(self, records: list[dict]) -> ImportResult:
    # Similar to 3921 but:
    # event_type = TransactionType.PURCHASE
    # equity_type = EquityType.ESPP
    # cost_per_share = purchase_price_per_share (Box 5)
    # amt_cost_per_share = None (ESPP has no AMT implications at purchase)
    # Must preserve offering_date on the event for qualifying disposition check
    return ImportResult(...)
```

**1099-B:**
```python
def _parse_1099b(self, records: list[dict]) -> ImportResult:
    sales = []
    for record in records:
        sale = Sale(
            id=str(uuid4()),
            lot_id="",                          # Not matched yet — happens at reconcile
            security=Security(ticker="UNKNOWN", name=record["description"]),
            sale_date=date.fromisoformat(record["date_sold"]),
            shares=Decimal("0"),                # Often not in 1099-B; inferred at reconcile
            proceeds_per_share=Decimal("0"),     # Total proceeds stored; per-share computed later
            broker_reported_basis=Decimal(record.get("cost_basis", "0")),
            basis_reported_to_irs=record.get("basis_reported_to_irs", True),
            broker_source=BrokerSource.MANUAL,
        )
        # Store total proceeds in raw_data or as a custom field
        sales.append(sale)
    return ImportResult(form_type=FORM_1099B, forms=[], events=[], lots=[], sales=sales)
```

**1099-DIV and 1099-INT:** Direct JSON → model mapping, no events/lots/sales.

**Validation rules (`validate` method):**
- W-2: box1_wages > 0, box2_federal_withheld >= 0, box2 <= box1
- 3921: exercise_date > grant_date, fmv > 0, shares > 0, exercise_price > 0
- 3922: purchase_date > offering_date, fmv > 0, shares > 0, purchase_price > 0, purchase_price <= fmv_on_purchase_date
- 1099-B: each record has description, date_sold, proceeds > 0
- 1099-DIV: ordinary_dividends >= qualified_dividends
- 1099-INT: interest_income >= 0

---

#### 2. Database Schema Updates (`app/db/schema.py`)

Add tables for form data that doesn't map to events/lots/sales:

```sql
CREATE TABLE IF NOT EXISTS w2_forms (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    employer_name TEXT NOT NULL,
    box1_wages TEXT NOT NULL,        -- Decimal as string
    box2_federal_withheld TEXT NOT NULL,
    box3_ss_wages TEXT,
    box4_ss_withheld TEXT,
    box5_medicare_wages TEXT,
    box6_medicare_withheld TEXT,
    box12_codes TEXT,                -- JSON string
    box14_other TEXT,                -- JSON string
    box16_state_wages TEXT,
    box17_state_withheld TEXT,
    state TEXT DEFAULT 'CA',
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
    federal_tax_withheld TEXT,
    FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS form_1099int (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    payer_name TEXT,
    interest_income TEXT NOT NULL,
    early_withdrawal_penalty TEXT,
    federal_tax_withheld TEXT,
    FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);
```

---

#### 3. Repository Updates (`app/db/repository.py`)

Add CRUD methods:
- `save_w2(w2: W2, batch_id: str) -> str`
- `get_w2s(tax_year: int) -> list[W2]`
- `save_1099div(form: Form1099DIV, batch_id: str) -> str`
- `save_1099int(form: Form1099INT, batch_id: str) -> str`
- `create_import_batch(source: str, tax_year: int, file_path: str) -> str`
- `get_import_batches(tax_year: int) -> list[dict]`

---

#### 4. CLI `import-data` Command (`app/cli.py`)

Replace the stub with a working implementation:

```
taxbot import-data manual inputs/w2_2024.json --year 2024
taxbot import-data manual inputs/3921_2024.json --year 2024
taxbot import-data manual inputs/1099b_2024.json --year 2024
```

**Flow:**
1. Validate file exists and is JSON
2. Create ManualAdapter instance
3. Call `adapter.parse(file_path)` → ImportResult
4. Call `adapter.validate(result)` → check for errors
5. Override tax_year if --year provided
6. Initialize database (create schema if not exists)
7. Create import batch record
8. Save all forms, events, lots, and sales to database
9. Print summary: "Imported 1 W-2 from Coinbase Inc (tax year 2024)"

**Database location:** `~/.taxbot/taxbot.db` (or configurable via `--db` flag)

**Idempotency:** Before saving, check if the same form data already exists (by employer_name + tax_year for W-2, by exercise_date + shares for 3921, etc.). Warn on duplicates but don't block.

---

#### 5. Test Specifications

**`tests/test_ingestion/test_manual_adapter.py`** — Unit tests:

| Test | Input | Expected Output |
|---|---|---|
| `test_parse_w2_json` | W-2 JSON from parse output | ImportResult with 1 W2 model, no events/lots/sales |
| `test_parse_w2_decimal_conversion` | W-2 JSON with string amounts | All monetary fields are Decimal |
| `test_parse_w2_box12_codes` | W-2 with box12_codes dict | dict[str, Decimal] correctly parsed |
| `test_parse_3921_creates_event_and_lot` | 3921 JSON array | 1 EquityEvent (EXERCISE/ISO) + 1 Lot with correct basis |
| `test_parse_3921_lot_has_amt_basis` | 3921 JSON | lot.amt_cost_per_share = fmv_on_exercise_date |
| `test_parse_3922_creates_event_and_lot` | 3922 JSON array | 1 EquityEvent (PURCHASE/ESPP) + 1 Lot |
| `test_parse_3922_preserves_offering_date` | 3922 JSON | event.offering_date populated |
| `test_parse_1099b_creates_sales` | 1099-B JSON array | list of Sale records |
| `test_parse_1099div` | 1099-DIV JSON | ImportResult with 1 Form1099DIV |
| `test_parse_1099int` | 1099-INT JSON | ImportResult with 1 Form1099INT |
| `test_validate_w2_valid` | Complete W-2 | Empty error list |
| `test_validate_w2_missing_wages` | W-2 without box1 | Error: "Missing required field: box1_wages" |
| `test_validate_3921_exercise_before_grant` | Bad dates | Error: "exercise_date must be after grant_date" |
| `test_detect_form_type_from_json` | Various JSON shapes | Correct FormType detected |
| `test_parse_file_not_found` | Missing path | Raises appropriate error |

**`tests/test_cli_import.py`** — Integration tests:

| Test | Command | Expected |
|---|---|---|
| `test_import_help` | `import-data --help` | Shows help with source, file, year options |
| `test_import_w2_json` | `import-data manual w2.json --year 2024` | Success message, data in DB |
| `test_import_3921_json` | `import-data manual 3921.json --year 2024` | Event + lot created in DB |
| `test_import_invalid_json` | `import-data manual bad.json` | Error message |
| `test_import_nonexistent_file` | `import-data manual missing.json` | "File not found" error |
| `test_import_duplicate_warning` | Import same file twice | Warning about duplicate on second import |

---

## Validation Criteria

- [ ] `taxbot parse "W2.pdf" --vision --dry-run` produces valid JSON (already works)
- [ ] `taxbot import-data manual inputs/w2_2024.json --year 2024` succeeds and prints summary
- [ ] `taxbot import-data manual inputs/3921_2024.json --year 2024` creates lot with correct ISO dual basis
- [ ] `taxbot import-data manual inputs/1099b_2024.json --year 2024` creates sale records
- [ ] Database file created at expected location
- [ ] All monetary values stored as Decimal strings (no floating point)
- [ ] Duplicate imports produce a warning, not an error
- [ ] `python -m pytest tests/ -v` — all existing + new tests pass
- [ ] Ruff lint passes

### Cross-Reference Checks

For the user's actual W-2 data (Coinbase 2024):
- After import: W-2 in DB should show box1_wages = 614328.46, box2_federal_withheld = 109772.46
- box12_codes should contain C: 405.08, D: 12801.27, DD: 8965.82
- box14_other should contain RSU: 282417.52, VPDI: 1760.00
- No SSN, EIN, or other PII in the database

---

## Risk Flags

- **1099-B lot matching deferred:** Sales imported from 1099-B will have `lot_id=""` until the reconciliation step matches them. This is by design — do not try to match at import time because all lots may not be imported yet.
- **Security ticker unknown:** Parse output doesn't always include ticker symbols. Use "UNKNOWN" as placeholder. The reconciliation step can infer tickers from lot/sale descriptions.
- **ESPP offering date critical:** If the Form 3922 offering_date is lost during import, qualifying disposition determination will fail. The ManualAdapter MUST preserve this field on the EquityEvent.
- **Box 14 is informational:** W-2 Box 14 amounts (RSU, ESPP) are employer-reported summaries of equity income already included in Box 1. They do NOT represent separate income — do not double-count.
- **Multiple W-2s:** A taxpayer may have multiple W-2s (multiple employers, or corrected W-2c). The import must support multiple W-2s per tax year.

---

## Agent Assignments

### [PYTHON ENGINEER]
1. Implement `ManualAdapter.parse()` and `ManualAdapter.validate()` in `app/ingestion/manual.py`
2. Add `ImportResult` dataclass to `app/ingestion/base.py`
3. Update `app/db/schema.py` with new tables (w2_forms, form_1099div, form_1099int)
4. Update `app/db/repository.py` with new CRUD methods
5. Wire `import-data` CLI command in `app/cli.py`
6. Write all tests specified above
7. Verify with actual parse output from user's W-2

### [ACCOUNTANT]
- After implementation, verify that imported lot basis values match IRS rules:
  - ISO lot: regular basis = strike price, AMT basis = FMV at exercise
  - ESPP lot: basis = purchase price (pre-disposition adjustment)
- Verify W-2 withholding totals are correctly stored for estimation

### [CPA REVIEW]
- Review the ImportResult structure to confirm it captures all data needed by downstream engines
- Verify that no tax computation happens at import time (separation of concerns)
- Confirm PII handling: no SSN/EIN stored in database

---

## Log

### [CPA] 2026-02-11T12:00
- Import pipeline plan created.
- Analyzed data flow from parse → import → database → engines.
- Specified ManualAdapter implementation with exact field mappings for all 6 form types.
- Documented which actions happen at import vs. reconciliation.
- Defined 15 unit tests and 6 integration tests.
- Identified 5 risk areas (lot matching deferral, ticker unknowns, ESPP offering date, Box 14 double-counting, multiple W-2s).
- Plan ready for Python Engineer implementation.
