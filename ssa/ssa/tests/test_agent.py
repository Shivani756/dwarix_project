import unittest

from agent import ConversationAgent
from knowledge import KnowledgeBase
from test_knowledge import record


class ConversationAgentTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase([
            record("renewal", "Renewal window", "For this synthetic demo only, review may begin 30 days before the demo renewal date.", category="policy"),
            record("cost-objection", "Premium concern", "For this synthetic demo, if the premium seems too expensive or unaffordable, do not suggest changing coverage. Offer a representative callback to discuss options.", category="objection"),
            record("callback-faq", "Mock callback", "A mock callback request saves your preferred time for this demo. No real call is scheduled.", category="faq"),
        ])
        self.agent = ConversationAgent(self.kb)

    def test_answer_cites_a_retrieved_record(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "When can I review the renewal?")

        self.assertEqual(reply["status"], "answered")
        self.assertIn("30 days", reply["answer"])
        self.assertEqual(reply["citations"][0]["record_id"], "renewal")

    def test_unsupported_question_returns_unavailable_without_citation(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "Can you guarantee my medical condition is covered?")

        self.assertEqual(reply["status"], "unavailable")
        self.assertIn("unavailable", reply["answer"].lower())
        self.assertEqual(reply["citations"], [])

    def test_incomplete_or_conflicting_policy_details_trigger_clarification(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "I don't know which policy schedule is current.")

        self.assertEqual(reply["status"], "clarification")
        self.assertTrue(reply["clarifying_question"])

    def test_indonesian_clarification_stays_in_indonesian(self):
        session = self.agent.new_session("id-ID")

        reply = self.agent.handle(session, "Saya tidak tahu jadwal mana yang paling baru.")

        self.assertEqual(reply["status"], "clarification")
        self.assertIn("jadwal", reply["answer"].lower())
        self.assertNotIn("Magandang", reply["answer"])

    def test_human_request_creates_escalation_summary_without_personal_number(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "Please connect me to a person, call 09171234567.")

        self.assertEqual(reply["status"], "escalated")
        self.assertEqual(reply["business_action"]["type"], "mock_escalation")
        self.assertNotIn("09171234567", str(reply["business_action"]))

    def test_callback_action_records_preference_but_never_requests_phone_number(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "Please ask a representative to call after 3 pm.")

        self.assertEqual(reply["business_action"]["type"], "mock_callback_request")
        self.assertEqual(reply["business_action"]["preferred_window"], "after 3 pm")
        self.assertNotIn("phone_number", reply["business_action"])
        self.assertNotIn("consent", reply["business_action"])

    def test_prior_renewal_intent_does_not_override_later_objection_or_question(self):
        session = self.agent.new_session("en-PH")
        self.agent.handle(session, "I want to renew.")

        objection = self.agent.handle(session, "The premium seems too expensive.")
        question = self.agent.handle(session, "When can I review the renewal?")

        self.assertEqual(objection["status"], "answered")
        self.assertEqual(objection["citations"][0]["record_id"], "cost-objection")
        self.assertEqual(question["status"], "answered")
        self.assertEqual(question["citations"][0]["record_id"], "renewal")
        self.assertEqual(question["qualification"]["renewal_intent"], "yes")

    def test_callback_refusal_does_not_create_request(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "Do not call me back.")

        self.assertIsNone(reply["business_action"])
        self.assertNotEqual(reply["status"], "callback_requested")

    def test_callback_definition_question_is_answered_without_request(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "What is a callback?")

        self.assertEqual(reply["status"], "answered")
        self.assertEqual(reply["citations"][0]["record_id"], "callback-faq")
        self.assertIsNone(reply["business_action"])

    def test_affirmative_reply_confirms_only_the_callback_that_was_offered(self):
        session = self.agent.new_session("en-PH")
        self.agent.handle(session, "The premium seems too expensive.")

        reply = self.agent.handle(session, "Yes please.")

        self.assertEqual(reply["status"], "callback_requested")
        self.assertTrue(reply["business_action"]["explicit_request"])
        self.assertNotIn("consent", reply["business_action"])

    def test_qualification_records_intent_without_claiming_eligibility(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "I want to renew.")

        self.assertEqual(reply["qualification"]["renewal_intent"], "yes")
        self.assertNotIn("eligible", reply["answer"].lower())

    def test_objection_uses_approved_objection_record(self):
        session = self.agent.new_session("en-PH")

        reply = self.agent.handle(session, "The premium seems too expensive.")

        self.assertEqual(reply["status"], "answered")
        self.assertEqual(reply["citations"][0]["record_id"], "cost-objection")


if __name__ == "__main__":
    unittest.main()
