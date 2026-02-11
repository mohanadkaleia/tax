# System Infrastructure — CPA Tax Plan

**Session ID:** tax-2026-02-10-infra-001
**Date:** 2026-02-10
**Status:** In Progress
**Tax Year:** 2025

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation)
- Tax Planner (strategy engine specs)

**Scope:**
- Define the complete system infrastructure for the EquityTax Reconciler.
- Specify all data models, engine interfaces, ingestion adapters, CLI commands, database schema, and report outputs.
- Provide exact formulas, IRS citations, and validation criteria for every tax computation.
- Definition of "done": A fully runnable skeleton where every module exists, all imports resolve, CLI executes (with stub responses), and `pytest` collects all tests (even if marked pending).

---

## Tax Analysis

### Forms & Documents Involved

| Form | Role in System | IRS Reference |
|---|---|---|
| W-2 | Wages, equity compensation income (Boxes 1, 2, 12, 14, 16, 17) | IRS Instructions for Form W-2 |
| 1099-B | Brokerage proceeds and cost basis | IRS Instructions for Form 1099-B |
| 1099-DIV | Dividend income (ordinary and qualified) | IRS Instructions for Form 1099-DIV |
| 1099-INT | Interest income | IRS Instructions for Form 1099-INT |
| Form 3921 | ISO exercise records: grant date, exercise date, FMV, exercise price, shares | IRS Instructions for Form 3921 |
| Form 3922 | ESPP transfer records: offering date, purchase date, FMV at grant/purchase, purchase price, shares | IRS Instructions for Form 3922 |
| Form 8949 | Sales and Other Dispositions of Capital Assets (output) | IRS Instructions for Form 8949 |
| Form 6251 | Alternative Minimum Tax (output for ISO holders) | IRS Instructions for Form 6251 |
| Schedule D | Capital Gains and Losses summary (output) | IRS Instructions for Schedule D |
| California 540 | State tax return | FTB Form 540 Instructions |
| California Schedule CA | State adjustments to federal AGI | FTB Schedule CA Instructions |

### Applicable Tax Rules

#### RSU Taxation (Pub. 525, "Restricted Stock")
- **At vest:** Ordinary income = FMV at vest x shares vested. Included in W-2 Box 1.
- **At sale:** Capital gain/loss = proceeds - FMV at vest. Holding period starts day after vest.
- **Basis correction:** Brokers frequently report $0 basis. Correct basis = FMV at vest.
- **Form 8949 code:** B (basis reported to IRS is incorrect).

#### NSO Taxation (Pub. 525, "Nonstatutory Stock Options")
- **At exercise:** Ordinary income = (FMV at exercise - strike price) x shares. Included in W-2 Box 1, Box 12 Code V.
- **At sale:** Capital gain/loss = proceeds - (strike + ordinary income recognized).
- **Basis:** Strike price + recognized ordinary income.

#### ESPP Taxation (Pub. 525, "Employee Stock Purchase Plans"; Form 3922 Instructions)
- **At purchase:** No taxable event for Section 423 plans.
- **At sale (Qualifying Disposition — held > 2 years from offering date AND > 1 year from purchase date):**
  - Ordinary income = LESSER OF:
    - (a) FMV at sale - purchase price paid, OR
    - (b) FMV at offering date x discount percentage (typically 15%)
  - Basis = purchase price + ordinary income recognized.
  - Remainder is LTCG.
- **At sale (Disqualifying Disposition):**
  - Ordinary income = FMV at purchase date - purchase price paid (the "spread" at purchase).
  - Basis = purchase price + ordinary income recognized = FMV at purchase date.
  - Remainder is ST or LT capital gain/loss depending on holding period from purchase date.

#### ISO Taxation (Pub. 525, "Incentive Stock Options"; Form 3921/6251 Instructions)
- **At exercise:** No regular tax income. AMT preference item = (FMV at exercise - strike price) x shares (Form 6251 Line 2i).
- **Regular tax basis:** Strike price (until disqualifying disposition).
- **AMT basis:** FMV at exercise date.
- **Disqualifying disposition:** If sold < 2 years from grant OR < 1 year from exercise, ordinary income is recognized.
- **AMT credit:** Prior-year AMT generates Minimum Tax Credit (Form 8801) that carries forward indefinitely.

