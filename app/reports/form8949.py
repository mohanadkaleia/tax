"""Form 8949 report generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.equity_event import SaleResult
from app.models.reports import Form8949Line

TEMPLATE_DIR = Path(__file__).parent / "templates"


class Form8949Generator:
    """Generates Form 8949 from sale results."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def generate_lines(self, sale_results: list[SaleResult]) -> list[Form8949Line]:
        """Convert sale results to Form 8949 lines."""
        lines: list[Form8949Line] = []
        for result in sale_results:
            line = Form8949Line(
                description=f"{result.shares} sh {result.security.ticker}",
                date_acquired=result.acquisition_date,
                date_sold=result.sale_date,
                proceeds=result.proceeds,
                cost_basis=result.correct_basis,
                adjustment_code=result.adjustment_code,
                adjustment_amount=result.adjustment_amount,
                gain_loss=result.gain_loss,
                category=result.form_8949_category,
            )
            lines.append(line)
        return lines

    def render(self, lines: list[Form8949Line]) -> str:
        """Render Form 8949 report using Jinja2 template."""
        template = self.env.get_template("form8949.txt")
        return template.render(lines=lines)
