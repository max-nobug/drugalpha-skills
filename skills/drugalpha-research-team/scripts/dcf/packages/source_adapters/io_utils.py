"""Small IO helpers for read-only source adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def published_data_root(key, fallback):
    from .published_runtime_paths import runtime_data_root
    return runtime_data_root(key, fallback)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    """Write only to DCF-owned paths, never DrugAlpha source directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_count_to_10k(
    value: str | int | float | None,
    *,
    reference_year: int | None = None,
) -> float | None:
    """Parse Chinese epidemiology count text into 10k people units."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text in {"-", "NA", "N/A"}:
        return None

    if reference_year is not None:
        dated_counts: list[tuple[int, float]] = []
        # Support both common knowledge-base forms:
        # ``376万人（2020）`` and ``预计2025年432万人``.
        count_then_year = re.compile(
            r"(\d+(?:\.\d+)?)\s*(亿|万)?人[^；;]{0,24}?(\d{4})年?"
        )
        year_then_count = re.compile(
            r"(\d{4})年[^；;]{0,24}?(\d+(?:\.\d+)?)\s*(亿|万)?人"
        )
        for match in count_then_year.finditer(text):
            dated_counts.append(
                (int(match.group(3)), _count_number_to_10k(match.group(1), match.group(2)))
            )
        for match in year_then_count.finditer(text):
            dated_counts.append(
                (int(match.group(1)), _count_number_to_10k(match.group(2), match.group(3)))
            )
        eligible = [item for item in dated_counts if item[0] <= reference_year]
        if eligible:
            return max(eligible, key=lambda item: item[0])[1]
        if dated_counts:
            return min(dated_counts, key=lambda item: item[0])[1]

    match = re.search(r"(\d+(?:\.\d+)?)\s*(亿|万)?人", text)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        return float(match.group(1))

    return _count_number_to_10k(match.group(1), match.group(2))


def _count_number_to_10k(number: str | float, unit: str | None) -> float:
    number = float(number)
    if unit == "亿":
        return number * 10000
    return number


def parse_ratio_midpoint(value: str | int | float | None) -> float | None:
    """Parse a ratio text such as '约 30%-50%' into midpoint decimal."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) <= 1 else float(value) / 100

    text = str(value)
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*[-–~至到]\s*(\d+(?:\.\d+)?)\s*%", text)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return (low + high) / 2 / 100

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if single_match:
        return float(single_match.group(1)) / 100

    return None


def slugify_for_file(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value).strip("_") or "snapshot"
