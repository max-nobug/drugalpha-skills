"""Internal revision of the materials and native rules used by client DCF tasks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from ..source_adapters.clinical_pfs import _clinical_data_dir, _published_pfs_rows, _published_indication_rows


def source_revision(snapshot: dict, clinical_rows: dict, code_version: str) -> str:
    material = deepcopy(snapshot)
    # Source builder timestamps and diagnostic locations are not research data.
    for field in ("snapshotId", "createdAt", "readOnlySources", "cachePath"):
        material.pop(field, None)
    payload = {"source": material, "clinical": clinical_rows, "nativeCode": code_version}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "dcf-source-" + hashlib.sha256(encoded).hexdigest()


def financial_source_revision(assumptions: dict) -> str:
    # The fetched task material is pinned, not continuously re-fetched from the
    # provider. Changes to any row/equity/source/curve invalidate that receipt.
    return "dcf-financial-" + hashlib.sha256(json.dumps(assumptions, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def native_code_revision(engine_root: Path) -> str:
    digest = hashlib.sha256()
    for folder in ("packages", "scripts", "config"):
        for file in sorted((engine_root / folder).rglob("*")):
            if not file.is_file() or file.suffix not in (".py", ".json", ".csv") or "__pycache__" in file.parts:
                continue
            digest.update(file.relative_to(engine_root).as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    workspace = next((parent for parent in engine_root.parents if (parent / "drugalpha_api_mcp").is_dir()), None)
    if workspace:
        for relative in ("drugalpha_ai/server/modules/dcfModel/domesticTreatmentCostPrefill.js",
            "drugalpha_api_mcp/scripts/local-dcf-session.ts", "drugalpha_api_mcp/scripts/local-dcf-native.py",
            "drugalpha_api_mcp/scripts/local-dcf-runner.ts", "drugalpha_api_mcp/scripts/local-dcf-worker.ts",
            "drugalpha_api_mcp/scripts/local-dcf-financial.py", "drugalpha_api_mcp/scripts/local-valuation-reader.ts",
            "drugalpha_api_mcp/src/domain/valuation.ts"):
            file = workspace / relative
            digest.update(relative.encode())
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def current_source_revision(snapshot: dict) -> str:
    # Do not reuse the native process-local clinical read cache at this boundary.
    _published_pfs_rows.cache_clear()
    _published_indication_rows.cache_clear()
    root = str(_clinical_data_dir())
    rows = {"pfs": sorted(_published_pfs_rows(root), key=lambda r: json.dumps(r, sort_keys=True)),
            "indications": sorted(_published_indication_rows(root), key=lambda r: json.dumps(r, sort_keys=True))}
    return source_revision(snapshot, rows, native_code_revision(Path(__file__).resolve().parents[2]))
