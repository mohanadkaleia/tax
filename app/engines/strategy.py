"""Tax strategy analysis and recommendation engine."""

from pydantic import BaseModel

from app.models.equity_event import Lot
from app.models.reports import TaxEstimate


class StrategyRecommendation(BaseModel):
    """A single tax strategy recommendation."""

    name: str
    situation: str
    mechanism: str
    quantified_impact: str
    action_steps: list[str]
    deadline: str | None = None
    risk_level: str  # Low / Moderate / High
    california_impact: str | None = None


class StrategyEngine:
    """Analyzes tax situation and produces strategy recommendations."""

    def analyze(
        self,
        tax_estimate: TaxEstimate,
        lots: list[Lot],
    ) -> list[StrategyRecommendation]:
        """Analyze the current tax position and generate recommendations.

        Strategy areas (per Tax Planner agent spec):
        - Tax-loss harvesting
        - ESPP holding period optimization
        - ISO exercise timing
        - Income smoothing across years
        - AMT credit recovery planning
        - Estimated tax payment optimization
        """
        # TODO: Implement strategy analysis per Tax Planner specifications
        _ = tax_estimate, lots
        return []
