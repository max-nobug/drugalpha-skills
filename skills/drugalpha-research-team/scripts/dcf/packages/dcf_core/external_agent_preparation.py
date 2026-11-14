"""Prepare native DCF draft and native prefill tasks without AI or web search."""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from .draft_builder import build_draft_model_from_snapshot
from ..ai_assumptions.proposal_engine import build_indicator_tasks, DEFAULT_FIELDS


def prepare_external_agent_dcf(snapshot: dict[str, Any]) -> dict[str, Any]:
    draft = build_draft_model_from_snapshot(deepcopy(snapshot))
    market_count = sum(len(i.get("markets") or []) for p in draft.get("products") or []
                       for i in p.get("indications") or [])
    # Request the complete native task list, rather than the AI batch default 20.
    tasks = build_indicator_tasks(draft, max_tasks=max(1, market_count * len(DEFAULT_FIELDS) + 1),
                                  public_search=False)
    return {"draft": draft, "prefillTasks": tasks,
            "valuationSource": (snapshot.get("valuationTracking") or {}).get("valuationSource", "platform"),
            "sourceReleaseId": (snapshot.get("valuationTracking") or {}).get("selectedReleaseId"),
            "warnings": deepcopy(draft.get("warnings") or []),
            "status": "prepared_for_user_agent", "platformAiUsed": False}
