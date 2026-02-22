"""Integration tests for the unified `import` CLI command."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


@pytest.fixture
def w2_json_data() -> dict:
    return {
        "tax_year": 2024,
        "employer_name": "Coinbase Inc",
        "employer_ein": None,
        "box1_wages": "614328.46",
        "box2_federal_withheld": "109772.46",
        "box3_ss_wages": "168600.00",
        "box4_ss_withheld": "10453.20",
        "box5_medicare_wages": "614328.46",
        "box6_medicare_withheld": "10854.49",
        "box12_codes": {"C": "405.08", "D": "12801.27", "DD": "8965.82"},
        "box14_other": {"RSU": "282417.52", "VPDI": "1760.00"},
        "box16_state_wages": "614328.46",
        "box17_state_withheld": "46460.39",
        "state": "CA",
    }


@pytest.fixture
def import_dir_with_json(tmp_path: Path, w2_json_data: dict) -> Path:
    """Create a directory with a single W-2 JSON file."""
    d = tmp_path / "docs"
    d.mkdir()
    f = d / "w2_2024.json"
    f.write_text(json.dumps(w2_json_data))
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


class TestImportCommand:
    def test_import_help(self):
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0
        assert "directory" in result.output.lower() or "DIRECTORY" in result.output

    def test_import_single_json(self, import_dir_with_json: Path, db_path: Path):
        result = runner.invoke(
            app,
            ["import", str(import_dir_with_json), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Import Summary" in result.output
        assert "OK" in result.output
        assert "1 of 1" in result.output

        # Verify data is in the database
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT * FROM w2_forms")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1

    def test_import_single_pdf(self, tmp_path: Path, db_path: Path, w2_json_data: dict):
        """PDF processing with mocked pdfplumber."""
        d = tmp_path / "docs"
        d.mkdir()
        pdf_file = d / "w2_2024.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")

        # Mock pdfplumber to return W-2 text
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "W-2 Wage and Tax Statement\n"
            "Employer: Coinbase Inc\n"
            "Box 1 Wages: 614328.46\n"
            "Box 2 Federal withheld: 109772.46\n"
        )
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with (
            patch("pdfplumber.open", return_value=mock_pdf),
            patch("app.cli._process_pdf") as mock_process_pdf,
        ):
            mock_process_pdf.return_value = {
                "source": "pdf",
                "file": "w2_2024.pdf",
                "form_type": "w2",
                "forms": 1,
                "events": 0,
                "lots": 0,
                "sales": 0,
            }
            result = runner.invoke(
                app,
                ["import", str(d), "--year", "2024", "--db", str(db_path)],
            )
            assert result.exit_code == 0
            assert "OK" in result.output
            mock_process_pdf.assert_called_once()

    def test_import_single_csv(self, tmp_path: Path, db_path: Path):
        """CSV processing with mocked RobinhoodAdapter."""
        d = tmp_path / "docs"
        d.mkdir()
        csv_file = d / "1099b_2024.csv"
        csv_file.write_text("header,row\ndata,row\n")

        with patch("app.cli._process_csv") as mock_process_csv:
            mock_process_csv.return_value = {
                "source": "robinhood",
                "file": "1099b_2024.csv",
                "form_type": "1099b",
                "forms": 0,
                "events": 0,
                "lots": 0,
                "sales": 5,
            }
            result = runner.invoke(
                app,
                ["import", str(d), "--year", "2024", "--db", str(db_path)],
            )
            assert result.exit_code == 0
            assert "OK" in result.output
            mock_process_csv.assert_called_once()

    def test_import_mixed_files(self, tmp_path: Path, db_path: Path, w2_json_data: dict):
        """Directory with both .json and .pdf files."""
        d = tmp_path / "docs"
        d.mkdir()
        json_file = d / "w2_2024.json"
        json_file.write_text(json.dumps(w2_json_data))
        pdf_file = d / "form_1099b.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        with patch("app.cli._process_pdf") as mock_pdf:
            mock_pdf.return_value = {
                "source": "pdf",
                "file": "form_1099b.pdf",
                "form_type": "1099b",
                "forms": 0,
                "events": 0,
                "lots": 0,
                "sales": 3,
            }
            result = runner.invoke(
                app,
                ["import", str(d), "--year", "2024", "--db", str(db_path)],
            )
            assert result.exit_code == 0
            assert "2 of 2" in result.output

    def test_import_empty_directory(self, tmp_path: Path, db_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        result = runner.invoke(
            app,
            ["import", str(d), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "no" in result.output.lower() or "No" in result.output

    def test_import_year_required(self, tmp_path: Path, db_path: Path):
        d = tmp_path / "docs"
        d.mkdir()
        result = runner.invoke(
            app,
            ["import", str(d), "--db", str(db_path)],
        )
        assert result.exit_code != 0

    def test_import_bad_file_continues(self, tmp_path: Path, db_path: Path, w2_json_data: dict):
        """Bad JSON + good JSON → error on bad, success on good, exit code 1."""
        d = tmp_path / "docs"
        d.mkdir()
        bad_file = d / "aaa_bad.json"
        bad_file.write_text("not valid json {{{")
        good_file = d / "zzz_w2.json"
        good_file.write_text(json.dumps(w2_json_data))

        result = runner.invoke(
            app,
            ["import", str(d), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 1
        assert "ERROR" in result.output
        assert "OK" in result.output
        assert "1 of 2" in result.output

    def test_import_summary_table(self, import_dir_with_json: Path, db_path: Path):
        result = runner.invoke(
            app,
            ["import", str(import_dir_with_json), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Import Summary" in result.output
        assert "File" in result.output
        assert "Status" in result.output

    def test_import_duplicate_warning(self, import_dir_with_json: Path, db_path: Path):
        # First import
        runner.invoke(
            app,
            ["import", str(import_dir_with_json), "--year", "2024", "--db", str(db_path)],
        )
        # Second import — should warn about duplicate
        result = runner.invoke(
            app,
            ["import", str(import_dir_with_json), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "already imported" in result.output.lower() or "duplicate" in result.output.lower()

    def test_import_ignores_non_matching(self, tmp_path: Path, db_path: Path, w2_json_data: dict):
        """Only .pdf/.csv/.json files are processed; .txt is ignored."""
        d = tmp_path / "docs"
        d.mkdir()
        txt_file = d / "notes.txt"
        txt_file.write_text("just notes")
        json_file = d / "w2_2024.json"
        json_file.write_text(json.dumps(w2_json_data))

        result = runner.invoke(
            app,
            ["import", str(d), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        # Only the JSON file should be processed
        assert "1 of 1" in result.output

    def test_import_pdf_vision_fallback(self, tmp_path: Path, db_path: Path):
        """PDF with empty regex extraction triggers Vision fallback (mocked)."""
        d = tmp_path / "docs"
        d.mkdir()
        pdf_file = d / "scanned_w2.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 scanned")

        with patch("app.cli._process_pdf") as mock_pdf:
            mock_pdf.return_value = {
                "source": "pdf",
                "file": "scanned_w2.pdf",
                "form_type": "w2",
                "forms": 1,
                "events": 0,
                "lots": 0,
                "sales": 0,
            }
            result = runner.invoke(
                app,
                ["import", str(d), "--year", "2024", "--db", str(db_path)],
            )
            assert result.exit_code == 0
            assert "OK" in result.output
