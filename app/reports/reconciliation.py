"""Reconciliation report generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.reports import ReconciliationLine

TEMPLATE_DIR = Path(__file__).parent / "templates"


class ReconciliationReportGenerator:
    """Generates broker vs. corrected basis reconciliation report."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, lines: list[ReconciliationLine]) -> str:
        """Render reconciliation report."""
        template = self.env.get_template("reconciliation.txt")
        return template.render(lines=lines)
