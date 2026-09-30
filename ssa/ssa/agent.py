"""Deterministic, source-grounded conversation controller for the demo."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from knowledge import KnowledgeBase, redact_pii


HUMAN_RE = re.compile(r"\b(human|person|speak to someone|talk to someone|speak to a representative|talk to a representative|petugas|kinatawan)\b", re.I)
CALLBACK_REQUEST_RE = re.compile(r"\b(call me(?: back)?|call back later|please call|ask (?:a|the) representative to call|request (?:a )?callback|i (?:want|need|would like) (?:a )?callback|i'd like (?:a )?callback|hubungi saya(?: lagi)?|telepon saya(?: lagi)?|mohon hubungi saya|mohon telepon saya|(?:paki-)?tawagan(?: ako)?)\b", re.I)
CALLBACK_INFO_RE = re.compile(r"\b(?:what(?:'s| is)|define|explain|how does|how do i (?:request|ask)|how can i (?:request|ask))\b.{0,70}\b(callback|call back|representative to call)\b", re.I)
CALLBACK_REFUSAL_RE = re.compile(r"\b(?:do not|don't|dont|never|no need to|please don't|please do not|i don't want you to|i do not want you to|i didn't|i did not|don't|do not)\s+(?:call me(?: back)?|call back|callback|request (?:a )?callback|want (?:a )?callback|contact me|hubungi saya|telepon saya)\b", re.I)
CONFIRM_RE = re.compile(r"\b(yes(?: please)?|sure|that would help|okay|ok|sige|oo)\b", re.I)
DECLINE_RE = re.compile(r"\b(no(?: thanks)?|no thank you|not now|huwag na|ayaw ko|tidak|jangan)\b", re.I)
INCOMPLETE_RE = re.compile(r"\b(not sure which|don't know which|do not know which|different dates|conflicting|not sure about the date|which schedule is current|tidak tahu jadwal mana|jadwal mana yang paling baru|jadwalnya tidak jelas)\b", re.I)
OBJECTION_RE = re.compile(r"\b(too expensive|can't afford|cannot afford|premium is high|not sure about renewing|not sure i want|mabigat|mahal|terlalu mahal|sulit bayar)\b", re.I)
YES_RE = re.compile(r"\b(i want to renew|i'd like to renew|i would like to renew|yes,? renew|gusto kong mag-renew|oo,? mag-renew|ingin memperpanjang|saya mau lanjut)\b", re.I)
NO_RE = re.compile(r"\b(i don't want to renew|i do not want to renew|no,? don't renew|ayaw kong mag-renew|tidak ingin memperpanjang|saya tidak mau lanjut)\b", re.I)


def _locale_scope(locale: str) -> tuple[str | None, str | None]:
    if locale.startswith("id"):
        return "ID", "consumer_finance"
    if locale.startswith("fil") or locale.startswith("en-PH"):
        return "PH", "life"
    return None, None


def _intent_message(locale: str, intent: str) -> str:
    if locale.startswith("id"):
        if intent == "yes":
            return "Saya mencatat minat Anda untuk melanjutkan. Petugas dapat memeriksa rincian pembiayaan yang berlaku."
        if intent == "no":
            return "Saya mencatat bahwa Anda belum ingin melanjutkan. Saya bisa meminta petugas untuk membantu."
        return "Saya mencatat bahwa Anda masih mempertimbangkan. Petugas dapat menjelaskan rincian yang berlaku."
    if locale.startswith("fil"):
        if intent == "yes":
            return "Naitala ko ang interes mong mag-renew. Maaaring kumpirmahin ng kinatawan ang aktuwal na detalye ng policy."
        if intent == "no":
            return "Naitala ko na ayaw mo munang mag-renew. Maaari akong humingi ng tulong ng kinatawan."
        return "Naitala ko na pinag-iisipan mo pa ito. Maaaring ipaliwanag ng kinatawan ang aktuwal na detalye ng policy."
    if intent == "yes":
        return "I've noted your interest in renewal. A representative can confirm the applicable policy details."
    if intent == "no":
        return "I've noted that you don't want to renew yet. I can ask a representative to help."
    return "I've noted that you're still considering it. A representative can explain the applicable details."


def _callback_action(message: str) -> dict[str, Any]:
    return {
        "type": "mock_callback_request",
        "explicit_request": True,
        "preferred_window": _window(message),
        "summary": message[:240],
        "source_ids": [],
    }


def _greeting(locale: str) -> str:
    if locale.startswith("id"):
        return "Selamat datang. Ini demo pengingat pembiayaan. Jangan bagikan nomor identitas atau rekening. Apakah Anda ingin memeriksa jadwal cicilan demo?"
    if locale.startswith("fil"):
        return "Kumusta! Demo ito ng life insurance renewal. Huwag magbahagi ng policy number o sensitibong detalye. Gusto mo bang alamin ang demo renewal information?"
    return "Hello! This is a synthetic insurance-renewal demo. Please don't share policy numbers or sensitive details. What would you like to check?"


def _clarification(locale: str) -> str:
    if locale.startswith("id"):
        return "Saya belum yakin jadwal mana yang terbaru. Apakah Anda ingin saya meminta petugas memeriksa sumber yang berlaku?"
    if locale.startswith("fil"):
        return "Hindi malinaw kung aling policy schedule ang kasalukuyan. Maaari ko itong ipa-review sa kinatawan."
    return "I don't have enough information to tell which schedule is current. Could you confirm which notice you're referring to, or should I ask a representative?"


def _window(text: str) -> str | None:
    match = re.search(r"\b(after|before|around|at)\s+([\w: ]{1,20}(?:am|pm|morning|afternoon|evening)?)\b", text, re.I)
    if not match:
        return None
    return " ".join(match.group(0).split())[:40]


class ConversationAgent:
    def __init__(self, knowledge_base: KnowledgeBase) -> None:
        self.kb = knowledge_base

    def new_session(self, locale: str = "en-PH") -> dict[str, Any]:
        return {
            "session_id": str(uuid.uuid4()),
            "locale": locale,
            "state": "greeting",
            "qualification": {"renewal_intent": "unknown"},
            "transcript": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def handle(self, session: dict[str, Any], message: str) -> dict[str, Any]:
        locale = str(session.get("locale", "en-PH"))
        market, product = _locale_scope(locale)
        safe_message, _ = redact_pii(message)
        session.setdefault("transcript", []).append({"role": "customer", "text": safe_message})
        intent = session.setdefault("qualification", {}).get("renewal_intent", "unknown")
        current_intent = None
        if YES_RE.search(message):
            intent = "yes"
            current_intent = "yes"
        elif NO_RE.search(message):
            intent = "no"
            current_intent = "no"
        elif re.search(r"\b(maybe|considering|not decided|pag-iisipan|pinag-iisipan|masih mempertimbangkan)\b", message, re.I):
            intent = "considering"
            current_intent = "considering"
        session["qualification"]["renewal_intent"] = intent
        pending_action = session.get("pending_action")

        if HUMAN_RE.search(message):
            session["pending_action"] = None
            action = {
                "type": "mock_escalation",
                "reason": "customer_requested_human",
                "summary": safe_message[:240],
                "source_ids": [],
            }
            response = {
                "status": "escalated",
                "answer": _human_message(locale),
                "citations": [],
                "business_action": action,
                "qualification": dict(session["qualification"]),
                "state": "escalation_requested",
            }
        elif INCOMPLETE_RE.search(message):
            session["pending_action"] = None
            response = {
                "status": "clarification",
                "answer": _clarification(locale),
                "clarifying_question": _clarification(locale),
                "citations": [],
                "qualification": dict(session["qualification"]),
                "state": "needs_clarification",
            }
        elif CALLBACK_REFUSAL_RE.search(message) or (pending_action == "callback" and DECLINE_RE.search(message)):
            session["pending_action"] = None
            response = {
                "status": "callback_declined",
                "answer": _callback_declined_message(locale),
                "citations": [],
                "qualification": dict(session["qualification"]),
                "state": "no_callback_requested",
            }
        elif pending_action == "callback" and CONFIRM_RE.search(message):
            session["pending_action"] = None
            response = {
                "status": "callback_requested",
                "answer": _callback_message(locale),
                "citations": [],
                "business_action": _callback_action(safe_message),
                "qualification": dict(session["qualification"]),
                "state": "next_step_recorded",
            }
        elif CALLBACK_REQUEST_RE.search(message) and not CALLBACK_INFO_RE.search(message):
            response = {
                "status": "callback_requested",
                "answer": _callback_message(locale),
                "citations": [],
                "business_action": _callback_action(safe_message),
                "qualification": dict(session["qualification"]),
                "state": "next_step_recorded",
            }
        elif current_intent and not _is_question(message):
            response = {
                "status": "intent_captured",
                "answer": _intent_message(locale, current_intent),
                "citations": [],
                "qualification": dict(session["qualification"]),
                "state": "qualification_recorded",
            }
        else:
            if pending_action:
                session["pending_action"] = None
            category = "objection" if OBJECTION_RE.search(message) else None
            answer = self.kb.answer(message, locale=locale, market=market, product=product, category=category)
            response = {
                **answer,
                "qualification": dict(session["qualification"]),
                "state": "answered" if answer["status"] == "answered" else answer["status"],
            }
            if category:
                response["intent"] = "objection"
                if "callback" in str(response.get("answer", "")).casefold():
                    session["pending_action"] = "callback"

        response.setdefault("business_action", None)
        response["session_id"] = session["session_id"]
        session["state"] = response["state"]
        session["transcript"].append({
            "role": "agent",
            "text": response["answer"],
            "status": response["status"],
            "source_ids": [citation["record_id"] for citation in response.get("citations", [])],
        })
        return response

    @staticmethod
    def greeting(locale: str = "en-PH") -> str:
        return _greeting(locale)


def _is_question(message: str) -> bool:
    return "?" in message or bool(re.search(r"\b(what|when|how|which|does|can|ano|kailan|paano|apakah|kapan|bagaimana)\b", message, re.I))


def _human_message(locale: str) -> str:
    if locale.startswith("id"):
        return "Tentu. Saya mencatat permintaan bantuan petugas manusia untuk demo ini."
    if locale.startswith("fil"):
        return "Sige. Naitala ko ang request mong makausap ang isang kinatawan."
    return "Of course. I've recorded your request for a human representative in this demo."


def _callback_message(locale: str) -> str:
    if locale.startswith("id"):
        return "Saya mencatat permintaan callback demo. Petugas dapat meninjau waktu yang Anda pilih."
    if locale.startswith("fil"):
        return "Naitala ko ang demo callback request at ang napili mong oras. Walang totoong tawag na na-schedule."
    return "I've recorded the demo callback request and your preferred time. No real call has been scheduled."


def _callback_declined_message(locale: str) -> str:
    if locale.startswith("id"):
        return "Baik, saya tidak mencatat permintaan callback. Tidak ada panggilan yang dijadwalkan."
    if locale.startswith("fil"):
        return "Sige, hindi ako magtatala ng callback request. Walang tawag na ise-schedule."
    return "Understood. I have not recorded a callback request, and no call will be scheduled."
