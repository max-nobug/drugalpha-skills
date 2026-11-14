"""Configuration for read-only DrugAlpha source adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CLINICAL_PLATFORM_ROOT = Path.cwd()


@dataclass(frozen=True)
class SourcePaths:
    """Resolved read-only DrugAlpha source paths."""

    pipeline_dir: Path
    disease_knowledge_dir: Path
    valuation_dir: Path
    dcf_data_dir: Path

    @classmethod
    def from_env(cls) -> "SourcePaths":
        root = Path(os.getenv("DRUGALPHA_SOURCE_ROOT") or DEFAULT_CLINICAL_PLATFORM_ROOT)
        return cls(
            pipeline_dir=Path(
                os.getenv("DRUGALPHA_PIPELINE_DIR")
                or root / "pipeline_tracking"
            ),
            disease_knowledge_dir=Path(
                os.getenv("DRUGALPHA_DISEASE_KNOWLEDGE_DIR")
                or root / "disease-knowledge"
            ),
            valuation_dir=Path(
                os.getenv("DRUGALPHA_VALUATION_DIR")
                or root / "valuation_tracking"
            ),
            dcf_data_dir=Path(os.getenv("DCF_DATA_DIR") or "data"),
        )

    def validate_readonly_sources(self) -> None:
        for label, path in [
            ("pipeline", self.pipeline_dir),
            ("disease_knowledge", self.disease_knowledge_dir),
            ("valuation", self.valuation_dir),
        ]:
            if not path.exists():
                raise FileNotFoundError(f"DrugAlpha {label} source not found: {path}")
            if not path.is_dir():
                raise NotADirectoryError(f"DrugAlpha {label} source is not a directory: {path}")

    @property
    def source_snapshot_cache_dir(self) -> Path:
        return self.dcf_data_dir / "cache" / "source_snapshots"
