
"""Validate independently sourced annual sales drivers against the native DCF scope."""

import math


EUROPE_US_RATIO = 0.5
EUROPE_PROXY_NOTE = "用户估值规则：欧洲销售按美国的 50% 折算，欧洲开发进度不影响该比例。"


ANNUAL_FIELDS = {
    "eligible_patients": ("可治疗患者数（人）", "#,##0", None),
    "class_penetration": ("同类药物渗透率", "0.0%", 1),
    "product_class_share": ("该药物在同类药物中份额", "0.0%", 1),
    "treatment_months": ("治疗时长（月）", "0.00", None),
    "monthly_net_price": ("月净价", "#,##0.00", None),
}


PATIENT_BASIS_FIELDS = {
    "patient_population": "患者规模（人）",
    "biomarker_ratio": "Biomarker 比例",
    "clinical_fit_ratio": "适应症基础人群占比",
    "target_line_ratio": "目标疗线比例",
    "access_ratio": "检测及治疗可及比例",
}


def _require_source(sources, field, key):
    source = sources.get(field)
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Missing evidence or estimation basis for {key} {field}")
    return source.strip()


def _validate_patient_basis(basis, sources, years, key):
    if not isinstance(basis, dict) or basis.keys() != PATIENT_BASIS_FIELDS.keys():
        raise ValueError(f"Provide patient_population and four named patient ratios: {key}")
    population = basis["patient_population"]
    if not isinstance(population, list) or len(population) != len(years):
        raise ValueError(f"Provide patient population for each forecast year: {key}")
    for field in PATIENT_BASIS_FIELDS:
        _require_source(sources, field, key)
    ratios = [
        _scalar(basis[field], f"{key} {field}", 1)
        for field in PATIENT_BASIS_FIELDS if field != "patient_population"
    ]
    scale = math.prod(ratios)
    return [_scalar(value, f"{key} patient population {year}") * scale for value, year in zip(population, years)]


def _market_rows(draft):
    for product in draft["products"]:
        if not product["included_in_valuation"]:
            continue
        for indication in product["indications"]:
            for market in indication["markets"]:
                yield product, indication, market


def _key(row):
    return row["indication_id"], row["market"]


