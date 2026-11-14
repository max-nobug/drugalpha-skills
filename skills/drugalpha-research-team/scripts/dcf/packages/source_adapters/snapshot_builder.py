"""Build DCF-owned source snapshots from read-only DrugAlpha data."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SourcePaths
from .disease_knowledge import DiseaseKnowledgeAdapter
from .io_utils import slugify_for_file, write_json
from .indication_facets import PublishedIndicationFacetsAdapter
from .pipeline import PipelineAdapter
from .valuation_tracking import ValuationTrackingAdapter, valuation_source_from_selected_model
from ..dcf_core.product_modeling import classify_product_modeling

_USE_PLATFORM_SOURCE = object()


def build_company_source_snapshot(
    company_key: str,
    *,
    paths: SourcePaths | None = None,
    write_cache: bool = False,
    selected_valuation: Any = _USE_PLATFORM_SOURCE,
) -> dict[str, Any]:
    paths = paths or SourcePaths.from_env()
    paths.validate_readonly_sources()

    pipeline = PipelineAdapter(paths.pipeline_dir)
    disease = DiseaseKnowledgeAdapter(paths.disease_knowledge_dir)
    valuation = ValuationTrackingAdapter(paths.valuation_dir)
    indication_facets = PublishedIndicationFacetsAdapter(paths.pipeline_dir)

    payload = pipeline.get_company_payload(company_key)
    company = payload.get("company") or {}
    products = pipeline.normalize_products(payload)
    facets_by_row_identity = indication_facets.get_company_facets(company_key)
    for product in products:
        for indication in product.get("indications") or []:
            indication["indicationFacets"] = dict(
                facets_by_row_identity.get(str(indication.get("sourceRowIdentity") or "")) or {}
            )
    valuation_source = (
        valuation_source_from_selected_model(company_key, selected_valuation)
        if selected_valuation is not _USE_PLATFORM_SOURCE else valuation.get_company_valuation(company_key)
    )
    _enrich_valuation_names_from_pipeline(valuation_source, products)
    valuation_by_drug_key = _valuation_by_drug_key(valuation_source)

    warnings = []
    matched_diseases: dict[str, dict[str, Any]] = {}

    for product in products:
        product["valuationTracking"] = _match_product_valuation(product, valuation_by_drug_key)
        _attach_valuation_display_metadata(product, product["valuationTracking"])
        product["isValuationTrackingProduct"] = bool(product["valuationTracking"].get("matchedDrugKey"))
        product["isMaturePipelineProduct"] = any(
            indication.get("isDcfMaturePhase") for indication in product.get("indications") or []
        )
        product["isDcfSourceProduct"] = bool(
            product["isValuationTrackingProduct"]
        )
        product["dcfSourceKinds"] = [
            source_kind
            for source_kind, included in (
                ("valuation_tracking", product["isValuationTrackingProduct"]),
                ("mature_pipeline", product["isMaturePipelineProduct"]),
            )
            if included
        ]
        modeling_mode, modeling_reason = classify_product_modeling(
            drug_name=product.get("drugName"),
            target=product.get("target"),
            drug_key=product["valuationTracking"].get("matchedDrugKey"),
            category=product.get("category") or _valuation_product_category(product["valuationTracking"]),
        )
        product["modelingMode"] = modeling_mode
        product["modelingReason"] = modeling_reason
        for indication in product["indications"]:
            if modeling_mode == "simplified_growth":
                indication["diseaseMatch"] = {"diseaseKey": None, "confidence": 0}
                continue
            match = disease.match_indication(indication.get("indicationText"))
            indication["diseaseMatch"] = match
            if not product["isDcfSourceProduct"]:
                continue
            if match.get("diseaseKey"):
                disease_key = match["diseaseKey"]
                if disease_key not in matched_diseases:
                    epi = disease.get_epidemiology(disease_key)
                    if epi:
                        matched_diseases[disease_key] = epi
                    else:
                        warnings.append(
                            _warning(
                                "MISSING_EPIDEMIOLOGY",
                                f"DrugAlpha 当前未找到患者流行病学数据，请手动补充：{indication.get('indicationText')}",
                                product.get("drugName"),
                                indication.get("indicationText"),
                            )
                        )
            else:
                warnings.append(
                    _warning(
                        "LOW_DISEASE_MATCH_CONFIDENCE",
                        f"适应症与疾病知识库匹配置信度较低，请确认疾病口径：{indication.get('indicationText')}",
                        product.get("drugName"),
                        indication.get("indicationText"),
                    )
                )

    # Only products with an explicitly configured positive peak-sales anchor enter
    # the DCF. Mature pipeline rows without a peak anchor remain upstream-only and
    # are not shown as zero-value DCF assets.
    products = _ensure_valuation_tracking_products(products, valuation_by_drug_key)
    for product in products:
        if not product.get("modelingMode"):
            modeling_mode, modeling_reason = classify_product_modeling(
                drug_name=product.get("drugName"),
                target=product.get("target"),
                drug_key=(product.get("valuationTracking") or {}).get("matchedDrugKey"),
                category=_valuation_product_category(product.get("valuationTracking") or {}),
            )
            product["modelingMode"] = modeling_mode
            product["modelingReason"] = modeling_reason
        if product.get("valuationOnly"):
            warnings.append(
                _warning(
                    "VALUATION_ASSET_PIPELINE_MATCH_PENDING",
                    f"估值跟踪资产尚未匹配到已发布管线，已作为待补充资产保留：{product.get('drugName')}",
                    product.get("drugName"),
                    None,
                )
            )

    snapshot_id = f"source_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{slugify_for_file(company_key)}"
    snapshot = {
        "snapshotId": snapshot_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "readOnlySources": {
            "pipelineDir": str(paths.pipeline_dir),
            "diseaseKnowledgeDir": str(paths.disease_knowledge_dir),
            "valuationDir": str(paths.valuation_dir),
            "publishedDiseaseStandardPointer": str(indication_facets.index_path),
        },
        "company": {
            "companyKey": company.get("companyKey") or company_key,
            "companyName": company.get("companyName") or company_key,
            "stockCodes": company.get("stockCodes"),
            "latestPublishedAt": company.get("latestPublishedAt"),
            "potentialCatalystsText": payload.get("potentialCatalystsText"),
        },
        "pipeline": {
            "publishedBatch": payload.get("publishedBatch"),
            "products": products,
        },
        "diseaseKnowledge": {
            "matchedDiseases": list(matched_diseases.values()),
        },
        "valuationTracking": valuation_source,
        "warnings": warnings,
        "disclaimer": "当前模型由AI程序生成，不代表DrugAlpha数据库估值，请根据实际情况自行调整参数确定估值。",
    }

    if write_cache:
        cache_path = paths.source_snapshot_cache_dir / f"{snapshot_id}.json"
        write_json(cache_path, snapshot)
        snapshot["cachePath"] = str(cache_path)

    return snapshot


def _valuation_by_drug_key(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in source.get("domesticPeakAssumptions") or []:
        if not _is_positive_included_peak(item, "estimatedPeakSales"):
            continue
        key = str(item.get("drugMasterId") or item.get("drugKey") or "").lower()
        result.setdefault(key, {})["domestic"] = item
    for item in source.get("overseasPeakAssumptions") or []:
        if not _is_positive_included_peak(item, "estimatedPeakSalesUsd"):
            continue
        key = str(item.get("drugMasterId") or item.get("drugKey") or "").lower()
        result.setdefault(key, {})["overseas"] = item
    return {
        key: item
        for key, item in result.items()
        if _has_configured_peak_sales(item)
    }


def _has_configured_peak_sales(item: dict[str, Any]) -> bool:
    domestic = item.get("domestic") or {}
    overseas = item.get("overseas") or {}
    return _is_positive_included_peak(domestic, "estimatedPeakSales") or _is_positive_included_peak(
        overseas,
        "estimatedPeakSalesUsd",
    )


def _is_positive_included_peak(source: dict[str, Any], field: str) -> bool:
    value = source.get(field)
    return (
        source.get("includedInValuation") is True
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    )


def _match_product_valuation(
    product: dict[str, Any],
    valuation_by_drug_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    product_key = product.get("productKey") or ""
    product_master_id = str(product.get("drugMasterId") or "").lower()
    if product_master_id and product_master_id in valuation_by_drug_key:
        matched = valuation_by_drug_key[product_master_id]
        return {
            "matchedDrugMasterId": product_master_id,
            "matchedDrugKey": _valuation_legacy_key(matched),
            "domestic": matched.get("domestic"),
            "overseas": matched.get("overseas"),
            "matchMethod": "exact_drug_master_id",
        }
    product_name = _normalize_drug_identity(product.get("drugName"))
    for key, matched in valuation_by_drug_key.items():
        valuation_name = _valuation_display_name(matched)
        if product_name and product_name == _normalize_drug_identity(valuation_name):
            return {
                "matchedDrugMasterId": _valuation_master_id(matched),
                "matchedDrugKey": _valuation_legacy_key(matched) or key,
                "drugName": valuation_name,
                "domestic": matched.get("domestic"),
                "overseas": matched.get("overseas"),
                "matchMethod": "exact_valuation_drug_name",
            }

    if product_key in valuation_by_drug_key:
        matched = valuation_by_drug_key[product_key]
        return {
            "matchedDrugMasterId": _valuation_master_id(matched),
            "matchedDrugKey": product_key,
            "domestic": matched.get("domestic"),
            "overseas": matched.get("overseas"),
            "matchMethod": "exact_product_key",
        }

    product_drug_key = product_key.partition("::")[0]
    normalized_product_drug_key = _normalize_drug_identity(product_drug_key)
    for key, matched in valuation_by_drug_key.items():
        legacy_key = _valuation_legacy_key(matched) or key
        valuation_drug_key = legacy_key.partition("::")[0]
        if (
            normalized_product_drug_key
            and normalized_product_drug_key == _normalize_drug_identity(valuation_drug_key)
        ):
            return {
                "matchedDrugMasterId": _valuation_master_id(matched),
                "matchedDrugKey": legacy_key,
                "domestic": matched.get("domestic"),
                "overseas": matched.get("overseas"),
                "matchMethod": "exact_drug_alias_key",
            }

    product_aliases = _drug_identity_aliases(product_drug_key, product.get("drugName"))
    for key, matched in valuation_by_drug_key.items():
        legacy_key = _valuation_legacy_key(matched) or key
        valuation_drug_key = legacy_key.partition("::")[0]
        if _normalize_drug_identity(valuation_drug_key) in product_aliases:
            return {
                "matchedDrugMasterId": _valuation_master_id(matched),
                "matchedDrugKey": legacy_key,
                "domestic": matched.get("domestic"),
                "overseas": matched.get("overseas"),
                "matchMethod": "exact_drug_alias_token",
            }

    drug = (product.get("drugName") or "").split("/")[0].lower()
    target = (product.get("target") or "").lower()
    for key, matched in valuation_by_drug_key.items():
        legacy_key = _valuation_legacy_key(matched) or key
        if drug and drug in legacy_key and (not target or target in legacy_key or legacy_key.split("::")[-1] in target):
            return {
                "matchedDrugMasterId": _valuation_master_id(matched),
                "matchedDrugKey": legacy_key or key,
                "domestic": matched.get("domestic"),
                "overseas": matched.get("overseas"),
                "matchMethod": "drug_target_contains",
            }

    return {
        "matchedDrugMasterId": None,
        "matchedDrugKey": None,
        "domestic": None,
        "overseas": None,
        "matchMethod": None,
    }


def _valuation_master_id(tracked_assumptions: dict[str, Any]) -> str | None:
    domestic = tracked_assumptions.get("domestic") or {}
    overseas = tracked_assumptions.get("overseas") or {}
    return domestic.get("drugMasterId") or overseas.get("drugMasterId")


def _valuation_legacy_key(tracked_assumptions: dict[str, Any]) -> str:
    domestic = tracked_assumptions.get("domestic") or {}
    overseas = tracked_assumptions.get("overseas") or {}
    return str(domestic.get("drugKey") or overseas.get("drugKey") or "").lower()


def _normalize_drug_identity(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _drug_identity_aliases(*values: str | None) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        normalized = unicodedata.normalize("NFKC", value or "")
        for token in re.split(r"[/\\|、,，;；]+", normalized):
            identity = _normalize_drug_identity(token)
            if identity:
                aliases.add(identity)
    return aliases


def _enrich_valuation_names_from_pipeline(
    valuation_source: dict[str, Any],
    products: list[dict[str, Any]],
) -> None:
    for market_key in ("domesticPeakAssumptions", "overseasPeakAssumptions"):
        for assumption in valuation_source.get(market_key) or []:
            if assumption.get("drugName"):
                continue
            valuation_drug_key = str(assumption.get("drugKey") or "").partition("::")[0]
            valuation_identity = _normalize_drug_identity(valuation_drug_key)
            if not valuation_identity:
                continue
            candidates = [
                product
                for product in products
                if valuation_identity in _drug_identity_aliases(
                    str(product.get("productKey") or "").partition("::")[0],
                    product.get("drugName"),
                )
            ]
            if len(candidates) != 1:
                continue
            assumption["drugName"] = candidates[0].get("drugName")
            assumption["target"] = candidates[0].get("target")


def _valuation_display_name(tracked_assumptions: dict[str, Any]) -> str | None:
    domestic = tracked_assumptions.get("domestic") or {}
    overseas = tracked_assumptions.get("overseas") or {}
    return domestic.get("drugName") or overseas.get("drugName")


def _valuation_product_category(tracked_assumptions: dict[str, Any]) -> str | None:
    domestic = tracked_assumptions.get("domestic") or {}
    overseas = tracked_assumptions.get("overseas") or {}
    return domestic.get("category") or overseas.get("category")


def _attach_valuation_display_metadata(
    product: dict[str, Any],
    valuation_tracking: dict[str, Any],
) -> None:
    drug_name = product.get("drugName")
    target = product.get("target")
    if drug_name:
        valuation_tracking["drugName"] = drug_name
    if target:
        valuation_tracking["target"] = target
    for market in ("domestic", "overseas"):
        assumption = valuation_tracking.get(market)
        if not isinstance(assumption, dict):
            continue
        if drug_name:
            assumption["drugName"] = drug_name
        if target:
            assumption["target"] = target


def _ensure_valuation_tracking_products(
    products: list[dict[str, Any]],
    valuation_by_drug_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_keys = {
        product.get("valuationTracking", {}).get("matchedDrugMasterId")
        or product.get("valuationTracking", {}).get("matchedDrugKey")
        for product in products
        if product.get("valuationTracking", {}).get("matchedDrugKey")
    }
    for identity_key, tracked_assumptions in valuation_by_drug_key.items():
        if identity_key in matched_keys:
            continue
        drug_key = _valuation_legacy_key(tracked_assumptions) or identity_key
        drug_master_id = _valuation_master_id(tracked_assumptions)
        key_drug_name, _, key_target = drug_key.partition("::")
        domestic = tracked_assumptions.get("domestic") or {}
        overseas = tracked_assumptions.get("overseas") or {}
        drug_name = domestic.get("drugName") or overseas.get("drugName") or key_drug_name or drug_key
        target = domestic.get("target") or overseas.get("target") or key_target or None
        products.append({
            "drugMasterId": drug_master_id,
            "productKey": drug_key,
            "drugName": drug_name,
            "target": target,
            "isValuationTrackingProduct": True,
            "isMaturePipelineProduct": False,
            "isDcfSourceProduct": True,
            "dcfSourceKinds": ["valuation_tracking"],
            "valuationOnly": True,
            "valuationTracking": {
                "matchedDrugMasterId": drug_master_id,
                "matchedDrugKey": drug_key,
                "domestic": tracked_assumptions.get("domestic"),
                "overseas": tracked_assumptions.get("overseas"),
                "matchMethod": "valuation_tracking_only",
            },
            "indications": [{
                "sourceRowIdentity": f"valuation_tracking::{drug_key}",
                "rowOrder": None,
                "rowType": "valuation_tracking_only",
                "indicationText": "估值跟踪资产，适应症待补充",
                "phase": None,
                "statusStartDate": None,
                "comparator": None,
                "primaryEndpoint": None,
                "enrollmentText": None,
                "trialRegNo": None,
                "sponsor": None,
                "fieldArea": None,
                "latestProgress": None,
                "sourceLinks": {},
                "diseaseMatch": {"diseaseKey": None, "confidence": 0},
            }],
        })
    return products


def _warning(
    code: str,
    message: str,
    product_name: str | None,
    indication_text: str | None,
) -> dict[str, Any]:
    return {
        "level": "info",
        "code": code,
        "message": message,
        "productName": product_name,
        "indicationText": indication_text,
    }
