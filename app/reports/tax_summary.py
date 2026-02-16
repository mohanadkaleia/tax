"""Tax estimate summary report generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.reports import TaxEstimate

TEMPLATE_DIR = Path(__file__).parent / "templates"


class TaxSummaryGenerator:
    """Generates a human-readable tax estimate summary report."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, estimate: TaxEstimate) -> str:
        """Render tax estimate summary report."""
        template = self.env.get_template("tax_summary.txt")
        return template.render(est=estimate)
