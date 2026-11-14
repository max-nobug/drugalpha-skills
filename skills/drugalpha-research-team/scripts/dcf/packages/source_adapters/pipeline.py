"""Read-only adapter for DrugAlpha pipeline tracking published data."""

from __future__ import annotations

import re
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.dcf_core.launch_timing import extract_status_start_date_from_identity
from packages.dcf_core.phase_rules import is_marketed_phase, is_nda_bla_phase

from .io_utils import read_json


def _identity_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


@dataclass(frozen=True)
class PipelineAdapter:
    pipeline_dir: Path

    @property
    def published_dir(self) -> Path:
        from .io_utils import published_data_root
        return published_data_root('INNOVEST_PIPELINE_PUBLISHED_ROOT', self.pipeline_dir / 'data/published')

    def list_companies(self) -> list[dict[str, Any]]:
        payload = read_json(self.published_dir / "public_companies.json")
        return payload.get("items", [])

    def get_company_payload(self, company_key: str) -> dict[str, Any]:
        filename = urllib.parse.quote(company_key) + ".json"
        return read_json(self.published_dir / "public_company_payloads" / filename)

    def get_company_drug_identities(self, company_key: str) -> dict[str, dict[str, str]]:
        registry_path = self.pipeline_dir / "data" / "drug_master_registry.json"
        if not registry_path.exists():
            return {}
        result: dict[str, dict[str, str]] = {}
        name_matches: dict[str, list[dict[str, str]]] = defaultdict(list)
        for master in read_json(registry_path).get("items") or []:
            master_id = str(master.get("drugMasterId") or "").strip()
            if not master_id:
                continue
            for link in master.get("companyLinks") or []:
                if str(link.get("companyKey") or "").strip() != company_key:
                    continue
                identity = {
                    "drugMasterId": master_id,
                    "drugKey": str(link.get("drugKey") or "").strip(),
                    "category": str(link.get("category") or "").strip(),
                }
                name = _identity_token(link.get("drugName"))
                target = _identity_token(link.get("target"))
                if name:
                    result[f"{name}::{target}"] = identity
                    name_matches[name].append(identity)
        for name, matches in name_matches.items():
            unique = {item["drugMasterId"]: item for item in matches}
            if len(unique) == 1:
                result[name] = next(iter(unique.values()))
        return result

    def get_company_drug_categories(self, company_key: str) -> dict[str, str]:
        """Read the published company drug classifications without mutating them.

        The public pipeline row payload intentionally contains trial fields only.
        Product category (for example ``biosimilar``) lives in the pipeline
        company drug registry, so DCF must bring it into its own snapshot before
        deciding whether a product belongs in the simplified-growth branch.
        """
        registry_path = self.pipeline_dir / "data" / "admin_registry.json"
        if not registry_path.exists():
            return {}
        config = (read_json(registry_path).get("companyDrugConfigs") or {}).get(company_key) or {}
        categories: dict[str, str] = {}
        for item in config.get("items") or []:
            key = str(item.get("drugMasterId") or item.get("drugKey") or "").strip().lower()
            category = str(item.get("category") or "").strip()
            if key and category:
                categories[key] = category
        return categories

    def normalize_products(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        company_key = str((payload.get("company") or {}).get("companyKey") or "")
        identities = self.get_company_drug_identities(company_key)
        category_by_identity = self.get_company_drug_categories(company_key)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in payload.get("publishedRows", []):
            drug_name = row.get("drugName")
            if not drug_name:
                continue
            identity = identities.get(
                f"{_identity_token(drug_name)}::{_identity_token(row.get('target'))}"
            ) or identities.get(_identity_token(drug_name)) or {}
            row_with_identity = {
                **row,
                "drugMasterId": row.get("drugMasterId") or identity.get("drugMasterId"),
            }
            key = str(row_with_identity.get("drugMasterId") or drug_name).lower()
            groups[key].append(row_with_identity)

        products = []
        for rows in groups.values():
            first = rows[0]
            product_key = f"{first.get('drugName')}::{first.get('target') or ''}".lower()
            drug_master_id = first.get("drugMasterId")
            indications = []
            for row in rows:
                latest_progress = _latest_progress(row.get("progressUpdates") or [])
                phase = row.get("phase")
                status_start_date = (
                    row.get("statusStartDate")
                    or extract_status_start_date_from_identity(row.get("rowIdentity"))
                )
                indications.append(
                    {
                        "sourceRowIdentity": row.get("rowIdentity"),
                        "rowOrder": row.get("rowOrder"),
                        "rowType": row.get("rowType"),
                        "indicationText": row.get("indication"),
                        "phase": phase,
                        "isDcfMaturePhase": _is_dcf_mature_phase(phase),
                        "statusStartDate": status_start_date,
                        "comparator": row.get("comparator"),
                        "primaryEndpoint": row.get("primaryEndpoint"),
                        "enrollmentText": row.get("enrollmentText"),
                        "trialRegNo": row.get("trialRegNo"),
                        "sponsor": row.get("sponsor"),
                        "fieldArea": row.get("fieldArea"),
                        "latestProgress": latest_progress,
                        "sourceLinks": (row.get("rawPayload") or {}).get("__links__", {}),
                    }
                )

            products.append(
                {
                    "drugMasterId": drug_master_id,
                    "productKey": product_key,
                    "drugName": first.get("drugName"),
                    "target": first.get("target"),
                    "category": category_by_identity.get(str(drug_master_id or product_key).lower()),
                    "indications": indications,
                }
            )
        return products


def _latest_progress(progress_updates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in progress_updates:
        if item.get("value"):
            return item
    return None


def _is_dcf_mature_phase(phase: Any) -> bool:
    value = str(phase or "").strip()
    if not value:
        return False
    upper = re.sub(r"\s+", "", value.upper())
    if is_marketed_phase(value) or is_nda_bla_phase(value):
        return True
    return bool(
        re.search(r"(^|[^0-9])3期($|[^0-9])", upper)
        or "III期" in upper
        or re.search(r"PHASE(?:3|III)($|[^A-Z0-9])", upper)
    )
