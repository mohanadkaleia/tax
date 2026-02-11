"""Ingestion adapters for importing tax data from various sources."""

from app.ingestion.base import BaseAdapter
from app.ingestion.manual import ManualAdapter
from app.ingestion.robinhood import RobinhoodAdapter
from app.ingestion.shareworks import ShareworksAdapter

__all__ = ["BaseAdapter", "ManualAdapter", "RobinhoodAdapter", "ShareworksAdapter"]
