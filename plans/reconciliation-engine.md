# Reconciliation Engine — CPA Tax Plan

**Session ID:** tax-2026-02-12-reconciliation-engine-001
**Date:** 2026-02-12
**Status:** Planning
**Tax Year:** 2024

**Participants:**
- Tax Expert CPA (lead)
- Python Engineer (primary implementor)
- Accountant (validation and reconciliation sign-off)
- Tax Planner (as needed for strategy implications)

**Scope:**
- Implement the reconciliation engine — the CORE of TaxBot 9000 — which takes imported lots and sales, matches them, corrects cost basis, determines holding period and Form 8949 category, computes gain/loss, handles ESPP dispositions and ISO AMT adjustments, and produces SaleResult records with a complete audit log.
- This engine bridges the gap between raw imported data and tax-ready output. Without it, no Form 8949 can be generated, no tax estimate can be computed, and no strategy recommendations can be made.
- Wire the `reconcile` CLI command to orchestrate the full pipeline.
- Definition of "done": A user can run `taxbot reconcile 2024` and see all sales matched to lots, basis corrected, SaleResults persisted to the database, and a reconciliation audit log printed. All downstream engines (estimator, strategy, report) can consume the SaleResult records.

---

## Tax Analysis

### Forms & Documents Involved

| Form | Role in Reconciliation | Reference |
|---|---|---|
| 1099-B | Source of broker-reported proceeds and (often incorrect) cost basis | Form 8949 Instructions |
| Form 3921 | ISO exercise records — provides exercise price and FMV for dual-basis tracking | Form 3921 Instructions |
| Form 3922 | ESPP purchase records — provides offering date, purchase price, FMV for disposition determination | Form 3922 Instructions |
| W-2 (Box 12 Code V, Box 14) | Cross-reference: confirms ordinary income from NSO exercises and RSU vests | Pub. 525 |
| Form 8949 | OUTPUT: Corrected cost-basis sales — one line per sale | Form 8949 Instructions |
| Form 6251 | OUTPUT: ISO AMT preference items (Line 2i) | Form 6251 Instructions |
| Schedule D | OUTPUT: Summary totals by Form 8949 category (A-F) | Schedule D Instructions |

### Applicable Tax Rules

#### 1. RSU Cost Basis Correction (Pub. 525, "Restricted Stock Units")

- **Rule:** RSU basis = FMV at vest date. This amount is already included in W-2 Box 1 as ordinary income.
- **Broker problem:** Morgan Stanley Shareworks and similar brokers often report $0 basis on 1099-B because the shares were "transferred" at no cost to the employee. The IRS DOES receive the $0 basis from the broker.
- **Correction:** Set correct_basis = lot.cost_per_share (FMV at vest) x sale.shares.
- **Form 8949 treatment:** Report the sale with adjustment code B (basis reported to IRS is incorrect). The adjustment amount = correct_basis - broker_reported_basis. The gain/loss on Form 8949 = proceeds - correct_basis.
- **IRS citation:** Pub. 525 states: "The fair market value of the stock at the time it is transferred to you (minus any amount you paid for the stock) is included in your income for the year of the transfer."

#### 2. NSO Cost Basis Correction (Pub. 525, "Nonstatutory Stock Options")

- **Rule:** NSO basis = exercise price + spread recognized as ordinary income at exercise.
- **Broker problem:** Broker may report only the exercise price as basis, omitting the ordinary income component. Or may report $0.
- **Correction:** lot.cost_per_share already includes both components (set at import from brokerage data). Set correct_basis = lot.cost_per_share x sale.shares.
- **Form 8949 treatment:** Same as RSU — adjustment code B if basis differs.
- **IRS citation:** Pub. 525: "Your basis in the stock you receive is the amount you paid plus any amount you had to include in income."

#### 3. ESPP Disposition Income (Pub. 525, "Employee Stock Purchase Plans"; Form 3922 Instructions)

- **Rule — Qualifying Disposition:** Sale occurs more than 2 years after offering date AND more than 1 year after purchase date.
  - Ordinary income = LESSER OF:
    - (a) Actual gain: (sale_price - purchase_price) x shares
    - (b) Discount at offering date: (FMV_offering - purchase_price) x shares
  - If sale price < purchase price (loss), ordinary income = $0.
  - Adjusted basis = (purchase_price x shares) + ordinary_income.
  - Capital gain/loss = proceeds - adjusted_basis. This is always LONG-TERM (by definition, since held > 1 year from purchase).

- **Rule — Disqualifying Disposition:** Any sale that does NOT meet both holding periods.
  - Ordinary income = spread at purchase date = (FMV_purchase - purchase_price) x shares.
  - Adjusted basis = (purchase_price x shares) + ordinary_income = FMV_purchase x shares.
  - Capital gain/loss = proceeds - adjusted_basis. May be SHORT-TERM or LONG-TERM depending on how long after purchase_date the sale occurred.

- **Broker problem:** Broker typically reports purchase_price as cost basis, ignoring the ordinary income adjustment. Or reports $0.
- **Correction:** Compute ordinary income per disposition type, then adjusted_basis = (purchase_price x shares) + ordinary_income.
- **Form 8949 treatment:** Adjustment code B. The adjustment = correct_basis - broker_reported_basis.
- **IRS citation:** Form 3922 Instructions: "Use this form to determine any adjustments you must make ... when you dispose of the stock."

#### 4. ISO Dual-Basis Tracking (Form 6251, Form 3921 Instructions, Pub. 525)

- **Regular tax basis:** exercise_price x shares (lot.cost_per_share).
- **AMT basis:** FMV_at_exercise x shares (lot.amt_cost_per_share).
- **At exercise:** No regular income. AMT preference item = (FMV_exercise - exercise_price) x shares. This goes on Form 6251, Line 2i.
- **At sale (disqualifying disposition — does not meet both ISO holding periods):**
  - Ordinary income recognized = spread at exercise = (FMV_exercise - exercise_price) x shares (or actual gain if less).
  - Regular basis adjusted upward by ordinary income.
  - AMT preference item for the year of sale = NEGATIVE of the prior AMT preference (reversal).
  - Net effect: AMT and regular tax converge when there is a disqualifying disposition.
- **At sale (qualifying disposition — held > 2 years from grant AND > 1 year from exercise):**
  - All gain is long-term capital gain.
  - Regular basis = exercise_price. AMT basis = FMV_exercise.
  - Regular gain = proceeds - (exercise_price x shares).
  - AMT gain = proceeds - (FMV_exercise x shares).
  - The AMT adjustment for the year of sale = AMT_gain - regular_gain (this is negative, reversing the prior preference).
- **ISO holding period test:** Per IRC Section 422(a)(1):
  - Must hold > 2 years from grant_date AND > 1 year from exercise_date.
  - If EITHER test fails, it is a disqualifying disposition.
- **Broker problem:** Broker reports exercise price as basis (no adjustment for AMT). This is actually correct for regular tax, but broker may report $0 instead. AMT basis is never reported by the broker.
- **IRS citation:** Form 6251 Instructions, Line 2i: "Enter the excess of the fair market value over the amount paid for stock acquired through the exercise of an incentive stock option."

#### 5. Form 8949 Category Determination (Form 8949 Instructions)

Per the Form 8949 Instructions, transactions must be separated into six categories:

| Category | Holding Period | Basis Reporting | 1099-B Received |
|---|---|---|---|
| A (Part I, Box A) | Short-term | Reported to IRS | Yes |
| B (Part I, Box B) | Short-term | NOT reported to IRS | Yes |
| C (Part I, Box C) | Short-term | N/A | No |
| D (Part II, Box D) | Long-term | Reported to IRS | Yes |
| E (Part II, Box E) | Long-term | NOT reported to IRS | Yes |
| F (Part II, Box F) | Long-term | N/A | No |

**Holding period determination** (Form 8949 Instructions, "How To Determine Your Holding Period"):
- Holding period begins the DAY AFTER acquisition date.
- Long-term = held MORE THAN one year (sale_date > acquisition_date + 1 year + 1 day).
- The existing `BasisCorrectionEngine._holding_period()` correctly implements this.

#### 6. Adjustment Code Determination (Form 8949 Instructions, Column (f))

| Code | When to Use | Description |
|---|---|---|
| B | Basis reported to IRS is incorrect | Most common for equity compensation. Broker reports $0 or partial basis. |
| e (lowercase) | Basis not reported to IRS (shown as $0 on 1099-B) | Used when box 1e of 1099-B is blank or zero AND basis_reported_to_irs = True. |
| O | Other adjustments | Wash sales, ESPP ordinary income, or other adjustments not covered by B or e. |
| (none) | No adjustment needed | Rare for equity compensation — means broker-reported basis is correct. |

**Combined codes:** When multiple adjustments apply (e.g., both basis correction AND wash sale), use code "B;W" or report on separate lines. For TaxBot, we will use "O" for combined adjustments and document in notes.

#### 7. Wash Sale Rules (Pub. 550, "Wash Sales")

- If substantially identical stock is purchased within 30 days before or after a sale at a loss, the loss is disallowed.
- The disallowed loss is added to the basis of the replacement shares.
- The 1099-B may already report wash_sale_loss_disallowed (Box 1g).
- **For this phase:** Trust the broker-reported wash sale amount from the 1099-B. Do NOT independently compute wash sales (this is a future enhancement).
- **Form 8949 treatment:** If wash_sale_disallowed > 0, add adjustment code W. Adjustment amount = wash_sale_disallowed (positive number, added back to basis).

