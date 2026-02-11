"""ISO AMT worksheet generator."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.reports import AMTWorksheetLine

TEMPLATE_DIR = Path(__file__).parent / "templates"


class AMTWorksheetGenerator:
    """Generates ISO AMT preference worksheet."""

    def __init__(self) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def render(self, lines: list[AMTWorksheetLine]) -> str:
        """Render AMT worksheet."""
        template = self.env.get_template("amt_worksheet.txt")
        return template.render(lines=lines)
