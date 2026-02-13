"""Tax-due estimation engine.

Computes federal and California state tax liability using configurable brackets.
Implements:
  - Progressive ordinary income tax (federal + CA)
  - LTCG/qualified dividend stacking per IRS Qualified Dividends and Capital Gain Tax Worksheet
  - Net Investment Income Tax (NIIT) per IRC Section 1411
  - Alternative Minimum Tax (AMT) per Form 6251
  - Capital loss netting per Schedule D / IRC Section 1211(b)
  - California Mental Health Services Tax per CA R&TC Section 17043(a)
"""

from decimal import Decimal

from app.engines.brackets import (
    AMT_28_PERCENT_THRESHOLD,
    AMT_EXEMPTION,
    AMT_PHASEOUT_START,
    CA_MENTAL_HEALTH_RATE,
    CA_MENTAL_HEALTH_THRESHOLD,
    CALIFORNIA_BRACKETS,
    CALIFORNIA_STANDARD_DEDUCTION,
    CAPITAL_LOSS_LIMIT,
    FEDERAL_BRACKETS,
    FEDERAL_LTCG_BRACKETS,
    FEDERAL_STANDARD_DEDUCTION,
    NIIT_RATE,
    NIIT_THRESHOLD,
)
from app.models.enums import FilingStatus
from app.models.reports import TaxEstimate


