"""Market-specific post-launch sales ramp conventions.

The ramp is expressed as a percentage of mature/peak penetration.  It is kept
separate from phase/POS: development risk is applied later to risk-adjusted
revenue and must not alter commercial adoption speed.
"""

from __future__ import annotations

from typing import Any


DEFAULT_YEARS_TO_PEAK = 7
POST_PEAK_PLATEAU_YEARS = 1
POST_PEAK_DECLINE_RATE = 0.03

# Six adoption anchors stretched from launch year through the default peak year.
# China assumes a 10% first-year contribution followed by NRDL/commercial
# expansion.  Overseas uses a slightly more conservative early ramp for a
# China-origin asset entering partner, payer and launch execution.
MARKET_RAMP_POINTS: dict[str, tuple[float, ...]] = {
    "china": (0.10, 0.30, 0.55, 0.75, 0.90, 1.00),
    "us": (0.08, 0.25, 0.50, 0.70, 0.85, 1.00),
}

MARKET_RAMP_PROFILE_IDS = {
    "china": "china_standard_launch_plus_7y_post_peak_3pct",
    "us": "overseas_standard_launch_plus_7y_post_peak_3pct",
}


def default_peak_year(launch_year: Any) -> int | None:
    """Return the standard peak year (launch year + seven years)."""
    if not isinstance(launch_year, int) or isinstance(launch_year, bool):
        return None
    return launch_year + DEFAULT_YEARS_TO_PEAK


def resolve_peak_year(
    *,
    launch_year: Any,
    market: str,
    suggested_peak_year: Any = None,
) -> tuple[int | None, str]:
    """Resolve a market peak year while retaining an auditable basis note."""
    if not isinstance(launch_year, int) or isinstance(launch_year, bool):
        return None, "缺少上市年，峰值年待确认"
    if (
        isinstance(suggested_peak_year, int)
        and not isinstance(suggested_peak_year, bool)
        and suggested_peak_year >= launch_year
    ):
        return suggested_peak_year, "DrugAlpha AI适应症选择建议；可在Excel中修改"
    market_label = "国内" if market == "china" else "海外"
    return default_peak_year(launch_year), f"{market_label}标准爬坡：上市年+{DEFAULT_YEARS_TO_PEAK}年"


def ramp_points(market: str) -> tuple[float, ...]:
    return MARKET_RAMP_POINTS.get(market, MARKET_RAMP_POINTS["china"])


def ramp_profile_id(market: str) -> str:
    return MARKET_RAMP_PROFILE_IDS.get(market, MARKET_RAMP_PROFILE_IDS["china"])


def sales_ramp_factor(
    year: int,
    *,
    launch_year: Any,
    peak_year: Any,
    market: str,
) -> float:
    """Return the share of mature penetration/sales for one forecast year.

    When users edit the peak year, the six-point standard profile is compressed
    or stretched by linear interpolation.  The peak year and the following year
    remain at 100%; ordinary commercial erosion starts in the second post-peak
    year.  This is separate from patent-cliff erosion, which remains outside the
    current model scope.
    """
    if (
        not isinstance(launch_year, int)
        or isinstance(launch_year, bool)
        or not isinstance(peak_year, int)
        or isinstance(peak_year, bool)
        or year < launch_year
    ):
        return 0.0
    if year >= peak_year:
        years_after_plateau = year - peak_year - POST_PEAK_PLATEAU_YEARS
        if years_after_plateau <= 0:
            return 1.0
        return (1.0 - POST_PEAK_DECLINE_RATE) ** years_after_plateau
    if peak_year <= launch_year:
        return 1.0

    points = ramp_points(market)
    progress = (year - launch_year) / (peak_year - launch_year)
    scaled = progress * (len(points) - 1)
    lower = min(int(scaled), len(points) - 2)
    fraction = scaled - lower
    return points[lower] + (points[lower + 1] - points[lower]) * fraction


def penetration_curve_from_peak(
    *,
    years: list[int],
    peak_penetration: float,
    launch_year: Any,
    peak_year: Any,
    market: str,
) -> list[float]:
    return [
        round(
            peak_penetration
            * sales_ramp_factor(
                year,
                launch_year=launch_year,
                peak_year=peak_year,
                market=market,
            ),
            4,
        )
        for year in years
    ]


def ramp_description(market: str) -> str:
    values = "/".join(f"{value:.0%}" for value in ramp_points(market))
    market_label = "国内" if market == "china" else "海外"
    return (
        f"{market_label}上市第1年至峰值年按 {values} 的成熟渗透率比例爬坡；"
        f"峰值年及后{POST_PEAK_PLATEAU_YEARS}年维持100%，随后每年自然下降"
        f"{POST_PEAK_DECLINE_RATE:.0%}，当前不含专利悬崖"
    )