#### Form 8949 Reconciliation (Form 8949 Instructions)
- **Category A:** Short-term, basis reported to IRS (covered).
- **Category B:** Short-term, basis NOT reported to IRS.
- **Category C:** Short-term, no 1099-B received.
- **Category D:** Long-term, basis reported to IRS (covered).
- **Category E:** Long-term, basis NOT reported to IRS.
- **Category F:** Long-term, no 1099-B received.
- **Adjustment codes:** B = basis incorrect, e = wash sale loss disallowed, O = other adjustment.
- **Columns:** (a) description, (b) date acquired, (c) date sold, (d) proceeds, (e) basis reported, (f) adjustment code, (g) adjustment amount, (h) gain/loss.

#### Federal Tax Estimation (2025 Tax Year)
- Tax brackets (MFJ): 10% up to $23,850 | 12% to $96,950 | 22% to $206,700 | 24% to $394,600 | 32% to $501,050 | 35% to $751,600 | 37% above.
- Tax brackets (Single): 10% up to $11,925 | 12% to $48,475 | 22% to $103,350 | 24% to $197,300 | 32% to $250,525 | 35% to $626,350 | 37% above.
- Standard deduction: $30,000 (MFJ) / $15,000 (Single).
- NIIT: 3.8% on net investment income when MAGI > $250,000 (MFJ) / $200,000 (Single).
- AMT exemption: $133,300 (MFJ) / $85,700 (Single), phases out at $1,218,700 (MFJ) / $609,350 (Single).
- LTCG rates: 0% / 15% / 20% depending on taxable income bracket.

#### California Tax Estimation (2025 Tax Year)
- Brackets (Single): 1% to 13.3% across 10 brackets.
- Mental Health Services Tax: additional 1% on income above $1,000,000.
- Standard deduction: ~$5,540 (Single) / ~$11,080 (MFJ).
- California taxes capital gains at ordinary income rates (no preferential LTCG rate).
- California generally conforms to federal AMT but with different exemption amounts.

---

## Data Model Specification

### Core Enumerations

```python
class EquityType(str, Enum):
    RSU = "RSU"
    ISO = "ISO"
    NSO = "NSO"
    ESPP = "ESPP"

class TransactionType(str, Enum):
    VEST = "VEST"           # RSU vest
    EXERCISE = "EXERCISE"   # ISO/NSO exercise
    PURCHASE = "PURCHASE"   # ESPP purchase
    SALE = "SALE"           # Any equity sale
    DIVIDEND = "DIVIDEND"   # Dividend received
    INTEREST = "INTEREST"   # Interest received

class DispositionType(str, Enum):
    QUALIFYING = "QUALIFYING"
    DISQUALIFYING = "DISQUALIFYING"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class HoldingPeriod(str, Enum):
    SHORT_TERM = "SHORT_TERM"   # <= 1 year
    LONG_TERM = "LONG_TERM"     # > 1 year

class Form8949Category(str, Enum):
    A = "A"  # ST, basis reported
    B = "B"  # ST, basis not reported
    C = "C"  # ST, no 1099-B
    D = "D"  # LT, basis reported
    E = "E"  # LT, basis not reported
    F = "F"  # LT, no 1099-B

class AdjustmentCode(str, Enum):
    B = "B"   # Basis incorrect
    E = "e"   # Wash sale loss disallowed (lowercase per IRS)
    O = "O"   # Other
    NONE = ""

class FilingStatus(str, Enum):
    SINGLE = "SINGLE"
    MFJ = "MARRIED_FILING_JOINTLY"
    MFS = "MARRIED_FILING_SEPARATELY"
    HOH = "HEAD_OF_HOUSEHOLD"

class BrokerSource(str, Enum):
    SHAREWORKS = "SHAREWORKS"
    ROBINHOOD = "ROBINHOOD"
    MANUAL = "MANUAL"
```

