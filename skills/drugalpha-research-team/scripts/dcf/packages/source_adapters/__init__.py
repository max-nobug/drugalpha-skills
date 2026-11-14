"""Read-only adapters for DrugAlpha source data."""

from .snapshot_builder import build_company_source_snapshot

__all__ = ["build_company_source_snapshot"]
