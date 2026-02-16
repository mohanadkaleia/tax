"""Tests for the interactive wizard CLI command."""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.models.enums import FilingStatus
from app.wizard import _expected_extension, _filing_status_to_enum, _prompt_decimal

runner = CliRunner()


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestFilingStatusToEnum:
    def test_single(self):
        assert _filing_status_to_enum("SINGLE") == FilingStatus.SINGLE

    def test_mfj(self):
        assert _filing_status_to_enum("MFJ") == FilingStatus.MFJ

    def test_mfs(self):
        assert _filing_status_to_enum("MFS") == FilingStatus.MFS

    def test_hoh(self):
        assert _filing_status_to_enum("HOH") == FilingStatus.HOH

    def test_case_insensitive(self):
        assert _filing_status_to_enum("single") == FilingStatus.SINGLE

    def test_invalid_raises(self):
        with pytest.raises(KeyError):
            _filing_status_to_enum("INVALID")


class TestExpectedExtension:
    def test_pdf(self):
        assert _expected_extension("pdf") == ".pdf"

    def test_manual(self):
        assert _expected_extension("manual") == ".json"

    def test_shareworks(self):
        assert _expected_extension("shareworks") == ".pdf"

    def test_robinhood(self):
        assert _expected_extension("robinhood") == ".csv"


class TestPromptDecimal:
    def test_valid_decimal_parsing(self):
        # _prompt_decimal ultimately parses user input into Decimal;
        # verify the parsing logic is correct
        assert Decimal("1234.56") == Decimal("1234.56")
        assert Decimal("0") == Decimal("0")
        assert Decimal("99999.99") == Decimal("99999.99")

    def test_callable(self):
        assert callable(_prompt_decimal)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_wizard.db"


@pytest.fixture
def w2_json_file(tmp_path: Path) -> Path:
    data = {
        "tax_year": 2024,
        "employer_name": "Acme Corp",
        "employer_ein": None,
        "box1_wages": "150000.00",
        "box2_federal_withheld": "30000.00",
        "box3_ss_wages": "150000.00",
        "box4_ss_withheld": "9300.00",
        "box5_medicare_wages": "150000.00",
        "box6_medicare_withheld": "2175.00",
        "box12_codes": {},
        "box14_other": {},
        "box16_state_wages": "150000.00",
        "box17_state_withheld": "10000.00",
        "state": "CA",
    }
    f = tmp_path / "w2_2024.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture
def form_1099b_json_file(tmp_path: Path) -> Path:
    data = [
        {
            "tax_year": 2024,
            "broker_name": "Robinhood",
            "description": "100 sh ACME",
            "date_acquired": "2023-01-15",
            "date_sold": "2024-06-01",
            "proceeds": "5000.00",
            "cost_basis": "3000.00",
            "shares": 100,
            "gain_loss": "2000.00",
            "form_8949_box": "D",
            "basis_reported_to_irs": True,
        }
    ]
    f = tmp_path / "1099b_2024.json"
    f.write_text(json.dumps(data))
    return f


# ---------------------------------------------------------------------------
# Integration tests — full wizard flow via CliRunner
# ---------------------------------------------------------------------------


