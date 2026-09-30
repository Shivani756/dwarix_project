from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import ConversationAgent
from knowledge import KnowledgeBase, load_records


def run(output_path: Path) -> dict:
    agent = ConversationAgent(KnowledgeBase(load_records(ROOT / "data" / "demo_kb.json")))
    scenarios = json.loads((ROOT / "data" / "test_scenarios.json").read_text(encoding="utf-8"))
    results = []
    counts: Counter[str] = Counter()
    for scenario in scenarios:
        session = agent.new_session(scenario["locale"])
        greeting = agent.greeting(scenario["locale"])
        reply = agent.handle(session, scenario["question"])
        citation_ids = [citation["record_id"] for citation in reply["citations"]]
        correct = reply["status"] == scenario["expected_status"] and (
            citation_ids[0] == scenario["expected_record_id"] if scenario["expected_record_id"] else not citation_ids
        )
        verdict = "correct" if correct else "incorrect"
        counts[verdict] += 1
        results.append({
            "scenario_id": scenario["id"],
            "group": scenario["group"],
            "locale": scenario["locale"],
            "test_type": "scripted logic simulation; no audio recording",
            "transcript": [
                {"role": "agent", "text": greeting},
                {"role": "customer", "text": scenario["question"]},
                {"role": "agent", "text": reply["answer"], "status": reply["status"], "source_ids": citation_ids},
            ],
            "expected_status": scenario["expected_status"],
            "observed_status": reply["status"],
            "expected_record_id": scenario["expected_record_id"],
            "observed_record_ids": citation_ids,
            "verdict": verdict,
        })
    report = {
        "created_for": "synthetic test personas and demo-only policy content",
        "audio_recordings": 0,
        "logic_scenarios": len(results),
        "verdict_counts": dict(counts),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run scripted Q1 and Q3 agent logic scenarios.")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "scenario_results.json")
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({"logic_scenarios": report["logic_scenarios"], "verdict_counts": report["verdict_counts"], "audio_recordings": 0, "output": str(args.output)}, indent=2))
    if report["verdict_counts"].get("incorrect", 0):
        raise SystemExit(1)
