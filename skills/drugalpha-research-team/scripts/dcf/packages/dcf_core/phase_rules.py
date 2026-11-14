"""Shared DCF phase normalization for marketed and filing-stage assets."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


WITHDRAWN_TOKENS = ("撤回", "撤销", "WITHDRAWN")
PRE_MARKET_TOKENS = ("申请", "申报", "准备", "拟", "PRENDA", "NDA", "BLA", "ANDA")


def normalize_phase(value: Any) -> str:
    return re.sub(r"[\s_-]+", "", unicodedata.normalize("NFKC", str(value or "")).upper())


def is_withdrawn_phase(value: Any) -> bool:
    phase = normalize_phase(value)
    return any(token in phase for token in WITHDRAWN_TOKENS)


def is_marketed_phase(value: Any) -> bool:
    """Recognize generic and market-qualified approval/listing labels."""

    phase = normalize_phase(value)
    if not phase or is_withdrawn_phase(phase):
        return False
    if any(token in phase for token in ("MARKETED", "APPROVED", "商业化")):
        return True
    if any(token in phase for token in ("已上市", "获批上市", "批准上市")):
        return True
    return "上市" in phase and not any(token in phase for token in PRE_MARKET_TOKENS)


def is_nda_bla_phase(value: Any) -> bool:
    """Include NDA/BLA, preNDA, preparation NDA and ANDA, but not withdrawals."""

    phase = normalize_phase(value)
    if not phase or is_withdrawn_phase(phase):
        return False
    return (
        "NDA" in phase
        or "BLA" in phase
        or any(token in phase for token in ("上市申请", "申请上市", "申报上市"))
    )
