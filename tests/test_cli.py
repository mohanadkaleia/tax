"""Tests for CLI commands."""

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


class TestCLI:
    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "TaxBot" in result.output

    def test_import_help(self):
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0

    def test_reconcile_help(self):
        result = runner.invoke(app, ["reconcile", "--help"])
        assert result.exit_code == 0

    def test_estimate_help(self):
        result = runner.invoke(app, ["estimate", "--help"])
        assert result.exit_code == 0

    def test_strategy_help(self):
        result = runner.invoke(app, ["strategy", "--help"])
        assert result.exit_code == 0

    def test_report_help(self):
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
