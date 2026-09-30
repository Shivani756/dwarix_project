from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge import KnowledgeBase, load_records


def run(output_path: Path) -> dict:
    kb = KnowledgeBase(load_records(ROOT / "data" / "demo_kb.json"))
    queries = json.loads((ROOT / "data" / "eval_queries.json").read_text(encoding="utf-8"))
    unsupported = json.loads((ROOT / "data" / "unsupported_queries.json").read_text(encoding="utf-8"))
    results = []
    counts: Counter[str] = Counter()

    for query in queries:
        ranked = kb.search(
            query["question"],
            market=query["market"],
            locale=query["locale"],
            product=query["product"],
            category=query["category"],
            top_k=3,
        )
        answer = kb.answer(
            query["question"],
            market=query["market"],
            locale=query["locale"],
            product=query["product"],
            category=query["category"],
        )
        retrieved = ranked[0] if ranked else None
        expected = query["expected_record_id"]
        verdict = "correct" if answer["status"] == "answered" and retrieved and retrieved["record"]["record_id"] == expected else (
            "partially_correct" if any(item["record"]["record_id"] == expected for item in ranked) else "incorrect"
        )
        counts[verdict] += 1
        results.append({
            "question": query["question"],
            "category": query["category"],
            "filters": {"market": query["market"], "locale": query["locale"], "product": query["product"]},
            "retrieved_record": {
                "record_id": retrieved["record"]["record_id"],
                "title": retrieved["record"]["title"],
                "content": retrieved["record"]["content"],
                "score": retrieved["score"],
                "source_reference": retrieved["citation"],
            } if retrieved else None,
            "expected_record_id": expected,
            "relevance_explanation": "The top retrieved record matches the labeled question category and approved market/product filters." if verdict == "correct" else "The expected record is present in the retrieved set but did not rank first." if verdict == "partially_correct" else "The expected record was not retrieved in the top three results.",
            "answer_status": answer["status"],
            "verdict": verdict,
        })

    for query in unsupported:
        answer = kb.answer(query["question"], market=query["market"], locale=query["locale"], product=query["product"])
        verdict = "correct" if answer["status"] == "unavailable" and not answer["citations"] else "incorrect"
        counts[verdict] += 1
        results.append({
            "question": query["question"],
            "category": "unsupported",
            "filters": {"market": query["market"], "locale": query["locale"], "product": query["product"]},
            "retrieved_record": None,
            "expected_record_id": None,
            "relevance_explanation": "No approved record supports a medical coverage guarantee; the agent should decline to speculate." if verdict == "correct" else "The retrieval returned evidence for an unsupported coverage guarantee.",
            "answer_status": answer["status"],
            "answer": answer["answer"],
            "verdict": verdict,
        })

    report = {
        "created_for": "synthetic demo corpus; not real insurer or lending policy",
        "retrieval_method": "weighted lexical token retrieval with market, locale, product, and category filters; embeddings are not configured in this local build",
        "total_queries": len(results),
        "verdict_counts": dict(counts),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the synthetic knowledge-base retrieval evaluation.")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "retrieval_results.json")
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({"total_queries": report["total_queries"], "verdict_counts": report["verdict_counts"], "output": str(args.output)}, indent=2))