def _scalar(value, name, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (
        maximum is not None and value > maximum
    ):
        raise ValueError(f"Invalid input: {name}")
    return value


def _validate_patient_funnel(steps, expected, years, key):
    """Check an optional editable funnel against the model's annual patient input."""
    if steps is None or steps == []:
        return []
    if not isinstance(steps, list) or len(steps) < 2:
        raise ValueError(f"Patient funnel needs a base and at least one adjustment: {key}")
    totals = [0] * len(years)
    checked = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"Invalid patient funnel step: {key} step {index + 1}")
        operation = step.get("operation")
        valid_operations = ("base",) if index == 0 else ("multiply", "add", "subtract")
        label, source, series = step.get("label"), step.get("source"), step.get("values")
        if operation not in valid_operations or not all(
            isinstance(text, str) and text.strip() for text in (label, source)
        ):
            raise ValueError(f"Invalid patient funnel operation, label or evidence: {key} step {index + 1}")
        if not isinstance(series, list) or len(series) != len(years):
            raise ValueError(f"Provide each forecast year for patient funnel: {key} {label}")
        for year_index, value in enumerate(series):
            _scalar(value, f"{key} {label} {years[year_index]}", 1 if operation == "multiply" else None)
            if operation == "base":
                totals[year_index] = value
            elif operation == "multiply":
                totals[year_index] *= value
            elif operation == "add":
                totals[year_index] += value
            else:
                totals[year_index] = max(0, totals[year_index] - value)
        checked.append({"operation": operation, "label": label.strip(), "source": source.strip(), "values": series})
    for year, calculated, supplied in zip(years, totals, expected):
        if not math.isclose(calculated, supplied, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError(f"Patient funnel does not reconcile to eligible_patients: {key} {year}")
    return checked


def revenue_input_template(draft):
    return {
        "company": draft["company_name"],
        "years": draft["forecast_years"],
        "valuation_scope": draft.get("valuation_scope", []),
        "markets": [
            {
                "drug": product["drug_name"],
                "indication": indication["indication_text"],
                "indication_id": indication["indication_id"],
                "market": market["market"],
                "currency": market["currency"],
                "values": dict.fromkeys([
                    *ANNUAL_FIELDS, "launch_year", "success_probability", "economic_share",
                    *(["exchange_rate"] if market["market"] != "china" else []),
                ]),
                "assumptions": {},
                "sources": {},
                "penetration_basis": "assumption",
                "patient_basis": dict.fromkeys(PATIENT_BASIS_FIELDS),
                "product_penetration": None,
                **({"europe_ratio": EUROPE_US_RATIO} if market["market"] == "us" else {}),
                "economic_ownership": 1.0,
            }
            for product, indication, market in _market_rows(draft)
        ],
    }


def apply_independent_revenue(draft, payload):
    if payload.get("years") != draft["forecast_years"] or payload.get("company") != draft["company_name"]:
        raise ValueError("Independent revenue inputs must match the company and forecast years")
    years = draft["forecast_years"]
    template = {_key(row): row for row in revenue_input_template(draft)["markets"]}
    supplied = {}
    for row in payload["markets"]:
        key = _key(row)
        if key not in template or key in supplied:
            raise ValueError(f"Unexpected or duplicate revenue market: {key}")
        supplied[key] = row
    missing = template.keys() - supplied.keys()
    if missing or (not template and not draft.get("valuation_scope")):
        raise ValueError(f"Independent revenue scope has missing inputs: {sorted(missing)}")

    expected_markets = {(product["product_id"], market["market"]) for product, _, market in _market_rows(draft)}
    unmatched_markets = {
        (item["product_id"], item["market"]) for item in draft.get("valuation_scope", [])
    } - expected_markets
    for gap in payload.get("unmodeled_market_reasons", []):
        key = gap.get("product_id"), gap.get("market")
        reason = gap.get("reason")
        if key not in unmatched_markets or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Document an unmatched in-scope market once, with a specific reason: {key}")
        unmatched_markets.remove(key)
        draft.setdefault("unquantified_scope", []).append({
            "product_id": key[0], "market": key[1], "reason": reason.strip(),
        })

    unquantified = []
    for product, indication, market in _market_rows(draft):
        key = indication["indication_id"], market["market"]
        entry = supplied[key]
        status = entry.get("status", "modeled")
        if status == "unquantified":
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Unquantified scope requires a reason: {key}")
            unquantified.append((indication, market))
            draft.setdefault("unquantified_scope", []).append({
                "product_id": product["product_id"],
                "market": market["market"],
                "reason": f"{indication['indication_text']}：{reason.strip()}",
            })
            continue
        if status != "modeled":
            raise ValueError(f"Unknown revenue input status: {key}")
        values, evidence = entry["values"], entry["sources"]
        assumptions = entry.get("assumptions", {})
        for label, mapping in (("assumptions", assumptions), ("sources", evidence)):
            if not isinstance(mapping, dict) or any(not isinstance(note, str) for note in mapping.values()):
                raise ValueError(f"{label} must map field names to text: {key}")
        assumptions = {field: note.strip() for field, note in assumptions.items() if note.strip()}
        evidence = {field: note.strip() for field, note in evidence.items() if note.strip()}
        sources = {**assumptions, **evidence}
        penetration_basis = entry.get("penetration_basis", "assumption")
        if penetration_basis not in ("assumption", "observed"):
            raise ValueError(f"Invalid penetration_basis for {key}")
        if penetration_basis == "observed":
            observed_fields = ("product_penetration",) if entry.get("product_penetration") is not None else (
                "class_penetration", "product_class_share",
            )
            for field in observed_fields:
                _require_source(evidence, field, key)
        if entry.get("currency") != market["currency"]:
            raise ValueError(f"Net prices must use the market currency: {key}")
        basis = entry.get("patient_basis")
        if isinstance(basis, dict) and all(value is None for value in basis.values()):
            basis = None
        penetration = entry.get("product_penetration")
        if basis is None and (penetration is not None or entry.get("europe_ratio") not in (None, 0)
                              or entry.get("economic_ownership", 1) != 1):
            raise ValueError(f"Product penetration, Europe proxy and separate ownership require patient_basis: {key}")
        if basis is not None and entry.get("patient_funnel"):
            raise ValueError(f"Choose either patient_basis or patient_funnel: {key}")
        expected_fields = template[key]["values"].keys()
        allowed_optional = {"eligible_patients", "class_penetration", "product_class_share"} if basis is not None else set()
        if values.keys() - expected_fields or expected_fields - values.keys() - allowed_optional:
            raise ValueError(f"Unexpected or missing revenue fields for {key}")
        values = dict(values)
        if basis is not None:
            derived_patients = _validate_patient_basis(basis, sources, years, key)
            supplied_patients = values.get("eligible_patients")
            if supplied_patients is not None and (
                not isinstance(supplied_patients, list) or len(supplied_patients) != len(years)
                or any(not math.isclose(_scalar(value, f"{key} eligible_patients"), derived, rel_tol=1e-9, abs_tol=1e-6)
                       for value, derived in zip(supplied_patients, derived_patients))
            ):
                raise ValueError(f"patient_basis does not reconcile to eligible_patients: {key}")
            values["eligible_patients"] = derived_patients
            sources = {**sources, "eligible_patients": "由患者规模、标志物及三个筛选比例计算"}
            if penetration is not None:
                if any(values.get(field) is not None for field in ("class_penetration", "product_class_share")):
                    raise ValueError(f"Use direct product_penetration or class/share, not both: {key}")
                values["class_penetration"] = penetration
                values["product_class_share"] = [1] * len(years)
                direct_source = _require_source(sources, "product_penetration", key)
                sources.update(class_penetration=direct_source, product_class_share="产品渗透率已包含本品份额")
        conditional_only = values["success_probability"] is None
        if conditional_only and basis is None:
            raise ValueError(f"Success-condition modeling requires patient_basis: {key}")
        for field in values:
            _require_source(sources, field, key)
        for field, (_, _, maximum) in ANNUAL_FIELDS.items():
            series = values.get(field)
            if not isinstance(series, list) or len(series) != len(years):
                raise ValueError(f"Provide a value for each forecast year: {key} {field}")
            for year, value in zip(years, series):
                _scalar(value, f"{key} {field} {year}", maximum)
        funnel = _validate_patient_funnel(entry.get("patient_funnel"), values["eligible_patients"], years, key)
        if basis is not None and market["market"] == "us":
            europe_ratio = EUROPE_US_RATIO
            sources["europe_ratio"] = assumptions["europe_ratio"] = EUROPE_PROXY_NOTE
            evidence.pop("europe_ratio", None)
        else:
            europe_ratio = _scalar(entry.get("europe_ratio", 0), f"{key} Europe / US", 1)
        ownership = _scalar(entry.get("economic_ownership", 1), f"{key} economic ownership", 1)
        if europe_ratio:
            if market["market"] != "us" or market["currency"] != "USD":
                raise ValueError(f"Europe proxy requires a US dollar market: {key}")
            _require_source(sources, "europe_ratio", key)
        if ownership != 1:
            _require_source(sources, "economic_ownership", key)
        launch = values["launch_year"]
        if type(launch) is not int or not 1900 <= launch <= 2200:
            raise ValueError(f"Invalid launch year: {key}")
        for idx, year in enumerate(years):
            if year >= launch and all(values[field][idx] > 0 for field in (
                "eligible_patients", "class_penetration", "product_class_share"
            )) and any(values[field][idx] <= 0 for field in ("treatment_months", "monthly_net_price")):
                raise ValueError(f"Active treatment requires positive duration and net price: {key} {year}")
        fx = _scalar(values.get("exchange_rate", 1), f"{key} FX")
        if fx == 0:
            raise ValueError(f"Exchange rate must be positive: {key}")

        product["independent_revenue"] = True
        market["launch_year"] = launch
        market["launch_year_basis"] = sources["launch_year"]
        market["conditional_only"] = conditional_only
        market["success_probability"] = (
            0 if conditional_only else _scalar(values["success_probability"], f"{key} POS", 1)
        )
        share_key = "equity_ratio" if market["market"] == "china" else "revenue_share_ratio"
        market[share_key] = _scalar(values["economic_share"], f"{key} company economics", 1)
        market["exchange_rate"] = fx
        market["europe_ratio"] = europe_ratio
        market["economic_ownership"] = ownership
        fields = set(values)
        if basis is not None:
            fields.update(PATIENT_BASIS_FIELDS)
            fields.update(field for field in ("product_penetration", "europe_ratio", "economic_ownership") if field in sources)
        market["revenue_input_sources"] = {field: _require_source(sources, field, key) for field in sorted(fields)}
        if "assumptions" in entry or (basis is not None and market["market"] == "us"):
            market["revenue_input_assumptions"] = assumptions
            market["revenue_input_evidence"] = evidence
        market["penetration_basis"] = penetration_basis
        market["patient_flow"] = {
            "base_patient_count_10k": values["eligible_patients"][0] / 10000,
            "patient_growth_rate": 0.0,
            "biomarker_ratio": 1.0,
            "target_treatment_ratio": 1.0,
            "annual_eligible_patients_10k": [value / 10000 for value in values["eligible_patients"]],
            "penetration_curve": values["class_penetration"],
            "product_share_curve": values["product_class_share"],
            "treatment_months": values["treatment_months"],
            "monthly_price": [value / 10000 for value in values["monthly_net_price"]],
            "price_basis": "per_month",
            "patient_funnel": funnel,
            "patient_basis": basis,
        }
    for indication, market in unquantified:
        indication["markets"].remove(market)
    draft["revenue_mode"] = "independent_patient_flow"
