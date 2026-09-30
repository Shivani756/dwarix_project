import tempfile
import unittest
from pathlib import Path

from knowledge import KnowledgeBase, ingest_file, redact_pii


def record(record_id, title, content, *, category="faq", market="PH", locale="en-PH", product="life"):
    return {
        "record_id": record_id,
        "title": title,
        "content": content,
        "category": category,
        "product": product,
        "market": market,
        "locale": locale,
        "source_id": f"source-{record_id}",
        "source_uri": f"demo://{record_id}",
        "source_section": title,
        "effective_from": "2025-01-01",
        "effective_to": None,
        "version": "1.0",
        "pii_status": "false",
        "review_status": "demo_approved",
        "content_hash": "",
        "conflict_group": None,
    }


class KnowledgeBaseTests(unittest.TestCase):
    def test_search_returns_relevant_approved_record_and_citation(self):
        kb = KnowledgeBase([
            record("renewal-window", "Renewal timing", "A demo policy can be reviewed 30 days before its renewal date.", category="policy"),
            record("payment-help", "Payment support", "Ask the service team about payment options.", category="faq"),
        ])

        result = kb.answer("When can I review renewal?", locale="en-PH")

        self.assertEqual(result["status"], "answered")
        self.assertIn("30 days", result["answer"])
        self.assertEqual(result["citations"][0]["record_id"], "renewal-window")

    def test_search_filters_market_and_locale(self):
        kb = KnowledgeBase([
            record("ph", "Renewal", "Philippines demo renewal information.", market="PH", locale="en-PH"),
            record("id", "Cicilan", "Informasi cicilan untuk demo Indonesia.", market="ID", locale="id-ID", product="consumer_finance"),
        ])

        results = kb.search("demo cicilan information", market="ID")

        self.assertTrue(results)
        self.assertEqual(results[0]["record"]["record_id"], "id")

    def test_answer_can_restrict_retrieval_to_a_known_business_category(self):
        kb = KnowledgeBase([
            record("policy", "Installment schedule", "Check the demo jatuh tempo for the installment.", category="policy", market="ID", locale="id-ID", product="consumer_finance"),
            record("qualification", "Callback qualification", "The agent can capture installment interest and a preferred callback window.", category="qualification", market="ID", locale="id-ID", product="consumer_finance"),
        ])

        result = kb.answer("What callback window can the agent capture for an installment?", locale="id-ID", market="ID", product="consumer_finance", category="qualification")

        self.assertEqual(result["citations"][0]["record_id"], "qualification")

    def test_search_synonyms_find_a_local_record_without_speaking_the_synonyms(self):
        local = record("id-schedule", "Jadwal cicilan", "Demo saja: periksa tanggal jatuh tempo angsuran melalui kanal resmi.", market="ID", locale="id-ID", product="consumer_finance", category="policy")
        local["search_terms"] = "installment due date reminder schedule"
        kb = KnowledgeBase([local])

        result = kb.answer("When is the installment reminder due?", locale="id-ID", market="ID", product="consumer_finance", category="policy")

        self.assertEqual(result["status"], "answered")
        self.assertIn("jatuh tempo", result["answer"])
        self.assertNotIn("installment", result["answer"])

    def test_unknown_and_conflicting_sources_fail_closed(self):
        first = record("term-a", "Policy term", "Demo premium term A is twelve months.", category="policy")
        second = record("term-b", "Policy term", "Demo premium term B is six months.", category="policy")
        first["conflict_group"] = second["conflict_group"] = "premium-term"
        kb = KnowledgeBase([first, second])

        unknown = kb.answer("What is the capital of France?", locale="en-PH")
        conflict = kb.answer("What is the premium term?", locale="en-PH")

        self.assertEqual(unknown["status"], "unavailable")
        self.assertIn("unavailable", unknown["answer"].lower())
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(len(conflict["citations"]), 2)

    def test_current_policy_version_survives_near_duplicate_old_version(self):
        old = record("renewal-old", "Demo renewal review window", "Review may begin 30 days before the demo renewal date.", category="policy")
        old.update({"source_id": "renewal-notice", "version": "1.0", "effective_from": "2025-01-01", "effective_to": "2026-08-31"})
        current = record("renewal-current", "Demo renewal review window", "Review may begin 60 days before the demo renewal date.", category="policy")
        current.update({"source_id": "renewal-notice", "version": "2.0", "effective_from": "2026-09-01", "effective_to": None})

        kb = KnowledgeBase([old, current])
        result = kb.answer("When may I begin the renewal review?", locale="en-PH", market="PH", product="life")

        self.assertEqual(len(kb.records), 2)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citations"][0]["record_id"], "renewal-current")
        self.assertIn("60 days", result["answer"])

    def test_active_versions_with_same_identity_return_conflict(self):
        first = record("renewal-a", "Demo renewal review window", "Review may begin 30 days before the demo renewal date.", category="policy")
        second = record("renewal-b", "Demo renewal review window", "Review may begin 60 days before the demo renewal date.", category="policy")
        first["source_id"] = second["source_id"] = "renewal-notice"
        first["version"], second["version"] = "1.0", "2.0"

        result = KnowledgeBase([first, second]).answer("When may I begin the renewal review?", locale="en-PH", market="PH", product="life")

        self.assertEqual(result["status"], "conflict")
        self.assertEqual({item["record_id"] for item in result["citations"]}, {"renewal-a", "renewal-b"})

    def test_unsupported_premium_amount_is_not_answered_from_an_objection_script(self):
        guidance = record("premium-guidance", "Responding to a premium cost objection", "If a customer says a premium seems too expensive, acknowledge the concern and offer a representative callback.", category="objection")

        result = KnowledgeBase([guidance]).answer("How much is my premium?", locale="en-PH", market="PH", product="life")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["citations"], [])

    def test_mentioning_a_term_without_defining_it_is_not_answered_as_a_definition(self):
        guidance = record("rider-guidance", "Optional rider discussion", "The demo can record interest in a rider discussion only.", category="product")

        result = KnowledgeBase([guidance]).answer("What is a rider?", locale="en-PH", market="PH", product="life")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["citations"], [])

    def test_html_preserves_inline_word_boundaries_and_table_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "terms.html"
            source.write_text("<h2>Renewal terms</h2><p>Renew <strong>30</strong> days</p><div>Current</div><div>schedule</div><table><tr><td>Premium</td><td>PHP 1,500</td></tr></table>", encoding="utf-8")

            records, warnings = ingest_file(source, metadata={"review_status": "approved"})

        self.assertFalse(warnings)
        self.assertIn("Renew 30 days", records[0]["content"])
        self.assertIn("Current\nschedule", records[0]["content"])
        self.assertIn("Premium", records[0]["content"])
        self.assertIn("PHP 1,500", records[0]["content"])
        self.assertNotIn("PremiumPHP", records[0]["content"])

    def test_redacted_heading_is_not_leaked_in_source_section(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "terms.html"
            source.write_text("<h2>Contact billing at person@example.com</h2><p>Use the demo schedule.</p>", encoding="utf-8")

            records, warnings = ingest_file(source)

        self.assertFalse(warnings)
        self.assertNotIn("person@example.com", records[0]["title"])
        self.assertNotIn("person@example.com", records[0]["source_section"])

    def test_indonesian_conflict_fallback_stays_in_indonesian(self):
        first = record("term-a", "Premium term", "Demo term is twelve months.", category="policy", market="ID", locale="id-ID", product="consumer_finance")
        second = record("term-b", "Premium term", "Demo term is six months.", category="policy", market="ID", locale="id-ID", product="consumer_finance")
        first["conflict_group"] = second["conflict_group"] = "term"

        result = KnowledgeBase([first, second]).answer("What is the premium term?", locale="id-ID", market="ID", product="consumer_finance")

        self.assertEqual(result["status"], "conflict")
        self.assertIn("informasi", result["answer"].lower())
        self.assertNotIn("May informasi", result["answer"])

    def test_unapproved_and_expired_records_are_not_retrieved(self):
        unapproved = record("draft", "Draft", "Renewal window is 30 days.")
        unapproved["review_status"] = "needs_review"
        expired = record("old", "Old", "Renewal window is 60 days.")
        expired["effective_to"] = "2020-01-01"
        kb = KnowledgeBase([unapproved, expired])

        self.assertEqual(kb.search("renewal window"), [])
        self.assertEqual(kb.answer("renewal window")["status"], "unavailable")

    def test_html_ingestion_removes_boilerplate_and_redacts_pii(self):
        source = """<html><nav>Home Products About</nav><main><h1>Renewal</h1>
        <p>Review the renewal notice. Contact demo@example.com.</p></main><footer>Copyright</footer></html>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "renewal.html"
            path.write_text(source, encoding="utf-8")

            records, warnings = ingest_file(path, metadata={"market": "PH", "locale": "en-PH"})

        self.assertFalse(warnings)
        self.assertEqual(len(records), 1)
        content = records[0]["content"]
        self.assertIn("Review the renewal notice", content)
        self.assertNotIn("Home Products", content)
        self.assertNotIn("Copyright", content)
        self.assertNotIn("demo@example.com", content)
        self.assertEqual(records[0]["pii_status"], "redacted")

    def test_html_chunks_headings_and_ignores_cookie_banner(self):
        source = """<html><body><div class='cookie-banner'>Accept cookies</div>
        <main><h1>Renewal dates</h1><p>Review the date notice.</p>
        <h2>Callback</h2><p>Choose a preferred time.</p></main></body></html>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "renewal.html"
            path.write_text(source, encoding="utf-8")

            records, _ = ingest_file(path)

        self.assertEqual([record["title"] for record in records], ["Renewal dates", "Callback"])
        self.assertNotIn("Accept cookies", " ".join(record["content"] for record in records))

    def test_markdown_creates_records_per_heading(self):
        source = "# Premium\nThe premium is synthetic.\n## Callback\nA mock time window can be saved."
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.md"
            path.write_text(source, encoding="utf-8")

            records, _ = ingest_file(path)

        self.assertEqual([record["title"] for record in records], ["Premium", "Callback"])
        self.assertIn("mock time window", records[1]["content"])

    def test_csv_ingestion_maps_columns_to_record_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.csv"
            path.write_text("title,content,category,market,locale\nReminder,No amount is available.,faq,ID,id-ID\n", encoding="utf-8")

            records, warnings = ingest_file(path)

        self.assertFalse(warnings)
        self.assertEqual(records[0]["title"], "Reminder")
        self.assertEqual(records[0]["category"], "faq")
        self.assertEqual(records[0]["market"], "ID")
        self.assertEqual(records[0]["review_status"], "needs_review")

    def test_web_ingestion_rejects_non_https_without_requesting_it(self):
        records, warnings = ingest_file("http://127.0.0.1/private", metadata={"market": "PH"})

        self.assertEqual(records, [])
        self.assertIn("HTTPS", warnings[0])

    def test_image_only_pdf_is_flagged_for_manual_review(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is optional for PDF ingestion")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scanned.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with path.open("wb") as output:
                writer.write(output)

            records, warnings = ingest_file(path)

        self.assertEqual(records, [])
        self.assertIn("no extractable text", warnings[0])

    def test_redaction_masks_phone_numbers_without_changing_amounts(self):
        cleaned, detected = redact_pii("Call 09171234567 about the PHP 1,500 demo premium.")

        self.assertTrue(detected)
        self.assertNotIn("09171234567", cleaned)
        self.assertIn("PHP 1,500", cleaned)

    def test_near_duplicate_records_keep_one_canonical_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "source-one" / "notice.txt"
            second = Path(folder) / "source-two" / "notice.txt"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("A demo renewal notice is available before the policy renewal date.", encoding="utf-8")
            second.write_text("A demo renewal notice is available before the policy's renewal date.", encoding="utf-8")

            metadata = {"market": "PH", "locale": "en-PH", "source_id": "same-approved-source"}
            records, warnings = ingest_file(first, metadata=metadata)
            more_records, more_warnings = ingest_file(second, metadata=metadata)
            kb = KnowledgeBase(records + more_records)

        self.assertFalse(warnings or more_warnings)
        self.assertEqual(len(kb.records), 1)


if __name__ == "__main__":
    unittest.main()
