"""Read-only adapter for DrugAlpha valuation tracking data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from copy import deepcopy

from .io_utils import read_json


def valuation_source_from_selected_model(company_key: str, selected: dict[str, Any]) -> dict[str, Any]:
    """Map the server-selected webpage model into native DCF source fields.

    This internal adapter never selects an account, reads private history,
    merges platform values or interprets PS/PE as DCF cash flows.
    """
    if not isinstance(selected, dict) or selected.get("valuationSource") not in ("personal", "platform"):
        raise ValueError("Selected valuation source unavailable")
    company = selected.get("company")
    model = selected.get("model")
    if not isinstance(company, dict) or company.get("companyKey") != company_key or not isinstance(model, dict):
        raise ValueError("Selected valuation company mismatch")
    for field in ("domesticItems", "overseasItems"):
        if not isinstance(model.get(field), list) or any(not isinstance(item, dict) for item in model[field]):
            raise ValueError("Selected valuation model unavailable")
    if selected["valuationSource"] == "platform" and selected.get("personalSavedAt") is not None:
        raise ValueError("Selected valuation source inconsistent")
    return {
        "companyKey": company_key,
        "domesticPeakAssumptions": deepcopy(model["domesticItems"]),
        "overseasPeakAssumptions": deepcopy(model["overseasItems"]),
        "traditionalBusinessProfit": deepcopy(model.get("traditionalBusinessProfit")),
        "publicPayloadRow": None,
        "latestMarketCaps": deepcopy(company.get("listings") or []),
        "valuationSource": selected["valuationSource"],
        "personalSavedAt": selected.get("personalSavedAt"),
        "selectedReleaseId": selected.get("releaseId"),
    }


@dataclass(frozen=True)
class ValuationTrackingAdapter:
    valuation_dir: Path

    @property
    def data_dir(self) -> Path:
        from .io_utils import published_data_root
        return published_data_root('INNOVEST_VALUATION_RUNTIME_ROOT', self.valuation_dir / 'data')

    def get_company_valuation(self, company_key: str) -> dict[str, Any]:
        domestic = self._company_from_json("domestic_peak_assumptions.json", company_key)
        overseas = self._company_from_json("overseas_peak_assumptions.json", company_key)
        released_peak = self._latest_peak_snapshot_company(company_key)
        domestic_items = self._merge_released_product_metadata(
            list((domestic or {}).get("items", {}).values()),
            (released_peak or {}).get("domesticItems") or [],
        )
        overseas_items = self._merge_released_product_metadata(
            list((overseas or {}).get("items", {}).values()),
            (released_peak or {}).get("overseasItems") or [],
        )
        public_row = self._public_payload_row(company_key)
        listings = self._latest_market_caps(company_key)

        return {
            "companyKey": company_key,
            "domesticPeakAssumptions": domestic_items,
            "overseasPeakAssumptions": overseas_items,
            "traditionalBusinessProfit": (domestic or {}).get("traditionalBusinessProfit"),
            "publicPayloadRow": public_row,
            "latestMarketCaps": listings,
        }

    def _latest_peak_snapshot_company(self, company_key: str) -> dict[str, Any] | None:
        from .io_utils import published_data_root
        snapshot_root = published_data_root('INNOVEST_VALUATION_PEAK_SNAPSHOTS_ROOT', self.valuation_dir / 'data/peak_snapshots')
        candidates = sorted(snapshot_root.glob("*/valuation_peak_snapshot.json"), reverse=True)
        if not candidates:
            return None
        payload = read_json(candidates[0])
        for company in payload.get("companies") or []:
            if company.get("companyKey") == company_key:
                return company
        return None

    @staticmethod
    def _merge_released_product_metadata(
        assumptions: list[dict[str, Any]],
        released_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        released_by_key = {
            str(item.get("drugKey") or "").strip().lower(): item
            for item in released_items
            if item.get("drugKey")
        }
        released_by_master = {
            str(item.get("drugMasterId") or "").strip(): item
            for item in released_items
            if item.get("drugMasterId")
        }
        result = []
        for assumption in assumptions:
            item = dict(assumption)
            released = (
                released_by_master.get(str(item.get("drugMasterId") or "").strip())
                or released_by_key.get(str(item.get("drugKey") or "").strip().lower())
                or {}
            )
            for field in ("drugName", "target", "category", "categoryLabel", "developmentStage"):
                if released.get(field) is not None:
                    item[field] = released[field]
            result.append(item)
        return result

    def _company_from_json(self, filename: str, company_key: str) -> dict[str, Any] | None:
        from .io_utils import published_data_root
        payload = read_json(published_data_root('INNOVEST_VALUATION_ADMIN_ROOT', self.valuation_dir / 'data') / filename)
        return (payload.get("companies") or {}).get(company_key)

    def _public_payload_row(self, company_key: str) -> dict[str, Any] | None:
        from .io_utils import published_data_root
        path = published_data_root('INNOVEST_VALUATION_PUBLISHED_ROOT', self.valuation_dir / 'data/published') / 'public_payload.json'
        if not path.exists():
            return None
        payload = read_json(path)
        for row in payload.get("rows") or []:
            if row.get("companyKey") == company_key:
                return row
        return None

    def _latest_market_caps(self, company_key: str) -> list[dict[str, Any]]:
        db_path = self.data_dir / "valuation_tracking.db"
        if not db_path.exists():
            return []
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                select listing_id, company_key, company_name, market, market_label,
                       stock_code, latest_trading_date, latest_market_cap_cny_100m
                from valuation_listings
                where company_key = ?
                order by market, stock_code
                """,
                (company_key,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
