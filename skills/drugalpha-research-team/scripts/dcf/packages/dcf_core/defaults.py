"""Defaults for MVP editable DCF draft models."""

from __future__ import annotations

from .schemas import ScenarioDraft
from .success_probability import DOMESTIC_PHASE_POS


FORECAST_YEARS = list(range(2026, 2036))


PHASE_TO_LAUNCH_YEAR = {
    "已上市": 2026,
    "上市": 2026,
    "获批上市": 2026,
    "NDA": 2027,
    "BLA": 2027,
    "3期": 2029,
}


PHASE_TO_POS = {
    "已上市": DOMESTIC_PHASE_POS["上市"],
    "上市": DOMESTIC_PHASE_POS["上市"],
    "获批上市": DOMESTIC_PHASE_POS["上市"],
    "NDA": DOMESTIC_PHASE_POS["NDA_BLA"],
    "BLA": DOMESTIC_PHASE_POS["NDA_BLA"],
    "3期": DOMESTIC_PHASE_POS["PHASE_3"],
    "2期": DOMESTIC_PHASE_POS["PHASE_2"],
}


VALUATION_PHASES = set(PHASE_TO_LAUNCH_YEAR)


DEFAULT_SCENARIOS = [
    ScenarioDraft(
        name="base",
        description="基准收入兑现",
        revenue_realization_multiplier=1.0,
        penetration_multiplier=1.0,
        price_multiplier=1.0,
        launch_delay_years=0,
        discount_rate=0.10,
        terminal_growth_rate=0.02,
        gross_margin=0.90,
        sales_expense_rate=0.18,
        rd_expense_rate=0.16,
        admin_expense_rate=0.05,
        tax_rate=0.15,
    ),
    ScenarioDraft(
        name="bull",
        description="产品收入兑现率提高至120%",
        revenue_realization_multiplier=1.20,
        penetration_multiplier=1.0,
        price_multiplier=1.0,
        launch_delay_years=0,
        discount_rate=0.10,
        terminal_growth_rate=0.02,
        gross_margin=0.90,
        sales_expense_rate=0.18,
        rd_expense_rate=0.16,
        admin_expense_rate=0.05,
        tax_rate=0.15,
    ),
    ScenarioDraft(
        name="bear",
        description="产品收入兑现率降低至80%",
        revenue_realization_multiplier=0.80,
        penetration_multiplier=1.0,
        price_multiplier=1.0,
        launch_delay_years=0,
        discount_rate=0.10,
        terminal_growth_rate=0.02,
        gross_margin=0.90,
        sales_expense_rate=0.18,
        rd_expense_rate=0.16,
        admin_expense_rate=0.05,
        tax_rate=0.15,
    ),
]
