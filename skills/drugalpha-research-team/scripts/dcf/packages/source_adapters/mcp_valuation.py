"""Adapt complete DrugAlpha valuation-tracking MCP pages to the independent DCF scope."""

from math import isfinite


MARKETS = {"domestic": ("china", "estimatedPeakSales"), "overseas": ("us", "estimatedPeakSalesUsd")}


def build_mcp_valuation_snapshot(payload, research):
    pages = payload if isinstance(payload, list) else [payload]
    if not pages or any(not isinstance(page, dict) for page in pages):
        raise ValueError("Supply a DrugAlpha valuation MCP response or a list of pages in order")
    first = pages[0]["data"]
    company = first["company"]
    version = (first.get("platformVersion"), first.get("valuationSource"), first.get("personalSavedAt"))
    company_key = company.get("companyKey") or company["companyName"]
    items = []
    for index, page in enumerate(pages):
        data = page["data"]
        page_company = data["company"]
        if (page_company.get("companyKey") or page_company["companyName"]) != company_key or (
            data.get("platformVersion"), data.get("valuationSource"), data.get("personalSavedAt")
        ) != version:
            raise ValueError("Valuation pages must cover the same company and version")
        pagination = page["pagination"]
        if pagination.get("hasMore") != (index < len(pages) - 1):
            raise ValueError("Valuation MCP pagination is incomplete or pages are out of order")
        items.extend(data["items"])
    if type(first.get("totalItems")) is not int or len(items) != first["totalItems"]:
        raise ValueError("Valuation MCP item count does not match the complete scope")

    products = {}
    pairs = {}
    seen = set()
    for item in items:
        market = item["market"]
        internal_market, peak_key = MARKETS[market]
        name = item["drugName"]
        key = item.get("drugMasterId") or item.get("drugKey") or name
        pair = key, internal_market
        if pair in seen:
            raise ValueError(f"Duplicate drug and market in valuation MCP scope: {name} / {market}")
        seen.add(pair)
        peak = item.get(peak_key)
        if item.get("includedInValuation") is not True or type(peak) not in (int, float) or not isfinite(peak) or peak <= 0:
            continue
        product = products.setdefault(key, {
            "productKey": key, "drugName": name, "target": item.get("target"),
            "isDcfSourceProduct": True, "isValuationTrackingProduct": True,
            "dcfSourceKinds": ["valuation_tracking"], "valuationOnly": True,
            "valuationTracking": {"matchedDrugKey": item.get("drugKey") or key},
            "indications": [],
        })
        # Only eligibility is carried forward. No platform forecast or economics enters the model.
        product["valuationTracking"][market] = {"includedInValuation": True, peak_key: 1}
        for alias in name.split("/") + [name, product["drugName"], key]:
            alias_key = alias.strip().casefold(), internal_market
            if alias_key in pairs and pairs[alias_key][0] != key:
                raise ValueError(f"Ambiguous valuation scope alias: {alias} / {market}")
            pairs[alias_key] = key, product

    indications_seen = set()
    for entry in research:
        name, market = entry["drug_name"], entry["market"]
        matched = pairs.get((name.strip().casefold(), market))
        indication, source = entry["indication"].strip(), entry["source"].strip()
        if not matched or not indication or not source:
            raise ValueError(f"Research indication needs an in-scope drug/market and evidence: {name} / {market}")
        key, product = matched
        identity = key, market, indication.casefold()
        if identity in indications_seen:
            raise ValueError(f"Duplicate research indication: {name} / {market} / {indication}")
        indications_seen.add(identity)
        product["indications"].append({
            "sourceRowIdentity": f"{key}:{market}:{len(product['indications']) + 1}",
            "rowType": "research_indication",
            "indicationText": indication,
            "phase": entry.get("phase"),
            "representativeSelectionBasis": source,
            "dcfResearchMarket": market,
        })

    if not products:
        raise ValueError("No included drug and market has a positive peak in valuation MCP")
    return {
        "snapshotId": pages[0].get("releaseId"),
        "company": {k: company[k] for k in ("companyKey", "companyName", "stockCodes") if k in company},
        "pipeline": {"products": list(products.values())},
    }
