import json
import unittest
from pathlib import Path

from agent import ConversationAgent
from knowledge import KnowledgeBase, load_records


ROOT = Path(__file__).resolve().parents[1]


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(load_records(ROOT / "data" / "demo_kb.json"))

    def test_evaluation_set_covers_five_categories_twice_with_expected_top_result(self):
        queries = json.loads((ROOT / "data" / "eval_queries.json").read_text(encoding="utf-8"))
        categories = {category: 0 for category in ("product", "policy", "qualification", "faq", "objection")}

        for item in queries:
            categories[item["category"]] += 1
            result = self.kb.answer(
                item["question"],
                locale=item["locale"],
                market=item["market"],
                product=item["product"],
                category=item["category"],
            )
            self.assertEqual(result["status"], "answered", item["question"])
            self.assertEqual(result["citations"][0]["record_id"], item["expected_record_id"], item["question"])

        self.assertEqual(len(queries), 10)
        self.assertEqual(categories, {category: 2 for category in categories})

    def test_out_of_scope_medical_coverage_question_has_no_citation(self):
        item = json.loads((ROOT / "data" / "unsupported_queries.json").read_text(encoding="utf-8"))[0]

        result = self.kb.answer(item["question"], locale=item["locale"], market=item["market"], product=item["product"])

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["citations"], [])
        self.assertIn("unavailable", result["answer"].lower())

    def test_amount_question_does_not_get_objection_guidance_as_a_price(self):
        agent = ConversationAgent(self.kb)

        result = agent.handle(agent.new_session("en-PH"), "How much is my premium?")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["citations"], [])

    def test_objection_answer_uses_customer_wording_not_internal_rule_text(self):
        agent = ConversationAgent(self.kb)

        result = agent.handle(agent.new_session("en-PH"), "The premium is too expensive.")

        self.assertEqual(result["status"], "answered")
        self.assertIn("I understand", result["answer"])
        self.assertNotIn("If a customer says", result["answer"])

    def test_taglish_terminology_query_gets_a_definition_not_the_greeting(self):
        agent = ConversationAgent(self.kb)

        result = agent.handle(agent.new_session("fil-PH"), "Ano ang ibig sabihin ng rider at beneficiary?")

        self.assertEqual(result["status"], "answered")
        self.assertIn("rider ay optional", result["answer"])
        self.assertIn("beneficiary ay taong", result["answer"])
        self.assertNotIn("Kumusta!", result["answer"])


if __name__ == "__main__":
    unittest.main()