### Core Models (`app/models/equity_event.py`)

```python
class Security(BaseModel):
    ticker: str
    name: str
    cusip: str | None = None

class Lot(BaseModel):
    id: str                                    # UUID
    equity_type: EquityType
    security: Security
    acquisition_date: date
    shares: Decimal = Field(ge=0)
    cost_per_share: Decimal                    # Regular tax basis per share
    amt_cost_per_share: Decimal | None = None  # AMT basis (ISOs only)
    shares_remaining: Decimal = Field(ge=0)    # Unsold shares
    source_event_id: str                       # Traceability to originating event
    broker_source: BrokerSource
    notes: str | None = None

    @property
    def total_cost_basis(self) -> Decimal:
        return self.shares * self.cost_per_share

    @property
    def total_amt_basis(self) -> Decimal | None:
        if self.amt_cost_per_share is None:
            return None
        return self.shares * self.amt_cost_per_share

class EquityEvent(BaseModel):
    id: str                                    # UUID
    event_type: TransactionType
    equity_type: EquityType
    security: Security
    event_date: date
    shares: Decimal = Field(ge=0)
    price_per_share: Decimal                   # FMV at event date
    strike_price: Decimal | None = None        # For options
    purchase_price: Decimal | None = None      # For ESPP
    offering_date: date | None = None          # For ESPP (Form 3922)
    grant_date: date | None = None             # For ISO/NSO (Form 3921)
    ordinary_income: Decimal | None = None     # Computed income
    broker_source: BrokerSource
    raw_data: dict | None = None               # Preserve original import data

class Sale(BaseModel):
    id: str                                    # UUID
    lot_id: str                                # FK to Lot
    security: Security
    sale_date: date
    shares: Decimal = Field(ge=0)
    proceeds_per_share: Decimal
    broker_reported_basis: Decimal | None = None
    broker_reported_basis_per_share: Decimal | None = None
    wash_sale_disallowed: Decimal = Decimal("0")
    form_1099b_received: bool = True
    basis_reported_to_irs: bool = True         # Box 12 on 1099-B
    broker_source: BrokerSource

    @property
    def total_proceeds(self) -> Decimal:
        return self.shares * self.proceeds_per_share

class SaleResult(BaseModel):
    """Output of basis correction engine for a single sale."""
    sale_id: str
    lot_id: str
    security: Security
    acquisition_date: date
    sale_date: date
    shares: Decimal
    proceeds: Decimal
    broker_reported_basis: Decimal | None
    correct_basis: Decimal
    adjustment_amount: Decimal               # correct_basis - broker_reported_basis
    adjustment_code: AdjustmentCode
    holding_period: HoldingPeriod
    form_8949_category: Form8949Category
    gain_loss: Decimal                        # proceeds - correct_basis
    ordinary_income: Decimal                  # For ESPP/ISO disqualifying
    amt_adjustment: Decimal                   # For ISO AMT preference
    wash_sale_disallowed: Decimal
    notes: str | None = None
```

### Tax Form Models (`app/models/tax_forms.py`)