### Key Findings

1. **Every equity compensation sale will need a basis correction.** Brokers systematically underreport basis for RSUs, ESPPs, and ISOs. The reconciliation engine is not optional — it is required for correct tax reporting.

2. **ESPP requires the Form 3922 data at sale time, not just at purchase.** The ordinary income computation depends on offering_date, FMV_offering, and FMV_purchase — all from the Form 3922. The engine must look up the corresponding Form 3922 (or the EquityEvent with offering_date) for each ESPP sale.

3. **ISO requires dual tracking throughout.** Every ISO SaleResult must carry both regular and AMT gain/loss figures. The AMT adjustment field on SaleResult is critical for Form 6251.

4. **Lot matching is non-trivial.** Sales from 1099-B may not have lot IDs. The engine must match by security + date range + share count. FIFO is the default method.

5. **Partial lot sales are common.** A single lot of 100 shares may be sold in multiple transactions. The lot matcher must track shares_remaining and allocate accordingly.

---

## Calculations

### Federal Tax (Deferred to Estimator Engine)

The reconciliation engine does NOT compute tax liability. It produces the corrected Form 8949 data that the estimator engine consumes. However, the reconciliation engine must correctly classify:
- Ordinary income (from ESPP dispositions and ISO disqualifying dispositions) — reported on W-2 or Form 1040 Line 1 (additional income).
- Short-term capital gains — Schedule D Part I.
- Long-term capital gains — Schedule D Part II.
- AMT preference items — Form 6251 Line 2i.

### California State Tax (Deferred to Estimator Engine)

California conforms to federal treatment of equity compensation with one critical exception:
- **California does NOT conform to the federal ISO AMT exclusion.** Per FTB Publication 1001, California treats ISO exercises as taxable events, similar to NSO treatment. This means the reconciliation engine must flag ISO exercises for separate California treatment. However, this is an estimator concern — the reconciliation engine just produces the SaleResult with amt_adjustment populated.

### AMT (Produced by Reconciliation)

