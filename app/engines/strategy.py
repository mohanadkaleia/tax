"""Tax strategy analysis and recommendation engine.

Analyzes a taxpayer's current position and generates actionable, quantified
recommendations to reduce current-year and future tax liability. Each strategy
runs a "what-if" scenario through the TaxEstimator and computes the delta.

Strategies implemented:
  A.1 Tax-Loss Harvesting         A.2 Retirement Contributions
  A.3 HSA Maximization            A.4 Charitable Bunching
  A.5 SALT Analysis               B.1 ESPP Holding Period
  B.2 ISO Exercise Timing         B.3 RSU Harvesting Coordination
  B.4 NSO Exercise Timing         C.1 Holding Period Analysis
  C.3 Wash Sale Detection         C.4 NIIT Analysis
  D.1 Income Shifting             D.3 Loss Carryforward
  D.4 Estimated Payments

IRS/CA authorities cited per strategy in the plan: plans/tax-strategy.md
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.engines.brackets import (
    AMT_28_PERCENT_THRESHOLD,
    AMT_EXEMPTION,
    AMT_PHASEOUT_START,
    CAPITAL_LOSS_LIMIT,
    FEDERAL_STANDARD_DEDUCTION,
    NIIT_THRESHOLD,
)
from app.engines.estimator import TaxEstimator
from app.models.enums import FilingStatus
from app.models.reports import TaxEstimate

# ---------------------------------------------------------------------------
# Strategy-specific enums
# ---------------------------------------------------------------------------

class StrategyCategory(StrEnum):
    CURRENT_YEAR = "CURRENT_YEAR"
    EQUITY_COMPENSATION = "EQUITY_COMPENSATION"
    CAPITAL_GAINS = "CAPITAL_GAINS"
    MULTI_YEAR = "MULTI_YEAR"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class StrategyRecommendation(BaseModel):
    """A single tax strategy recommendation."""

    name: str
    category: StrategyCategory
    priority: Priority
    situation: str
    mechanism: str
    quantified_impact: str
    estimated_savings: Decimal
    action_steps: list[str]
    deadline: date | None = None
    risk_level: RiskLevel
    california_impact: str | None = None
    irs_authority: str | None = None
    warnings: list[str] = []
    interactions: list[str] = []


class UserInputs(BaseModel):
    """User-provided inputs that cannot be derived from the database."""

    age: int | None = None
    has_hdhp: bool = False
    hsa_coverage: str | None = None  # "self" or "family"
    current_hsa_contributions: Decimal = Decimal("0")
    annual_charitable_giving: Decimal = Decimal("0")
    property_tax: Decimal = Decimal("0")
    mortgage_interest: Decimal = Decimal("0")
    other_itemized_deductions: Decimal = Decimal("0")
    prior_year_federal_tax: Decimal | None = None
    prior_year_state_tax: Decimal | None = None
    amt_credit_carryforward: Decimal = Decimal("0")
    capital_loss_carryforward: Decimal = Decimal("0")
    projected_income_next_year: Decimal | None = None
    future_vest_dates: list[dict] | None = None
    current_market_prices: dict[str, Decimal] = {}
    planned_sales: dict[str, Decimal] = {}
    # ISO/NSO grant data — each dict has: ticker, shares, strike_price, grant_date, expiration_date
    unexercised_iso_grants: list[dict] | None = None
    unexercised_nso_grants: list[dict] | None = None


class StrategyReport(BaseModel):
    """Complete strategy analysis output."""

    tax_year: int
    filing_status: FilingStatus
    baseline_estimate: TaxEstimate
    recommendations: list[StrategyRecommendation]
    total_potential_savings: Decimal
    generated_at: str
    warnings: list[str] = []
    data_completeness: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Contribution limits per IRS Notice 2023-75 (2024) / 2024-80 (2025)
# ---------------------------------------------------------------------------

_RETIREMENT_LIMITS: dict[int, dict[str, Decimal]] = {
    2024: {
        "401k_under50": Decimal("23000"),
        "401k_50plus": Decimal("30500"),
        "ira": Decimal("7000"),
        "ira_50plus": Decimal("8000"),
        "hsa_self": Decimal("4150"),
        "hsa_family": Decimal("8300"),
        "hsa_catchup_55": Decimal("1000"),
    },
    2025: {
        "401k_under50": Decimal("23500"),
        "401k_50plus": Decimal("31000"),
        "401k_60_63": Decimal("34750"),
        "ira": Decimal("7000"),
        "ira_50plus": Decimal("8000"),
        "hsa_self": Decimal("4300"),
        "hsa_family": Decimal("8550"),
        "hsa_catchup_55": Decimal("1000"),
    },
}

_SALT_CAP = Decimal("10000")
_SALT_CAP_MFS = Decimal("5000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_years(d: date, years: int) -> date:
    """Add *years* to a date, handling Feb 29 → Feb 28."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _net_capital_losses(
    st_gains: Decimal,
    lt_gains: Decimal,
    filing_status: FilingStatus,
) -> tuple[Decimal, Decimal]:
    """Apply capital loss netting per IRC Section 1211(b).

    Returns (netted_st, netted_lt) ready for the estimator.
    """
    net = st_gains + lt_gains
    if net >= Decimal("0"):
        return st_gains, lt_gains

    limit = CAPITAL_LOSS_LIMIT.get(filing_status, Decimal("3000"))

    if st_gains < Decimal("0") and lt_gains >= Decimal("0"):
        combined = st_gains + lt_gains
        if combined < Decimal("0"):
            return max(combined, -limit), Decimal("0")
        return st_gains, lt_gains
    elif lt_gains < Decimal("0") and st_gains >= Decimal("0"):
        combined = lt_gains + st_gains
        if combined < Decimal("0"):
            return Decimal("0"), max(combined, -limit)
        return st_gains, lt_gains
    else:
        # Both negative
        limited_st = max(st_gains, -limit)
        remaining_limit = limit + limited_st
        limited_lt = max(lt_gains, -remaining_limit) if remaining_limit > Decimal("0") else Decimal("0")
        return limited_st, limited_lt