```python
class W2(BaseModel):
    employer_name: str
    employer_ein: str | None = None
    tax_year: int
    box1_wages: Decimal                        # Wages, tips, other compensation
    box2_federal_withheld: Decimal             # Federal income tax withheld
    box3_ss_wages: Decimal | None = None
    box4_ss_withheld: Decimal | None = None
    box5_medicare_wages: Decimal | None = None
    box6_medicare_withheld: Decimal | None = None
    box12_codes: dict[str, Decimal] = {}       # e.g. {"V": Decimal("5000"), "DD": Decimal("12000")}
    box14_other: dict[str, Decimal] = {}       # e.g. {"RSU": Decimal("50000"), "ESPP": Decimal("3000")}
    box16_state_wages: Decimal | None = None
    box17_state_withheld: Decimal | None = None
    state: str = "CA"

class Form1099B(BaseModel):
    broker_name: str
    tax_year: int
    description: str                           # Security description
    date_acquired: date | None = None          # May be "Various"
    date_sold: date
    proceeds: Decimal
    cost_basis: Decimal | None = None          # May be blank
    wash_sale_loss_disallowed: Decimal | None = None
    basis_reported_to_irs: bool
    box_type: str | None = None                # Short-term vs Long-term
    broker_source: BrokerSource
    raw_data: dict | None = None

class Form3921(BaseModel):
    """ISO exercise record."""
    tax_year: int
    grant_date: date
    exercise_date: date
    exercise_price_per_share: Decimal          # Box 3
    fmv_on_exercise_date: Decimal              # Box 4
    shares_transferred: Decimal                # Box 5
    employer_name: str | None = None

    @property
    def spread_per_share(self) -> Decimal:
        return self.fmv_on_exercise_date - self.exercise_price_per_share

    @property
    def total_amt_preference(self) -> Decimal:
        return self.spread_per_share * self.shares_transferred

class Form3922(BaseModel):
    """ESPP transfer record."""
    tax_year: int
    offering_date: date                        # Box 1 (date option granted)
    purchase_date: date                        # Box 2 (date option exercised)
    fmv_on_offering_date: Decimal              # Box 3
    fmv_on_purchase_date: Decimal              # Box 4
    purchase_price_per_share: Decimal           # Box 5
    shares_transferred: Decimal                # Box 6
    employer_name: str | None = None

    @property
    def discount_per_share(self) -> Decimal:
        return self.fmv_on_purchase_date - self.purchase_price_per_share

class Form1099DIV(BaseModel):
    broker_name: str
    tax_year: int
    ordinary_dividends: Decimal                # Box 1a
    qualified_dividends: Decimal               # Box 1b
    total_capital_gain_distributions: Decimal = Decimal("0")  # Box 2a
    federal_tax_withheld: Decimal = Decimal("0")
    state_tax_withheld: Decimal = Decimal("0")

class Form1099INT(BaseModel):
    payer_name: str
    tax_year: int
    interest_income: Decimal                   # Box 1
    early_withdrawal_penalty: Decimal = Decimal("0")  # Box 2
    federal_tax_withheld: Decimal = Decimal("0")
    state_tax_withheld: Decimal = Decimal("0")
```

### Report Output Models (`app/models/reports.py`)

```python
class Form8949Line(BaseModel):
    description: str
    date_acquired: date | str                  # "Various" allowed
    date_sold: date
    proceeds: Decimal
    cost_basis: Decimal
    adjustment_code: AdjustmentCode
    adjustment_amount: Decimal
    gain_loss: Decimal
    category: Form8949Category

class ReconciliationLine(BaseModel):
    sale_id: str
    security: str
    sale_date: date
    shares: Decimal
    broker_proceeds: Decimal
    broker_basis: Decimal | None
    correct_basis: Decimal
    adjustment: Decimal
    adjustment_code: AdjustmentCode
    gain_loss_broker: Decimal | None
    gain_loss_correct: Decimal
    difference: Decimal
    notes: str | None = None

class ESPPIncomeLine(BaseModel):
    security: str
    offering_date: date
    purchase_date: date
    sale_date: date
    shares: Decimal
    purchase_price: Decimal
    fmv_at_purchase: Decimal
    fmv_at_offering: Decimal
    sale_proceeds: Decimal
    disposition_type: DispositionType
    ordinary_income: Decimal
    adjusted_basis: Decimal
    capital_gain_loss: Decimal
    holding_period: HoldingPeriod

class AMTWorksheetLine(BaseModel):
    security: str
    grant_date: date
    exercise_date: date
    shares: Decimal
    strike_price: Decimal
    fmv_at_exercise: Decimal
    spread_per_share: Decimal
    total_amt_preference: Decimal
    regular_basis: Decimal
    amt_basis: Decimal

class TaxEstimate(BaseModel):
    tax_year: int
    filing_status: FilingStatus
    # Income
    w2_wages: Decimal
    interest_income: Decimal
    dividend_income: Decimal
    qualified_dividends: Decimal
    short_term_gains: Decimal
    long_term_gains: Decimal
    total_income: Decimal
    agi: Decimal
    # Deductions
    standard_deduction: Decimal
    itemized_deductions: Decimal | None = None
    deduction_used: Decimal
    taxable_income: Decimal
    # Federal
    federal_regular_tax: Decimal
    federal_ltcg_tax: Decimal
    federal_niit: Decimal
    federal_amt: Decimal
    federal_total_tax: Decimal
    federal_withheld: Decimal
    federal_estimated_payments: Decimal = Decimal("0")
    federal_balance_due: Decimal
    # California
    ca_taxable_income: Decimal
    ca_tax: Decimal
    ca_mental_health_tax: Decimal
    ca_total_tax: Decimal
    ca_withheld: Decimal
    ca_estimated_payments: Decimal = Decimal("0")
    ca_balance_due: Decimal
    # Total
    total_tax: Decimal
    total_withheld: Decimal
    total_balance_due: Decimal

class AuditEntry(BaseModel):
    timestamp: datetime
    engine: str
    operation: str
    inputs: dict
    output: dict
    notes: str | None = None
```

