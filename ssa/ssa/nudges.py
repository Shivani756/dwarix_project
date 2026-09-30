"""Deterministic real-time call signals with confidence gates and suppression."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


SIGNALS = (
    {
        "signal": "compliance_gap",
        "pattern": re.compile(r"\b(guaranteed to cover everything|covers everything|no exclusions|definitely covered|tiyak na covered|pasti ditanggung)\b", re.I),
        "message": "Pause and verify that claim against an approved source; correct it before continuing.",
        "speaker": "agent",
        "confidence": 0.94,
        "priority": "high",
    },
    {
        "signal": "missed_cross_sell",
        "pattern": re.compile(r"\b(another policy|second policy|second vehicle|another car|additional coverage|rider|riders)\b", re.I),
        "message": "The customer raised another policy or rider. Ask whether they want an authorized representative to explain approved options.",
        "speaker": "customer",
        "confidence": 0.78,
        "priority": "normal",
    },
    {
        "signal": "rising_frustration",
        "pattern": re.compile(r"\b(too expensive|can't afford|cannot afford|frustrated|angry|ridiculous|upset|nakakainis|mahal masyado|kesal|terlalu mahal)\b", re.I),
        "message": "Acknowledge the concern and give the customer room to explain before continuing.",
        "speaker": "customer",
        "confidence": 0.82,
        "priority": "normal",
    },
    {
        "signal": "payment_difficulty",
        "pattern": re.compile(r"\b(cannot pay|can't pay|trouble paying|payment difficulty|unable to pay|sulit bayar|gak bisa bayar|tidak mampu membayar|kesulitan membayar)\b", re.I),
        "message": "Offer only the approved support or callback path; don't promise a fee waiver or change to terms.",
        "speaker": "customer",
        "confidence": 0.84,
        "priority": "high",
    },
    {
        "signal": "callback_need",
        "pattern": re.compile(r"\b(call me back|call back later|callback|hubungi saya lagi|telepon nanti)\b", re.I),
        "message": "Record a callback request and preferred time if the customer has given it; don't ask for a phone number in this demo.",
        "speaker": "customer",
        "confidence": 0.9,
        "priority": "normal",
    },
)

LOCALIZED_MESSAGES = {
    "fil": {
        "compliance_gap": "I-pause muna at i-check ang claim sa approved source. Itama ito bago magpatuloy.",
        "missed_cross_sell": "Nabanggit ng customer ang ibang policy o rider. Itanong kung gusto niyang magtanong sa representative tungkol sa approved options.",
        "rising_frustration": "Kilalanin ang concern at bigyan ng oras ang customer na magpaliwanag bago magpatuloy.",
        "payment_difficulty": "Ialok lamang ang approved support o callback; huwag mangakong mawawala ang fee o magbabago ang terms.",
        "callback_need": "Itala ang callback request at preferred time kung sinabi; huwag humingi ng phone number sa demo.",
        "disclosure_gap": "Ibigay ang approved demo at policy-terms reminder bago tapusin ang tawag.",
    },
    "id": {
        "compliance_gap": "Hentikan sebentar dan periksa klaim itu pada sumber yang disetujui. Koreksi sebelum melanjutkan.",
        "missed_cross_sell": "Pelanggan menyebut polis lain atau rider. Tanyakan apakah ia ingin petugas menjelaskan pilihan yang disetujui.",
        "rising_frustration": "Akui kekhawatiran pelanggan dan beri waktu untuk menjelaskan sebelum melanjutkan.",
        "payment_difficulty": "Tawarkan callback petugas untuk pilihan dukungan resmi; jangan menjanjikan penghapusan denda atau perubahan tenor.",
        "callback_need": "Catat permintaan callback dan waktu pilihan jika sudah disebut; jangan meminta nomor telepon dalam demo.",
        "disclosure_gap": "Sampaikan pengingat demo dan ketentuan polis yang disetujui sebelum menutup panggilan.",
    },
}


class NudgeEngine:
    def __init__(self, *, confidence_threshold: float = 0.65, cooldown_seconds: int = 8, expiry_seconds: int = 20) -> None:
        self.confidence_threshold = confidence_threshold
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.expiry = timedelta(seconds=expiry_seconds)
        self._last_emitted: dict[tuple[str, str], datetime] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._disclosure_seen: set[str] = set()

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        return current if current.tzinfo else current.replace(tzinfo=timezone.utc)

    def _emit(self, call_id: str, signal: str, message: str, confidence: float, priority: str, now: datetime) -> dict[str, Any] | None:
        if confidence < self.confidence_threshold:
            return None
        key = (call_id, signal)
        previous = self._last_emitted.get(key)
        if previous and now - previous < self.cooldown:
            return None
        event = {
            "id": str(uuid.uuid4()),
            "call_id": call_id,
            "signal": signal,
            "message": message,
            "confidence": confidence,
            "priority": priority,
            "created_at": now.isoformat(),
            "expires_at": (now + self.expiry).isoformat(),
        }
        self._last_emitted[key] = now
        self._events.setdefault(call_id, []).append(event)
        return event

    def process(
        self,
        call_id: str,
        text: str,
        *,
        closing: bool = False,
        locale: str = "en-PH",
        speaker: str = "customer",
        disclosure_delivered: bool = False,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        current = self._now(now)
        if disclosure_delivered:
            self._disclosure_seen.add(call_id)
        emitted = []
        for rule in SIGNALS:
            if speaker == rule["speaker"] and rule["pattern"].search(text):
                language = locale.lower().split("-", 1)[0]
                message = LOCALIZED_MESSAGES.get(language, {}).get(rule["signal"], rule["message"])
                event = self._emit(call_id, rule["signal"], message, rule["confidence"], rule["priority"], current)
                if event:
                    emitted.append(event)
        if closing and call_id not in self._disclosure_seen:
            language = locale.lower().split("-", 1)[0]
            message = LOCALIZED_MESSAGES.get(language, {}).get(
                "disclosure_gap", "Give the approved demo and policy-terms reminder before ending the call."
            )
            event = self._emit(
                call_id,
                "disclosure_gap",
                message,
                0.93,
                "high",
                current,
            )
            if event:
                emitted.append(event)
        return emitted

    def active(self, call_id: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = self._now(now)
        events = self._events.get(call_id, [])
        active_events = []
        for event in events:
            expiry = datetime.fromisoformat(event["expires_at"])
            if expiry > current:
                active_events.append(event)
        return sorted(active_events, key=lambda item: (item["priority"] != "high", item["created_at"]))


def percentile(samples: list[float], percentile_value: float) -> float | None:
    """Return the nearest-rank percentile of nonnegative millisecond samples."""
    if not samples:
        return None
    ordered = sorted(max(0.0, float(sample)) for sample in samples)
    rank = max(1, int((percentile_value / 100) * len(ordered) + 0.999999))
    return round(ordered[min(rank - 1, len(ordered) - 1)], 2)
