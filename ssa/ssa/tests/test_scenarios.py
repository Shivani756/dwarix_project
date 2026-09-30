import json
import unittest
from pathlib import Path

from agent import ConversationAgent
from knowledge import KnowledgeBase, load_records


ROOT = Path(__file__).resolve().parents[1]


class ScriptedScenarioTests(unittest.TestCase):
    def test_q1_and_q3_scenarios_match_expected_status_and_sources(self):
        kb = KnowledgeBase(load_records(ROOT / "data" / "demo_kb.json"))
        agent = ConversationAgent(kb)
        scenarios = json.loads((ROOT / "data" / "test_scenarios.json").read_text(encoding="utf-8"))
        groups = {"q1": 0, "q3": 0}

        for scenario in scenarios:
            groups[scenario["group"]] += 1
            reply = agent.handle(agent.new_session(scenario["locale"]), scenario["question"])
            self.assertEqual(reply["status"], scenario["expected_status"], scenario["id"])
            citations = [item["record_id"] for item in reply["citations"]]
            if scenario["expected_record_id"]:
                self.assertEqual(citations[0], scenario["expected_record_id"], scenario["id"])
            else:
                self.assertEqual(citations, [], scenario["id"])
            if scenario["id"] == "q3_id_finance_terms":
                self.assertIn("angsuran", reply["answer"].lower())
                self.assertNotIn("the agent may", reply["answer"].lower())

        self.assertEqual(groups, {"q1": 5, "q3": 6})


if __name__ == "__main__":
    unittest.main()
