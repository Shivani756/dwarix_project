from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge import KnowledgeBase, ingest_file, load_records, save_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse local source files or public HTTPS webpages into traceable KB records.")
    parser.add_argument("sources", nargs="+", help="Local .html/.md/.txt/.csv/.pdf path or an HTTPS webpage URL")
    parser.add_argument("--market", default="GLOBAL")
    parser.add_argument("--locale", default="en")
    parser.add_argument("--product", default="general")
    parser.add_argument("--category", default="faq")
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--record-type", choices=("customer_facing", "agent_guidance"), default="customer_facing")
    parser.add_argument("--response-text", help="Approved customer-facing wording for records marked agent_guidance")
    parser.add_argument("--review-status", choices=("needs_review", "approved"), default="needs_review")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "imported_records.json")
    parser.add_argument("--replace", action="store_true", help="Replace the output dataset after at least one source record is extracted; default is to merge.")
    args = parser.parse_args()

    metadata = {
        "market": args.market,
        "locale": args.locale,
        "product": args.product,
        "category": args.category,
        "version": args.version,
        "record_type": args.record_type,
        "response_text": args.response_text,
        "review_status": args.review_status,
    }
    records = []
    failures = []
    for source in args.sources:
        extracted, warnings = ingest_file(source, metadata=metadata)
        records.extend(extracted)
        failures.extend({"source": source, "warning": warning} for warning in warnings)
    if not records:
        existing_count = len(load_records(args.output)) if args.output.is_file() else 0
        print(json.dumps({
            "records_added": 0,
            "existing_records_preserved": existing_count,
            "review_status": args.review_status,
            "output": str(args.output),
            "warnings": failures,
        }, ensure_ascii=False, indent=2))
        return 1
    existing = load_records(args.output) if args.output.is_file() and not args.replace else []
    deduplicated = KnowledgeBase(existing + records).records
    save_records(args.output, deduplicated)
    print(json.dumps({
        "records_written": len(deduplicated),
        "records_added": len(records),
        "merged_existing": bool(existing),
        "review_status": args.review_status,
        "output": str(args.output),
        "warnings": failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
