"""Local-only HTTP app for the synthetic voice-agent prototype."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from agent import ConversationAgent
from knowledge import KnowledgeBase, load_records, redact_pii
from nudges import NudgeEngine, percentile


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
EVIDENCE_DIR = ROOT / "evidence" / "calls"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4", "audio/wav": ".wav", "audio/x-wav": ".wav"}
LOCALES = {"en-PH", "fil-PH", "id-ID"}


class DemoApplication:
    def __init__(self, *, data_dir: Path = DATA_DIR, evidence_dir: Path = EVIDENCE_DIR, web_dir: Path = WEB_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.evidence_dir = Path(evidence_dir)
        self.web_dir = Path(web_dir)
        records = []
        for path in (self.data_dir / "demo_kb.json", self.data_dir / "imported_records.json"):
            if path.exists():
                records.extend(load_records(path))
        self.kb = KnowledgeBase(records)
        self.agent = ConversationAgent(self.kb)
        self.nudges = NudgeEngine()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.measurements: dict[str, list[float]] = defaultdict(list)

    def dispatch(self, method: str, target: str, payload: dict[str, Any] | None = None) -> tuple[int, str, Any]:
        payload = payload or {}
        parsed = urlsplit(target)
        path = unquote(parsed.path)
        try:
            if method == "GET":
                return self._get(path, parse_qs(parsed.query))
            if method == "POST":
                return self._post(path, payload)
            return 405, "application/json", {"error": "Method not allowed."}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return 400, "application/json", {"error": str(exc)}

    def _get(self, path: str, query: dict[str, list[str]]) -> tuple[int, str, Any]:
        if path == "/api/health":
            return 200, "application/json", {
                "app": "Knowledge-grounded Voice Agent",
                "mode": "local synthetic demo",
                "stt": {"provider": "Browser Web Speech API", "model": "browser-managed; not exposed by the browser", "configured": True, "languages": ["en-PH", "fil-PH", "id-ID"]},
                "tts": {"provider": "browser speechSynthesis", "model": "installed browser voices", "configured": True},
                "retrieval": {"mode": "weighted lexical", "embeddings_configured": False, "records": len(self.kb.records)},
                "llm": {"configured": False, "mode": "extractive answers from approved records"},
                "recording": {"requires_consent": True, "persona_required": True, "storage": "local evidence/calls"},
            }
        if path == "/api/kb/records":
            return 200, "application/json", {"records": self.kb.records, "count": len(self.kb.records)}
        if path == "/api/scenarios":
            scenario_path = self.data_dir / "test_scenarios.json"
            if not scenario_path.exists():
                return 200, "application/json", {"scenarios": []}
            return 200, "application/json", {"scenarios": json.loads(scenario_path.read_text(encoding="utf-8"))}
        if path == "/api/localization":
            examples_path = self.data_dir / "localization_examples.json"
            if not examples_path.exists():
                return 200, "application/json", {"PH": [], "ID": []}
            return 200, "application/json", json.loads(examples_path.read_text(encoding="utf-8"))
        if path == "/api/metrics":
            return 200, "application/json", self._metrics_report()
        if path == "/api/evidence":
            return 200, "application/json", {"calls": self._evidence_list()}
        if path == "/api/evaluation":
            from scripts.evaluate_kb import run
            report = run(self.evidence_dir.parent / "retrieval_results.json")
            return 200, "application/json", report
        if path == "/api/signals":
            call_id = query.get("call_id", [""])[0]
            return 200, "application/json", {"nudges": self.nudges.active(call_id)}
        if path == "/" or path.startswith("/web/"):
            relative = "index.html" if path == "/" else path.removeprefix("/web/")
            return self._static(relative)
        match = re.fullmatch(r"/api/evidence/([0-9a-f-]{36})(?:/audio)?", path, re.I)
        if match:
            evidence_id = match.group(1)
            record = self._load_evidence(evidence_id)
            if record is None:
                return 404, "application/json", {"error": "Evidence record not found."}
            if path.endswith("/audio"):
                audio_path = self.evidence_dir / record.get("audio_file", "")
                if not audio_path.is_file() or audio_path.parent.resolve() != self.evidence_dir.resolve():
                    return 404, "application/json", {"error": "Recording not found."}
                return 200, record["audio_mime"], audio_path.read_bytes()
            return 200, "application/json", record
        return 404, "application/json", {"error": "Not found."}

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, str, Any]:
        if path == "/api/session":
            locale = str(payload.get("locale", "en-PH"))
            if locale not in LOCALES:
                raise ValueError("Choose one of the supported demo locales.")
            session = self.agent.new_session(locale)
            session["transcript"].append({"role": "agent", "text": self.agent.greeting(locale), "status": "greeting", "source_ids": []})
            self.sessions[session["session_id"]] = session
            return 201, "application/json", {
                "session_id": session["session_id"],
                "locale": locale,
                "greeting": self.agent.greeting(locale),
                "created_at": session["created_at"],
            }
        if path == "/api/answer":
            session_id = str(payload.get("session_id", ""))
            session = self.sessions.get(session_id)
            if not session:
                return 404, "application/json", {"error": "Call session not found."}
            message = str(payload.get("message", "")).strip()
            if not message or len(message) > 2000:
                raise ValueError("Message must contain between 1 and 2,000 characters.")
            started = time.perf_counter()
            answer = self.agent.handle(session, message)
            answer["timings_ms"] = {"retrieval_and_response": round((time.perf_counter() - started) * 1000, 2)}
            return 200, "application/json", answer
        if path == "/api/signals":
            call_id = str(payload.get("call_id", ""))
            if not call_id or len(call_id) > 80:
                raise ValueError("A valid call ID is required.")
            text = str(payload.get("text", ""))
            if len(text) > 4000:
                raise ValueError("Signal input exceeds 4,000 characters.")
            speaker = str(payload.get("speaker", "customer"))
            if speaker not in {"customer", "agent"}:
                raise ValueError("Speaker must be customer or agent.")
            input_kind = str(payload.get("input_kind", "typed"))
            if input_kind not in {"speech", "typed", "generated"}:
                raise ValueError("Input kind must be speech, typed, or generated.")
            started = time.perf_counter()
            locale = self.sessions.get(call_id, {}).get("locale", str(payload.get("locale", "en-PH")))
            emitted = self.nudges.process(
                call_id,
                text,
                closing=bool(payload.get("closing", False)),
                locale=locale,
                speaker=speaker,
                disclosure_delivered=payload.get("disclosure_delivered") is True,
            )
            signal_ms = round((time.perf_counter() - started) * 1000, 2)
            self.measurements["signal_extraction"].append(signal_ms)
            try:
                asr_ms = min(120000.0, max(0.0, float(payload.get("asr_latency_ms", 0))))
            except (ValueError, TypeError):
                asr_ms = 0.0
            if speaker == "customer" and input_kind == "speech":
                self.measurements["asr"].append(asr_ms)
            return 200, "application/json", {
                "emitted": emitted,
                "active": self.nudges.active(call_id),
                "timings_ms": {"asr": round(asr_ms, 2), "signal_extraction": signal_ms, "llm": 0, "delivery": 0, "end_to_end": round(asr_ms + signal_ms, 2)},
            }
        if path == "/api/metrics":
            names = {"asr", "signal_extraction", "llm", "delivery", "end_to_end"}
            for name in names:
                value = payload.get(name)
                if value is None:
                    value = payload.get(f"{name}_ms")
                if isinstance(value, (int, float)) and 0 <= value <= 120000:
                    self.measurements[name].append(float(value))
            return 200, "application/json", self._metrics_report()
        if path == "/api/close":
            call_id = str(payload.get("call_id", ""))
            if not call_id:
                raise ValueError("A call ID is required.")
            locale = self.sessions.get(call_id, {}).get("locale", str(payload.get("locale", "en-PH")))
            emitted = self.nudges.process(call_id, str(payload.get("text", "Thank you. Goodbye.")), closing=True, locale=locale, speaker="system")
            return 200, "application/json", {"emitted": emitted, "active": self.nudges.active(call_id)}
        if path == "/api/evidence":
            return self._save_evidence(payload)
        return 404, "application/json", {"error": "Not found."}

    def _save_evidence(self, payload: dict[str, Any]) -> tuple[int, str, Any]:
        if payload.get("consent") is not True:
            raise ValueError("Explicit recording consent is required before saving a call.")
        if payload.get("test_persona") is not True:
            raise ValueError("Use a synthetic test persona; do not record customer data.")
        locale = str(payload.get("locale", "en-PH"))
        if locale not in LOCALES:
            raise ValueError("Unsupported call locale.")
        transcript = payload.get("transcript")
        if not isinstance(transcript, list) or not transcript:
            raise ValueError("A non-empty transcript is required.")
        safe_transcript = []
        for entry in transcript[:500]:
            if not isinstance(entry, dict) or entry.get("role") not in {"customer", "agent"}:
                continue
            safe_text, _ = redact_pii(str(entry.get("text", ""))[:2000])
            raw_timestamp = str(entry.get("created_at", ""))[:64]
            try:
                datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
                timestamp = raw_timestamp
            except ValueError:
                timestamp = datetime.now(timezone.utc).isoformat()
            source_ids = entry.get("source_ids", [])
            if not isinstance(source_ids, list):
                source_ids = []
            safe_transcript.append({
                "role": entry["role"],
                "text": safe_text,
                "status": str(entry.get("status", ""))[:40],
                "source_ids": [str(item)[:80] for item in source_ids if isinstance(item, str)][:10],
                "created_at": timestamp,
            })
        if not safe_transcript:
            raise ValueError("Transcript did not contain valid call turns.")
        mime = str(payload.get("audio_mime", ""))
        extension = AUDIO_TYPES.get(mime)
        encoded_audio = str(payload.get("audio_base64", ""))
        if not extension or not encoded_audio or len(encoded_audio) > int(MAX_AUDIO_BYTES * 4 / 3) + 8:
            raise ValueError("A supported audio recording up to 20 MiB is required.")
        try:
            audio = base64.b64decode(encoded_audio, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Recording data was not valid base64.") from exc
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("Recording must be between 1 byte and 20 MiB.")

        evidence_id = str(uuid.uuid4())
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        audio_name = f"{evidence_id}{extension}"
        audio_path = self.evidence_dir / audio_name
        audio_path.write_bytes(audio)
        record = {
            "evidence_id": evidence_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "locale": locale,
            "test_persona": True,
            "consent_recorded": True,
            "audio_file": audio_name,
            "audio_mime": mime,
            "audio_source": "microphone",
            "audio_notice": "Audio contains microphone input only; the full speaker-labeled transcript includes agent and customer turns.",
            "transcript": safe_transcript,
            "results": payload.get("results", {}) if isinstance(payload.get("results"), dict) else {},
            "recording_notice": "Stored locally after explicit consent. Delete evidence/calls when no longer needed.",
        }
        (self.evidence_dir / f"{evidence_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return 201, "application/json", {**record, "audio_saved": True}

    def _metrics_report(self) -> dict[str, Any]:
        components = {}
        for name in ("asr", "signal_extraction", "llm", "delivery", "end_to_end"):
            samples = self.measurements.get(name, [])
            components[name] = {
                "count": len(samples),
                "p50_ms": percentile(samples, 50),
                "p95_ms": percentile(samples, 95),
            }
        return {
            "components": components,
            "environment": "localhost demo; timings vary by browser, microphone, and network; LLM component is not used",
            "sample_count": max((item["count"] for item in components.values()), default=0),
        }

    def _evidence_list(self) -> list[dict[str, Any]]:
        if not self.evidence_dir.exists():
            return []
        records = []
        for path in sorted(self.evidence_dir.glob("*.json"), reverse=True):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                records.append({key: item[key] for key in ("evidence_id", "created_at", "locale", "audio_file", "audio_mime", "audio_source", "audio_notice", "transcript", "results") if key in item})
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return records

    def _load_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f-]{36}", evidence_id, re.I):
            return None
        path = self.evidence_dir / f"{evidence_id}.json"
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            return item if item.get("evidence_id") == evidence_id else None
        except (OSError, json.JSONDecodeError):
            return None

    def _static(self, relative: str) -> tuple[int, str, Any]:
        base = self.web_dir.resolve()
        target = (base / relative).resolve()
        if base not in target.parents and target != base:
            return 404, "application/json", {"error": "Not found."}
        if not target.is_file():
            return 404, "application/json", {"error": "Not found."}
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime += "; charset=utf-8"
        return 200, mime, target.read_bytes()


class DemoRequestHandler(BaseHTTPRequestHandler):
    app: DemoApplication

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_AUDIO_BYTES * 2:
            self._send(400, "application/json", {"error": "Request body must be between 1 byte and 40 MiB."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON request body must be an object.")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send(400, "application/json", {"error": f"Invalid JSON body: {exc}"})
            return
        self._handle("POST", payload)

    def _handle(self, method: str, payload: dict[str, Any] | None = None) -> None:
        status, content_type, body = self.app.dispatch(method, self.path, payload)
        self._send(status, content_type, body)

    def _send(self, status: int, content_type: str, body: Any) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8") if content_type == "application/json" else str(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        super().log_message(format, *args)


def create_server(port: int = 8765) -> ThreadingHTTPServer:
    app = DemoApplication()
    handler = type("BoundDemoRequestHandler", (DemoRequestHandler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local-only knowledge-grounded voice-agent demo.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VOICE_DEMO_PORT", "8765")))
    args = parser.parse_args()
    server = create_server(args.port)
    print(f"Voice-agent demo available at http://127.0.0.1:{args.port}")
    print("The server only binds to localhost. Press Ctrl+C to stop it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local demo server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
