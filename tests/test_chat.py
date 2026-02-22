"""Tests for the CPA expert chat module."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.chat import build_system_prompt
from app.cli import app
from app.db.repository import TaxRepository
from app.db.schema import create_schema
from app.models.tax_forms import W2


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    conn = create_schema(db_path)
    return conn


@pytest.fixture
def repo(test_db):
    """Return a TaxRepository backed by the test database."""
    return TaxRepository(test_db)


class TestBuildSystemPrompt:
    def test_with_w2_data(self, repo):
        """System prompt includes W-2 employer name and wages."""
        w2 = W2(
            employer_name="Acme Corp",
            tax_year=2024,
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("45000"),
            box12_codes={},
            box14_other={},
            box16_state_wages=Decimal("200000"),
            box17_state_withheld=Decimal("18000"),
        )
        batch_id = repo.create_import_batch(
            source="manual",
            tax_year=2024,
            file_path="test.json",
            form_type="w2",
        )
        repo.save_w2(w2, batch_id)

        prompt = build_system_prompt(repo, 2024)

        assert "Acme Corp" in prompt
        assert "200,000.00" in prompt
        assert "Senior Certified Public Accountant" in prompt

    def test_empty_db(self, repo):
        """System prompt builds correctly with no data imported."""
        prompt = build_system_prompt(repo, 2024)

        assert "Senior Certified Public Accountant" in prompt
        assert "W-2 Forms: None imported" in prompt
        assert "Sale Results: Reconciliation not yet run" in prompt
        assert "Lots: None imported" in prompt
        assert "Equity Events: None imported" in prompt
        assert "Reconciliation: Not yet run" in prompt


    def test_build_system_prompt_includes_estimate(self, repo):
        """System prompt includes computed tax estimate when W-2 data exists."""
        w2 = W2(
            employer_name="Acme Corp",
            tax_year=2024,
            box1_wages=Decimal("200000"),
            box2_federal_withheld=Decimal("45000"),
            box12_codes={},
            box14_other={},
            box16_state_wages=Decimal("200000"),
            box17_state_withheld=Decimal("18000"),
        )
        batch_id = repo.create_import_batch(
            source="manual",
            tax_year=2024,
            file_path="test.json",
            form_type="w2",
        )
        repo.save_w2(w2, batch_id)

        prompt = build_system_prompt(repo, 2024)

        assert "Computed Tax Estimate" in prompt
        assert "Total Tax" in prompt
        assert "Balance Due" in prompt
        assert "Federal Balance Due" in prompt
        assert "CA Balance Due" in prompt
        assert "filing status: SINGLE" in prompt

    def test_build_system_prompt_no_estimate_when_empty(self, repo):
        """System prompt omits tax estimate when no data exists."""
        prompt = build_system_prompt(repo, 2024)

        assert "Computed Tax Estimate" not in prompt


class TestChatCommand:
    def test_no_api_key(self, tmp_path):
        """Chat command exits with error when ANTHROPIC_API_KEY is not set."""
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        with patch.dict("os.environ", {}, clear=True):
            # Ensure key is unset
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)

            result = runner.invoke(app, ["chat", "--db", str(db_path)])

        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output or "ANTHROPIC_API_KEY" in (result.stderr or "")

    def test_exit_immediately(self, tmp_path):
        """Chat loop terminates cleanly on 'exit' input."""
        db_path = tmp_path / "test.db"
        create_schema(db_path).close()

        mock_client = MagicMock()
        mock_console = MagicMock()
        # Simulate user typing "exit"
        mock_console.input.return_value = "exit"

        from app.chat import run_chat

        run_chat(mock_console, mock_client, "claude-sonnet-4-20250514", "You are a CPA.")

        # Should not have called the API
        mock_client.messages.stream.assert_not_called()
        # Should have printed goodbye
        mock_console.print.assert_any_call("\n[dim]Goodbye.[/dim]")

    def test_single_exchange(self, tmp_path):
        """Chat loop handles one question then exit."""
        mock_client = MagicMock()
        mock_console = MagicMock()

        # First call returns a question, second call returns "exit"
        mock_console.input.side_effect = ["What is an RSU?", "exit"]

        # Mock the streaming context manager
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["An RSU is ", "a restricted stock unit."])
        mock_client.messages.stream.return_value = mock_stream

        from app.chat import run_chat

        run_chat(mock_console, mock_client, "claude-sonnet-4-20250514", "You are a CPA.")

        # API was called once
        mock_client.messages.stream.assert_called_once()
        call_kwargs = mock_client.messages.stream.call_args
        assert call_kwargs.kwargs["messages"][0]["content"] == "What is an RSU?"

    def test_ctrl_c_exits_cleanly(self, tmp_path):
        """Chat loop handles KeyboardInterrupt gracefully."""
        mock_client = MagicMock()
        mock_console = MagicMock()
        mock_console.input.side_effect = KeyboardInterrupt()

        from app.chat import run_chat

        run_chat(mock_console, mock_client, "claude-sonnet-4-20250514", "You are a CPA.")

        mock_console.print.assert_any_call("\n[dim]Goodbye.[/dim]")

    def test_eof_exits_cleanly(self, tmp_path):
        """Chat loop handles EOFError (Ctrl-D) gracefully."""
        mock_client = MagicMock()
        mock_console = MagicMock()
        mock_console.input.side_effect = EOFError()

        from app.chat import run_chat

        run_chat(mock_console, mock_client, "claude-sonnet-4-20250514", "You are a CPA.")

        mock_console.print.assert_any_call("\n[dim]Goodbye.[/dim]")
