"""Read-only adapter for the published disease-standard source projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import os
import subprocess
import json
import sys


@dataclass(frozen=True)
class PublishedIndicationFacetsAdapter:
    pipeline_dir: Path

    @property
    def index_path(self) -> Path:
        return Path(os.environ.get('DISEASE_RETRIEVAL_RELEASE_ROOT') or
                    self.pipeline_dir.parent / 'clinical-platform/config/disease-taxonomy/business-retrieval') / 'current.json'

    def get_company_facets(self, company_key: str) -> dict[str, dict[str, Any]]:
        script = self.pipeline_dir.parent / 'clinical-platform/scripts/disease_taxonomy/published_pipeline_projection.py'
        result = subprocess.run([os.environ.get('PYTHON') or sys.executable, str(script)],
                                check=True, capture_output=True, text=True, encoding='utf-8')
        payload = json.loads(result.stdout)
        return {
            str(item.get("rowIdentity")): dict(item.get("indicationFacets") or {})
            for item in payload.get("items") or []
            if item.get("companyKey") == company_key and item.get("rowIdentity")
        }
