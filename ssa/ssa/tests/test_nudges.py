import unittest
from datetime import datetime, timedelta, timezone

from nudges import NudgeEngine


class NudgeEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = NudgeEngine(cooldown_seconds=8, expiry_seconds=20)
        self.start = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def test_customer_rider_signal_creates_missed_offer_nudge(self):
        nudges = self.engine.process("call-a", "I already have another policy and want to hear about a rider.", now=self.start)

        self.assertEqual(nudges[0]["signal"], "missed_cross_sell")
        self.assertIn("authorized", nudges[0]["message"].lower())

    def test_risky_claim_creates_high_priority_compliance_nudge(self):
        nudges = self.engine.process("call-a", "Your policy is guaranteed to cover everything with no exclusions.", speaker="agent", now=self.start)

        self.assertEqual(nudges[0]["signal"], "compliance_gap")
        self.assertEqual(nudges[0]["priority"], "high")

    def test_frustration_and_payment_difficulty_have_actionable_nudges(self):
        upset = self.engine.process("call-a", "This is too expensive and I cannot pay this month.", now=self.start)

        self.assertEqual({item["signal"] for item in upset}, {"rising_frustration", "payment_difficulty"})
        self.assertTrue(all(item["message"] for item in upset))

    def test_ambiguous_noise_does_not_create_a_nudge(self):
        nudges = self.engine.process("call-a", "um maybe the thing, not sure, background noise", now=self.start)

        self.assertEqual(nudges, [])

    def test_duplicate_signal_is_suppressed_during_cooldown(self):
        first = self.engine.process("call-a", "I am frustrated.", now=self.start)
        duplicate = self.engine.process("call-a", "I am frustrated again.", now=self.start + timedelta(seconds=2))

        self.assertEqual(len(first), 1)
        self.assertEqual(duplicate, [])

    def test_stale_nudges_expire_from_active_feed(self):
        self.engine.process("call-a", "I am frustrated.", now=self.start)

        self.assertEqual(self.engine.active("call-a", now=self.start + timedelta(seconds=21)), [])

    def test_missing_demo_disclosure_is_flagged_before_close(self):
        nudges = self.engine.process("call-a", "Thank you, goodbye.", closing=True, now=self.start)

        self.assertEqual(nudges[0]["signal"], "disclosure_gap")
        self.assertEqual(nudges[0]["priority"], "high")

    def test_indonesian_nudge_stays_in_the_customer_language(self):
        nudges = self.engine.process("call-id", "Saya kesulitan membayar cicilan.", locale="id-ID", now=self.start)

        self.assertEqual(nudges[0]["signal"], "payment_difficulty")
        self.assertIn("petugas", nudges[0]["message"].lower())
        self.assertNotIn("payment", nudges[0]["message"].lower())

    def test_agent_rider_answer_does_not_trigger_customer_buying_signal(self):
        nudges = self.engine.process("call-agent", "Ask about the rider only if the customer wants an approved option.", speaker="agent", now=self.start)

        self.assertEqual(nudges, [])

    def test_filipino_nudge_uses_fil_locale_copy(self):
        nudges = self.engine.process("call-fil", "Mahal masyado ang premium.", locale="fil-PH", now=self.start)

        self.assertEqual(nudges[0]["signal"], "rising_frustration")
        self.assertIn("Kilalanin", nudges[0]["message"])

    def test_localized_greetings_satisfy_disclosure_before_close(self):
        greetings = {
            "en-PH": "Hello! This is a synthetic insurance-renewal demo.",
            "fil-PH": "Kumusta! Demo ito ng life insurance renewal.",
            "id-ID": "Selamat datang. Ini demo pengingat pembiayaan.",
        }
        for index, (locale, greeting) in enumerate(greetings.items()):
            call_id = f"call-greeting-{index}"
            self.engine.process(call_id, greeting, speaker="agent", disclosure_delivered=True, locale=locale, now=self.start)
            closing = self.engine.process(call_id, "Thank you, goodbye.", speaker="system", closing=True, locale=locale, now=self.start)
            self.assertNotIn("disclosure_gap", {item["signal"] for item in closing}, locale)


if __name__ == "__main__":
    unittest.main()