class TaxEstimator:
    """Estimates federal and California tax liability."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def estimate(
        self,
        tax_year: int,
        filing_status: FilingStatus,
        w2_wages: Decimal,
        interest_income: Decimal = Decimal("0"),
        dividend_income: Decimal = Decimal("0"),
        qualified_dividends: Decimal = Decimal("0"),
        short_term_gains: Decimal = Decimal("0"),
        long_term_gains: Decimal = Decimal("0"),
        amt_iso_preference: Decimal = Decimal("0"),
        federal_withheld: Decimal = Decimal("0"),
        state_withheld: Decimal = Decimal("0"),
        federal_estimated_payments: Decimal = Decimal("0"),
        state_estimated_payments: Decimal = Decimal("0"),
        itemized_deductions: Decimal | None = None,
    ) -> TaxEstimate:
        """Compute full tax estimate.

        Capital loss netting should be done BEFORE calling this method.
        The short_term_gains and long_term_gains values passed here should
        already reflect the $3,000/$1,500 capital loss limitation.
        """
        # --- Income aggregation ---
        total_income = (
            w2_wages + interest_income + dividend_income
            + short_term_gains + long_term_gains
        )
        agi = total_income

        # --- Federal deductions ---
        std_ded = FEDERAL_STANDARD_DEDUCTION.get(tax_year, {}).get(
            filing_status, Decimal("14600")
        )
        deduction_used = max(itemized_deductions or Decimal("0"), std_ded)
        taxable_income = max(agi - deduction_used, Decimal("0"))

        # --- Split ordinary vs. preferential income ---
        preferential_income = max(
            long_term_gains + qualified_dividends, Decimal("0")
        )
        ordinary_taxable = max(taxable_income - preferential_income, Decimal("0"))

        # --- Federal ordinary income tax ---
        federal_regular = self.compute_federal_tax(
            ordinary_taxable, filing_status, tax_year
        )

        # --- Federal LTCG/qualified dividend tax ---
        federal_ltcg = self.compute_ltcg_tax(
            preferential_income, taxable_income, filing_status, tax_year
        )

        # --- NIIT ---
        investment_income = (
            interest_income + dividend_income
            + max(short_term_gains, Decimal("0"))
            + max(long_term_gains, Decimal("0"))
        )
        federal_niit = self.compute_niit(investment_income, agi, filing_status)

        # --- AMT ---
        federal_amt = self.compute_amt(
            taxable_income=taxable_income,
            preferential_income=preferential_income,
            amt_preference=amt_iso_preference,
            regular_tax=federal_regular + federal_ltcg,
            filing_status=filing_status,
            tax_year=tax_year,
        )

        # --- Federal totals ---
        federal_total = federal_regular + federal_ltcg + federal_niit + federal_amt
        federal_balance = federal_total - federal_withheld - federal_estimated_payments

        # --- California ---
        ca_std_ded = CALIFORNIA_STANDARD_DEDUCTION.get(tax_year, {}).get(
            filing_status, Decimal("5540")
        )
        ca_deduction = max(itemized_deductions or Decimal("0"), ca_std_ded)
        ca_taxable = max(agi - ca_deduction, Decimal("0"))
        ca_tax = self.compute_california_tax(ca_taxable, filing_status, tax_year)
        ca_mh = (
            max(ca_taxable - CA_MENTAL_HEALTH_THRESHOLD, Decimal("0"))
            * CA_MENTAL_HEALTH_RATE
        )
        ca_total = ca_tax + ca_mh
        ca_balance = ca_total - state_withheld - state_estimated_payments

        return TaxEstimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=w2_wages,
            interest_income=interest_income,
            dividend_income=dividend_income,
            qualified_dividends=qualified_dividends,
            short_term_gains=short_term_gains,
            long_term_gains=long_term_gains,
            total_income=total_income,
            agi=agi,
            standard_deduction=std_ded,
            itemized_deductions=itemized_deductions,
            deduction_used=deduction_used,
            taxable_income=taxable_income,
            federal_regular_tax=federal_regular,
            federal_ltcg_tax=federal_ltcg,
            federal_niit=federal_niit,
            federal_amt=federal_amt,
            federal_total_tax=federal_total,
            federal_withheld=federal_withheld,
            federal_estimated_payments=federal_estimated_payments,
            federal_balance_due=federal_balance,
            ca_taxable_income=ca_taxable,
            ca_tax=ca_tax,
            ca_mental_health_tax=ca_mh,
            ca_total_tax=ca_total,
            ca_withheld=state_withheld,
            ca_estimated_payments=state_estimated_payments,
            ca_balance_due=ca_balance,
            total_tax=federal_total + ca_total,
            total_withheld=federal_withheld + state_withheld,
            total_balance_due=federal_balance + ca_balance,
        )

    def estimate_from_db(
        self,
        repo: "TaxRepository",  # noqa: F821
        tax_year: int,
        filing_status: FilingStatus,
        federal_estimated_payments: Decimal = Decimal("0"),
        state_estimated_payments: Decimal = Decimal("0"),
        itemized_deductions: Decimal | None = None,
    ) -> TaxEstimate:
        """Load data from the repository and compute a tax estimate.

        Aggregates W-2 wages/withholdings, 1099-DIV/INT income, and
        reconciliation results (capital gains). Performs capital loss
        netting before delegating to estimate().
        """
        self.warnings = []

        # --- W-2 aggregation ---
        w2_records = repo.get_w2s(tax_year)
        if not w2_records:
            self.warnings.append("No W-2 data found. Using $0 wages.")

        w2_wages = Decimal("0")
        federal_withheld = Decimal("0")
        state_withheld = Decimal("0")
        for w2 in w2_records:
            w2_wages += Decimal(str(w2["box1_wages"]))
            federal_withheld += Decimal(str(w2["box2_federal_withheld"]))
            if w2.get("box17_state_withheld"):
                state_withheld += Decimal(str(w2["box17_state_withheld"]))

        # --- 1099-DIV aggregation ---
        div_records = repo.get_1099divs(tax_year)
        dividend_income = Decimal("0")
        qualified_dividends = Decimal("0")
        for div in div_records:
            dividend_income += Decimal(str(div["ordinary_dividends"]))
            qualified_dividends += Decimal(str(div["qualified_dividends"]))
            if div.get("federal_tax_withheld"):
                federal_withheld += Decimal(str(div["federal_tax_withheld"]))

        # --- 1099-INT aggregation ---
        int_records = repo.get_1099ints(tax_year)
        interest_income = Decimal("0")
        for intform in int_records:
            interest_income += Decimal(str(intform["interest_income"]))
            if intform.get("federal_tax_withheld"):
                federal_withheld += Decimal(str(intform["federal_tax_withheld"]))

        # --- Reconciliation results (capital gains) ---
        sale_results = repo.get_sale_results(tax_year)
        short_term_gains = Decimal("0")
        long_term_gains = Decimal("0")
        amt_iso_preference = Decimal("0")
        total_sale_ordinary_income = Decimal("0")

        if not sale_results:
            recon_runs = repo.get_reconciliation_runs(tax_year)
            if not recon_runs:
                self.warnings.append(
                    "No reconciliation run found. Capital gains set to $0. "
                    "Run `taxbot reconcile` first if you have 1099-B data."
                )

        for sr in sale_results:
            gain = Decimal(str(sr["gain_loss"]))
            holding = sr["holding_period"]
            if holding == "SHORT_TERM":
                short_term_gains += gain
            else:
                long_term_gains += gain

            oi = Decimal(str(sr.get("ordinary_income", "0")))
            total_sale_ordinary_income += oi

            amt_adj = Decimal(str(sr.get("amt_adjustment", "0")))
            amt_iso_preference += amt_adj

        if total_sale_ordinary_income > Decimal("0"):
            self.warnings.append(
                f"Equity compensation ordinary income of "
                f"${total_sale_ordinary_income:,.2f} detected in sale results. "
                f"Verify this is already included in your W-2 Box 1 wages "
                f"to avoid double-counting."
            )

        # --- Capital loss netting (IRC Section 1211(b)) ---
        loss_limit = CAPITAL_LOSS_LIMIT.get(filing_status, Decimal("3000"))
        net_capital = short_term_gains + long_term_gains

        if net_capital < Decimal("0"):
            # Net capital loss — limited deduction
            capital_loss_deduction = max(net_capital, -loss_limit)
            carryforward = net_capital - capital_loss_deduction
            if carryforward < Decimal("0"):
                self.warnings.append(
                    f"Capital loss of ${abs(net_capital):,.2f} exceeds the "
                    f"${loss_limit:,.2f} annual limit. "
                    f"${abs(carryforward):,.2f} carries forward to next year."
                )
            # Distribute the limited loss back to ST/LT for reporting
            # All gains/losses net together; report the limited amount
            if short_term_gains < Decimal("0") and long_term_gains >= Decimal("0"):
                # ST loss absorbs LT gain first, then limited
                net_st = short_term_gains + long_term_gains
                if net_st < Decimal("0"):
                    short_term_gains = max(net_st, -loss_limit)
                    long_term_gains = Decimal("0")
                # else: ST loss partially offsets LT gain, both sides positive-ish
            elif long_term_gains < Decimal("0") and short_term_gains >= Decimal("0"):
                net_lt = long_term_gains + short_term_gains
                if net_lt < Decimal("0"):
                    long_term_gains = max(net_lt, -loss_limit)
                    short_term_gains = Decimal("0")
            else:
                # Both negative
                short_term_gains = max(short_term_gains, -loss_limit)
                remaining_limit = loss_limit + short_term_gains  # how much limit left
                if remaining_limit > Decimal("0"):
                    long_term_gains = max(long_term_gains, -remaining_limit)
                else:
                    long_term_gains = Decimal("0")

        return self.estimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=w2_wages,
            interest_income=interest_income,
            dividend_income=dividend_income,
            qualified_dividends=qualified_dividends,
            short_term_gains=short_term_gains,
            long_term_gains=long_term_gains,
            amt_iso_preference=amt_iso_preference,
            federal_withheld=federal_withheld,
            state_withheld=state_withheld,
            federal_estimated_payments=federal_estimated_payments,
            state_estimated_payments=state_estimated_payments,
            itemized_deductions=itemized_deductions,
        )

    # ------------------------------------------------------------------
    # Tax computation methods
    # ------------------------------------------------------------------

    def compute_federal_tax(
        self, taxable_income: Decimal, filing_status: FilingStatus, tax_year: int
    ) -> Decimal:
        """Compute federal ordinary income tax using progressive brackets."""
        brackets = FEDERAL_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            raise ValueError(f"No federal brackets for {tax_year}/{filing_status}")
        return self._apply_brackets(taxable_income, brackets)

    def compute_ltcg_tax(
        self,
        ltcg_and_qualified_divs: Decimal,
        taxable_income: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> Decimal:
        """Compute federal tax on LTCG and qualified dividends.

        Uses the stacking method from the Qualified Dividends and
        Capital Gain Tax Worksheet (Form 1040 Instructions).

        The preferential income sits on top of ordinary income in the
        bracket structure. The portion that falls in each LTCG bracket
        is taxed at that bracket's rate.
        """
        if ltcg_and_qualified_divs <= Decimal("0"):
            return Decimal("0")

        brackets = FEDERAL_LTCG_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            return ltcg_and_qualified_divs * Decimal("0.15")

        # Ordinary income fills the bottom of the brackets first
        ordinary_income_top = max(
            taxable_income - ltcg_and_qualified_divs, Decimal("0")
        )

        tax = Decimal("0")
        remaining_pref = ltcg_and_qualified_divs
        prev_bound = Decimal("0")

        for upper_bound, rate in brackets:
            if remaining_pref <= Decimal("0"):
                break

            if upper_bound is None:
                # Top bracket — all remaining preferential income
                tax += remaining_pref * rate
                remaining_pref = Decimal("0")
            else:
                # Bracket space available above ordinary income
                bracket_start = max(prev_bound, ordinary_income_top)
                if bracket_start >= upper_bound:
                    prev_bound = upper_bound
                    continue
                bracket_space = upper_bound - bracket_start
                taxed_here = min(remaining_pref, bracket_space)
                tax += taxed_here * rate
                remaining_pref -= taxed_here
                prev_bound = upper_bound

        return tax

    def compute_niit(
        self, investment_income: Decimal, agi: Decimal, filing_status: FilingStatus
    ) -> Decimal:
        """Compute Net Investment Income Tax (3.8%) per IRC Section 1411."""
        threshold = NIIT_THRESHOLD.get(filing_status, Decimal("200000"))
        excess_agi = max(agi - threshold, Decimal("0"))
        niit_base = min(max(investment_income, Decimal("0")), excess_agi)
        return niit_base * NIIT_RATE

    def compute_amt(
        self,
        taxable_income: Decimal,
        preferential_income: Decimal,
        amt_preference: Decimal,
        regular_tax: Decimal,
        filing_status: FilingStatus,
        tax_year: int,
    ) -> Decimal:
        """Compute Alternative Minimum Tax per Form 6251.

        Args:
            taxable_income: Regular taxable income (Form 1040, Line 15).
            preferential_income: Qualified dividends + net LTCG.
            amt_preference: Net AMT preference items (ISO exercises).
            regular_tax: Regular federal tax (ordinary + LTCG rates).
            filing_status: Filing status.
            tax_year: Tax year.

        Returns:
            Federal AMT amount (zero if no AMT owed).
        """
        if amt_preference == Decimal("0"):
            return Decimal("0")

        # Step 1: AMTI
        amti = taxable_income + amt_preference

        # Step 2: Exemption with phase-out
        exemption_data = AMT_EXEMPTION.get(tax_year, {})
        phaseout_data = AMT_PHASEOUT_START.get(tax_year, {})
        if not exemption_data or filing_status not in exemption_data:
            self.warnings.append(
                f"No AMT exemption data for {tax_year}/{filing_status}. "
                f"AMT computation skipped."
            )
            return Decimal("0")

        exemption_amount = exemption_data[filing_status]
        phaseout_start = phaseout_data[filing_status]
        exemption_reduction = (
            max(amti - phaseout_start, Decimal("0")) * Decimal("0.25")
        )
        amt_exemption = max(exemption_amount - exemption_reduction, Decimal("0"))

        # Step 3: AMT base
        amt_base = max(amti - amt_exemption, Decimal("0"))

        if amt_base == Decimal("0"):
            return Decimal("0")

        # Step 4: Compute tentative minimum tax
        # Preferential income still gets LTCG rates under AMT
        amt_ordinary_base = max(amt_base - preferential_income, Decimal("0"))
        breakpoint = AMT_28_PERCENT_THRESHOLD.get(tax_year, Decimal("232600"))

        if amt_ordinary_base <= breakpoint:
            amt_on_ordinary = amt_ordinary_base * Decimal("0.26")
        else:
            amt_on_ordinary = (
                breakpoint * Decimal("0.26")
                + (amt_ordinary_base - breakpoint) * Decimal("0.28")
            )

        amt_on_preferential = self.compute_ltcg_tax(
            preferential_income, amt_base, filing_status, tax_year
        )

        tentative_minimum_tax = amt_on_ordinary + amt_on_preferential

        # Step 5: AMT = excess over regular tax
        amt = max(tentative_minimum_tax - regular_tax, Decimal("0"))
        return amt

    def compute_california_tax(
        self, taxable_income: Decimal, filing_status: FilingStatus, tax_year: int
    ) -> Decimal:
        """Compute California state income tax. All income taxed at ordinary rates."""
        brackets = CALIFORNIA_BRACKETS.get(tax_year, {}).get(filing_status)
        if not brackets:
            raise ValueError(f"No CA brackets for {tax_year}/{filing_status}")
        return self._apply_brackets(taxable_income, brackets)

    @staticmethod
    def _apply_brackets(
        income: Decimal, brackets: list[tuple[Decimal | None, Decimal]]
    ) -> Decimal:
        """Apply progressive tax brackets to income."""
        tax = Decimal("0")
        prev_bound = Decimal("0")

        for upper_bound, rate in brackets:
            if upper_bound is None:
                taxable_in_bracket = max(income - prev_bound, Decimal("0"))
            else:
                taxable_in_bracket = max(
                    min(income, upper_bound) - prev_bound, Decimal("0")
                )
            tax += taxable_in_bracket * rate
            prev_bound = upper_bound if upper_bound is not None else income
            if upper_bound is not None and income <= upper_bound:
                break

        return tax
