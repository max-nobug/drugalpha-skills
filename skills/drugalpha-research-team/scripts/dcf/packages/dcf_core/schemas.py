"""Dataclass schemas for editable patient-flow DCF draft models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ModelWarning:
    level: str
    code: str
    message: str
    product_name: str | None = None
    indication_text: str | None = None


@dataclass
class PopulationFilter:
    key: str
    label: str
    ratio: float | None
    raw_text: str | None
    source: str
    requires_user_input: bool = False
    source_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PatientFlowAssumptions:
    market: str
    base_patient_count_10k: float | None
    base_patient_metric: str
    patient_growth_rate: float
    biomarker_ratio: float = 1.0
    biomarker_ratio_source: str = ""
    biomarker_ratio_requires_review: bool = False
    biomarker_ratio_source_details: list[dict[str, Any]] = field(default_factory=list)
    target_treatment_ratio: float = 1.0
    treatment_status_rule_id: str = "T0"
    treatment_status_rule_label: str = "无明确治疗状态限制"
    treatment_status_rule_reason: str = ""
    target_treatment_ratio_mode: str = "fixed_rule"
    target_treatment_ratio_source: str = ""
    target_treatment_ratio_source_url: str = ""
    target_treatment_ratio_requires_research: bool = False
    population_filters: list[PopulationFilter] = field(default_factory=list)
    penetration_curve: list[float | None] = field(default_factory=list)
    monthly_price: list[float | None] = field(default_factory=list)
    drug_cost_category: str = "oncology"
    drug_cost_category_label: str = "肿瘤药物"
    price_basis: str = "per_month"
    price_source: str = ""
    treatment_months: list[float | None] = field(default_factory=list)
    treatment_months_mode: str = "line_rule_fallback"
    treatment_months_reference_pfs: float | None = None
    treatment_months_source: str = ""
    treatment_months_source_detail: str = ""
    treatment_months_requires_review: bool = True
    treatment_months_references: list[dict[str, Any]] = field(default_factory=list)
    source_notes: list[dict[str, Any]] = field(default_factory=list)
    requires_user_input: bool = False


@dataclass
class MarketDraft:
    market: str
    currency: str
    patient_flow: PatientFlowAssumptions
    launch_year: int | None = None
    launch_year_basis: str | None = None
    peak_year: int | None = None
    peak_year_basis: str | None = None
    sales_ramp_profile: str | None = None
    peak_sales_anchor: float | None = None
    peak_sales_anchor_unit: str | None = None
    equity_ratio: float | None = None
    revenue_share_ratio: float | None = None
    success_probability: float | None = None
    exchange_rate: float | None = None
    market_space_capacity_100m: float | None = None
    peak_sales_allocation_weight: float | None = None
    allocated_peak_sales_anchor: float | None = None
    peak_sales_allocation_method: str | None = None


@dataclass
class IndicationDraft:
    indication_id: str
    source_row_identity: str | None
    indication_text: str | None
    trial_reg_no: str | None
    trial_reg_nos: list[str]
    trial_evidence: list[dict[str, Any]]
    merged_trial_count: int
    representative_selection_basis: str | None
    sponsor: str | None
    sponsors: list[str]
    overseas_eligible: bool
    overseas_eligibility_reason: str | None
    disease_key: str | None
    disease_title: str | None
    disease_match_confidence: float
    line_of_therapy: str | None
    phase: str | None
    status_start_date: str | None
    launch_year: int | None
    launch_year_basis: str | None
    peak_year: int | None
    peak_year_basis: str | None
    pos: float | None
    markets: list[MarketDraft]
    warnings: list[ModelWarning] = field(default_factory=list)


@dataclass
class ProductDraft:
    product_id: str
    drug_name: str | None
    drug_key: str | None
    target: str | None
    modeling_mode: str
    modeling_reason: str
    included_in_valuation: bool
    source_kinds: list[str]
    valuation_only: bool
    domestic_peak_sales_cny_100m: float | None
    domestic_equity_ratio: float | None
    overseas_peak_sales_usd_100m: float | None
    overseas_revenue_share_ratio: float | None
    overseas_success_probability: float | None
    exchange_rate: float | None
    indications: list[IndicationDraft]
    simplified_actual_sales: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    valuation_indication_selection: dict[str, Any] | None = None
    warnings: list[ModelWarning] = field(default_factory=list)


@dataclass
class ScenarioDraft:
    name: str
    description: str
    revenue_realization_multiplier: float
    penetration_multiplier: float
    price_multiplier: float
    launch_delay_years: int
    discount_rate: float
    terminal_growth_rate: float
    gross_margin: float
    sales_expense_rate: float
    rd_expense_rate: float
    admin_expense_rate: float
    tax_rate: float


@dataclass
class CompanyDraftModel:
    model_type: str
    source_snapshot_id: str
    company_key: str
    company_name: str
    stock_codes: str | None
    forecast_years: list[int]
    products: list[ProductDraft]
    scenarios: list[ScenarioDraft]
    warnings: list[ModelWarning]
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