For each ISO sale, the reconciliation engine computes:
- **amt_adjustment** on SaleResult = AMT_gain - regular_gain.
  - For qualifying dispositions: this is negative (reverses prior year's preference).
  - For disqualifying dispositions: this is also negative (full reversal).
- The ISOAMTEngine.compute_amt_preference() handles the exercise-year preference. The reconciliation engine handles the sale-year reversal.

---

## Implementation Instructions

### For Python Engineer

#### Overview

Files to create or modify:

| File | Action | Description |
|---|---|---|
| `app/engines/reconciliation.py` | **Create** | Main ReconciliationEngine — orchestrates matching, basis correction, and SaleResult generation |
| `app/engines/basis.py` | **Modify** | Complete ESPP and ISO basis correction methods |
| `app/engines/lot_matcher.py` | **Modify** | Add fuzzy matching for unmatched 1099-B sales |
| `app/db/repository.py` | **Modify** | Add query methods needed by reconciliation |
| `app/db/schema.py` | **Modify** | Add reconciliation_runs table and audit_log indexes |
| `app/cli.py` | **Modify** | Wire `reconcile` command to ReconciliationEngine |
| `app/exceptions.py` | **Modify** | Add new exception types for reconciliation errors |
| `tests/test_engines/test_reconciliation.py` | **Create** | Unit + integration tests for the reconciliation engine |
| `tests/test_engines/test_basis_espp.py` | **Create** | Unit tests for ESPP basis correction |
| `tests/test_engines/test_basis_iso.py` | **Create** | Unit tests for ISO basis correction |
| `tests/test_engines/test_lot_matcher_fuzzy.py` | **Create** | Unit tests for fuzzy lot matching |

---

#### 1. ReconciliationEngine (`app/engines/reconciliation.py`)

This is the main orchestrator. It ties together lot matching, basis correction, ESPP disposition logic, and ISO AMT tracking.

```python
"""Reconciliation engine — core of TaxBot 9000.

Matches 1099-B sales to acquisition lots, corrects cost basis,
determines Form 8949 categories, and produces SaleResult records.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.db.repository import TaxRepository
from app.engines.basis import BasisCorrectionEngine
from app.engines.espp import ESPPEngine
from app.engines.iso_amt import ISOAMTEngine
from app.engines.lot_matcher import LotMatcher
from app.exceptions import (
    LotNotFoundError,
    ReconciliationError,
)
from app.models.enums import (
    AdjustmentCode,
    DispositionType,
    EquityType,
    Form8949Category,
    HoldingPeriod,
)
from app.models.equity_event import EquityEvent, Lot, Sale, SaleResult, Security
from app.models.reports import AuditEntry, ReconciliationLine
from app.models.tax_forms import Form3922


@dataclass
class ReconciliationResult:
    """Output of a full reconciliation run."""
    tax_year: int
    sale_results: list[SaleResult] = field(default_factory=list)
    reconciliation_lines: list[ReconciliationLine] = field(default_factory=list)
    audit_log: list[AuditEntry] = field(default_factory=list)
    unmatched_sales: list[Sale] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_proceeds(self) -> Decimal:
        return sum((r.proceeds for r in self.sale_results), Decimal("0"))

    @property
    def total_correct_basis(self) -> Decimal:
        return sum((r.correct_basis for r in self.sale_results), Decimal("0"))

    @property
    def total_gain_loss(self) -> Decimal:
        return sum((r.gain_loss for r in self.sale_results), Decimal("0"))

    @property
    def total_ordinary_income(self) -> Decimal:
        return sum((r.ordinary_income for r in self.sale_results), Decimal("0"))

    @property
    def total_amt_adjustment(self) -> Decimal:
        return sum((r.amt_adjustment for r in self.sale_results), Decimal("0"))


class ReconciliationEngine:
    """Orchestrates the full reconciliation pipeline."""

    def __init__(self, repo: TaxRepository):
        self.repo = repo
        self.basis_engine = BasisCorrectionEngine()
        self.espp_engine = ESPPEngine()
        self.iso_amt_engine = ISOAMTEngine()
        self.lot_matcher = LotMatcher()

    def reconcile(self, tax_year: int) -> ReconciliationResult:
        """Run the full reconciliation for a tax year.

        Pipeline:
        1. Load all lots and sales for the tax year.
        2. For each sale, match to a lot (or lots for partial sales).
        3. Correct cost basis based on equity type.
        4. Determine holding period and Form 8949 category.
        5. Compute adjustment codes and amounts.
        6. Handle ESPP disposition income.
        7. Handle ISO AMT adjustments.
        8. Generate SaleResult records.
        9. Persist results and audit log.
        """
        result = ReconciliationResult(tax_year=tax_year)

        # Step 1: Load data
        lots = self._load_lots()
        sales = self._load_sales(tax_year)
        events = self._load_events()

        if not sales:
            result.warnings.append(f"No sales found for tax year {tax_year}")
            return result

        if not lots:
            result.errors.append(
                f"No lots found. Import 3921/3922/brokerage data before reconciling."
            )
            return result

        self._log(result, "reconciliation", "start",
                  {"tax_year": tax_year, "lots": len(lots), "sales": len(sales)},
                  {"status": "started"})

        # Step 2-8: Process each sale
        for sale in sales:
            try:
                sale_results = self._process_sale(sale, lots, events, result)
                result.sale_results.extend(sale_results)
            except LotNotFoundError as exc:
                result.unmatched_sales.append(sale)
                result.warnings.append(
                    f"Sale {sale.id} ({sale.security.name}, {sale.sale_date}): "
                    f"No matching lot found — {exc}"
                )
                self._log(result, "reconciliation", "lot_not_found",
                          {"sale_id": sale.id}, {"error": str(exc)})
            except ReconciliationError as exc:
                result.errors.append(f"Sale {sale.id}: {exc}")
                self._log(result, "reconciliation", "error",
                          {"sale_id": sale.id}, {"error": str(exc)})

        # Step 9: Generate reconciliation lines for the report
        for sr in result.sale_results:
            # Find the original sale for broker-reported values
            original_sale = next((s for s in sales if s.id == sr.sale_id), None)
            broker_basis = sr.broker_reported_basis or Decimal("0")
            broker_gain = (sr.proceeds - broker_basis) if broker_basis else None

            recon_line = ReconciliationLine(
                sale_id=sr.sale_id,
                security=sr.security.ticker,
                sale_date=sr.sale_date,
                shares=sr.shares,
                broker_proceeds=sr.proceeds,
                broker_basis=sr.broker_reported_basis,
                correct_basis=sr.correct_basis,
                adjustment=sr.adjustment_amount,
                adjustment_code=sr.adjustment_code,
                gain_loss_broker=broker_gain,
                gain_loss_correct=sr.gain_loss,
                difference=(sr.gain_loss - broker_gain) if broker_gain is not None else sr.gain_loss,
                notes=sr.notes,
            )
            result.reconciliation_lines.append(recon_line)

        self._log(result, "reconciliation", "complete",
                  {"tax_year": tax_year},
                  {
                      "sale_results": len(result.sale_results),
                      "unmatched": len(result.unmatched_sales),
                      "total_proceeds": str(result.total_proceeds),
                      "total_basis": str(result.total_correct_basis),
                      "total_gain_loss": str(result.total_gain_loss),
                      "total_ordinary_income": str(result.total_ordinary_income),
                  })

        return result

    def _process_sale(
        self,
        sale: Sale,
        lots: list[Lot],
        events: list[EquityEvent],
        result: ReconciliationResult,
    ) -> list[SaleResult]:
        """Process a single sale: match, correct basis, compute results.

        Returns a list because a single sale may span multiple lots (partial allocation).
        """
        # Filter lots to same security
        matching_lots = self._filter_lots_by_security(lots, sale)
        if not matching_lots:
            raise LotNotFoundError(
                f"No lots found for security '{sale.security.ticker}' / '{sale.security.name}'"
            )

        # Match sale to lots
        if sale.lot_id and sale.lot_id != "":
            # Specific lot ID already assigned (e.g., from brokerage data)
            allocations = self.lot_matcher.match(matching_lots, sale, method="SPECIFIC")
        else:
            # FIFO matching
            allocations = self.lot_matcher.match(matching_lots, sale, method="FIFO")

        if not allocations:
            raise LotNotFoundError(
                f"Could not allocate shares for sale {sale.id} "
                f"({sale.shares} shares of {sale.security.name})"
            )

        # Verify full allocation
        total_allocated = sum(shares for _, shares in allocations)
        if total_allocated < sale.shares:
            result.warnings.append(
                f"Sale {sale.id}: Only {total_allocated} of {sale.shares} shares "
                f"could be allocated to lots. Possible missing lot data."
            )

        sale_results = []
        for lot, allocated_shares in allocations:
            # Create a sub-sale for this lot allocation
            sub_sale = Sale(
                id=sale.id,
                lot_id=lot.id,
                security=sale.security,
                sale_date=sale.sale_date,
                shares=allocated_shares,
                proceeds_per_share=sale.proceeds_per_share,
                broker_reported_basis=self._prorate_basis(
                    sale.broker_reported_basis, allocated_shares, sale.shares
                ),
                broker_reported_basis_per_share=sale.broker_reported_basis_per_share,
                wash_sale_disallowed=self._prorate_basis(
                    sale.wash_sale_disallowed, allocated_shares, sale.shares
                ),
                form_1099b_received=sale.form_1099b_received,
                basis_reported_to_irs=sale.basis_reported_to_irs,
                broker_source=sale.broker_source,
            )

            # Dispatch to equity-type-specific handler
            sr = self._correct_basis_by_type(lot, sub_sale, events, result)
            sale_results.append(sr)

            # Decrement lot shares_remaining
            lot.shares_remaining -= allocated_shares
            self._log(result, "reconciliation", "lot_allocated",
                      {"lot_id": lot.id, "sale_id": sale.id, "shares": str(allocated_shares)},
                      {"shares_remaining": str(lot.shares_remaining)})

        return sale_results

    def _correct_basis_by_type(
        self,
        lot: Lot,
        sale: Sale,
        events: list[EquityEvent],
        result: ReconciliationResult,
    ) -> SaleResult:
        """Dispatch to the correct basis correction handler based on equity type."""
        match lot.equity_type:
            case EquityType.RSU:
                sr = self.basis_engine.correct_rsu_basis(lot, sale)
                self._log(result, "basis_correction", "rsu",
                          {"lot_id": lot.id, "sale_id": sale.id},
                          {"correct_basis": str(sr.correct_basis),
                           "adjustment": str(sr.adjustment_amount)},
                          notes="RSU basis = FMV at vest (Pub. 525)")
                return sr

            case EquityType.NSO:
                sr = self.basis_engine.correct_nso_basis(lot, sale)
                self._log(result, "basis_correction", "nso",
                          {"lot_id": lot.id, "sale_id": sale.id},
                          {"correct_basis": str(sr.correct_basis),
                           "adjustment": str(sr.adjustment_amount)},
                          notes="NSO basis = strike + spread (Pub. 525)")
                return sr

            case EquityType.ESPP:
                return self._correct_espp_basis(lot, sale, events, result)

            case EquityType.ISO:
                return self._correct_iso_basis(lot, sale, events, result)

            case _:
                raise ReconciliationError(
                    f"Unknown equity type '{lot.equity_type}' for lot {lot.id}"
                )

    def _correct_espp_basis(
        self,
        lot: Lot,
        sale: Sale,
        events: list[EquityEvent],
        result: ReconciliationResult,
    ) -> SaleResult:
        """ESPP basis correction with disposition income computation.

        Requires the Form 3922 data (via the EquityEvent) for the lot.
        """
        # Find the ESPP purchase event for this lot
        event = self._find_event_for_lot(lot, events)
        if event is None:
            raise ReconciliationError(
                f"ESPP lot {lot.id}: No purchase event found. "
                f"Cannot determine disposition type without offering_date."
            )

        # Build Form3922 from the event data
        form3922 = self._event_to_form3922(event)

        # Use ESPPEngine to compute disposition
        espp_result = self.espp_engine.compute_disposition(sale, lot, form3922)

        # Compute basis correction
        correct_basis = espp_result.adjusted_basis
        proceeds = sale.total_proceeds
        broker_basis = sale.broker_reported_basis or Decimal("0")
        adjustment = correct_basis - broker_basis
        holding = self.basis_engine._holding_period(lot.acquisition_date, sale.sale_date)
        category = self.basis_engine._form_8949_category(
            holding, sale.basis_reported_to_irs, sale.form_1099b_received
        )

        # Determine adjustment code
        if broker_basis == Decimal("0") and sale.basis_reported_to_irs:
            adj_code = AdjustmentCode.E
        elif adjustment != Decimal("0"):
            adj_code = AdjustmentCode.B
        else:
            adj_code = AdjustmentCode.NONE

        # Add wash sale adjustment if present
        wash_sale = sale.wash_sale_disallowed
        if wash_sale > Decimal("0"):
            adj_code = AdjustmentCode.OTHER  # Multiple adjustments
            correct_basis += wash_sale

        disposition_label = espp_result.disposition_type.value.lower()
        notes = (
            f"ESPP {disposition_label} disposition. "
            f"Offering date: {form3922.offering_date}. "
            f"Purchase price: {form3922.purchase_price_per_share}/sh. "
            f"FMV at purchase: {form3922.fmv_on_purchase_date}/sh. "
            f"FMV at offering: {form3922.fmv_on_offering_date}/sh. "
            f"Ordinary income: {espp_result.ordinary_income}. "
            f"Pub. 525 ESPP rules applied."
        )

        sr = SaleResult(
            sale_id=sale.id,
            lot_id=lot.id,
            security=lot.security,
            acquisition_date=lot.acquisition_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=correct_basis,
            adjustment_amount=adjustment,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=proceeds - correct_basis,
            ordinary_income=espp_result.ordinary_income,
            wash_sale_disallowed=wash_sale,
            notes=notes,
        )

        self._log(result, "basis_correction", "espp",
                  {"lot_id": lot.id, "sale_id": sale.id,
                   "disposition": disposition_label},
                  {"ordinary_income": str(espp_result.ordinary_income),
                   "correct_basis": str(correct_basis),
                   "adjustment": str(adjustment)},
                  notes=notes)

        return sr

    def _correct_iso_basis(
        self,
        lot: Lot,
        sale: Sale,
        events: list[EquityEvent],
        result: ReconciliationResult,
    ) -> SaleResult:
        """ISO basis correction with dual-basis (regular + AMT) tracking.

        Per Form 6251 Instructions and Pub. 525.
        """
        event = self._find_event_for_lot(lot, events)

        # Regular tax basis
        regular_basis_per_share = lot.cost_per_share  # = exercise price
        regular_basis = regular_basis_per_share * sale.shares

        # AMT basis
        amt_basis_per_share = lot.amt_cost_per_share or lot.cost_per_share
        amt_basis = amt_basis_per_share * sale.shares

        proceeds = sale.total_proceeds
        broker_basis = sale.broker_reported_basis or Decimal("0")

        # Determine if qualifying or disqualifying ISO disposition
        grant_date = event.grant_date if event else None
        exercise_date = lot.acquisition_date  # For ISOs, acquisition = exercise

        # ISO qualifying test: > 2 years from grant AND > 1 year from exercise
        is_qualifying = False
        if grant_date:
            two_years_from_grant = self._add_years(grant_date, 2)
            one_year_from_exercise = self._add_years(exercise_date, 1)
            is_qualifying = (
                sale.sale_date > two_years_from_grant
                and sale.sale_date > one_year_from_exercise
            )

        if is_qualifying:
            # Qualifying disposition: all gain is capital (regular basis = exercise price)
            correct_basis = regular_basis
            ordinary_income = Decimal("0")
            # AMT adjustment at sale = AMT gain - regular gain (negative, reverses prior preference)
            regular_gain = proceeds - regular_basis
            amt_gain = proceeds - amt_basis
            amt_adjustment = amt_gain - regular_gain  # Negative number
            notes = (
                f"ISO qualifying disposition. "
                f"Grant date: {grant_date}. Exercise date: {exercise_date}. "
                f"Regular basis: {regular_basis_per_share}/sh (exercise price). "
                f"AMT basis: {amt_basis_per_share}/sh (FMV at exercise). "
                f"AMT adjustment (reversal): {amt_adjustment}. "
                f"Form 6251 Line 2i."
            )
        else:
            # Disqualifying disposition: ordinary income = spread (or actual gain if less)
            spread_per_share = amt_basis_per_share - regular_basis_per_share
            actual_gain_per_share = sale.proceeds_per_share - regular_basis_per_share

            if actual_gain_per_share < spread_per_share:
                # If sold at a loss or gain less than spread, ordinary income is limited
                oi_per_share = max(actual_gain_per_share, Decimal("0"))
            else:
                oi_per_share = spread_per_share

            ordinary_income = oi_per_share * sale.shares
            # Adjust regular basis upward by ordinary income
            correct_basis = regular_basis + ordinary_income
            # AMT adjustment reverses the full prior preference
            # Since ordinary income recognized, AMT and regular converge
            regular_gain = proceeds - correct_basis
            amt_gain = proceeds - amt_basis
            amt_adjustment = amt_gain - regular_gain  # Should be ~0 for full disqualifying
            notes = (
                f"ISO disqualifying disposition. "
                f"Grant date: {grant_date}. Exercise date: {exercise_date}. "
                f"Spread at exercise: {spread_per_share}/sh. "
                f"Ordinary income recognized: {ordinary_income}. "
                f"Regular basis adjusted to: {correct_basis}. "
                f"AMT adjustment (reversal): {amt_adjustment}. "
                f"Pub. 525 ISO rules. Form 6251 Line 2i."
            )

        adjustment = correct_basis - broker_basis
        holding = self.basis_engine._holding_period(lot.acquisition_date, sale.sale_date)
        category = self.basis_engine._form_8949_category(
            holding, sale.basis_reported_to_irs, sale.form_1099b_received
        )

        # Determine adjustment code
        if broker_basis == Decimal("0") and sale.basis_reported_to_irs:
            adj_code = AdjustmentCode.E
        elif adjustment != Decimal("0"):
            adj_code = AdjustmentCode.B
        else:
            adj_code = AdjustmentCode.NONE

        wash_sale = sale.wash_sale_disallowed
        if wash_sale > Decimal("0"):
            adj_code = AdjustmentCode.OTHER
            correct_basis += wash_sale

        sr = SaleResult(
            sale_id=sale.id,
            lot_id=lot.id,
            security=lot.security,
            acquisition_date=lot.acquisition_date,
            sale_date=sale.sale_date,
            shares=sale.shares,
            proceeds=proceeds,
            broker_reported_basis=broker_basis,
            correct_basis=correct_basis,
            adjustment_amount=adjustment,
            adjustment_code=adj_code,
            holding_period=holding,
            form_8949_category=category,
            gain_loss=proceeds - correct_basis,
            ordinary_income=ordinary_income,
            amt_adjustment=amt_adjustment,
            wash_sale_disallowed=wash_sale,
            notes=notes,
        )

        self._log(result, "basis_correction", "iso",
                  {"lot_id": lot.id, "sale_id": sale.id,
                   "qualifying": is_qualifying},
                  {"ordinary_income": str(ordinary_income),
                   "correct_basis": str(correct_basis),
                   "amt_adjustment": str(amt_adjustment)},
                  notes=notes)

        return sr

    # --- Helper methods ---

    def _load_lots(self) -> list[Lot]:
        """Load all lots from the database and convert to Lot models."""
        rows = self.repo.get_lots()
        lots = []
        for row in rows:
            lot = Lot(
                id=row["id"],
                equity_type=EquityType(row["equity_type"]),
                security=Security(
                    ticker=row["ticker"],
                    name=row["security_name"],
                ),
                acquisition_date=date.fromisoformat(row["acquisition_date"]),
                shares=Decimal(row["shares"]),
                cost_per_share=Decimal(row["cost_per_share"]),
                amt_cost_per_share=(
                    Decimal(row["amt_cost_per_share"]) if row["amt_cost_per_share"] else None
                ),
                shares_remaining=Decimal(row["shares_remaining"]),
                source_event_id=row["source_event_id"],
                broker_source=BrokerSource(row["broker_source"]),
                notes=row.get("notes"),
            )
            lots.append(lot)
        return lots

    def _load_sales(self, tax_year: int) -> list[Sale]:
        """Load all sales for a tax year from the database."""
        rows = self.repo.get_sales(tax_year)
        sales = []
        for row in rows:
            sale = Sale(
                id=row["id"],
                lot_id=row["lot_id"] or "",
                security=Security(ticker=row["ticker"], name=""),
                sale_date=date.fromisoformat(row["sale_date"]),
                shares=Decimal(row["shares"]),
                proceeds_per_share=Decimal(row["proceeds_per_share"]),
                broker_reported_basis=(
                    Decimal(row["broker_reported_basis"])
                    if row["broker_reported_basis"] else None
                ),
                broker_reported_basis_per_share=(
                    Decimal(row["broker_reported_basis_per_share"])
                    if row.get("broker_reported_basis_per_share") else None
                ),
                wash_sale_disallowed=Decimal(row.get("wash_sale_disallowed", "0")),
                form_1099b_received=bool(row["form_1099b_received"]),
                basis_reported_to_irs=bool(row["basis_reported_to_irs"]),
                broker_source=BrokerSource(row["broker_source"]),
            )
            sales.append(sale)
        return sales

    def _load_events(self) -> list[EquityEvent]:
        """Load all equity events from the database."""
        rows = self.repo.get_events()
        events = []
        for row in rows:
            event = EquityEvent(
                id=row["id"],
                event_type=TransactionType(row["event_type"]),
                equity_type=EquityType(row["equity_type"]),
                security=Security(ticker=row["ticker"], name=row["security_name"]),
                event_date=date.fromisoformat(row["event_date"]),
                shares=Decimal(row["shares"]),
                price_per_share=Decimal(row["price_per_share"]),
                strike_price=(
                    Decimal(row["strike_price"]) if row.get("strike_price") else None
                ),
                purchase_price=(
                    Decimal(row["purchase_price"]) if row.get("purchase_price") else None
                ),
                offering_date=(
                    date.fromisoformat(row["offering_date"])
                    if row.get("offering_date") else None
                ),
                grant_date=(
                    date.fromisoformat(row["grant_date"])
                    if row.get("grant_date") else None
                ),
                ordinary_income=(
                    Decimal(row["ordinary_income"]) if row.get("ordinary_income") else None
                ),
                broker_source=BrokerSource(row["broker_source"]),
            )
            events.append(event)
        return events

    def _find_event_for_lot(self, lot: Lot, events: list[EquityEvent]) -> EquityEvent | None:
        """Find the equity event that created a given lot."""
        for event in events:
            if event.id == lot.source_event_id:
                return event
        return None

    def _event_to_form3922(self, event: EquityEvent) -> Form3922:
        """Reconstruct a Form3922 from an ESPP purchase event."""
        if event.offering_date is None:
            raise ReconciliationError(
                f"ESPP event {event.id} is missing offering_date. "
                f"Cannot determine qualifying disposition."
            )
        return Form3922(
            tax_year=event.event_date.year,
            offering_date=event.offering_date,
            purchase_date=event.event_date,
            fmv_on_offering_date=event.price_per_share,  # Stored as FMV on offering
            fmv_on_purchase_date=event.price_per_share,
            purchase_price_per_share=event.purchase_price or Decimal("0"),
            shares_transferred=event.shares,
        )

    def _filter_lots_by_security(self, lots: list[Lot], sale: Sale) -> list[Lot]:
        """Filter lots to those matching the sale's security.

        Matching logic (in priority order):
        1. Exact ticker match (when both are known / not "UNKNOWN")
        2. Fuzzy name match (substring of security name)
        3. Same equity type with compatible dates
        """
        # Priority 1: Exact ticker match
        if sale.security.ticker != "UNKNOWN":
            ticker_matches = [
                lot for lot in lots
                if lot.security.ticker == sale.security.ticker
                and lot.shares_remaining > Decimal("0")
            ]
            if ticker_matches:
                return ticker_matches

        # Priority 2: Name-based matching
        sale_name = sale.security.name.upper()
        name_matches = [
            lot for lot in lots
            if lot.shares_remaining > Decimal("0")
            and (
                lot.security.name.upper() in sale_name
                or sale_name in lot.security.name.upper()
                or self._names_overlap(lot.security.name, sale.security.name)
            )
        ]
        if name_matches:
            return name_matches

        # Priority 3: Return all lots with remaining shares (last resort)
        return [lot for lot in lots if lot.shares_remaining > Decimal("0")]

    @staticmethod
    def _names_overlap(name1: str, name2: str) -> bool:
        """Check if two security names share significant words."""
        stop_words = {"inc", "corp", "co", "ltd", "the", "of", "stock", "common", "class", "a", "b"}
        words1 = {w.lower() for w in name1.split() if w.lower() not in stop_words and len(w) > 2}
        words2 = {w.lower() for w in name2.split() if w.lower() not in stop_words and len(w) > 2}
        return bool(words1 & words2)

    @staticmethod
    def _prorate_basis(
        total: Decimal | None, allocated_shares: Decimal, total_shares: Decimal
    ) -> Decimal:
        """Prorate a total amount based on share allocation."""
        if total is None or total_shares == Decimal("0"):
            return Decimal("0")
        return (total * allocated_shares / total_shares).quantize(Decimal("0.01"))

    @staticmethod
    def _add_years(d: date, years: int) -> date:
        """Add years to a date, handling leap years."""
        try:
            return d.replace(year=d.year + years)
        except ValueError:
            return d.replace(year=d.year + years, day=28)

    @staticmethod
    def _log(
        result: ReconciliationResult,
        engine: str,
        operation: str,
        inputs: dict,
        output: dict,
        notes: str | None = None,
    ) -> None:
        """Append an audit entry to the reconciliation result."""
        result.audit_log.append(
            AuditEntry(
                timestamp=datetime.now(),
                engine=engine,
                operation=operation,
                inputs=inputs,
                output=output,
                notes=notes,
            )
        )

    def persist(self, result: ReconciliationResult) -> None:
        """Save all SaleResults and audit log entries to the database."""
        for sr in result.sale_results:
            self.repo.save_sale_result(sr)
        for entry in result.audit_log:
            self.repo.save_audit_entry(entry)
```

**Key design decisions:**
- The engine loads ALL lots (not just for one tax year) because lots may have been acquired in prior years.
- Sales are filtered by tax year (sale_date falls within the year).
- Partial lot allocations are supported: one sale can draw from multiple lots.
- The engine produces a ReconciliationResult with full audit trail.
- Every basis correction is logged with IRS citations.

---

#### 2. BasisCorrectionEngine Updates (`app/engines/basis.py`)

Complete the ESPP and ISO stubs. The ESPP method needs the Form3922 data, and the ISO method needs dual-basis tracking.

**Replace `correct_espp_basis` stub:**

```python
def correct_espp_basis(self, lot: Lot, sale: Sale, form3922: Form3922) -> SaleResult:
    """ESPP basis correction.

    Per Pub. 525 and Form 3922 Instructions:
    - Qualifying: ordinary_income = lesser of (actual gain, discount at offering)
    - Disqualifying: ordinary_income = spread at purchase
    - Adjusted basis = (purchase_price x shares) + ordinary_income
    """
    from app.engines.espp import ESPPEngine
    espp = ESPPEngine()
    espp_result = espp.compute_disposition(sale, lot, form3922)

    correct_basis = espp_result.adjusted_basis
    proceeds = sale.total_proceeds
    broker_basis = sale.broker_reported_basis or Decimal("0")
    adjustment = correct_basis - broker_basis
    holding = self._holding_period(lot.acquisition_date, sale.sale_date)
    category = self._form_8949_category(
        holding, sale.basis_reported_to_irs, sale.form_1099b_received
    )
    adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

    return SaleResult(
        sale_id=sale.id,
        lot_id=lot.id,
        security=lot.security,
        acquisition_date=lot.acquisition_date,
        sale_date=sale.sale_date,
        shares=sale.shares,
        proceeds=proceeds,
        broker_reported_basis=broker_basis,
        correct_basis=correct_basis,
        adjustment_amount=adjustment,
        adjustment_code=adj_code,
        holding_period=holding,
        form_8949_category=category,
        gain_loss=proceeds - correct_basis,
        ordinary_income=espp_result.ordinary_income,
    )
```

**Replace `correct_iso_basis` stub:**

```python
def correct_iso_basis(
    self, lot: Lot, sale: Sale, form3921: Form3921, is_qualifying: bool
) -> SaleResult:
    """ISO basis correction with dual-basis tracking.

    Per Form 6251 Instructions and Pub. 525:
    - Regular basis = exercise price (lot.cost_per_share)
    - AMT basis = FMV at exercise (lot.amt_cost_per_share)
    - Qualifying: all gain is capital. AMT adjustment reverses prior preference.
    - Disqualifying: ordinary income = spread (or actual gain if less).
    """
    regular_basis = lot.cost_per_share * sale.shares
    amt_basis = (lot.amt_cost_per_share or lot.cost_per_share) * sale.shares
    proceeds = sale.total_proceeds
    broker_basis = sale.broker_reported_basis or Decimal("0")

    if is_qualifying:
        correct_basis = regular_basis
        ordinary_income = Decimal("0")
    else:
        spread = (lot.amt_cost_per_share or lot.cost_per_share) - lot.cost_per_share
        actual_gain_per_share = sale.proceeds_per_share - lot.cost_per_share
        oi_per_share = min(spread, max(actual_gain_per_share, Decimal("0")))
        ordinary_income = oi_per_share * sale.shares
        correct_basis = regular_basis + ordinary_income

    # AMT adjustment = AMT gain - regular gain
    regular_gain = proceeds - correct_basis
    amt_gain = proceeds - amt_basis
    amt_adjustment = amt_gain - regular_gain

    adjustment = correct_basis - broker_basis
    holding = self._holding_period(lot.acquisition_date, sale.sale_date)
    category = self._form_8949_category(
        holding, sale.basis_reported_to_irs, sale.form_1099b_received
    )
    adj_code = AdjustmentCode.B if adjustment != 0 else AdjustmentCode.NONE

    return SaleResult(
        sale_id=sale.id,
        lot_id=lot.id,
        security=lot.security,
        acquisition_date=lot.acquisition_date,
        sale_date=sale.sale_date,
        shares=sale.shares,
        proceeds=proceeds,
        broker_reported_basis=broker_basis,
        correct_basis=correct_basis,
        adjustment_amount=adjustment,
        adjustment_code=adj_code,
        holding_period=holding,
        form_8949_category=category,
        gain_loss=proceeds - correct_basis,
        ordinary_income=ordinary_income,
        amt_adjustment=amt_adjustment,
    )
```

---

#### 3. LotMatcher Updates (`app/engines/lot_matcher.py`)

Add a fuzzy matching method for 1099-B sales that have no lot_id and may not have an exact ticker match.

```python
def match_fuzzy(
    self,
    lots: list[Lot],
    sale: Sale,
    tolerance_days: int = 5,
) -> list[tuple[Lot, Decimal]]:
    """Fuzzy match: find lots whose acquisition date and share count
    are close to the sale's expected acquisition.

    Used as a fallback when FIFO matching returns no results because
    the security identifiers don't match exactly.

    Args:
        lots: All available lots.
        sale: The sale to match.
        tolerance_days: Date window for fuzzy matching.

    Returns:
        List of (lot, shares_allocated) tuples.
    """
    candidates = [
        lot for lot in lots
        if lot.shares_remaining > Decimal("0")
        and lot.acquisition_date <= sale.sale_date
    ]

    if not candidates:
        return []

    # Sort by acquisition date (FIFO) and apply
    candidates.sort(key=lambda l: l.acquisition_date)
    remaining = sale.shares
    allocations: list[tuple[Lot, Decimal]] = []

    for lot in candidates:
        if remaining <= Decimal("0"):
            break
        allocated = min(lot.shares_remaining, remaining)
        allocations.append((lot, allocated))
        remaining -= allocated

    return allocations
```

---

#### 4. Repository Updates (`app/db/repository.py`)

Add these new methods needed by the reconciliation engine:

```python
def get_sales(self, tax_year: int) -> list[dict]:
    """Retrieve all sales for a given tax year (by sale_date)."""
    cursor = self.conn.execute(
        "SELECT * FROM sales WHERE sale_date LIKE ?",
        (f"{tax_year}-%",),
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def get_events(self) -> list[dict]:
    """Retrieve all equity events."""
    cursor = self.conn.execute("SELECT * FROM equity_events")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

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

def save_audit_entry(self, entry: AuditEntry) -> None:
    """Insert an audit log entry."""
    import json
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

def clear_sale_results(self, tax_year: int) -> int:
    """Delete all sale results for a tax year (for re-reconciliation).
    Returns the number of rows deleted."""
    cursor = self.conn.execute(
        "DELETE FROM sale_results WHERE sale_date LIKE ?",
        (f"{tax_year}-%",),
    )
    self.conn.commit()
    return cursor.rowcount

def update_lot_shares_remaining(self, lot_id: str, shares_remaining: Decimal) -> None:
    """Update the shares_remaining for a lot after allocation."""
    self.conn.execute(
        "UPDATE lots SET shares_remaining = ? WHERE id = ?",
        (str(shares_remaining), lot_id),
    )
    self.conn.commit()
```

**Import needed at top of repository.py:**
```python
from app.models.reports import AuditEntry
```

---

#### 5. Schema Updates (`app/db/schema.py`)

Add a `reconciliation_runs` table to track each reconciliation execution:

```sql
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
    warnings TEXT,    -- JSON array of warning strings
    errors TEXT,      -- JSON array of error strings
    status TEXT NOT NULL DEFAULT 'completed'
);
```

Add to the `SCHEMA_SQL` string before the closing `"""`. Bump `SCHEMA_VERSION` to 3.

---

#### 6. CLI `reconcile` Command (`app/cli.py`)

Replace the stub with a working implementation:

```python
@app.command()
def reconcile(
    year: int = typer.Argument(..., help="Tax year to reconcile"),
    db: Path = typer.Option(
        Path.home() / ".taxbot" / "taxbot.db",
        "--db",
        help="Path to the SQLite database file",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-reconcile even if results already exist (clears previous results)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print detailed audit log",
    ),
) -> None:
    """Run basis correction and reconciliation for a tax year."""
    from app.db.repository import TaxRepository
    from app.db.schema import create_schema
    from app.engines.reconciliation import ReconciliationEngine

    if not db.exists():
        typer.echo(f"Error: Database not found at {db}. Run 'taxbot import-data' first.", err=True)
        raise typer.Exit(1)

    conn = create_schema(db)
    repo = TaxRepository(conn)

    # Check for existing results
    existing = repo.get_sale_results(year)
    if existing and not force:
        typer.echo(
            f"Sale results already exist for {year} ({len(existing)} records). "
            f"Use --force to re-reconcile.",
            err=True,
        )
        raise typer.Exit(1)
    elif existing and force:
        cleared = repo.clear_sale_results(year)
        typer.echo(f"Cleared {cleared} existing sale results for {year}.")

    # Run reconciliation
    engine = ReconciliationEngine(repo)
    typer.echo(f"Reconciling tax year {year}...")
    result = engine.reconcile(year)

    # Persist results
    engine.persist(result)

    # Update lot shares_remaining in database
    lots = engine._load_lots()
    for lot in lots:
        repo.update_lot_shares_remaining(lot.id, lot.shares_remaining)

    conn.close()

    # Print summary
    typer.echo("")
    typer.echo(f"=== Reconciliation Complete: Tax Year {year} ===")
    typer.echo(f"Sales processed:     {len(result.sale_results)}")
    typer.echo(f"Unmatched sales:     {len(result.unmatched_sales)}")
    typer.echo(f"Total proceeds:      ${result.total_proceeds:,.2f}")
    typer.echo(f"Total correct basis: ${result.total_correct_basis:,.2f}")
    typer.echo(f"Total gain/loss:     ${result.total_gain_loss:,.2f}")
    if result.total_ordinary_income > 0:
        typer.echo(f"Ordinary income:     ${result.total_ordinary_income:,.2f}")
    if result.total_amt_adjustment != 0:
        typer.echo(f"AMT adjustment:      ${result.total_amt_adjustment:,.2f}")

    if result.warnings:
        typer.echo("")
        typer.echo("Warnings:")
        for warning in result.warnings:
            typer.echo(f"  - {warning}")

    if result.errors:
        typer.echo("")
        typer.echo("Errors:", err=True)
        for error in result.errors:
            typer.echo(f"  - {error}", err=True)

    if verbose:
        typer.echo("")
        typer.echo("Audit Log:")
        for entry in result.audit_log:
            typer.echo(
                f"  [{entry.timestamp:%H:%M:%S}] {entry.engine}.{entry.operation}: "
                f"{entry.notes or json.dumps(entry.output)}"
            )

    # Print Form 8949 category summary
    from collections import Counter
    categories = Counter(sr.form_8949_category for sr in result.sale_results)
    if categories:
        typer.echo("")
        typer.echo("Form 8949 Summary:")
        for cat in sorted(categories):
            typer.echo(f"  Category {cat.value}: {categories[cat]} transaction(s)")
```

---

#### 7. Exception Updates (`app/exceptions.py`)

Add these new exception classes:

```python
class SaleMatchError(TaxComputationError):
    """Raised when a sale cannot be matched to any lot."""

    def __init__(self, sale_id: str, reason: str):
        self.sale_id = sale_id
        super().__init__(f"Cannot match sale {sale_id}: {reason}")


class MissingEventDataError(TaxComputationError):
    """Raised when required event data (e.g., offering_date for ESPP) is missing."""

    def __init__(self, lot_id: str, field: str):
        self.lot_id = lot_id
        self.field = field
        super().__init__(f"Lot {lot_id}: missing required field '{field}' on source event")
```

---

#### 8. ESPP Engine Fix (`app/engines/espp.py`)

The existing `_event_to_form3922` method in ReconciliationEngine reconstructs a Form3922 from an EquityEvent. However, the EquityEvent model stores `price_per_share` as FMV on the event date (purchase date for ESPP). The Form3922 needs `fmv_on_offering_date` separately.

**Critical data flow issue:** At import time (manual.py _parse_3922), the EquityEvent stores:
- `price_per_share` = `fmv_on_purchase_date` (from Form 3922 Box 4)
- `purchase_price` = `purchase_price_per_share` (from Form 3922 Box 5)
- `offering_date` = `offering_date` (from Form 3922 Box 1)

But `fmv_on_offering_date` (Form 3922 Box 3) is NOT stored on the EquityEvent. This is needed for qualifying disposition computation.

**Fix needed in EquityEvent model (`app/models/equity_event.py`):**

Add a new field to EquityEvent:
```python
fmv_on_offering_date: Decimal | None = None
```

**Fix needed in ManualAdapter (`app/ingestion/manual.py`):**

In `_parse_3922`, when creating the EquityEvent, add:
```python
fmv_on_offering_date=form.fmv_on_offering_date,
```

**Fix needed in repository (`app/db/repository.py`):**

Update `save_event` to include the new field, and update `equity_events` table in schema.

**Fix needed in schema (`app/db/schema.py`):**

Add `fmv_on_offering_date TEXT` column to the `equity_events` table.

**Then in ReconciliationEngine._event_to_form3922:**

```python
def _event_to_form3922(self, event: EquityEvent) -> Form3922:
    return Form3922(
        tax_year=event.event_date.year,
        offering_date=event.offering_date,
        purchase_date=event.event_date,
        fmv_on_offering_date=event.fmv_on_offering_date or event.price_per_share,
        fmv_on_purchase_date=event.price_per_share,
        purchase_price_per_share=event.purchase_price or Decimal("0"),
        shares_transferred=event.shares,
    )
```

---

### Exact Field Mappings and Formulas

#### SaleResult Field Computation — Complete Reference

For each sale, the SaleResult is computed as follows:

| SaleResult Field | RSU Formula | NSO Formula | ESPP Formula | ISO Formula |
|---|---|---|---|---|
| `correct_basis` | `lot.cost_per_share * shares` | `lot.cost_per_share * shares` | `(purchase_price * shares) + ordinary_income` | Qualifying: `exercise_price * shares`. Disqualifying: `(exercise_price * shares) + ordinary_income` |
| `ordinary_income` | `Decimal("0")` | `Decimal("0")` | Qualifying: `min(actual_gain, discount_at_offering) * shares`. Disqualifying: `(FMV_purchase - purchase_price) * shares` | Qualifying: `Decimal("0")`. Disqualifying: `min(spread, max(actual_gain, 0)) * shares` |
| `amt_adjustment` | `Decimal("0")` | `Decimal("0")` | `Decimal("0")` | `(proceeds - amt_basis) - (proceeds - correct_basis)` = `correct_basis - amt_basis` |
| `adjustment_amount` | `correct_basis - broker_basis` | `correct_basis - broker_basis` | `correct_basis - broker_basis` | `correct_basis - broker_basis` |
| `gain_loss` | `proceeds - correct_basis` | `proceeds - correct_basis` | `proceeds - correct_basis` | `proceeds - correct_basis` |
| `holding_period` | `>1yr from vest? LONG : SHORT` | `>1yr from exercise? LONG : SHORT` | `>1yr from purchase? LONG : SHORT` | `>1yr from exercise? LONG : SHORT` |
| `form_8949_category` | See table above | See table above | See table above | See table above |

#### ESPP Ordinary Income — Detailed Formulas

**Variables from Form 3922:**
- `purchase_price` = Box 5 (purchase_price_per_share)
- `FMV_purchase` = Box 4 (fmv_on_purchase_date)
- `FMV_offering` = Box 3 (fmv_on_offering_date)
- `offering_date` = Box 1
- `purchase_date` = Box 2

**Variables from Sale:**
- `sale_price` = sale.proceeds_per_share
- `sale_date` = sale.sale_date
- `shares` = sale.shares

**Qualifying Disposition Test:**
```
is_qualifying = (sale_date > offering_date + 2 years) AND (sale_date > purchase_date + 1 year)
```

**Qualifying Disposition Income:**
```
actual_gain_per_share = sale_price - purchase_price
discount_at_offering = FMV_offering - purchase_price
per_share_income = min(actual_gain_per_share, discount_at_offering)
per_share_income = max(per_share_income, Decimal("0"))  # Floor at zero
ordinary_income = per_share_income * shares
```

**Disqualifying Disposition Income:**
```
spread_at_purchase = FMV_purchase - purchase_price
ordinary_income = spread_at_purchase * shares
```

**Adjusted Basis (both):**
```
adjusted_basis = (purchase_price * shares) + ordinary_income
```

#### ISO AMT Adjustment — Detailed Formulas

**At Exercise (handled by ISOAMTEngine, NOT the reconciliation engine):**
```
amt_preference = (FMV_exercise - exercise_price) * shares  # Form 6251 Line 2i
```

**At Sale — Qualifying:**
```
regular_basis = exercise_price * shares
amt_basis = FMV_exercise * shares
regular_gain = proceeds - regular_basis
amt_gain = proceeds - amt_basis
amt_adjustment = amt_gain - regular_gain  # Negative (reversal)
```

**At Sale — Disqualifying:**
```
spread_per_share = FMV_exercise - exercise_price
actual_gain_per_share = sale_price - exercise_price
oi_per_share = min(spread_per_share, max(actual_gain_per_share, Decimal("0")))
ordinary_income = oi_per_share * shares
regular_basis_adjusted = (exercise_price * shares) + ordinary_income
regular_gain = proceeds - regular_basis_adjusted
amt_gain = proceeds - amt_basis
amt_adjustment = amt_gain - regular_gain  # Approximately 0
```

---

### For Accountant

After the Python Engineer implements the engine:

1. **Lot Register Validation:**
   - For each lot, verify that `cost_per_share` matches the expected basis:
     - RSU: FMV at vest date
     - ISO: exercise price (regular), FMV at exercise (AMT)
     - ESPP: purchase price
     - NSO: exercise price + spread
   - Verify `shares_remaining` is correctly decremented after reconciliation.

2. **Basis Reconciliation:**
   - For each SaleResult, verify: `correct_basis + adjustment_amount = broker_reported_basis + correct_basis`. Wait — the correct formula is: `broker_reported_basis + adjustment_amount = correct_basis`. Verify this identity holds for every record.
   - Verify: `proceeds - correct_basis = gain_loss` for every record.
   - Verify: no floating point drift in Decimal computations.

3. **Income Classification Audit:**
   - Sum all `ordinary_income` from SaleResults. Compare to W-2 equity income items (Box 14 RSU, Box 12 Code V for NSO).
   - Note: ESPP disqualifying disposition ordinary income may NOT be on the W-2 if the sale occurred in the current year and W-2 has not yet reflected it. Flag this for CPA review.

4. **Cross-Reference Checks:**
   - Total proceeds from SaleResults must equal total proceeds from 1099-B forms.
   - Number of SaleResults must equal number of sales (or more, if partial lot allocations create multiple results per sale).

---

### For Tax Planner

- After reconciliation completes, review the ordinary income amounts to assess whether any ESPP dispositions could have been deferred to become qualifying (reducing ordinary income and converting to LTCG).
- Review ISO AMT adjustments to determine if AMT credit carryforwards exist from prior years.
- Flag any wash sale disallowed amounts for review.

---

## Validation Criteria

### Unit Test Specifications

**`tests/test_engines/test_reconciliation.py`:**

| Test | Description | Input | Expected Output |
|---|---|---|---|
| `test_reconcile_rsu_zero_basis` | RSU sale where broker reports $0 basis | Lot: RSU, cost=50.00/sh. Sale: 100sh at $60/sh, broker_basis=$0, basis_reported=True | correct_basis=$5000, adjustment=$5000, adj_code=B, gain=$1000, category=A or D |
| `test_reconcile_rsu_correct_basis` | RSU sale where broker reports correct basis | Same lot. broker_basis=$5000 | adjustment=$0, adj_code=NONE |
| `test_reconcile_nso_basis_correction` | NSO sale with partial basis reported | Lot: NSO, cost=75.00/sh (strike+spread). Sale: 50sh at $80/sh, broker_basis=$1250 (strike only) | correct_basis=$3750, adjustment=$2500, ordinary_income=$0 (already recognized) |
| `test_reconcile_espp_qualifying` | ESPP qualifying disposition | Lot: ESPP, purchase_price=$85/sh. Form3922: offering=$100, FMV_purchase=$100, FMV_offering=$110. Sale: 2.5yr after offering, 1.5yr after purchase, $120/sh | ordinary_income=min(35, 25)*shares=25*shares, adjusted_basis=(85+25)*shares |
| `test_reconcile_espp_disqualifying` | ESPP disqualifying disposition | Same lot. Sale: 6mo after purchase, $120/sh | ordinary_income=(100-85)*shares=15*shares, adjusted_basis=(85+15)*shares=100*shares |
| `test_reconcile_espp_qualifying_loss` | ESPP qualifying with sale below purchase price | Same lot. Sale: 2.5yr after offering, $80/sh | ordinary_income=$0 (loss), adjusted_basis=85*shares, gain_loss = negative |
| `test_reconcile_iso_qualifying` | ISO qualifying disposition | Lot: ISO, cost=10/sh, amt_cost=50/sh. Sale: 3yr after grant, 2yr after exercise, $70/sh, 100sh | regular_basis=$1000, correct_basis=$1000, ordinary_income=$0, amt_adjustment=($70*100-$50*100)-($70*100-$10*100)=-$4000 |
| `test_reconcile_iso_disqualifying` | ISO disqualifying disposition (early sale) | Same lot. Sale: 6mo after exercise, $70/sh | ordinary_income=min(40,60)*100=$4000, correct_basis=$1000+$4000=$5000, amt_adjustment~=0 |
| `test_reconcile_iso_disqualifying_loss` | ISO disqualifying disposition at a loss | Same lot. Sale: 6mo after exercise, $8/sh | ordinary_income=$0 (sold below exercise price), correct_basis=$1000 |
| `test_reconcile_partial_lot` | Sale spans two lots | Lot1: 50sh. Lot2: 50sh. Sale: 80sh | Two SaleResults: (lot1, 50sh) + (lot2, 30sh) |
| `test_reconcile_fifo_ordering` | FIFO selects oldest lot first | Lot1: acquired 2023-01-01. Lot2: acquired 2023-06-01. Sale: 2024-06-01 | Lot1 consumed first |
| `test_reconcile_holding_period_short` | Sale within 1 year | Acquired 2024-03-01, sold 2024-09-01 | SHORT_TERM |
| `test_reconcile_holding_period_long` | Sale after 1 year | Acquired 2023-03-01, sold 2024-06-01 | LONG_TERM |
| `test_reconcile_holding_period_boundary` | Sale exactly at 1 year + 1 day | Acquired 2023-06-15, sold 2024-06-16 | LONG_TERM |
| `test_reconcile_category_a` | Short-term, basis reported | SHORT_TERM, basis_reported=True, 1099b=True | Category A |
| `test_reconcile_category_b` | Short-term, basis NOT reported | SHORT_TERM, basis_reported=False, 1099b=True | Category B |
| `test_reconcile_category_d` | Long-term, basis reported | LONG_TERM, basis_reported=True, 1099b=True | Category D |
| `test_reconcile_category_e` | Long-term, basis NOT reported | LONG_TERM, basis_reported=False, 1099b=True | Category E |
| `test_reconcile_wash_sale_adjustment` | Wash sale adds to basis | Sale with wash_sale_disallowed=$500 | correct_basis includes $500, adj_code=O |
| `test_reconcile_no_sales` | No sales for tax year | Empty sales | Warning: "No sales found" |
| `test_reconcile_no_lots` | Sales exist but no lots | Sales present, no lots | Error: "No lots found" |
| `test_reconcile_unmatched_sale` | Sale with no matching lot | Sale for ticker "XYZ", no lots for "XYZ" | unmatched_sales contains the sale |
| `test_reconcile_audit_log` | Audit log populated | Any successful reconciliation | audit_log is non-empty, contains start + lot_allocated + complete entries |

**`tests/test_engines/test_basis_espp.py`:**

| Test | Description | Expected |
|---|---|---|
| `test_espp_qualifying_disposition_type` | 2yr + 1yr test passes | QUALIFYING |
| `test_espp_disqualifying_disposition_type` | Either test fails | DISQUALIFYING |
| `test_espp_qualifying_income_gain` | Sale above purchase, below offering | income = actual gain |
| `test_espp_qualifying_income_discount` | Sale above offering | income = discount at offering |
| `test_espp_qualifying_income_loss` | Sale below purchase | income = $0 |
| `test_espp_disqualifying_income` | Standard disqualifying | income = spread at purchase |
| `test_espp_adjusted_basis` | For both disposition types | basis = (purchase_price * shares) + ordinary_income |

**`tests/test_engines/test_basis_iso.py`:**

| Test | Description | Expected |
|---|---|---|
| `test_iso_qualifying_regular_basis` | Qualifying disposition | correct_basis = exercise_price * shares |
| `test_iso_qualifying_amt_adjustment` | Qualifying disposition | amt_adjustment = (amt_gain - regular_gain) < 0 |
| `test_iso_disqualifying_ordinary_income` | Disqualifying, gain > spread | oi = spread * shares |
| `test_iso_disqualifying_gain_less_than_spread` | Disqualifying, gain < spread | oi = actual_gain * shares |
| `test_iso_disqualifying_at_loss` | Disqualifying, sold at loss | oi = $0, correct_basis = exercise_price * shares |
| `test_iso_dual_basis_tracking` | Both bases computed correctly | regular_basis != amt_basis |

**`tests/test_engines/test_lot_matcher_fuzzy.py`:**

| Test | Description | Expected |
|---|---|---|
| `test_fuzzy_match_by_name` | Name overlap matching | Returns lots with overlapping security names |
| `test_fuzzy_fifo_fallback` | No exact match, FIFO fallback | Allocates from all available lots in date order |
| `test_no_lots_available` | All lots exhausted | Returns empty list |

### Integration Test Specifications

**`tests/test_engines/test_reconciliation.py` (integration section):**

| Test | Description |
|---|---|
| `test_full_pipeline_rsu` | Import W-2 + 1099-B + lot data, reconcile, verify SaleResults in DB |
| `test_full_pipeline_espp` | Import 3922 + 1099-B, reconcile, verify ESPP income computed |
| `test_full_pipeline_iso` | Import 3921 + 1099-B, reconcile, verify dual-basis AMT tracking |
| `test_reconcile_idempotent` | Run reconcile twice with --force, verify same results |
| `test_cli_reconcile_command` | Run `taxbot reconcile 2024` via Typer test client, verify output |
| `test_cli_reconcile_no_data` | Run reconcile with empty DB, verify error message |
| `test_cli_reconcile_force` | Run reconcile twice, second with --force, verify re-reconciliation |

### Cross-Reference Checks

After a successful reconciliation:

1. `SUM(sale_results.proceeds)` = `SUM(1099-B proceeds)` for the tax year.
2. `COUNT(sale_results)` >= `COUNT(sales)` (may be greater due to partial lots).
3. For RSU sales: `ordinary_income = 0` for all (income was at vest, already on W-2).
4. For ESPP qualifying: `holding_period = LONG_TERM` always.
5. For ISO qualifying: `ordinary_income = 0` always.
6. `broker_reported_basis + adjustment_amount = correct_basis` for every SaleResult.
7. `proceeds - correct_basis = gain_loss` for every SaleResult.
8. `SUM(lots.shares_remaining)` >= 0 for all lots (no negative remaining).

---

## Risk Flags

### High Risk

1. **ESPP offering_date data gap.** The current EquityEvent model does NOT store `fmv_on_offering_date`. This must be added before ESPP qualifying disposition income can be computed correctly. Without this field, all ESPP dispositions would default to using FMV at purchase as a proxy for FMV at offering, which is INCORRECT for qualifying dispositions.
   - **Mitigation:** Add `fmv_on_offering_date` to EquityEvent model, schema, repository, and manual adapter.

2. **1099-B shares field often missing.** The 1099-B typically reports total proceeds but NOT the number of shares sold. The ManualAdapter currently stores `shares=Decimal("0")` for 1099-B sales. The reconciliation engine MUST infer shares from proceeds and lot data, or the 1099-B supplemental statement must provide per-share data.
   - **Mitigation:** Add share inference logic: `shares = total_proceeds / lot.cost_per_share` or from supplemental brokerage data. Alternatively, require the parse output to include shares (most brokerage supplemental statements do).

3. **Ticker "UNKNOWN" everywhere.** Both lots and sales from manual import have ticker="UNKNOWN". The fuzzy matching must work reliably with name-based matching, not ticker matching.
   - **Mitigation:** The `_filter_lots_by_security` method includes name overlap logic. Additionally, consider requiring the user to provide a ticker mapping file or updating tickers at import time.

### Medium Risk

4. **ISO holding period requires grant_date.** The ISO qualifying disposition test needs `grant_date`, which is stored on the EquityEvent but NOT on the Lot. The engine must look up the source event to get grant_date. If the event is missing, the engine cannot determine qualifying vs. disqualifying.
   - **Mitigation:** The `_find_event_for_lot` method handles this. If no event found, treat as disqualifying (conservative).

5. **Partial lot allocations and sale_id reuse.** When a sale spans multiple lots, multiple SaleResults share the same `sale_id`. The `sale_results` table uses `sale_id` as PRIMARY KEY, which means only ONE result per sale can be stored.
   - **Mitigation:** Change the `sale_results` table PRIMARY KEY from `sale_id` to an auto-generated ID (or composite key of `sale_id + lot_id`). This is a SCHEMA CHANGE.

6. **Re-reconciliation data integrity.** The `--force` flag clears existing SaleResults but does NOT reset `lots.shares_remaining`. Running reconcile twice without resetting lot shares would double-decrement.
   - **Mitigation:** Before re-reconciliation, reload lots from the database (which has the original shares_remaining) OR reset shares_remaining = shares for all lots before starting.

### Low Risk

7. **Wash sale interaction with basis correction.** If both a basis correction AND a wash sale adjustment apply, the combined adjustment must be reported correctly. Using adjustment code "O" with notes is acceptable per IRS instructions.

8. **Multiple 1099-Bs from different brokers.** A taxpayer may have sales at both Morgan Stanley and Robinhood. The engine must handle sales from multiple brokers, matching each to the correct lots.

9. **Same-day sales.** Multiple sales on the same day for the same security. FIFO still applies (oldest lot first), but the order of processing sales on the same day does not matter for FIFO.

---

## Strategy Recommendations

### Immediate Actions
- (TAX PLANNER) After reconciliation, review any ESPP disqualifying dispositions. If the shares were sold shortly before the qualifying holding period cutoff, document the missed savings for future reference.
- (TAX PLANNER) Review ISO AMT adjustments. If the taxpayer has prior-year AMT credits (from ISO exercises), the reconciliation engine's amt_adjustment data feeds into the Form 8801 credit computation.

### Next Year Planning
- (TAX PLANNER) For current-year ESPP purchases, calculate the qualifying disposition cutoff dates. Recommend holding until those dates to convert ordinary income to LTCG.
- (TAX PLANNER) For current-year ISO exercises, model the AMT impact and determine optimal exercise timing.

### Long-Term Strategies
- (TAX PLANNER) Consider specific lot identification (vs. FIFO) for future sales to optimize tax treatment.
- (TAX PLANNER) Track cumulative AMT credit carryforwards across years.

### Quantified Savings
- (TAX PLANNER) To be computed after reconciliation runs with real data. Savings estimates depend on the actual ordinary income amounts from ESPP/ISO dispositions.

---

## Reconciliation Summary

### Lot Register
- (ACCOUNTANT) Pending — to be validated after implementation.

### Basis Verification
- (ACCOUNTANT) Pending — to be validated after implementation.

### Income Classification
- (ACCOUNTANT) Pending — to be validated after implementation.

---

## Agent Assignments

### [PYTHON ENGINEER]

**Priority order:**

1. **Schema fix (CRITICAL):** Add `fmv_on_offering_date` column to `equity_events` table. Add `reconciliation_runs` table. Change `sale_results` PRIMARY KEY to support multiple results per sale_id. Bump schema version to 3.

2. **Model fix:** Add `fmv_on_offering_date: Decimal | None = None` to `EquityEvent` in `app/models/equity_event.py`.

3. **ManualAdapter fix:** Update `_parse_3922` in `app/ingestion/manual.py` to populate `fmv_on_offering_date` on the EquityEvent.

4. **Repository fix:** Update `save_event` and `get_events` in `app/db/repository.py` to handle `fmv_on_offering_date`. Add new methods: `get_sales`, `get_events`, `get_sale_results`, `save_audit_entry`, `clear_sale_results`, `update_lot_shares_remaining`.

5. **Exception additions:** Add `SaleMatchError` and `MissingEventDataError` to `app/exceptions.py`.

6. **BasisCorrectionEngine:** Complete `correct_espp_basis` and `correct_iso_basis` methods in `app/engines/basis.py`.

7. **LotMatcher:** Add `match_fuzzy` method to `app/engines/lot_matcher.py`.

8. **ReconciliationEngine:** Create `app/engines/reconciliation.py` with the full implementation specified above.

9. **CLI:** Wire the `reconcile` command in `app/cli.py`.

10. **Tests:** Write all test files specified in Validation Criteria.

### [ACCOUNTANT]

After implementation:
- Validate lot cost basis values against IRS rules for all equity types.
- Verify `broker_reported_basis + adjustment_amount = correct_basis` identity.
- Verify `proceeds - correct_basis = gain_loss` identity.
- Cross-reference total proceeds with 1099-B source data.
- Sign off on the reconciliation report.

### [CPA REVIEW]

After all agents complete:
- Verify ESPP qualifying vs. disqualifying disposition logic matches Pub. 525.
- Verify ISO dual-basis tracking matches Form 6251 Instructions.
- Verify Form 8949 categories and adjustment codes match Form 8949 Instructions.
- Verify ordinary income classifications are correct and do not double-count with W-2.
- Review audit log for completeness.
- Confirm no PII leakage in SaleResult notes or audit log.

---

## Log

### [CPA] 2026-02-12T10:00
- Reconciliation engine plan created.
- Analyzed data flow from imported lots/sales through basis correction to SaleResult output.
- Documented exact tax rules for RSU, NSO, ESPP (qualifying + disqualifying), and ISO (qualifying + disqualifying + AMT) with IRS citations.
- Identified critical data gap: EquityEvent model missing `fmv_on_offering_date` for ESPP qualifying disposition computation.
- Identified schema issue: `sale_results.sale_id` PRIMARY KEY prevents multiple results per sale (needed for partial lot allocations).
- Specified ReconciliationEngine with full implementation pseudocode.
- Specified exact field mappings and formulas for all 4 equity types.
- Defined 25+ unit tests and 7 integration tests.
- Documented 9 risk flags (3 high, 3 medium, 3 low).
- Assigned implementation priority order for Python Engineer (10 steps).
- Plan ready for implementation.

---

## Review Notes

### [CPA Review]
- (CPA) Pending — final review after implementation.

### [Accountant Review]
- (ACCOUNTANT) Pending — reconciliation sign-off after implementation.

---

## Final Summary

### [CPA]
- Pending. The reconciliation engine plan is complete and ready for implementation. This is the most complex engine in TaxBot 9000 and the foundation for all downstream tax computation.

### Tax Due Estimate
- Federal: $__________ (computed by estimator engine after reconciliation)
- California: $__________ (computed by estimator engine after reconciliation)
- AMT (if any): $__________ (computed by estimator engine after reconciliation)
- Total Estimated: $__________
- Less Withholdings: $__________
- Balance Due / (Refund): $__________