---

## Implementation Instructions

### For Python Engineer

#### Phase 1: Project Skeleton (This Plan)

##### 1. Project Setup
- Create `pyproject.toml` with dependencies: pydantic>=2.0, pandas>=2.0, typer>=0.9, jinja2>=3.0, pytest>=7.0, ruff>=0.1.
- Create all directories per the architecture in CLAUDE.md.
- Create all `__init__.py` files with appropriate exports.

##### 2. Models (`app/models/`)
- Implement all models exactly as specified above.
- `app/models/__init__.py` — re-export all public models.
- `app/models/enums.py` — all enumerations.
- `app/models/equity_event.py` — Security, Lot, EquityEvent, Sale, SaleResult.
- `app/models/tax_forms.py` — W2, Form1099B, Form3921, Form3922, Form1099DIV, Form1099INT.
- `app/models/reports.py` — Form8949Line, ReconciliationLine, ESPPIncomeLine, AMTWorksheetLine, TaxEstimate, AuditEntry.

##### 3. Exceptions (`app/exceptions.py`)
```python
class TaxComputationError(Exception): ...
class BasisMismatchError(TaxComputationError): ...
class LotNotFoundError(TaxComputationError): ...
class InsufficientSharesError(TaxComputationError): ...
class ValidationError(TaxComputationError): ...
class ImportError(TaxComputationError): ...
class ReconciliationError(TaxComputationError): ...
```

##### 4. Ingestion Adapters (`app/ingestion/`)
- `base.py` — Abstract base class `BaseAdapter` with method signatures:
  - `parse(file_path: Path) -> list[EquityEvent | Form1099B]`
  - `validate(data: list) -> list[ValidationError]`
- `shareworks.py` — `ShareworksAdapter(BaseAdapter)` — stub.
- `robinhood.py` — `RobinhoodAdapter(BaseAdapter)` — stub.
- `manual.py` — `ManualAdapter(BaseAdapter)` — stub for W-2, 3921, 3922 manual entry.

##### 5. Normalization (`app/normalization/`)
- `events.py` — `EventNormalizer` class:
  - `normalize(raw_events: list[EquityEvent]) -> list[EquityEvent]`
  - Deduplication logic (stub).
  - Validation logic (stub).
- `ledger.py` — `LedgerBuilder` class:
  - `build_lots(events: list[EquityEvent]) -> list[Lot]`
  - `match_sales(lots: list[Lot], sales: list[Sale]) -> list[tuple[Lot, Sale]]`

##### 6. Engines (`app/engines/`)
- `lot_matcher.py` — `LotMatcher` class:
  - `match(lots: list[Lot], sale: Sale, method: str = "FIFO") -> list[tuple[Lot, Decimal]]`
