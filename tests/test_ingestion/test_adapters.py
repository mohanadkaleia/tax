"""Tests for ingestion adapters."""

import pytest

from app.ingestion.manual import ManualAdapter
from app.ingestion.robinhood import RobinhoodAdapter
from app.ingestion.shareworks import ShareworksAdapter


class TestShareworksAdapter:
    def test_adapter_exists(self):
        adapter = ShareworksAdapter()
        assert adapter is not None

    def test_parse_nonexistent_file(self, tmp_path):
        adapter = ShareworksAdapter()
        with pytest.raises(Exception):
            adapter.parse(tmp_path / "dummy.pdf")


class TestRobinhoodAdapter:
    def test_adapter_exists(self):
        adapter = RobinhoodAdapter()
        assert adapter is not None

    def test_parse_not_implemented(self, tmp_path):
        adapter = RobinhoodAdapter()
        with pytest.raises(NotImplementedError):
            adapter.parse(tmp_path / "dummy.csv")


class TestManualAdapter:
    def test_adapter_exists(self):
        adapter = ManualAdapter()
        assert adapter is not None

    def test_parse_file_not_found(self, tmp_path):
        adapter = ManualAdapter()
        with pytest.raises(FileNotFoundError):
            adapter.parse(tmp_path / "dummy.json")
