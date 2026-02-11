# EquityTax Agent Session

Session ID: tax-YYYY-MM-DD-<short-task-slug>-NNN
Date: YYYY-MM-DD
Task: <Short description of the task>
Tax Year: YYYY

Participants:
- Tax Expert CPA (lead — required for all sessions)
- Accountant (as needed)
- Tax Planner (as needed)
- Python Engineer (as needed)

Scope:
- What the task should accomplish.
- Which tax forms or documents are involved.
- Constraints, assumptions, or external dependencies.
- Definition of "done" for this session.

Status: Planning
<!-- One of: Planning | In Progress | Waiting for Review | Completed | Blocked -->

---

## Tax Analysis

### Forms & Documents Involved
- (CPA) List all relevant forms (W-2, 1099-B, 3921, 3922, etc.)

### Applicable Tax Rules
- (CPA) Cite IRS publications, IRC sections, and FTB guidance
- (CPA) Explain the correct tax treatment for each transaction type

### Key Findings
- (CPA) Discrepancies between broker-reported and correct data
- (CPA) Income classification decisions (ordinary vs. capital)
- (CPA) AMT implications (if ISOs are involved)

---

## Calculations

### Federal Tax
- (CPA) Step-by-step computation with IRS citations
- (CPA) Show AGI, deductions, taxable income, tax liability

### California State Tax
- (CPA) California-specific adjustments
- (CPA) State tax computation

### AMT (if applicable)
- (CPA) AMT preference items
- (CPA) AMT liability computation
- (CPA) Credit carryforward tracking

---

## Implementation Instructions

### For Accountant
- (CPA) Lot validation tasks
- (CPA) Reconciliation requirements
- (CPA) Journal entries to verify

### For Tax Planner
- (CPA) Scenarios to model
- (CPA) Strategy areas to evaluate
- (CPA) Data needed for projections

### For Python Engineer
- (CPA) Modules to build or modify
- (CPA) Input/output specifications
- (CPA) Formulas with exact variable names
- (CPA) Test cases with expected results

---

## Validation Criteria

- (CPA) How to verify the output is correct
- (CPA) Cross-reference checks
- (CPA) Specific test values

---

## Risk Flags

- (CPA) Audit risk areas
- (CPA) Ambiguous tax situations
- (CPA) Items needing taxpayer clarification
- (CPA) Aggressive positions (if any)

---

## Strategy Recommendations

### Immediate Actions
- (TAX PLANNER) Actions to take now

### Next Year Planning
- (TAX PLANNER) Forward-looking optimizations

### Long-Term Strategies
- (TAX PLANNER) Multi-year tax reduction plans

### Quantified Savings
- (TAX PLANNER) Dollar estimates for each recommendation

---

## Reconciliation Summary

### Lot Register
- (ACCOUNTANT) Lot tracking validation results

### Basis Verification
- (ACCOUNTANT) Broker vs. corrected basis comparison

### Income Classification
- (ACCOUNTANT) Ordinary income vs. capital gains breakdown

---

## Log

### [CPA] YYYY-MM-DDThh:mm
- Session created.
- Tax analysis and implementation plan documented.

<!-- Agents append log entries as work progresses: -->
<!-- ### [ACCOUNTANT] YYYY-MM-DDThh:mm -->
<!-- - Validated cost basis for X transactions. -->
<!-- - Found Y discrepancies. -->

<!-- ### [TAX PLANNER] YYYY-MM-DDThh:mm -->
<!-- - Modeled X scenarios. -->
<!-- - Identified $Y in potential savings. -->

<!-- ### [PYTHON ENGINEER] YYYY-MM-DDThh:mm -->
<!-- - Implemented [engine/module]. -->
<!-- - Added X tests, all passing. -->

---

## Review Notes

### [CPA Review]
- (CPA) Final review of all calculations and code output.

### [Accountant Review]
- (ACCOUNTANT) Reconciliation sign-off.

---

## Final Summary

### [CPA]
- Pending.

### Tax Due Estimate
- Federal: $__________
- California: $__________
- AMT (if any): $__________
- Total Estimated: $__________
- Less Withholdings: $__________
- Balance Due / (Refund): $__________
