"""Apply AI-prefill assumptions and peak-anchor calibration to a DCF draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.apply_to_draft import apply_ai_prefill_to_draft
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True)
    parser.add_argument("--ai-assumptions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    assumptions = json.loads(Path(args.ai_assumptions).read_text(encoding="utf-8"))
    enriched, summary = apply_ai_prefill_to_draft(draft, assumptions)
    write_json(Path(args.out), enriched)
    print(json.dumps({"outputPath": args.out, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