- `basis.py` — `BasisCorrectionEngine` class:
  - `correct(lot: Lot, sale: Sale) -> SaleResult`
  - `correct_rsu_basis(lot, sale) -> SaleResult`
  - `correct_nso_basis(lot, sale) -> SaleResult`
  - `correct_espp_basis(lot, sale, form3922: Form3922) -> SaleResult`
  - `correct_iso_basis(lot, sale, form3921: Form3921) -> SaleResult`
- `espp.py` — `ESPPEngine` class:
  - `compute_disposition(sale, lot, form3922: Form3922) -> ESPPIncomeLine`
  - `is_qualifying(offering_date, purchase_date, sale_date) -> bool`
  - `compute_ordinary_income(form3922, sale, disposition_type) -> Decimal`
- `iso_amt.py` — `ISOAMTEngine` class:
  - `compute_amt_preference(form3921: Form3921) -> AMTWorksheetLine`
  - `compute_amt_liability(preferences: list[AMTWorksheetLine], other_income: Decimal, filing_status: FilingStatus, tax_year: int) -> Decimal`
  - `compute_amt_credit(prior_year_amt: Decimal) -> Decimal`
- `estimator.py` — `TaxEstimator` class:
  - `estimate(income_data, deductions, filing_status, tax_year) -> TaxEstimate`
  - `compute_federal_tax(taxable_income, filing_status, tax_year) -> Decimal`
  - `compute_ltcg_tax(ltcg, qualified_divs, taxable_income, filing_status) -> Decimal`
  - `compute_niit(investment_income, agi, filing_status) -> Decimal`
  - `compute_california_tax(taxable_income, filing_status, tax_year) -> Decimal`
- `strategy.py` — `StrategyEngine` class:
  - `analyze(tax_estimate: TaxEstimate, lots: list[Lot], ...) -> list[StrategyRecommendation]`
  - Stub for now.

##### 7. Tax Brackets Configuration (`app/engines/brackets.py`)
- Store federal and California brackets as data structures, keyed by tax year and filing status.
- Never hardcode brackets inside computation functions.
- Include 2024 and 2025 brackets.

##### 8. Database (`app/db/`)
- `schema.py` — SQLite schema creation (tables for lots, events, sales, sale_results, audit_log, import_batches).
- `repository.py` — `TaxRepository` class with CRUD methods for each entity.
- `migrations.py` — Schema versioning stub.

##### 9. Reports (`app/reports/`)
- `form8949.py` — `Form8949Generator`: takes list[SaleResult] -> list[Form8949Line], renders via Jinja2 template.
- `espp_report.py` — `ESPPReportGenerator`: takes list[ESPPIncomeLine] -> rendered report.
- `amt_worksheet.py` — `AMTWorksheetGenerator`: takes list[AMTWorksheetLine] -> rendered worksheet.
- `reconciliation.py` — `ReconciliationReportGenerator`: takes list[ReconciliationLine] -> rendered report.
- `strategy_report.py` — `StrategyReportGenerator`: stub.
- `templates/` — Create Jinja2 template stubs for each report.

##### 10. CLI (`app/cli.py`)
- Typer app with these commands:
  - `import-data` — Import from a source (shareworks, robinhood, manual).
  - `reconcile` — Run basis correction and reconciliation for a tax year.
  - `estimate` — Compute tax estimate for a tax year.
  - `strategy` — Run strategy analysis.
  - `report` — Generate all reports to an output directory.
- All commands should be wired but can print stub messages initially.

##### 11. Tests (`tests/`)
- `conftest.py` — Shared fixtures: sample Lot, Sale, W2, Form3921, Form3922, Form1099B.
- `test_models/` — Test model creation and validation.
- `test_ingestion/` — Test adapter interfaces.
- `test_normalization/` — Test event normalizer and ledger builder.
- `test_engines/` — One test file per engine with at least one representative test case:
  - `test_basis.py` — RSU $0 basis correction.
  - `test_espp.py` — Qualifying vs disqualifying disposition.
  - `test_iso_amt.py` — AMT preference computation.
  - `test_estimator.py` — Basic federal tax estimate.
  - `test_lot_matcher.py` — FIFO matching.
