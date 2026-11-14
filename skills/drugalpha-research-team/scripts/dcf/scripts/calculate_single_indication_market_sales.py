"""JSON-stdio bridge for the shared patient-flow gross-sales engine."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.dcf_core.shared_patient_flow_sales import build_single_market_patient_flow, calculate_patient_flow_gross_sales
from packages.dcf_core.sales_ramp import default_peak_year


def main() -> int:
    payload = json.load(sys.stdin)
    years = [int(year) for year in payload.get("years") or []]
    market = str(payload.get("market") or "china")
    launch_year = payload.get("launchYear")
    peak_year = payload.get("peakYear") or default_peak_year(launch_year)
    flow = build_single_market_patient_flow(
        annual_patients=float(payload["annualPatients"]),
        biomarker_ratio=float(payload["biomarkerRatio"]),
        target_treatment_ratio=float(payload["targetTreatmentRatio"]),
        peak_penetration=float(payload["peakPenetration"]),
        monthly_treatment_cost=float(payload["monthlyTreatmentCost"]),
        treatment_months=float(payload["treatmentMonths"]),
        years=years,
        launch_year=launch_year,
        peak_year=peak_year,
        market=market,
        patient_growth_rate=float(payload.get("patientGrowthRate") or 0),
        price_curve_multiplier=payload.get("priceCurveMultiplier"),
    )
    result = calculate_patient_flow_gross_sales(patient_flow=flow, years=years)
    if result is None:
        raise ValueError("incomplete shared patient-flow inputs")
    result.update({"launchYear": launch_year, "peakYear": peak_year, "patientFlow": flow})
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
