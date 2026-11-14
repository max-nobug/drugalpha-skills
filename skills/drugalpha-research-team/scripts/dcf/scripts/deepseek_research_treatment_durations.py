"""Apply web-researched or adjacent-line mPFS to a DCF draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.ai_assumptions.treatment_duration_research import research_missing_treatment_durations
from packages.source_adapters.io_utils import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, help="Input DCF draft JSON")
    parser.add_argument("--out", help="Output draft JSON; defaults to overwriting --draft")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--max-tasks", type=int, default=200)
    args = parser.parse_args()

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    summary = research_missing_treatment_durations(
        draft,
        batch_size=args.batch_size,
        max_tasks=args.max_tasks,
    )
    output_path = Path(args.out) if args.out else draft_path
    write_json(output_path, draft)
    print(json.dumps({"ok": True, "outputPath": str(output_path), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
