"""Integration tests for the import-data CLI command."""

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


@pytest.fixture
def w2_json_file(tmp_path: Path) -> Path:
    data = {
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
    f = tmp_path / "w2_2024.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture
def form_3921_json_file(tmp_path: Path) -> Path:
    data = [
        {
            "tax_year": 2024,
            "corporation_name": "Acme Corp",
            "grant_date": "2022-01-15",
            "exercise_date": "2024-03-01",
            "exercise_price_per_share": "50.00",
            "fmv_on_exercise_date": "120.00",
            "shares_transferred": 200,
        }
    ]
    f = tmp_path / "3921_2024.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


class TestImportCLI:
    def test_import_help(self):
        result = runner.invoke(app, ["import-data", "--help"])
        assert result.exit_code == 0
        assert "manual" in result.output.lower() or "source" in result.output.lower()

    def test_import_w2_json(self, w2_json_file: Path, db_path: Path):
        result = runner.invoke(
            app,
            ["import-data", "manual", str(w2_json_file), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Imported" in result.output
        assert "Coinbase" in result.output
        assert "2024" in result.output

        # Verify data is in the database
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT * FROM w2_forms")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1

    def test_import_3921_json(self, form_3921_json_file: Path, db_path: Path):
        result = runner.invoke(
            app,
            ["import-data", "manual", str(form_3921_json_file), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Imported" in result.output
        assert "event" in result.output.lower() or "lot" in result.output.lower()

        # Verify events and lots in DB
        conn = sqlite3.connect(str(db_path))
        events = conn.execute("SELECT * FROM equity_events").fetchall()
        lots = conn.execute("SELECT * FROM lots").fetchall()
        conn.close()
        assert len(events) == 1
        assert len(lots) == 1

    def test_import_invalid_json(self, tmp_path: Path, db_path: Path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")
        result = runner.invoke(
            app,
            ["import-data", "manual", str(bad_file), "--db", str(db_path)],
        )
        assert result.exit_code == 1

    def test_import_nonexistent_file(self, db_path: Path):
        result = runner.invoke(
            app,
            ["import-data", "manual", "/nonexistent/missing.json", "--db", str(db_path)],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_import_duplicate_warning(self, w2_json_file: Path, db_path: Path):
        # First import
        runner.invoke(
            app,
            ["import-data", "manual", str(w2_json_file), "--year", "2024", "--db", str(db_path)],
        )
        # Second import — should warn about duplicate
        result = runner.invoke(
            app,
            ["import-data", "manual", str(w2_json_file), "--year", "2024", "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "already imported" in result.output.lower() or "duplicate" in result.output.lower()

    def test_import_non_json_file(self, tmp_path: Path, db_path: Path):
        txt_file = tmp_path / "data.txt"
        txt_file.write_text("not json")
        result = runner.invoke(
            app,
            ["import-data", "manual", str(txt_file), "--db", str(db_path)],
        )
        assert result.exit_code == 1
        assert "JSON" in result.output

    def test_import_unknown_source(self, w2_json_file: Path, db_path: Path):
        result = runner.invoke(
            app,
            ["import-data", "fidelity", str(w2_json_file), "--db", str(db_path)],
        )
        assert result.exit_code == 1
        assert "Unknown source" in result.output
