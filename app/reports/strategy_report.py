"""Strategy comparison report generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.engines.strategy import StrategyRecommendation

TEMPLATE_DIR = Path(__file__).parent / "templates"


class StrategyReportGenerator:
    """Generates tax strategy recommendation report."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, recommendations: list[StrategyRecommendation]) -> str:
        """Render strategy report."""
        template = self.env.get_template("strategy_report.txt")
        return template.render(recommendations=recommendations)
