"""Internal client-agent orchestration; reuse native rules, preserve unresolved tasks."""
from copy import deepcopy
import json
from .agent_research_request import patient_research_request, financial_research_request
from ..ai_assumptions.proposal_engine import build_indicator_tasks, DEFAULT_FIELDS, _valid_proposal_value
from ..ai_assumptions.apply_to_draft import apply_ai_prefill_to_draft
from ..financial_data.assumptions import build_assumption_curves
from ..financial_data.review import apply_external_financial_review
from .calculator import calculate_draft_model
from ..excel_export.workbook import export_valuation_workbook


def prepare_patient_stage(draft: dict, valuation_source: str) -> dict:
    draft = deepcopy(draft)
    count = sum(len(i.get("markets") or []) for p in draft.get("products") or [] for i in p.get("indications") or [])
    prepared = {"draft": draft, "prefillTasks": build_indicator_tasks(
        draft, max_tasks=max(1, count * len(DEFAULT_FIELDS) + 1), public_search=False),
        "valuationSource": valuation_source}
    return {"prepared": prepared, "agentRequest": patient_research_request(prepared)}


def apply_patient_stage(prepared: dict, responses: list[dict]) -> dict:
    json.dumps(responses, allow_nan=False)
    public = patient_research_request(prepared)
    tasks = {task["taskId"]: task for task in public["tasks"]}
    if not isinstance(responses, list):
        raise ValueError("Patient response must be a task list")
    proposals, seen, accepted, rejected = [], set(), set(), []
    allowed = {"taskId", "suggestedValue", "suggestedCurve", "basisComment", "sourceUrls", "confidence"}
    for response in responses:
        if not isinstance(response, dict) or set(response) - allowed:
            raise ValueError("Patient response contains unsupported fields")
        task_id = response.get("taskId")
        if task_id not in tasks or task_id in seen:
            raise ValueError("Patient task mismatch or duplicate")
        seen.add(task_id)
        task = tasks[task_id]
        urls = response.get("sourceUrls") or []
        if not isinstance(urls, list) or any(not isinstance(u, str) or not u.startswith(("https://", "http://")) for u in urls):
            raise ValueError("Patient evidence URLs invalid")
        proposal = {**deepcopy(response), "field": task["field"], "drugName": task["drugName"],
            "indication": task["indication"], "market": task["market"],
            "aiMode": "ai_prefill", "needsHumanReview": True, "researchProvider": "user_agent"}
        # Same numeric/curve constraints as the original proposal engine. URLs
        # here are client evidence, not claimed native search hits or verified facts.
        if not str(proposal.get("basisComment") or "").strip() or not _valid_proposal_value(proposal):
            rejected.append(task_id)
            continue
        proposals.append(proposal)
        accepted.add(task_id)
    enriched, application = apply_ai_prefill_to_draft(prepared["draft"], {"proposals": proposals})
    rejected_keys = {(r["drugName"], r["indication"], r["market"], r["field"]) for r in application["rejected"]}
    for task_id in list(accepted):
        task = tasks[task_id]
        if (task["drugName"], task["indication"], task["market"], task["field"]) in rejected_keys:
            accepted.remove(task_id)
            rejected.append(task_id)
    pending = [task_id for task_id in tasks if task_id not in accepted]
    return {"draft": enriched, "proposals": proposals, "application": application,
        "pendingTaskIds": pending, "rejectedTaskIds": rejected, "platformAiUsed": False}


def prepare_financial_stage(draft: dict, history: list[dict], source_status: dict | None = None) -> dict:
    valuation = calculate_draft_model(draft)
    base = next(r for r in valuation["scenarioResults"] if r["scenario"] == "base")
    assumptions = build_assumption_curves(history=deepcopy(history),
        forecast_revenue=base["companyRevenueCny100m"], years=valuation["forecastYears"])
    assumptions["sourceStatus"] = deepcopy(source_status or {"historyProvider": "来源尚未确认"})
    pending = [] if history else ["financial_history_unavailable"]
    if not source_status or not str(source_status.get("historyProvider") or "").strip():
        pending.append("financial_source_unavailable")
    return {"valuation": valuation, "financialAssumptions": assumptions,
        "agentRequest": financial_research_request(draft, assumptions),
        "pending": pending}


def financial_stage_from_native_assumptions(draft: dict, assumptions: dict) -> dict:
    """Keep the original financial fetch result, including equity/source metadata."""
    from scripts.prepare_export_financials import _missing_ifind_core_fields
    assumptions = deepcopy(assumptions)
    pending = []
    if not assumptions.get("historicalRows"):
        pending.append("financial_history_unavailable")
    if not str((assumptions.get("sourceStatus") or {}).get("historyProvider") or "").strip():
        pending.append("financial_source_unavailable")
    missing = _missing_ifind_core_fields(assumptions.get("latestHistorical") or {})
    if missing:
        pending.append("financial_core_fields_unavailable")
    return {"valuation": calculate_draft_model(draft), "financialAssumptions": assumptions,
        "agentRequest": financial_research_request(draft, assumptions), "pending": pending,
        "missingCoreFields": missing}


def export_agent_workbook(draft: dict, financial: dict, proposals: list[dict], review: dict,
                          pending: list[str], output_path) -> dict:
    # Native fallbacks remain in the internal draft, but cannot erase known gaps
    # or be delivered as successful agent prefill.
    if pending or financial["pending"]:
        raise ValueError("DCF agent research remains incomplete; export not performed")
    checked = apply_external_financial_review(draft, financial["financialAssumptions"], review)
    if checked["financialAdjustment"]["rejectedCount"]:
        raise ValueError("DCF financial suggestions rejected; export not performed")
    assumptions = {"proposals": proposals, **checked, "researchProvider": "user_agent", "platformAiUsed": False}
    export_valuation_workbook(draft, financial["valuation"], output_path,
        financial_assumptions=financial["financialAssumptions"], ai_assumptions=assumptions, apply_ai_assumptions=True)
    return {"platformAiUsed": False, "needsHumanReview": True, "formulaCachedValuesVerified": False}
