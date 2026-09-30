import base64
import json
import tempfile
import unittest
from pathlib import Path

from server import DemoApplication


ROOT = Path(__file__).resolve().parents[1]


class DemoApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = DemoApplication(
            data_dir=ROOT / "data",
            evidence_dir=Path(self.temp.name) / "evidence",
            web_dir=ROOT / "web",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def unpack(response):
        status, content_type, payload = response
        if content_type == "application/json" and isinstance(payload, (bytes, str)):
            payload = json.loads(payload)
        return status, payload

    def test_health_reports_browser_speech_and_local_retrieval_limits(self):
        status, body = self.unpack(self.app.dispatch("GET", "/api/health"))

        self.assertEqual(status, 200)
        self.assertEqual(body["stt"]["provider"], "Browser Web Speech API")
        self.assertEqual(body["retrieval"]["mode"], "weighted lexical")
        self.assertFalse(body["llm"]["configured"])

    def test_session_answer_route_returns_grounded_citation(self):
        status, session = self.unpack(self.app.dispatch("POST", "/api/session", {"locale": "en-PH"}))
        self.assertEqual(status, 201)

        answer_status, answer = self.unpack(self.app.dispatch("POST", "/api/answer", {
            "session_id": session["session_id"],
            "message": "When does the demo renewal review window open?",
        }))

        self.assertEqual(answer_status, 200)
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["citations"][0]["record_id"], "ph_renewal_window")

    def test_disclosure_event_is_required_before_close_and_is_locale_independent(self):
        for locale in ("en-PH", "fil-PH", "id-ID"):
            _, missing_disclosure = self.unpack(self.app.dispatch("POST", "/api/session", {"locale": locale}))
            _, missing_result = self.unpack(self.app.dispatch("POST", "/api/close", {"call_id": missing_disclosure["session_id"], "locale": locale}))
            self.assertIn("disclosure_gap", {item["signal"] for item in missing_result["emitted"]}, locale)

            _, session = self.unpack(self.app.dispatch("POST", "/api/session", {"locale": locale}))
            self.app.dispatch("POST", "/api/signals", {
                "call_id": session["session_id"],
                "locale": locale,
                "speaker": "agent",
                "input_kind": "generated",
                "disclosure_delivered": True,
            })
            _, result = self.unpack(self.app.dispatch("POST", "/api/close", {"call_id": session["session_id"], "locale": locale}))
            self.assertNotIn("disclosure_gap", {item["signal"] for item in result["emitted"]}, locale)

    def test_agent_rider_wording_does_not_emit_customer_interest_nudge(self):
        status, result = self.unpack(self.app.dispatch("POST", "/api/signals", {
            "call_id": "agent-message",
            "text": "Ask about the rider only if the customer wants an approved option.",
            "speaker": "agent",
            "input_kind": "generated",
        }))

        self.assertEqual(status, 200)
        self.assertNotIn("missed_cross_sell", {item["signal"] for item in result["emitted"]})

    def test_signal_route_emits_nudge_and_metrics(self):
        _, session = self.unpack(self.app.dispatch("POST", "/api/session", {"locale": "en-PH"}))

        status, result = self.unpack(self.app.dispatch("POST", "/api/signals", {
            "call_id": session["session_id"],
            "text": "I have another policy and want to ask about a rider.",
            "speaker": "customer",
            "asr_latency_ms": 450,
            "input_kind": "speech",
        }))

        self.assertEqual(status, 200)
        self.assertEqual(result["emitted"][0]["signal"], "missed_cross_sell")
        self.assertGreaterEqual(result["timings_ms"]["end_to_end"], 450)
        self.assertEqual(self.app.measurements["asr"], [450.0])

    def test_client_metric_names_are_recorded_and_typed_text_is_not_asr(self):
        status, report = self.unpack(self.app.dispatch("POST", "/api/metrics", {"delivery_ms": 12.5, "end_to_end_ms": 35.0}))

        self.assertEqual(status, 200)
        self.assertEqual(report["components"]["delivery"]["count"], 1)
        self.assertEqual(report["components"]["end_to_end"]["count"], 1)

        self.app.dispatch("POST", "/api/signals", {
            "call_id": "typed-call",
            "text": "I am frustrated.",
            "speaker": "customer",
            "input_kind": "typed",
        })
        _, after_typed = self.unpack(self.app.dispatch("GET", "/api/metrics"))
        self.assertEqual(after_typed["components"]["asr"]["count"], 0)

    def test_saved_transcript_keeps_turn_timestamps(self):
        created_at = "2026-09-29T10:00:00+00:00"
        status, saved = self.unpack(self.app.dispatch("POST", "/api/evidence", {
            "consent": True,
            "test_persona": True,
            "locale": "en-PH",
            "transcript": [{"role": "customer", "text": "Test utterance.", "created_at": created_at}],
            "audio_mime": "audio/webm",
            "audio_base64": base64.b64encode(b"synthetic-audio-bytes").decode("ascii"),
        }))

        self.assertEqual(status, 201)
        self.assertEqual(saved["transcript"][0]["created_at"], created_at)

    def test_evidence_requires_explicit_consent_and_test_persona(self):
        rejected, body = self.unpack(self.app.dispatch("POST", "/api/evidence", {"consent": False, "test_persona": True}))

        self.assertEqual(rejected, 400)
        self.assertIn("consent", body["error"].lower())

    def test_consented_recording_stores_audio_and_redacts_transcript_pii(self):
        status, saved = self.unpack(self.app.dispatch("POST", "/api/evidence", {
            "consent": True,
            "test_persona": True,
            "locale": "en-PH",
            "transcript": [
                {"role": "customer", "text": "Call me at 09171234567."},
                {"role": "agent", "text": "That information is unavailable."},
            ],
            "audio_mime": "audio/webm",
            "audio_base64": base64.b64encode(b"synthetic-audio-bytes").decode("ascii"),
            "results": {"grounding": "pass"},
        }))

        self.assertEqual(status, 201)
        self.assertTrue(saved["audio_saved"])
        self.assertEqual(saved["audio_source"], "microphone")
        transcript = saved["transcript"]
        self.assertIn("[REDACTED_PHONE]", transcript[0]["text"])
        self.assertNotIn("09171234567", json.dumps(transcript))
        audio_status, _, audio = self.app.dispatch("GET", f"/api/evidence/{saved['evidence_id']}/audio")
        self.assertEqual(audio_status, 200)
        self.assertEqual(audio, b"synthetic-audio-bytes")

    def test_static_home_page_is_served(self):
        status, content_type, payload = self.app.dispatch("GET", "/")

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIn(b"Try the voice agent", payload)

    def test_unknown_session_is_rejected(self):
        status, body = self.unpack(self.app.dispatch("POST", "/api/answer", {
            "session_id": "missing-session",
            "message": "When is renewal?",
        }))

        self.assertEqual(status, 404)
        self.assertIn("session", body["error"].lower())


if __name__ == "__main__":
    unittest.main()