def _parse_box12(w2: dict) -> dict[str, Decimal]:
    """Extract W-2 Box 12 codes as {code: amount}."""
    import json as _json

    raw = w2.get("box12_codes") or {}
    if isinstance(raw, str):
        raw = _json.loads(raw)
    return {k: Decimal(str(v)) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Strategy Engine
# ---------------------------------------------------------------------------

class StrategyEngine:
    """Analyzes tax situation and produces strategy recommendations."""

    def __init__(self) -> None:
        self.estimator = TaxEstimator()
        self.warnings: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        repo: "TaxRepository",  # noqa: F821
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs | None = None,
    ) -> StrategyReport:
        """Run all strategy analyses and return prioritized recommendations."""
        if user_inputs is None:
            user_inputs = UserInputs()
        self.warnings = []

        # Baseline tax estimate
        baseline = self.estimator.estimate_from_db(
            repo=repo,
            tax_year=tax_year,
            filing_status=filing_status,
        )

        # Load data from repository
        lots = repo.get_lots()
        sale_results = repo.get_sale_results(tax_year)
        events = repo.get_events()
        w2s = repo.get_w2s(tax_year)
        sales = repo.get_sales(tax_year)

        # Run each strategy analyzer
        recommendations: list[StrategyRecommendation] = []

        for analyzer in [
            lambda: self._analyze_retirement_contributions(
                baseline, w2s, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_hsa(
                baseline, w2s, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_salt(baseline, filing_status, user_inputs),
            lambda: self._analyze_niit(baseline, filing_status),
            lambda: self._analyze_holding_periods(
                lots, tax_year, filing_status, baseline, user_inputs,
            ),
            lambda: self._analyze_wash_sale_risk(
                lots, sales, events, sale_results,
            ),
            lambda: self._analyze_tax_loss_harvesting(
                baseline, lots, sale_results, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_espp_holding(
                baseline, lots, events, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_estimated_payments(
                baseline, tax_year, user_inputs,
            ),
            lambda: self._analyze_charitable_bunching(
                baseline, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_loss_carryforward(
                sale_results, filing_status, user_inputs,
            ),
            lambda: self._analyze_income_shifting(
                baseline, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_iso_exercise(
                baseline, lots, events, tax_year, filing_status, user_inputs,
            ),
            lambda: self._analyze_rsu_harvesting(
                baseline, lots, events, sale_results, tax_year, filing_status,
                user_inputs,
            ),
            lambda: self._analyze_nso_timing(
                baseline, tax_year, filing_status, user_inputs,
            ),
        ]:
            try:
                recommendations.extend(analyzer())
            except Exception as exc:
                self.warnings.append(f"Strategy analysis error: {exc}")

        # Sort by priority then savings descending
        _priority_order = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 1,
            Priority.MEDIUM: 2,
            Priority.LOW: 3,
        }
        recommendations.sort(
            key=lambda r: (_priority_order[r.priority], -r.estimated_savings),
        )

        total_savings = sum(r.estimated_savings for r in recommendations)

        return StrategyReport(
            tax_year=tax_year,
            filing_status=filing_status,
            baseline_estimate=baseline,
            recommendations=recommendations,
            total_potential_savings=total_savings,
            generated_at=datetime.now().isoformat(),
            warnings=self.warnings,
            data_completeness=self._check_data_completeness(
                w2s, lots, sale_results, events, user_inputs,
            ),
        )

    # ------------------------------------------------------------------
    # A.1 Tax-Loss Harvesting
    # ------------------------------------------------------------------

    def _analyze_tax_loss_harvesting(
        self,
        baseline: TaxEstimate,
        lots: list[dict],
        sale_results: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        prices = user_inputs.current_market_prices
        if not prices:
            return []

        recommendations: list[StrategyRecommendation] = []
        today = date.today()

        for lot in lots:
            shares_remaining = Decimal(str(lot.get("shares_remaining", "0")))
            if shares_remaining <= Decimal("0"):
                continue

            ticker = lot.get("ticker", "")
            if ticker not in prices:
                continue

            current_price = prices[ticker]
            cost_per_share = Decimal(str(lot["cost_per_share"]))
            unrealized = (current_price - cost_per_share) * shares_remaining

            if unrealized >= Decimal("0"):
                continue

            acq_date_str = lot.get("acquisition_date", "")
            if not acq_date_str:
                continue
            acq_date = date.fromisoformat(acq_date_str)
            is_long_term = (today - acq_date).days > 365

            # What-if: realize this loss
            if is_long_term:
                mod_st, mod_lt = _net_capital_losses(
                    baseline.short_term_gains,
                    baseline.long_term_gains + unrealized,
                    filing_status,
                )
            else:
                mod_st, mod_lt = _net_capital_losses(
                    baseline.short_term_gains + unrealized,
                    baseline.long_term_gains,
                    filing_status,
                )

            modified = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=mod_st,
                long_term_gains=mod_lt,
            )
            savings = baseline.total_tax - modified.total_tax

            if savings > Decimal("0"):
                holding_label = "long-term" if is_long_term else "short-term"
                recommendations.append(StrategyRecommendation(
                    name=f"Tax-Loss Harvest: {ticker}",
                    category=StrategyCategory.CURRENT_YEAR,
                    priority=Priority.HIGH if savings > Decimal("1000") else Priority.MEDIUM,
                    situation=(
                        f"{shares_remaining:,.0f} shares of {ticker} with "
                        f"${abs(unrealized):,.2f} unrealized {holding_label} loss."
                    ),
                    mechanism=(
                        f"Sell to realize the {holding_label} loss, "
                        "offsetting realized capital gains."
                    ),
                    quantified_impact=f"Estimated tax savings: ${savings:,.2f}",
                    estimated_savings=savings,
                    action_steps=[
                        f"Sell {shares_remaining:,.0f} shares of {ticker} before Dec 31, {tax_year}",
                        "Wait 31 days before repurchasing to avoid wash sale",
                        "Consider buying a correlated but not identical ETF during the waiting period",
                    ],
                    deadline=date(tax_year, 12, 31),
                    risk_level=RiskLevel.LOW,
                    california_impact=(
                        "CA treats capital gains as ordinary income. "
                        "Loss harvesting reduces CA tax as well."
                    ),
                    irs_authority="IRC Section 1211(b), 1212(b), 1091; Pub 550",
                    interactions=["Wash Sale Warning", "NIIT Impact"],
                    warnings=["Check for upcoming RSU vests that could trigger wash sale"],
                ))

        return recommendations

    # ------------------------------------------------------------------
    # A.2 Retirement Contributions
    # ------------------------------------------------------------------

    def _analyze_retirement_contributions(
        self,
        baseline: TaxEstimate,
        w2s: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        limits = _RETIREMENT_LIMITS.get(tax_year)
        if not limits:
            return recommendations

        # Current 401k contributions from W-2 Box 12
        current_401k = Decimal("0")
        current_roth_401k = Decimal("0")
        for w2 in w2s:
            box12 = _parse_box12(w2)
            current_401k += box12.get("D", Decimal("0"))
            current_roth_401k += box12.get("AA", Decimal("0"))

        # 401k limit based on age
        age = user_inputs.age
        if age is not None and tax_year >= 2025 and 60 <= age <= 63:
            limit_401k = limits.get("401k_60_63", limits["401k_under50"])
        elif age is not None and age >= 50:
            limit_401k = limits["401k_50plus"]
        else:
            limit_401k = limits["401k_under50"]

        total_401k = current_401k + current_roth_401k
        remaining_401k = max(limit_401k - total_401k, Decimal("0"))

        if remaining_401k > Decimal("0"):
            reduced_wages = baseline.w2_wages - remaining_401k
            modified = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=max(reduced_wages, Decimal("0")),
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains,
                long_term_gains=baseline.long_term_gains,
            )
            savings = baseline.total_tax - modified.total_tax

            recommendations.append(StrategyRecommendation(
                name="Maximize 401(k) Contributions",
                category=StrategyCategory.CURRENT_YEAR,
                priority=Priority.HIGH if savings > Decimal("1000") else Priority.MEDIUM,
                situation=(
                    f"Current 401(k) contributions: ${total_401k:,.0f}. "
                    f"Limit: ${limit_401k:,.0f}. Room: ${remaining_401k:,.0f}."
                ),
                mechanism=(
                    "Pre-tax 401(k) contributions reduce taxable income "
                    "dollar-for-dollar at your marginal rate."
                ),
                quantified_impact=f"Estimated tax savings: ${savings:,.2f}",
                estimated_savings=savings,
                action_steps=[
                    f"Increase 401(k) contribution rate to reach ${limit_401k:,.0f} by year-end",
                    "Contact HR/payroll to adjust contribution percentage",
                ],
                deadline=date(tax_year, 12, 31),
                risk_level=RiskLevel.LOW,
                california_impact="CA follows federal 401(k) treatment. Full state tax reduction.",
                irs_authority="IRC Section 401(k); IRS Notice 2023-75",
                interactions=["NIIT Threshold Management", "Estimated Tax Payments"],
            ))

        # Backdoor Roth IRA for high-income earners
        if baseline.agi > Decimal("161000"):
            ira_limit = limits.get("ira_50plus", limits["ira"]) if (age and age >= 50) else limits["ira"]
            recommendations.append(StrategyRecommendation(
                name="Backdoor Roth IRA",
                category=StrategyCategory.CURRENT_YEAR,
                priority=Priority.LOW,
                situation=(
                    f"AGI of ${baseline.agi:,.0f} exceeds Roth IRA income limits. "
                    "Direct Roth contributions are not allowed."
                ),
                mechanism=(
                    "Contribute to non-deductible Traditional IRA, then convert to Roth. "
                    "No current tax deduction, but future growth is tax-free."
                ),
                quantified_impact=f"No current-year savings. Tax-free growth on ${ira_limit:,.0f}.",
                estimated_savings=Decimal("0"),
                action_steps=[
                    "Verify no existing Traditional IRA balance (pro-rata rule applies)",
                    f"Contribute ${ira_limit:,.0f} to Traditional IRA (non-deductible)",
                    "Convert to Roth IRA immediately",
                    "File Form 8606 to report non-deductible contribution",
                ],
                deadline=date(tax_year + 1, 4, 15),
                risk_level=RiskLevel.LOW,
                california_impact="CA conforms to federal Roth IRA treatment.",
                irs_authority="IRC Section 408A(c)(3)(B); Pub 590-A",
            ))

        return recommendations

    # ------------------------------------------------------------------
    # A.3 HSA Maximization
    # ------------------------------------------------------------------

    def _analyze_hsa(
        self,
        baseline: TaxEstimate,
        w2s: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        if not user_inputs.has_hdhp:
            return []

        limits = _RETIREMENT_LIMITS.get(tax_year)
        if not limits:
            return []

        current_hsa = user_inputs.current_hsa_contributions
        for w2 in w2s:
            box12 = _parse_box12(w2)
            current_hsa += box12.get("W", Decimal("0"))

        coverage = user_inputs.hsa_coverage or "self"
        limit_key = "hsa_family" if coverage == "family" else "hsa_self"
        hsa_limit = limits[limit_key]
        if user_inputs.age and user_inputs.age >= 55:
            hsa_limit += limits.get("hsa_catchup_55", Decimal("1000"))

        remaining = max(hsa_limit - current_hsa, Decimal("0"))
        if remaining <= Decimal("0"):
            return []

        # HSA saves federal tax but NOT CA tax
        modified = self.estimator.estimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=max(baseline.w2_wages - remaining, Decimal("0")),
            interest_income=baseline.interest_income,
            dividend_income=baseline.dividend_income,
            qualified_dividends=baseline.qualified_dividends,
            short_term_gains=baseline.short_term_gains,
            long_term_gains=baseline.long_term_gains,
        )
        federal_savings = baseline.federal_total_tax - modified.federal_total_tax

        return [StrategyRecommendation(
            name="Maximize HSA Contributions",
            category=StrategyCategory.CURRENT_YEAR,
            priority=Priority.MEDIUM if federal_savings > Decimal("500") else Priority.LOW,
            situation=(
                f"Current HSA contributions: ${current_hsa:,.0f}. "
                f"Limit: ${hsa_limit:,.0f}. Room: ${remaining:,.0f}."
            ),
            mechanism=(
                "HSA contributions are triple-tax-advantaged: deductible, "
                "grow tax-free, and withdrawals for medical expenses are tax-free."
            ),
            quantified_impact=f"Estimated federal tax savings: ${federal_savings:,.2f}",
            estimated_savings=federal_savings,
            action_steps=[
                f"Contribute additional ${remaining:,.0f} to HSA",
                "Payroll contributions also save FICA (7.65%); direct contributions do not",
            ],
            deadline=date(tax_year + 1, 4, 15),
            risk_level=RiskLevel.LOW,
            california_impact=(
                "CALIFORNIA DOES NOT CONFORM to federal HSA treatment. "
                "CA taxes HSA contributions and earnings annually. "
                "Per CA R&TC Section 17215. Federal savings still apply."
            ),
            irs_authority="IRC Section 223; Pub 969",
            warnings=["CA does not recognize HSA tax benefits"],
        )]

    # ------------------------------------------------------------------
    # A.4 Charitable Bunching
    # ------------------------------------------------------------------

    def _analyze_charitable_bunching(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        charitable = user_inputs.annual_charitable_giving
        if charitable <= Decimal("0"):
            return []

        salt_cap = _SALT_CAP_MFS if filing_status == FilingStatus.MFS else _SALT_CAP
        salt_used = min(
            baseline.ca_total_tax + user_inputs.property_tax, salt_cap,
        )
        mortgage = user_inputs.mortgage_interest
        other_itemized = user_inputs.other_itemized_deductions

        total_itemized = salt_used + mortgage + charitable + other_itemized
        std_ded = FEDERAL_STANDARD_DEDUCTION.get(tax_year, {}).get(
            filing_status, Decimal("14600"),
        )

        if total_itemized > std_ded:
            return []  # Already itemizing

        bunched_charitable = charitable * 3
        bunched_itemized = salt_used + mortgage + bunched_charitable + other_itemized

        if bunched_itemized <= std_ded:
            return []

        modified = self.estimator.estimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=baseline.w2_wages,
            interest_income=baseline.interest_income,
            dividend_income=baseline.dividend_income,
            qualified_dividends=baseline.qualified_dividends,
            short_term_gains=baseline.short_term_gains,
            long_term_gains=baseline.long_term_gains,
            itemized_deductions=bunched_itemized,
        )
        savings = baseline.total_tax - modified.total_tax

        if savings <= Decimal("0"):
            return []

        excess = bunched_itemized - std_ded
        return [StrategyRecommendation(
            name="Charitable Giving Bunching Strategy",
            category=StrategyCategory.CURRENT_YEAR,
            priority=Priority.MEDIUM if savings > Decimal("500") else Priority.LOW,
            situation=(
                f"Current itemized deductions (${total_itemized:,.0f}) are below "
                f"the standard deduction (${std_ded:,.0f}). "
                f"Annual charitable giving: ${charitable:,.0f}."
            ),
            mechanism=(
                "Donate 2-3 years of planned gifts in one year via a donor-advised fund. "
                "Itemize this year, take standard deduction in off years."
            ),
            quantified_impact=(
                f"Bunched itemized: ${bunched_itemized:,.0f}. "
                f"Excess over standard deduction: ${excess:,.0f}. "
                f"Estimated tax savings: ${savings:,.2f}."
            ),
            estimated_savings=savings,
            action_steps=[
                "Open a donor-advised fund (DAF) at Fidelity, Schwab, or Vanguard",
                f"Contribute ${bunched_charitable:,.0f} to DAF before Dec 31",
                "Distribute grants from DAF over the next 2-3 years",
                "Take standard deduction in off years",
            ],
            deadline=date(tax_year, 12, 31),
            risk_level=RiskLevel.LOW,
            california_impact=(
                "CA conforms to federal charitable deduction rules. "
                "CA standard deduction is much lower so bunching may "
                "already push you to itemize for CA."
            ),
            irs_authority="IRC Section 170, 4966; Pub 526",
            interactions=["SALT Cap Analysis"],
        )]

    # ------------------------------------------------------------------
    # A.5 SALT Analysis
    # ------------------------------------------------------------------

    def _analyze_salt(
        self,
        baseline: TaxEstimate,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        ca_income_tax = baseline.ca_total_tax
        property_tax = user_inputs.property_tax
        total_salt = ca_income_tax + property_tax

        salt_cap = _SALT_CAP_MFS if filing_status == FilingStatus.MFS else _SALT_CAP
        wasted_salt = max(total_salt - salt_cap, Decimal("0"))

        if wasted_salt <= Decimal("0"):
            return []

        return [StrategyRecommendation(
            name="SALT Cap Analysis",
            category=StrategyCategory.CURRENT_YEAR,
            priority=Priority.LOW,
            situation=(
                f"Total state/local taxes: ${total_salt:,.0f}. "
                f"SALT cap: ${salt_cap:,.0f}. "
                f"Excess not deductible: ${wasted_salt:,.0f}."
            ),
            mechanism=(
                "The $10,000 SALT cap limits your state tax deduction. "
                "This affects whether itemizing is beneficial."
            ),
            quantified_impact=(
                f"${wasted_salt:,.0f} in state/local taxes provides no federal tax benefit."
            ),
            estimated_savings=Decimal("0"),
            action_steps=[
                "This is informational -- the SALT cap cannot be avoided for W-2 employees",
                "Consider this when evaluating charitable bunching and other itemized deductions",
            ],
            risk_level=RiskLevel.LOW,
            california_impact=(
                "CA does not have a SALT cap. "
                "Full CA tax is deductible on CA return if itemizing."
            ),
            irs_authority="IRC Section 164(b)(6)",
        )]

    # ------------------------------------------------------------------
    # B.1 ESPP Holding Period
    # ------------------------------------------------------------------

    def _analyze_espp_holding(
        self,
        baseline: TaxEstimate,
        lots: list[dict],
        events: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        prices = user_inputs.current_market_prices
        today = date.today()

        espp_lots = [
            lot for lot in lots
            if lot.get("equity_type") == "ESPP"
            and Decimal(str(lot.get("shares_remaining", "0"))) > Decimal("0")
        ]
        if not espp_lots:
            return recommendations

        for lot in espp_lots:
            ticker = lot.get("ticker", "")
            if ticker not in prices:
                continue

            current_price = prices[ticker]
            shares_remaining = Decimal(str(lot["shares_remaining"]))
            acq_date = date.fromisoformat(lot["acquisition_date"])

            # Find matching event for offering date info
            source_event_id = lot.get("source_event_id", "")
            ev = next((e for e in events if e.get("id") == source_event_id), None)
            if not ev:
                continue

            offering_date_str = ev.get("offering_date")
            purchase_price_str = ev.get("purchase_price")
            fmv_offering_str = ev.get("fmv_on_offering_date")
            if not offering_date_str or not purchase_price_str:
                continue

            offering_date = date.fromisoformat(offering_date_str)
            purchase_price = Decimal(str(purchase_price_str))
            fmv_on_offering = Decimal(str(fmv_offering_str)) if fmv_offering_str else None
            fmv_on_purchase = Decimal(str(ev.get("price_per_share", "0")))

            qualifying_date = max(
                _add_years(offering_date, 2),
                _add_years(acq_date, 1),
            )
            days_to_qualifying = (qualifying_date - today).days

            if days_to_qualifying <= 0 or not fmv_on_offering:
                continue  # Already qualifies or missing data

            # Disqualifying (sell now)
            disq_ordinary = (fmv_on_purchase - purchase_price) * shares_remaining
            disq_st_gain = (current_price - fmv_on_purchase) * shares_remaining

            # Qualifying (hold)
            actual_gain_per_share = current_price - purchase_price
            discount_at_offering = fmv_on_offering - purchase_price
            qual_ordinary_per_share = max(
                min(actual_gain_per_share, discount_at_offering), Decimal("0"),
            )
            qual_ordinary = qual_ordinary_per_share * shares_remaining
            total_gain = (current_price - purchase_price) * shares_remaining
            qual_ltcg = total_gain - qual_ordinary

            # What-if comparison
            disq_estimate = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages + disq_ordinary,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains + disq_st_gain,
                long_term_gains=baseline.long_term_gains,
            )
            qual_estimate = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages + qual_ordinary,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains,
                long_term_gains=baseline.long_term_gains + qual_ltcg,
            )
            savings = disq_estimate.total_tax - qual_estimate.total_tax

            if savings > Decimal("0"):
                recommendations.append(StrategyRecommendation(
                    name=f"ESPP Holding Period: {ticker}",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=(
                        Priority.MEDIUM if savings > Decimal("500") else Priority.LOW
                    ),
                    situation=(
                        f"ESPP shares purchased {acq_date}. "
                        f"Qualifying date: {qualifying_date}. "
                        f"Days remaining: {days_to_qualifying}."
                    ),
                    mechanism=(
                        "Holding for qualifying disposition converts "
                        "ordinary income to LTCG, reducing the tax rate."
                    ),
                    quantified_impact=f"Estimated tax savings from holding: ${savings:,.2f}.",
                    estimated_savings=savings,
                    action_steps=[
                        f"Hold until {qualifying_date}",
                        f"Disqualifying: ordinary ${disq_ordinary:,.2f} + ST gain ${disq_st_gain:,.2f}",
                        f"Qualifying: ordinary ${qual_ordinary:,.2f} + LTCG ${qual_ltcg:,.2f}",
                    ],
                    deadline=qualifying_date,
                    risk_level=(
                        RiskLevel.MODERATE if days_to_qualifying > 180 else RiskLevel.LOW
                    ),
                    california_impact=(
                        "CA taxes all gains at ordinary rates. "
                        "Holding benefit is primarily federal."
                    ),
                    irs_authority="IRC Section 423, 422(a)(1); Pub 525",
                ))

        return recommendations

    # ------------------------------------------------------------------
    # C.1 Holding Period Analysis
    # ------------------------------------------------------------------

    def _analyze_holding_periods(
        self,
        lots: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        baseline: TaxEstimate,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        today = date.today()
        prices = user_inputs.current_market_prices

        if not prices:
            self.warnings.append(
                "Market prices required for holding period analysis. "
                "Use --prices to provide."
            )
            return recommendations

        for lot in lots:
            shares_remaining = Decimal(str(lot.get("shares_remaining", "0")))
            if shares_remaining <= Decimal("0"):
                continue

            ticker = lot.get("ticker", "")
            if ticker not in prices:
                continue

            current_price = prices[ticker]
            cost_per_share = Decimal(str(lot["cost_per_share"]))
            unrealized_gain = (current_price - cost_per_share) * shares_remaining

            if unrealized_gain <= Decimal("0"):
                continue

            acq_date_str = lot.get("acquisition_date", "")
            if not acq_date_str:
                continue
            acq_date = date.fromisoformat(acq_date_str)
            one_year_date = _add_years(acq_date, 1)
            days_to_ltcg = (one_year_date - today).days

            if days_to_ltcg <= 0 or days_to_ltcg > 90:
                continue

            # What-if: sell now (ST) vs sell later (LT)
            st_estimate = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains + unrealized_gain,
                long_term_gains=baseline.long_term_gains,
            )
            lt_estimate = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains,
                long_term_gains=baseline.long_term_gains + unrealized_gain,
            )
            savings = st_estimate.total_tax - lt_estimate.total_tax

            if savings > Decimal("0"):
                recommendations.append(StrategyRecommendation(
                    name=f"Hold {ticker} for Long-Term Treatment",
                    category=StrategyCategory.CAPITAL_GAINS,
                    priority=(
                        Priority.HIGH if savings > Decimal("1000") else Priority.MEDIUM
                    ),
                    situation=(
                        f"{shares_remaining:,.0f} shares acquired {acq_date}. "
                        f"LTCG date: {one_year_date}. Days remaining: {days_to_ltcg}."
                    ),
                    mechanism=(
                        "Holding past 1 year converts short-term gain to long-term, "
                        "reducing the federal tax rate."
                    ),
                    quantified_impact=(
                        f"Unrealized gain: ${unrealized_gain:,.2f}. "
                        f"Tax savings: ${savings:,.2f}."
                    ),
                    estimated_savings=savings,
                    action_steps=[
                        f"Hold until {one_year_date} (do NOT sell before this date)",
                        "Set a calendar reminder for the LTCG date",
                    ],
                    deadline=one_year_date,
                    risk_level=(
                        RiskLevel.LOW if days_to_ltcg <= 30 else RiskLevel.MODERATE
                    ),
                    california_impact=(
                        "CA taxes all gains at ordinary rates. "
                        "Holding benefit is FEDERAL ONLY."
                    ),
                    irs_authority="IRC Section 1222; Section 1(h)",
                ))

        return recommendations

    # ------------------------------------------------------------------
    # C.3 Wash Sale Detection
    # ------------------------------------------------------------------

    def _analyze_wash_sale_risk(
        self,
        lots: list[dict],
        sales: list[dict],
        events: list[dict],
        sale_results: list[dict],
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []

        for sr in sale_results:
            gain_loss = Decimal(str(sr["gain_loss"]))
            if gain_loss >= Decimal("0"):
                continue

            sale_date_str = sr.get("sale_date", "")
            if not sale_date_str:
                continue
            sale_date_val = date.fromisoformat(sale_date_str)
            window_start = sale_date_val - timedelta(days=30)
            window_end = sale_date_val + timedelta(days=30)

            # Find ticker from lot or sale
            lot_id = sr.get("lot_id", "")
            ticker = None
            for lot in lots:
                if lot.get("id") == lot_id:
                    ticker = lot.get("ticker")
                    break
            if not ticker:
                sale_id = sr.get("sale_id", "")
                for sale in sales:
                    if sale.get("id") == sale_id:
                        ticker = sale.get("ticker")
                        break
            if not ticker:
                continue

            # Check events for wash sale conflict
            conflicting = []
            for event in events:
                if event.get("ticker") != ticker:
                    continue
                if event.get("event_type", "") not in ("VEST", "EXERCISE", "PURCHASE"):
                    continue
                event_date_str = event.get("event_date", "")
                if not event_date_str:
                    continue
                event_date_val = date.fromisoformat(event_date_str)
                if (
                    window_start <= event_date_val <= window_end
                    and event_date_val != sale_date_val
                ):
                    conflicting.append(event)

            if conflicting:
                ev = conflicting[0]
                recommendations.append(StrategyRecommendation(
                    name=f"Wash Sale Warning: {ticker}",
                    category=StrategyCategory.CAPITAL_GAINS,
                    priority=Priority.HIGH,
                    situation=(
                        f"Loss sale of {ticker} on {sale_date_val} "
                        f"(loss: ${abs(gain_loss):,.2f}) conflicts with "
                        f"{ev.get('event_type', 'event')} on {ev.get('event_date', 'unknown')}."
                    ),
                    mechanism=(
                        "Wash sale rule (IRC 1091) disallows the loss if substantially "
                        "identical securities are acquired within 30 days before or after."
                    ),
                    quantified_impact=f"Loss of ${abs(gain_loss):,.2f} may be disallowed.",
                    estimated_savings=Decimal("0"),
                    action_steps=[
                        "Review whether the purchase/vest is within the 61-day window",
                        "The loss is not permanently lost -- it increases the basis of replacement shares",
                    ],
                    risk_level=RiskLevel.HIGH,
                    irs_authority="IRC Section 1091; Pub 550",
                    interactions=["Tax-Loss Harvesting"],
                ))

        return recommendations

    # ------------------------------------------------------------------
    # C.4 NIIT Analysis
    # ------------------------------------------------------------------

    def _analyze_niit(
        self,
        baseline: TaxEstimate,
        filing_status: FilingStatus,
    ) -> list[StrategyRecommendation]:
        threshold = NIIT_THRESHOLD.get(filing_status, Decimal("200000"))
        excess_agi = baseline.agi - threshold
        if excess_agi <= Decimal("0"):
            return []

        niit_paid = baseline.federal_niit

        if excess_agi <= Decimal("50000"):
            return [StrategyRecommendation(
                name="NIIT Threshold Management",
                category=StrategyCategory.CAPITAL_GAINS,
                priority=Priority.MEDIUM,
                situation=(
                    f"AGI exceeds NIIT threshold by ${excess_agi:,.0f}. "
                    f"NIIT paid: ${niit_paid:,.2f}."
                ),
                mechanism=(
                    "Deferring investment income or increasing above-the-line "
                    "deductions could reduce AGI below the NIIT threshold."
                ),
                quantified_impact=f"Eliminating NIIT entirely would save ${niit_paid:,.2f}.",
                estimated_savings=niit_paid,
                action_steps=[
                    "Increase 401(k) contributions to reduce AGI",
                    "Defer capital gain realizations to next year if possible",
                    "Consider HSA contributions (reduces AGI for federal purposes)",
                ],
                risk_level=RiskLevel.MODERATE,
                irs_authority="IRC Section 1411; Form 8960",
                interactions=["Maximize 401(k) Contributions", "Tax-Loss Harvesting"],
            )]

        return [StrategyRecommendation(
            name="NIIT Impact Analysis",
            category=StrategyCategory.CAPITAL_GAINS,
            priority=Priority.LOW,
            situation=(
                f"AGI exceeds NIIT threshold by ${excess_agi:,.0f}. "
                "NIIT cannot be avoided at this income level."
            ),
            mechanism=(
                "Tax-loss harvesting reduces net investment income, "
                "reducing the NIIT base."
            ),
            quantified_impact=(
                f"Current NIIT: ${niit_paid:,.2f}. "
                "Each $1,000 of harvested losses saves $38 in NIIT."
            ),
            estimated_savings=Decimal("0"),
            action_steps=[
                "Tax-loss harvesting reduces NIIT base",
                "Consider deferring capital gains to future years",
            ],
            risk_level=RiskLevel.LOW,
            irs_authority="IRC Section 1411; Form 8960",
            interactions=["Tax-Loss Harvesting"],
        )]

    # ------------------------------------------------------------------
    # D.1 Income Shifting
    # ------------------------------------------------------------------

    def _analyze_income_shifting(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        projected = user_inputs.projected_income_next_year
        if projected is None or projected <= Decimal("0"):
            return []

        current_agi = baseline.agi
        if current_agi <= Decimal("0"):
            return []

        # Compute next-year estimate (may fail if brackets don't exist)
        try:
            next_est = self.estimator.estimate(
                tax_year=tax_year + 1,
                filing_status=filing_status,
                w2_wages=projected,
            )
        except (ValueError, KeyError):
            return []

        # Compare effective rates as proxy for marginal rates
        current_rate = baseline.total_tax / current_agi
        next_rate = next_est.total_tax / projected
        rate_diff = current_rate - next_rate

        if rate_diff > Decimal("0.02"):
            per_1000 = rate_diff * Decimal("1000")
            return [StrategyRecommendation(
                name="Income Shifting: Defer to Lower-Rate Year",
                category=StrategyCategory.MULTI_YEAR,
                priority=Priority.MEDIUM,
                situation=(
                    f"Current-year AGI: ${current_agi:,.0f}. "
                    f"Projected next-year income: ${projected:,.0f}."
                ),
                mechanism=(
                    "Next year's expected income is lower, so deferring "
                    "income shifts taxation to a lower effective rate."
                ),
                quantified_impact=f"Each $1,000 shifted saves ~${per_1000:,.0f}.",
                estimated_savings=per_1000 * 10,
                action_steps=[
                    "Defer capital gain realizations to next year",
                    "Accelerate deductible expenses to this year",
                    "Consider increasing 401(k) this year",
                ],
                risk_level=RiskLevel.MODERATE,
                irs_authority="IRC Section 451 (timing of income)",
                interactions=["Maximize 401(k) Contributions"],
            )]

        if rate_diff < Decimal("-0.02"):
            per_1000 = abs(rate_diff) * Decimal("1000")
            return [StrategyRecommendation(
                name="Income Shifting: Accelerate to Lower-Rate Year",
                category=StrategyCategory.MULTI_YEAR,
                priority=Priority.MEDIUM,
                situation=(
                    f"Current-year AGI: ${current_agi:,.0f}. "
                    f"Projected next-year income: ${projected:,.0f}."
                ),
                mechanism=(
                    "Next year's expected income is higher, so accelerating "
                    "income to this year taxes it at a lower effective rate."
                ),
                quantified_impact=f"Each $1,000 accelerated saves ~${per_1000:,.0f}.",
                estimated_savings=per_1000 * 10,
                action_steps=[
                    "Consider exercising stock options this year",
                    "Realize capital gains this year at lower rates",
                ],
                risk_level=RiskLevel.MODERATE,
                irs_authority="IRC Section 451 (timing of income)",
            )]

        return []

    # ------------------------------------------------------------------
    # D.3 Loss Carryforward
    # ------------------------------------------------------------------

    def _analyze_loss_carryforward(
        self,
        sale_results: list[dict],
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        if user_inputs.capital_loss_carryforward >= Decimal("0"):
            return []

        net_capital = sum(
            Decimal(str(sr["gain_loss"])) for sr in sale_results
        )
        total = net_capital + user_inputs.capital_loss_carryforward

        loss_limit = CAPITAL_LOSS_LIMIT.get(filing_status, Decimal("3000"))
        if total >= -loss_limit:
            return []

        new_carryforward = total + loss_limit
        return [StrategyRecommendation(
            name="Capital Loss Carryforward",
            category=StrategyCategory.MULTI_YEAR,
            priority=Priority.LOW,
            situation=(
                f"Net capital losses (including "
                f"${abs(user_inputs.capital_loss_carryforward):,.0f} carryforward): "
                f"${abs(total):,.0f}. "
                f"Deductible this year: ${loss_limit:,.0f}. "
                f"New carryforward: ${abs(new_carryforward):,.0f}."
            ),
            mechanism=(
                "Excess capital losses carry forward indefinitely per "
                "IRC Section 1212(b). Consider realizing gains to use the carryforward."
            ),
            quantified_impact=(
                f"Carryforward of ${abs(new_carryforward):,.0f} "
                "available to offset future gains."
            ),
            estimated_savings=Decimal("0"),
            action_steps=[
                "Consider realizing long-term gains to use the carryforward",
                "Track carryforward on Schedule D",
            ],
            risk_level=RiskLevel.LOW,
            irs_authority="IRC Section 1212(b); Schedule D",
        )]

    # ------------------------------------------------------------------
    # D.4 Estimated Payments
    # ------------------------------------------------------------------

    def _analyze_estimated_payments(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []

        if user_inputs.prior_year_federal_tax is None:
            self.warnings.append(
                "Prior year tax not provided. Estimated payment analysis may be incomplete. "
                "Use --prior-year-tax to provide."
            )
            return recommendations

        # Federal safe harbor
        current_90pct = baseline.federal_total_tax * Decimal("0.90")
        prior_110pct = user_inputs.prior_year_federal_tax * Decimal("1.10")
        federal_safe_harbor = min(current_90pct, prior_110pct)

        total_paid = baseline.federal_withheld + baseline.federal_estimated_payments
        shortfall = max(federal_safe_harbor - total_paid, Decimal("0"))

        if shortfall > Decimal("0"):
            today = date.today()
            quarterly_dates = [
                date(tax_year, 4, 15),
                date(tax_year, 6, 15),
                date(tax_year, 9, 15),
                date(tax_year + 1, 1, 15),
            ]
            remaining_qs = [d for d in quarterly_dates if d > today]
            num_remaining = max(len(remaining_qs), 1)
            per_quarter = shortfall / num_remaining
            penalty_est = shortfall * Decimal("0.08")

            recommendations.append(StrategyRecommendation(
                name="Estimated Tax Payment Shortfall",
                category=StrategyCategory.MULTI_YEAR,
                priority=(
                    Priority.CRITICAL if shortfall > Decimal("10000") else Priority.HIGH
                ),
                situation=(
                    f"Federal safe harbor: ${federal_safe_harbor:,.0f}. "
                    f"Total paid: ${total_paid:,.0f}. "
                    f"Shortfall: ${shortfall:,.0f}."
                ),
                mechanism=(
                    "Underpayment penalty applies if withholding + estimated "
                    "payments are below the safe harbor amount."
                ),
                quantified_impact=(
                    f"Penalty risk: ~${penalty_est:,.0f} (estimated at ~8% annualized)."
                ),
                estimated_savings=penalty_est,
                action_steps=[
                    f"Pay ${per_quarter:,.0f} per remaining quarter via IRS Direct Pay or EFTPS",
                    "Alternatively, increase W-4 withholding with employer",
                    "Federal quarterly due dates: Apr 15, Jun 15, Sep 15, Jan 15",
                ],
                deadline=remaining_qs[0] if remaining_qs else date(tax_year + 1, 1, 15),
                risk_level=(
                    RiskLevel.HIGH if shortfall > Decimal("10000") else RiskLevel.MODERATE
                ),
                irs_authority="IRC Section 6654; Pub 505; Form 2210",
            ))

        # California safe harbor
        if user_inputs.prior_year_state_tax is not None:
            ca_90 = baseline.ca_total_tax * Decimal("0.90")
            ca_110 = user_inputs.prior_year_state_tax * Decimal("1.10")
            ca_safe = min(ca_90, ca_110)

            total_ca = baseline.ca_withheld + baseline.ca_estimated_payments
            ca_shortfall = max(ca_safe - total_ca, Decimal("0"))

            if ca_shortfall > Decimal("0"):
                recommendations.append(StrategyRecommendation(
                    name="CA Estimated Tax Payment Shortfall",
                    category=StrategyCategory.MULTI_YEAR,
                    priority=(
                        Priority.HIGH if ca_shortfall > Decimal("5000")
                        else Priority.MEDIUM
                    ),
                    situation=(
                        f"CA safe harbor: ${ca_safe:,.0f}. "
                        f"Total paid: ${total_ca:,.0f}. "
                        f"Shortfall: ${ca_shortfall:,.0f}."
                    ),
                    mechanism="CA underpayment penalty applies similarly to federal.",
                    quantified_impact=f"Shortfall: ${ca_shortfall:,.0f}.",
                    estimated_savings=ca_shortfall * Decimal("0.07"),
                    action_steps=[
                        "Pay CA estimated tax via FTB Web Pay",
                        "CA quarterly schedule: Apr 15 (30%), Jun 15 (40%), Jan 15 (30%)",
                    ],
                    risk_level=RiskLevel.MODERATE,
                    california_impact="CA R&TC Section 19136",
                    irs_authority="CA R&TC Section 19136",
                ))

        return recommendations

    # ------------------------------------------------------------------
    # B.2 ISO Exercise Timing
    # ------------------------------------------------------------------

    def _analyze_iso_exercise(
        self,
        baseline: TaxEstimate,
        lots: list[dict],
        events: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        prices = user_inputs.current_market_prices

        # Step 1: Compute AMT headroom via binary search
        amt_headroom = self._compute_amt_headroom(
            baseline, tax_year, filing_status,
        )

        # Step 2: Analyze unexercised ISO grants (from user input)
        iso_grants = user_inputs.unexercised_iso_grants or []
        for grant in iso_grants:
            ticker = grant.get("ticker", "")
            shares = Decimal(str(grant.get("shares", "0")))
            strike_price = Decimal(str(grant.get("strike_price", "0")))
            expiration_str = grant.get("expiration_date", "")

            if ticker not in prices or shares <= Decimal("0"):
                continue

            current_price = prices[ticker]
            spread_per_share = current_price - strike_price
            if spread_per_share <= Decimal("0"):
                # Underwater — no benefit to exercising
                recommendations.append(StrategyRecommendation(
                    name=f"ISO Analysis: {ticker} (Underwater)",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.LOW,
                    situation=(
                        f"{shares:,.0f} ISOs at ${strike_price:,.2f} strike. "
                        f"Current price: ${current_price:,.2f}. Options are underwater."
                    ),
                    mechanism="Underwater options have no intrinsic value to exercise.",
                    quantified_impact="No action needed — exercise would create no benefit.",
                    estimated_savings=Decimal("0"),
                    action_steps=[
                        "Monitor stock price for recovery above strike price",
                        "Track expiration date to avoid losing options",
                    ],
                    risk_level=RiskLevel.LOW,
                    california_impact="No CA impact — no exercise event.",
                    irs_authority="IRC Sections 421-424",
                ))
                continue

            total_spread = spread_per_share * shares

            # Can the full exercise fit within AMT headroom?
            if total_spread <= amt_headroom:
                recommendations.append(StrategyRecommendation(
                    name=f"ISO Exercise: {ticker} (Within AMT Headroom)",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.HIGH,
                    situation=(
                        f"{shares:,.0f} ISOs at ${strike_price:,.2f} strike. "
                        f"Current FMV: ${current_price:,.2f}. "
                        f"Spread: ${total_spread:,.2f}. "
                        f"AMT headroom: ${amt_headroom:,.2f}."
                    ),
                    mechanism=(
                        "Exercising within AMT headroom triggers NO additional "
                        "tax. Starts the long-term holding period clock for "
                        "qualifying disposition treatment."
                    ),
                    quantified_impact=(
                        f"Exercise creates $0 AMT. "
                        f"Remaining AMT headroom: ${amt_headroom - total_spread:,.2f}."
                    ),
                    estimated_savings=Decimal("0"),
                    action_steps=[
                        f"Exercise all {shares:,.0f} ISO shares at ${strike_price:,.2f}",
                        "Hold for >1 year from exercise AND >2 years from grant for qualifying disposition",
                        f"Exercise must settle by Dec 31, {tax_year}",
                    ],
                    deadline=date(tax_year, 12, 30),
                    risk_level=RiskLevel.MODERATE,
                    california_impact=(
                        "CA does NOT have AMT (repealed 2005). "
                        "ISO exercise has no CA tax impact until sale. "
                        "At sale, CA treats ISOs like NSOs for state purposes."
                    ),
                    irs_authority="IRC Sections 421-424, 55-59; Form 6251",
                    warnings=[
                        "Stock price risk: if stock drops after exercise, you still owe AMT on the spread at exercise",
                    ],
                ))
            else:
                # Compute AMT cost for full exercise
                full_exercise_est = self.estimator.estimate(
                    tax_year=tax_year,
                    filing_status=filing_status,
                    w2_wages=baseline.w2_wages,
                    interest_income=baseline.interest_income,
                    dividend_income=baseline.dividend_income,
                    qualified_dividends=baseline.qualified_dividends,
                    short_term_gains=baseline.short_term_gains,
                    long_term_gains=baseline.long_term_gains,
                    amt_iso_preference=total_spread,
                )
                amt_cost = full_exercise_est.federal_amt

                # Compute how many shares fit within headroom
                shares_in_headroom = Decimal("0")
                if spread_per_share > Decimal("0"):
                    shares_in_headroom = (
                        amt_headroom / spread_per_share
                    ).to_integral_value()
                    shares_in_headroom = min(shares_in_headroom, shares)

                # Also compare exercise+hold vs exercise+sell (disqualifying)
                disq_est = self.estimator.estimate(
                    tax_year=tax_year,
                    filing_status=filing_status,
                    w2_wages=baseline.w2_wages + total_spread,
                    interest_income=baseline.interest_income,
                    dividend_income=baseline.dividend_income,
                    qualified_dividends=baseline.qualified_dividends,
                    short_term_gains=baseline.short_term_gains,
                    long_term_gains=baseline.long_term_gains,
                )
                disq_tax_increase = disq_est.total_tax - baseline.total_tax

                action_steps = []
                if shares_in_headroom > Decimal("0"):
                    action_steps.append(
                        f"Exercise {shares_in_headroom:,.0f} shares within AMT headroom ($0 AMT)"
                    )
                action_steps.extend([
                    f"Full exercise of {shares:,.0f} shares triggers ~${amt_cost:,.0f} AMT",
                    f"AMT paid generates a ${amt_cost:,.0f} credit carryforward (Form 8801)",
                    f"Same-day sale (disqualifying) would add ${total_spread:,.0f} ordinary income "
                    f"(~${disq_tax_increase:,.0f} additional tax)",
                    f"Exercise must settle by Dec 30, {tax_year}",
                ])

                # Check expiration urgency
                warnings = [
                    "Stock price risk: if stock drops after exercise, AMT is still owed on spread at exercise date",
                ]
                if expiration_str:
                    exp_date = date.fromisoformat(expiration_str)
                    days_to_exp = (exp_date - date.today()).days
                    if days_to_exp <= 90:
                        warnings.append(
                            f"OPTIONS EXPIRE {expiration_str} ({days_to_exp} days). "
                            "Exercise before expiration to avoid losing value."
                        )

                recommendations.append(StrategyRecommendation(
                    name=f"ISO Exercise: {ticker} (Exceeds AMT Headroom)",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.HIGH,
                    situation=(
                        f"{shares:,.0f} ISOs at ${strike_price:,.2f} strike. "
                        f"Current FMV: ${current_price:,.2f}. "
                        f"Spread: ${total_spread:,.2f}. "
                        f"AMT headroom: ${amt_headroom:,.2f}."
                    ),
                    mechanism=(
                        f"Full exercise exceeds AMT headroom by "
                        f"${total_spread - amt_headroom:,.2f}. "
                        f"AMT of ~${amt_cost:,.0f} would be triggered. "
                        "AMT paid on ISO exercises generates a credit "
                        "carryforward (Form 8801) that offsets future regular tax."
                    ),
                    quantified_impact=(
                        f"Exercise+hold: ${amt_cost:,.0f} AMT "
                        f"(generates carryforward credit). "
                        f"Exercise+sell: ${disq_tax_increase:,.0f} ordinary income tax."
                    ),
                    estimated_savings=Decimal("0"),
                    action_steps=action_steps,
                    deadline=date(tax_year, 12, 30),
                    risk_level=RiskLevel.HIGH,
                    california_impact=(
                        "CA does NOT have AMT. ISO exercise has no CA tax "
                        "impact until sale. At sale, CA treats ISOs like NSOs "
                        "(ordinary income on spread)."
                    ),
                    irs_authority="IRC Sections 421-424, 55-59; Form 6251; Form 8801",
                    warnings=warnings,
                    interactions=["AMT Credit Carryforward"],
                ))

        # Step 3: AMT credit carryforward utilization
        prior_amt_credit = user_inputs.amt_credit_carryforward
        if prior_amt_credit > Decimal("0"):
            # Credit usable = regular tax + LTCG tax - TMT (tentative minimum tax)
            # When regular tax > TMT, the excess is available for credit
            tmt = self._compute_tmt(baseline, tax_year, filing_status)
            regular_plus_ltcg = (
                baseline.federal_regular_tax + baseline.federal_ltcg_tax
            )
            credit_usable = max(regular_plus_ltcg - tmt, Decimal("0"))
            credit_used = min(prior_amt_credit, credit_usable)

            if credit_used > Decimal("0"):
                remaining = prior_amt_credit - credit_used
                recommendations.append(StrategyRecommendation(
                    name="AMT Credit Carryforward Utilization",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.HIGH,
                    situation=(
                        f"Prior AMT credit carryforward: ${prior_amt_credit:,.0f}. "
                        f"Usable this year: ${credit_used:,.0f}."
                    ),
                    mechanism=(
                        "AMT credits from prior ISO exercises offset current "
                        "regular tax. Per Form 8801, the credit equals the "
                        "excess of regular tax over tentative minimum tax."
                    ),
                    quantified_impact=f"${credit_used:,.0f} AMT credit reduces federal tax.",
                    estimated_savings=credit_used,
                    action_steps=[
                        "File Form 8801 with your return to claim the credit",
                        f"Remaining carryforward after use: ${remaining:,.0f}",
                    ],
                    deadline=date(tax_year + 1, 4, 15),
                    risk_level=RiskLevel.LOW,
                    california_impact="CA does not have AMT credit — federal benefit only.",
                    irs_authority="IRC Section 53; Form 8801",
                ))
            elif prior_amt_credit > Decimal("0"):
                recommendations.append(StrategyRecommendation(
                    name="AMT Credit Carryforward (Not Usable This Year)",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.LOW,
                    situation=(
                        f"Prior AMT credit: ${prior_amt_credit:,.0f}. "
                        "Regular tax does not exceed TMT this year — "
                        "credit cannot be used."
                    ),
                    mechanism=(
                        "The credit carries forward indefinitely. It becomes "
                        "usable when regular tax exceeds the tentative "
                        "minimum tax in a future year."
                    ),
                    quantified_impact="$0 usable this year. Credit carries forward.",
                    estimated_savings=Decimal("0"),
                    action_steps=[
                        f"Carry ${prior_amt_credit:,.0f} AMT credit forward to next year",
                        "Track on Form 8801 each year",
                    ],
                    risk_level=RiskLevel.LOW,
                    irs_authority="IRC Section 53; Form 8801",
                ))

        return recommendations

    def _compute_amt_headroom(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        filing_status: FilingStatus,
    ) -> Decimal:
        """Binary search for maximum ISO spread that produces $0 AMT."""
        # If already paying AMT, headroom is 0
        if baseline.federal_amt > Decimal("0"):
            return Decimal("0")

        low = Decimal("0")
        high = Decimal("500000")

        # Quick check: if even max produces no AMT, return max
        test_high = self.estimator.estimate(
            tax_year=tax_year,
            filing_status=filing_status,
            w2_wages=baseline.w2_wages,
            interest_income=baseline.interest_income,
            dividend_income=baseline.dividend_income,
            qualified_dividends=baseline.qualified_dividends,
            short_term_gains=baseline.short_term_gains,
            long_term_gains=baseline.long_term_gains,
            amt_iso_preference=high,
        )
        if test_high.federal_amt == Decimal("0"):
            return high

        # Binary search
        for _ in range(30):  # converge within $100
            if high - low <= Decimal("100"):
                break
            mid = (low + high) / 2
            test_est = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains,
                long_term_gains=baseline.long_term_gains,
                amt_iso_preference=mid,
            )
            if test_est.federal_amt > Decimal("0"):
                high = mid
            else:
                low = mid

        return low

    def _compute_tmt(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        filing_status: FilingStatus,
    ) -> Decimal:
        """Compute tentative minimum tax (without ISO preferences) for AMT credit calc."""
        exemptions = AMT_EXEMPTION.get(tax_year, {})
        phaseouts = AMT_PHASEOUT_START.get(tax_year, {})
        threshold_28 = AMT_28_PERCENT_THRESHOLD.get(tax_year, Decimal("232600"))

        exemption = exemptions.get(filing_status, Decimal("85700"))
        phaseout_start = phaseouts.get(filing_status, Decimal("609350"))

        # AMTI = taxable income (no ISO preferences)
        amti = baseline.taxable_income

        # Phase out exemption
        if amti > phaseout_start:
            reduction = (amti - phaseout_start) * Decimal("0.25")
            exemption = max(exemption - reduction, Decimal("0"))

        amt_base = max(amti - exemption, Decimal("0"))

        # Two-tier AMT rates: 26% up to threshold, 28% above
        if filing_status == FilingStatus.MFS:
            threshold_28 = threshold_28 / 2

        if amt_base <= threshold_28:
            tmt = amt_base * Decimal("0.26")
        else:
            tmt = (
                threshold_28 * Decimal("0.26")
                + (amt_base - threshold_28) * Decimal("0.28")
            )

        return tmt

    # ------------------------------------------------------------------
    # B.3 RSU Harvesting Coordination
    # ------------------------------------------------------------------

    def _analyze_rsu_harvesting(
        self,
        baseline: TaxEstimate,
        lots: list[dict],
        events: list[dict],
        sale_results: list[dict],
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        prices = user_inputs.current_market_prices
        if not prices:
            return recommendations

        today = date.today()
        future_vests = user_inputs.future_vest_dates or []

        # Find RSU lots with unrealized losses
        rsu_lots = [
            lot for lot in lots
            if lot.get("equity_type") == "RSU"
            and Decimal(str(lot.get("shares_remaining", "0"))) > Decimal("0")
        ]

        for lot in rsu_lots:
            ticker = lot.get("ticker", "")
            if ticker not in prices:
                continue

            current_price = prices[ticker]
            cost_per_share = Decimal(str(lot["cost_per_share"]))
            shares_remaining = Decimal(str(lot["shares_remaining"]))
            unrealized = (current_price - cost_per_share) * shares_remaining

            if unrealized >= Decimal("0"):
                continue  # Only interested in losses

            acq_date_str = lot.get("acquisition_date", "")
            if not acq_date_str:
                continue
            acq_date = date.fromisoformat(acq_date_str)
            is_long_term = (today - acq_date).days > 365
            holding_label = "long-term" if is_long_term else "short-term"

            # Check for upcoming RSU vests (wash sale risk)
            upcoming_vests_for_ticker = []

            # Check future_vest_dates from user input
            for fv in future_vests:
                fv_ticker = fv.get("ticker", "")
                fv_date_str = fv.get("vest_date", fv.get("date", ""))
                if fv_ticker != ticker or not fv_date_str:
                    continue
                fv_date = date.fromisoformat(fv_date_str)
                if today <= fv_date <= today + timedelta(days=60):
                    upcoming_vests_for_ticker.append(fv)

            # Also check events in the database for upcoming vests
            for event in events:
                if event.get("ticker") != ticker:
                    continue
                if event.get("event_type") not in ("VEST", "PURCHASE"):
                    continue
                ev_date_str = event.get("event_date", "")
                if not ev_date_str:
                    continue
                ev_date = date.fromisoformat(ev_date_str)
                if today <= ev_date <= today + timedelta(days=60):
                    upcoming_vests_for_ticker.append(event)

            # What-if: harvest this RSU loss
            if is_long_term:
                mod_st, mod_lt = _net_capital_losses(
                    baseline.short_term_gains,
                    baseline.long_term_gains + unrealized,
                    filing_status,
                )
            else:
                mod_st, mod_lt = _net_capital_losses(
                    baseline.short_term_gains + unrealized,
                    baseline.long_term_gains,
                    filing_status,
                )

            modified = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=mod_st,
                long_term_gains=mod_lt,
            )
            savings = baseline.total_tax - modified.total_tax

            if savings <= Decimal("0"):
                continue

            warnings = []
            action_steps = []

            if upcoming_vests_for_ticker:
                # Wash sale risk exists
                vest_info = upcoming_vests_for_ticker[0]
                vest_date_str = vest_info.get(
                    "vest_date", vest_info.get("date", vest_info.get("event_date", ""))
                )
                vest_date = date.fromisoformat(vest_date_str) if vest_date_str else None

                if vest_date:
                    safe_sell_before = vest_date - timedelta(days=31)
                    safe_sell_after = vest_date + timedelta(days=31)
                    warnings.append(
                        f"RSU vest on {vest_date} creates wash sale risk. "
                        f"Sell before {safe_sell_before} or after {safe_sell_after} "
                        "to avoid wash sale."
                    )
                    if safe_sell_before >= today:
                        action_steps.append(
                            f"Sell {shares_remaining:,.0f} shares before {safe_sell_before} "
                            f"to avoid wash sale from {vest_date} vest"
                        )
                    else:
                        action_steps.append(
                            f"Wait until after {safe_sell_after} to harvest "
                            f"(too close to {vest_date} vest)"
                        )
                else:
                    warnings.append(
                        f"Upcoming RSU vest for {ticker} may trigger wash sale."
                    )
            else:
                action_steps.append(
                    f"Sell {shares_remaining:,.0f} shares of {ticker} to harvest "
                    f"${abs(unrealized):,.2f} {holding_label} loss"
                )

            action_steps.extend([
                "Wait 31 days before repurchasing to avoid wash sale",
                "Consider buying a correlated ETF during the waiting period",
            ])

            recommendations.append(StrategyRecommendation(
                name=f"RSU Harvest: {ticker}",
                category=StrategyCategory.EQUITY_COMPENSATION,
                priority=(
                    Priority.HIGH if savings > Decimal("1000") else Priority.MEDIUM
                ),
                situation=(
                    f"RSU lot: {shares_remaining:,.0f} shares vested {acq_date}, "
                    f"cost ${cost_per_share:,.2f}. Current: ${current_price:,.2f}. "
                    f"Unrealized {holding_label} loss: ${abs(unrealized):,.2f}."
                ),
                mechanism=(
                    "Selling depreciated RSU shares realizes the capital loss, "
                    "offsetting realized gains and reducing tax liability."
                ),
                quantified_impact=f"Estimated tax savings: ${savings:,.2f}.",
                estimated_savings=savings,
                action_steps=action_steps,
                deadline=date(tax_year, 12, 31),
                risk_level=(
                    RiskLevel.MODERATE if upcoming_vests_for_ticker
                    else RiskLevel.LOW
                ),
                california_impact=(
                    "CA treats all capital gains/losses at ordinary rates. "
                    "Loss harvesting reduces CA tax as well."
                ),
                irs_authority="IRC Sections 1211(b), 1212(b), 1091; Pub 550",
                warnings=warnings,
                interactions=["Wash Sale Warning", "Tax-Loss Harvesting"],
            ))

        return recommendations

    # ------------------------------------------------------------------
    # B.4 NSO Exercise Timing
    # ------------------------------------------------------------------

    def _analyze_nso_timing(
        self,
        baseline: TaxEstimate,
        tax_year: int,
        filing_status: FilingStatus,
        user_inputs: UserInputs,
    ) -> list[StrategyRecommendation]:
        recommendations: list[StrategyRecommendation] = []
        prices = user_inputs.current_market_prices
        nso_grants = user_inputs.unexercised_nso_grants or []

        if not nso_grants or not prices:
            return recommendations

        today = date.today()

        for grant in nso_grants:
            ticker = grant.get("ticker", "")
            shares = Decimal(str(grant.get("shares", "0")))
            strike_price = Decimal(str(grant.get("strike_price", "0")))
            expiration_str = grant.get("expiration_date", "")

            if ticker not in prices or shares <= Decimal("0"):
                continue

            current_price = prices[ticker]
            spread_per_share = current_price - strike_price
            total_spread = spread_per_share * shares

            if total_spread <= Decimal("0"):
                # Underwater
                warnings = []
                if expiration_str:
                    exp_date = date.fromisoformat(expiration_str)
                    days_to_exp = (exp_date - today).days
                    if days_to_exp <= 90:
                        warnings.append(
                            f"Options expire {expiration_str} ({days_to_exp} days). "
                            "Monitor stock price."
                        )
                recommendations.append(StrategyRecommendation(
                    name=f"NSO Analysis: {ticker} (Underwater)",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=Priority.LOW,
                    situation=(
                        f"{shares:,.0f} NSOs at ${strike_price:,.2f} strike. "
                        f"Current price: ${current_price:,.2f}. Options are underwater."
                    ),
                    mechanism="Underwater NSOs have no intrinsic value.",
                    quantified_impact="No action needed.",
                    estimated_savings=Decimal("0"),
                    action_steps=["Monitor stock price for recovery"],
                    risk_level=RiskLevel.LOW,
                    irs_authority="IRC Section 83; Pub 525",
                    warnings=warnings,
                ))
                continue

            # Option 1: Exercise this year — spread is ordinary income
            exercise_this_year = self.estimator.estimate(
                tax_year=tax_year,
                filing_status=filing_status,
                w2_wages=baseline.w2_wages + total_spread,
                interest_income=baseline.interest_income,
                dividend_income=baseline.dividend_income,
                qualified_dividends=baseline.qualified_dividends,
                short_term_gains=baseline.short_term_gains,
                long_term_gains=baseline.long_term_gains,
            )
            tax_this_year = exercise_this_year.total_tax - baseline.total_tax

            # Option 2: Exercise next year
            projected = user_inputs.projected_income_next_year
            tax_next_year = None
            savings = Decimal("0")

            if projected is not None and projected > Decimal("0"):
                try:
                    next_baseline = self.estimator.estimate(
                        tax_year=tax_year + 1,
                        filing_status=filing_status,
                        w2_wages=projected,
                    )
                    next_with_exercise = self.estimator.estimate(
                        tax_year=tax_year + 1,
                        filing_status=filing_status,
                        w2_wages=projected + total_spread,
                    )
                    tax_next_year = next_with_exercise.total_tax - next_baseline.total_tax
                    savings = tax_this_year - tax_next_year
                except (ValueError, KeyError):
                    pass

            # Check expiration urgency
            warnings = []
            exp_urgent = False
            if expiration_str:
                exp_date = date.fromisoformat(expiration_str)
                days_to_exp = (exp_date - today).days
                if days_to_exp <= 90:
                    exp_urgent = True
                    warnings.append(
                        f"OPTIONS EXPIRE {expiration_str} ({days_to_exp} days). "
                        "Exercise before expiration or lose the options."
                    )

            if savings > Decimal("500") and not exp_urgent:
                # Deferral is beneficial
                recommendations.append(StrategyRecommendation(
                    name=f"NSO Timing: Defer {ticker} Exercise",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=(
                        Priority.HIGH if savings > Decimal("5000")
                        else Priority.MEDIUM
                    ),
                    situation=(
                        f"{shares:,.0f} NSOs at ${strike_price:,.2f} strike. "
                        f"Spread: ${total_spread:,.2f}. "
                        f"Tax if exercised this year: ~${tax_this_year:,.0f}. "
                        f"Tax if exercised next year: ~${tax_next_year:,.0f}."
                    ),
                    mechanism=(
                        "NSO exercise spread is ordinary income (IRC Section 83). "
                        "Deferring to a lower-income year reduces the marginal rate."
                    ),
                    quantified_impact=(
                        f"Deferring saves ~${savings:,.0f} "
                        f"(${tax_this_year:,.0f} this year vs ${tax_next_year:,.0f} next year)."
                    ),
                    estimated_savings=savings,
                    action_steps=[
                        f"Defer exercise of {shares:,.0f} NSO shares to {tax_year + 1}",
                        "Monitor stock price — deferral carries stock price risk",
                        "Verify exercise window is available next year",
                    ],
                    deadline=None,
                    risk_level=RiskLevel.MODERATE,
                    california_impact=(
                        "CA taxes NSO spread as ordinary income at exercise. "
                        "Deferral benefits apply to both federal and CA tax."
                    ),
                    irs_authority="IRC Section 83; Pub 525",
                    warnings=warnings,
                    interactions=["Income Shifting"],
                ))
            elif savings < Decimal("-500"):
                # Exercise this year is better (or next year is higher income)
                benefit = abs(savings)
                recommendations.append(StrategyRecommendation(
                    name=f"NSO Timing: Exercise {ticker} This Year",
                    category=StrategyCategory.EQUITY_COMPENSATION,
                    priority=(
                        Priority.HIGH if benefit > Decimal("5000")
                        else Priority.MEDIUM
                    ),
                    situation=(
                        f"{shares:,.0f} NSOs at ${strike_price:,.2f} strike. "
                        f"Spread: ${total_spread:,.2f}. "
                        f"Tax if exercised this year: ~${tax_this_year:,.0f}."
                    ),
                    mechanism=(
                        "Next year's projected income is higher. Exercising "
                        "this year captures the spread at a lower marginal rate."
                    ),
                    quantified_impact=(
                        f"Exercising this year saves ~${benefit:,.0f} vs deferring."
                    ),
                    estimated_savings=benefit,
                    action_steps=[
                        f"Exercise {shares:,.0f} NSO shares before Dec 31, {tax_year}",
                        "Consider selling shares immediately to reduce stock concentration",
                    ],
                    deadline=date(tax_year, 12, 31),
                    risk_level=RiskLevel.LOW,
                    california_impact=(
                        "CA taxes NSO spread as ordinary income at exercise. "
                        "Exercising this year also reduces CA tax."
                    ),
                    irs_authority="IRC Section 83; Pub 525",
                    warnings=warnings,
                ))
            else:
                # No meaningful difference — informational
                action_steps = [
                    f"Exercise {shares:,.0f} NSO shares when convenient — "
                    "no significant timing advantage",
                ]
                if exp_urgent:
                    action_steps.insert(0, "Exercise before expiration!")

                if total_spread > Decimal("0"):
                    recommendations.append(StrategyRecommendation(
                        name=f"NSO Exercise: {ticker}",
                        category=StrategyCategory.EQUITY_COMPENSATION,
                        priority=Priority.HIGH if exp_urgent else Priority.LOW,
                        situation=(
                            f"{shares:,.0f} NSOs at ${strike_price:,.2f} strike. "
                            f"Spread: ${total_spread:,.2f}. "
                            f"Tax on exercise: ~${tax_this_year:,.0f}."
                        ),
                        mechanism=(
                            "NSO exercise creates ordinary income. "
                            "No significant rate differential between years."
                        ),
                        quantified_impact=(
                            f"Tax cost on exercise: ~${tax_this_year:,.0f}. "
                            "No deferral advantage."
                        ),
                        estimated_savings=Decimal("0"),
                        action_steps=action_steps,
                        deadline=(
                            date.fromisoformat(expiration_str) if expiration_str
                            else None
                        ),
                        risk_level=(
                            RiskLevel.HIGH if exp_urgent else RiskLevel.LOW
                        ),
                        california_impact=(
                            "CA taxes NSO spread as ordinary income at exercise."
                        ),
                        irs_authority="IRC Section 83; Pub 525",
                        warnings=warnings,
                    ))

        return recommendations

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_data_completeness(
        self,
        w2s: list[dict],
        lots: list[dict],
        sale_results: list[dict],
        events: list[dict],
        user_inputs: UserInputs,
    ) -> dict[str, bool]:
        return {
            "w2_data": len(w2s) > 0,
            "lots": len(lots) > 0,
            "sale_results": len(sale_results) > 0,
            "events": len(events) > 0,
            "market_prices": len(user_inputs.current_market_prices) > 0,
            "prior_year_tax": user_inputs.prior_year_federal_tax is not None,
        }