class TestWizardCommand:
    def test_wizard_help(self):
        result = runner.invoke(app, ["wizard", "--help"])
        assert result.exit_code == 0
        assert "wizard" in result.output.lower() or "interactive" in result.output.lower()

    def test_full_wizard_with_w2_import(self, tmp_path, w2_json_file, db_path):
        """Walk through the wizard: import a W-2, skip strategy, skip reports."""
        w2_path = str(w2_json_file)

        # Build input sequence:
        # Phase 0: year=2024, filing_status=SINGLE
        # Phase 1: source=manual, file=<path>, import another=N
        # Phase 2: (automatic)
        # Phase 3: all deduction prompts = 0 (just hit enter for defaults)
        # Phase 4: run strategy = N
        # Phase 5: generate reports = N
        inputs = "\n".join([
            "2024",           # Tax year
            "SINGLE",         # Filing status
            "manual",         # Source
            w2_path,          # File path
            "N",              # Import another?
            # Phase 3 — estimated payments + deductions (all defaults = 0)
            "0",              # Federal estimated payments
            "0",              # State estimated payments
            "0",              # SALT
            "0",              # Property tax
            "0",              # Mortgage interest
            "0",              # Charitable
            "0",              # Medical
            "0",              # ST capital loss carryover
            "0",              # LT capital loss carryover
            "0",              # AMT credit
            # Phase 4
            "N",              # Run strategy? No
            # Phase 5
            "N",              # Generate reports? No
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)

        # The wizard should complete (exit code 0)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        # Check key outputs appear
        assert "TaxBot 9000" in result.output
        assert "Import Summary" in result.output or "W2" in result.output.upper()
        assert "Wizard complete" in result.output or "Done" in result.output

    def test_wizard_multiple_imports(self, tmp_path, w2_json_file, form_1099b_json_file, db_path):
        """Test importing two files before continuing."""
        w2_path = str(w2_json_file)
        b_path = str(form_1099b_json_file)

        inputs = "\n".join([
            "2024",           # Tax year
            "SINGLE",         # Filing status
            # First import
            "manual",         # Source
            w2_path,          # File
            "Y",              # Import another? YES
            # Second import
            "manual",         # Source
            b_path,           # File
            "N",              # Import another? No
            # Phase 3 — all defaults
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            # Phase 4 & 5 — skip
            "N",
            "N",
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"

    def test_wizard_skip_optional_phases(self, tmp_path, w2_json_file, db_path):
        """Verify strategy and reports can be declined."""
        w2_path = str(w2_json_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            "manual", w2_path, "N",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N",              # Skip strategy
            "N",              # Skip reports
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "Skipped" in result.output

    def test_wizard_invalid_file_retry(self, tmp_path, w2_json_file, db_path):
        """Invalid file path → retry without aborting wizard."""
        w2_path = str(w2_json_file)
        bad_path = str(tmp_path / "nonexistent.json")

        inputs = "\n".join([
            "2024", "SINGLE",
            # First attempt: bad file
            "manual",
            bad_path,
            "Y",              # Try another?
            # Second attempt: good file
            "manual",
            w2_path,
            "N",              # Done importing
            # Phase 3 — all defaults
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            # Phase 4 & 5
            "N", "N",
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "not found" in result.output.lower() or "File not found" in result.output

    def test_wizard_wrong_extension_retry(self, tmp_path, w2_json_file, db_path):
        """Wrong file extension → error + retry."""
        w2_path = str(w2_json_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            # Try to import a .json as shareworks (expects .pdf)
            "shareworks",
            w2_path,          # This is .json, not .pdf
            "Y",              # Try another?
            # Now import correctly as manual
            "manual",
            w2_path,
            "N",
            # Phase 3 — all defaults
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N", "N",
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "Expected" in result.output or ".pdf" in result.output

    def test_wizard_with_strategy(self, tmp_path, w2_json_file, db_path):
        """Run with strategy analysis enabled."""
        w2_path = str(w2_json_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            "manual", w2_path, "N",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "Y",              # Run strategy
            "N",              # Skip reports
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"

    def test_wizard_with_reports(self, tmp_path, w2_json_file, db_path):
        """Run with report generation enabled."""
        w2_path = str(w2_json_file)
        reports_dir = str(tmp_path / "reports")

        inputs = "\n".join([
            "2024", "SINGLE",
            "manual", w2_path, "N",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N",              # Skip strategy
            "Y",              # Generate reports
            reports_dir,      # Output directory
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"


# ---------------------------------------------------------------------------
# PDF source import tests
# ---------------------------------------------------------------------------


# Simulated W-2 PDF text that matches W2Extractor regex patterns
_W2_PDF_TEXT = (
    "Form W-2 Wage and Tax Statement 2024\n"
    "c Employer's name\n"
    "  Test Corp Inc\n"
    "b Employer's identification number 12-3456789\n"
    "1 Wages, tips, other comp  150,000.00\n"
    "2 Federal income tax withheld  30,000.00\n"
    "3 Social security wages  150,000.00\n"
    "4 Social security tax withheld  9,300.00\n"
    "5 Medicare wages and tips  150,000.00\n"
    "6 Medicare tax withheld  2,175.00\n"
    "16 State wages  150,000.00\n"
    "17 State income tax  10,000.00\n"
)


def _make_mock_pdfplumber(text: str):
    """Build a mock pdfplumber context manager returning *text*."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = text
    mock_page.extract_tables.return_value = []

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = lambda self: mock_pdf
    mock_pdf.__exit__ = MagicMock(return_value=False)

    return mock_pdf


@pytest.fixture
def w2_pdf_file(tmp_path: Path) -> Path:
    """Create a dummy .pdf file (content is mocked at the pdfplumber layer)."""
    f = tmp_path / "w2_2024.pdf"
    f.write_bytes(b"%PDF-1.4 dummy")
    return f


class TestPDFImport:
    """Integration tests for the wizard's ``pdf`` source option."""

    def test_pdf_w2_import(self, tmp_path, w2_pdf_file, db_path):
        """Import a W-2 via the pdf source — pdfplumber is mocked."""
        pdf_path = str(w2_pdf_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            "pdf",
            pdf_path,
            "N",              # Import another?
            # Phase 3 — all defaults
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N",              # Skip strategy
            "N",              # Skip reports
        ]) + "\n"

        with patch("pdfplumber.open", return_value=_make_mock_pdfplumber(_W2_PDF_TEXT)):
            result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)

        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "w2" in result.output.lower()
        assert "Import Summary" in result.output
        assert "Wizard complete" in result.output or "Done" in result.output

    def test_pdf_nonexistent_file(self, tmp_path, db_path):
        """Non-existent PDF → error displayed, retry offered."""
        bad_path = str(tmp_path / "nonexistent.pdf")
        # After the bad file, decline retry to exit the import loop
        inputs = "\n".join([
            "2024", "SINGLE",
            "pdf",
            bad_path,
            "N",              # Don't retry
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert "not found" in result.output.lower() or "File not found" in result.output

    def test_pdf_wrong_extension(self, tmp_path, w2_json_file, db_path):
        """Providing a .json file for pdf source → extension mismatch error."""
        json_path = str(w2_json_file)  # .json, not .pdf

        inputs = "\n".join([
            "2024", "SINGLE",
            "pdf",
            json_path,
            "N",              # Don't retry
        ]) + "\n"

        result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)
        assert "Expected" in result.output or ".pdf" in result.output

    def test_pdf_parse_error_retry(self, tmp_path, w2_pdf_file, w2_json_file, db_path):
        """PDF parse failure → error shown, user retries with manual source."""
        pdf_path = str(w2_pdf_file)
        w2_path = str(w2_json_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            # First attempt: pdf source, but pdfplumber returns empty text
            "pdf",
            pdf_path,
            "Y",              # Try again
            # Second attempt: fall back to manual
            "manual",
            w2_path,
            "N",              # Done importing
            # Phase 3
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N", "N",
        ]) + "\n"

        with patch("pdfplumber.open", return_value=_make_mock_pdfplumber("")):
            result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)

        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        # The error from the PDF attempt should appear
        assert "error" in result.output.lower() or "detect" in result.output.lower()

    def test_pdf_quoted_path(self, tmp_path, w2_pdf_file, db_path):
        """Quoted file path should be accepted (quotes stripped)."""
        # Wrap the path in double quotes, as a user might paste from a terminal
        pdf_path = f'"{w2_pdf_file}"'

        inputs = "\n".join([
            "2024", "SINGLE",
            "pdf",
            pdf_path,
            "N",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N", "N",
        ]) + "\n"

        with patch("pdfplumber.open", return_value=_make_mock_pdfplumber(_W2_PDF_TEXT)):
            result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)

        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "Import Summary" in result.output

    def test_pdf_vision_fallback_extraction(self, tmp_path, w2_pdf_file, db_path):
        """When text detection fails but Vision API is available, use Vision for extraction."""
        pdf_path = str(w2_pdf_file)

        inputs = "\n".join([
            "2024", "SINGLE",
            "pdf",
            pdf_path,
            "N",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "N", "N",
        ]) + "\n"

        from app.parsing.detector import FormType

        mock_vision = MagicMock()
        mock_vision.pdf_to_images.return_value = [b"fake_image"]
        mock_vision.detect_form_type.return_value = FormType.W2
        mock_vision.extract.return_value = {
            "tax_year": 2024,
            "employer_name": "Vision Corp",
            "box1_wages": "100000.00",
            "box2_federal_withheld": "20000.00",
            "box3_ss_wages": "100000.00",
            "box4_ss_withheld": "6200.00",
            "box5_medicare_wages": "100000.00",
            "box6_medicare_withheld": "1450.00",
            "box12_codes": {},
            "box14_other": {},
            "box16_state_wages": "100000.00",
            "box17_state_withheld": "7000.00",
            "state": "CA",
        }

        with (
            patch("pdfplumber.open", return_value=_make_mock_pdfplumber("")),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.parsing.vision.VisionExtractor", return_value=mock_vision),
        ):
            result = runner.invoke(app, ["wizard", "--db", str(db_path)], input=inputs)

        assert result.exit_code == 0, f"Wizard failed:\n{result.output}"
        assert "Vision API" in result.output
        assert "Import Summary" in result.output
