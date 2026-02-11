"""ESPP income report generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.reports import ESPPIncomeLine

TEMPLATE_DIR = Path(__file__).parent / "templates"


class ESPPReportGenerator:
    """Generates ESPP income report."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, lines: list[ESPPIncomeLine]) -> str:
        """Render ESPP income report."""
        template = self.env.get_template("espp_report.txt")
        return template.render(lines=lines)
