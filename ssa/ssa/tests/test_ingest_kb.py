import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import ConversationAgent
from knowledge import KnowledgeBase, load_records
from scripts import ingest_kb


class IngestCommandTests(unittest.TestCase):
    def test_failed_import_does_not_replace_existing_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "imported.json"
            original = [{"record_id": "preserve-me", "content": "Existing records stay intact."}]
            output.write_text(json.dumps(original), encoding="utf-8")
            missing = root / "missing.txt"

            with patch.object(sys, "argv", ["ingest_kb.py", str(missing), "--output", str(output)]), contextlib.redirect_stdout(io.StringIO()):
                result = ingest_kb.main()

            self.assertEqual(result, 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), original)

    def test_successful_import_merges_records_and_matches_agent_product(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "notice.md"
            output = root / "imported.json"
            source.write_text("# Renewal window\nFor this synthetic demo, review starts 45 days before renewal.", encoding="utf-8")
            previous = {
                "record_id": "previous", "title": "Older FAQ", "content": "Existing imported context.", "category": "faq",
                "product": "life", "market": "PH", "locale": "en-PH", "source_id": "existing", "source_uri": "demo://existing",
                "source_section": "FAQ", "effective_from": "2025-01-01", "effective_to": None, "version": "1.0",
                "pii_status": "false", "review_status": "approved", "content_hash": "", "conflict_group": None,
            }
            output.write_text(json.dumps([previous]), encoding="utf-8")
            argv = ["ingest_kb.py", str(source), "--market", "PH", "--locale", "en-PH", "--product", "life", "--category", "policy", "--review-status", "approved", "--output", str(output)]

            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                result = ingest_kb.main()

            records = load_records(output)
            agent = ConversationAgent(KnowledgeBase(records))
            reply = agent.handle(agent.new_session("en-PH"), "When does the renewal review start?")

        self.assertEqual(result, 0)
        self.assertEqual(len(records), 2)
        self.assertEqual(reply["status"], "answered")
        self.assertIn("45 days", reply["answer"])


if __name__ == "__main__":
    unittest.main()
