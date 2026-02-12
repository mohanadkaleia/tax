"""Shared test fixtures for PDF parsing tests."""

from pathlib import Path

import pytest
from fpdf import FPDF


def _create_pdf(text: str, tmp_path: Path, filename: str = "test.pdf") -> Path:
    """Create a minimal PDF with the given text content."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in text.split("\n"):
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    out_path = tmp_path / filename
    pdf.output(str(out_path))
    return out_path


@pytest.fixture()
def w2_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic W-2 PDF."""
    text = """Form W-2 Wage and Tax Statement 2025
Employer's name
Acme Corp
1 Wages, tips, other comp 250,000.00
2 Federal income tax withheld 55,000.00
16 State wages 250,000.00
17 State income tax 22,000.00
12a V 5,000.00
14 Other
RSU 50,000.00
ESPP 3,000.00
Employee SSN 123-45-6789
Employer EIN 12-3456789"""
    return _create_pdf(text, tmp_path, "w2_test.pdf")


@pytest.fixture()
def form3921_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic Form 3921 PDF."""
    text = """Form 3921 Exercise of an Incentive Stock Option 2025
Transferor's name
Acme Corp
1 Date of grant 01/15/2022
2 Date of exercise 03/01/2025
3 Exercise price per share $50.00
4 Fair market value per share on exercise date $120.00
5 No. of shares transferred 200
Employee SSN 123-45-6789"""
    return _create_pdf(text, tmp_path, "form3921_test.pdf")


@pytest.fixture()
def form3922_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic Form 3922 PDF."""
    text = """Form 3922 Transfer of Stock Acquired Through Employee Stock Purchase Plan 2025
Transferor's name
Acme Corp
1 Date of option grant 01/01/2024
2 Date of transfer 06/30/2024
3 FMV on grant date $140.00
4 FMV on transfer date $150.00
5 Price paid per share $127.50
6 No. of shares transferred 50
Employee SSN 987-65-4321"""
    return _create_pdf(text, tmp_path, "form3922_test.pdf")


@pytest.fixture()
def form1099div_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic 1099-DIV PDF."""
    text = """Form 1099-DIV Dividends and Distributions 2025
Payer's name
Vanguard Group
1a Total ordinary dividends 1,234.56
1b Qualified dividends 987.65
2a Total capital gain distributions 500.00
4 Federal income tax withheld 0.00
Recipient's TIN 123-45-6789"""
    return _create_pdf(text, tmp_path, "form1099div_test.pdf")


@pytest.fixture()
def form1099int_pdf(tmp_path: Path) -> Path:
    """Generate a synthetic 1099-INT PDF."""
    text = """Form 1099-INT Interest Income 2025
Payer's name
Chase Bank
1 Interest income 456.78
2 Early withdrawal penalty 0.00
4 Federal income tax withheld 0.00
Recipient's TIN 123-45-6789"""
    return _create_pdf(text, tmp_path, "form1099int_test.pdf")