- `test_reports/` — Test report generation.
- `test_cli.py` — Test CLI commands execute without error.

---

## Validation Criteria

### Skeleton Completeness
- [ ] All directories exist per architecture diagram.
- [ ] All `__init__.py` files created with exports.
- [ ] `python -m app.cli --help` runs successfully.
- [ ] `python -m pytest tests/ --collect-only` discovers all test files.
- [ ] All Pydantic models can be instantiated with sample data.
- [ ] No circular imports.
- [ ] Ruff passes with no errors.

### Model Validation
- [ ] All monetary fields use `Decimal`, never `float`.
- [ ] All date fields use `datetime.date`.
- [ ] Enumerations cover all known values.
- [ ] Computed properties return correct types.

### Tax Rule Validation (For Future Engine Implementation)
- [ ] RSU basis correction: broker_basis=$0, correct_basis=FMV at vest, adjustment_code=B.
- [ ] ESPP qualifying: ordinary income = lesser of (discount, actual gain), remainder = LTCG.
- [ ] ESPP disqualifying: ordinary income = spread at purchase, remainder = STCG/LTCG.
- [ ] ISO AMT: preference = (FMV at exercise - strike) x shares.
- [ ] Holding period: > 1 year from day after acquisition = long-term.

---

## Risk Flags

- **Broker data quality:** Shareworks and Robinhood export formats change periodically. Adapters must be defensively coded.
- **AMT complexity:** ISO AMT requires tracking two parallel basis systems. The data model must support this from day one.
- **ESPP double taxation:** The most common error in ESPP taxation. The system must always adjust basis for recognized ordinary income.
- **Wash sales across accounts:** If the taxpayer sells at a loss in Robinhood and buys substantially identical stock in Shareworks within 30 days, the loss is disallowed. Cross-account tracking is essential.
- **California non-conformity:** California does not have preferential LTCG rates. All gains are taxed at ordinary rates. The estimator must handle this correctly.

---

## Agent Assignments

### [PYTHON ENGINEER]
- Implement the complete project skeleton as described in Phase 1 above.
- Ensure all modules are importable and tests are collectible.
- Use exact model definitions from this plan.
- Follow all coding conventions from the Python Engineer agent spec.

### [ACCOUNTANT]
- Review the data models for accounting completeness.
- Validate that the lot tracking model supports all required operations.
- Confirm journal entry patterns can be represented.

### [TAX PLANNER]
- Review the StrategyEngine interface.
- Provide specifications for strategy recommendation models.
- Confirm projection models can be built on this infrastructure.

---

## Log

### [CPA] 2026-02-10T23:00
- Infrastructure plan created.
- All data models specified with IRS citations.
- Implementation instructions provided for Python Engineer.
- Tax rules documented for RSU, NSO, ESPP, ISO, and Form 8949.
- Federal and California estimation requirements documented.
- Validation criteria and risk flags identified.

### [PYTHON ENGINEER] 2026-02-10T23:30
- Project skeleton fully implemented.
- Created pyproject.toml with all dependencies (pydantic, pandas, typer, jinja2, pytest, ruff).
- Created virtual environment at .venv/.
- Implemented all Pydantic models: enums, equity_event, tax_forms, reports.
- Implemented exceptions module with typed error hierarchy.
- Implemented 3 ingestion adapters (Shareworks, Robinhood, Manual) with abstract base class.
- Implemented normalization layer (EventNormalizer, LedgerBuilder).
- Implemented 6 engine modules: lot_matcher, basis, espp, iso_amt, estimator, strategy.
- Implemented tax brackets configuration (federal + California, 2024-2025).
- Implemented database layer: schema (SQLite), repository (CRUD), migrations stub.
- Implemented 5 report generators with Jinja2 templates.
- Implemented Typer CLI with 5 commands (import-data, reconcile, estimate, strategy, report).
- Created comprehensive test suite: 37 tests across 11 test files, all passing.
- Ruff lint passes with 0 errors.
- CLI runs successfully (`python -m app.cli --help`).
- All validation criteria from the CPA plan are met.
